"""Módulo de Sorteos y Ruleta de la Suerte para directos.
Registra espectadores activos en el live de TikTok (comentarios, likes, regalos, comando !sorteo)
y permite hacer sorteos automáticos de cofres de Streamloots con ruleta visual y sonido."""
import json
import random
import re
import sys
import time
from pathlib import Path
from threading import Lock
from flask import Blueprint, jsonify, render_template, request
import live

BASE = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
ARCHIVO = BASE / "sorteo.json"

bp = Blueprint("sorteo", __name__)
lock = Lock()

# Registro de espectadores activos en memoria: nombre -> {"ts": time, "uid": str, "tipo": str, "sorteo": bool}
_activos = {}
_ganadores = []
_participantes = []
_spin_id = 0
_ultimo_ganador = None

_CFG_DEF = {
    "premio": "Cofre de Streamloots",
    "eliminar_ganador": True,
    "tiempo_activos": 10,  # minutos
    "participantes": []
}
cfg = dict(_CFG_DEF)


def _guardar():
    try:
        data = {
            "premio": cfg.get("premio", "Cofre de Streamloots"),
            "eliminar_ganador": bool(cfg.get("eliminar_ganador", True)),
            "tiempo_activos": int(cfg.get("tiempo_activos", 10)),
            "participantes": list(_participantes)[:300],
            "ganadores": list(_ganadores)[-50:]
        }
        ARCHIVO.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _cargar():
    global _participantes, _ganadores, cfg
    try:
        if ARCHIVO.exists():
            d = json.loads(ARCHIVO.read_text(encoding="utf-8"))
            cfg["premio"] = str(d.get("premio", "Cofre de Streamloots"))[:60]
            cfg["eliminar_ganador"] = bool(d.get("eliminar_ganador", True))
            cfg["tiempo_activos"] = max(0, min(120, int(d.get("tiempo_activos", 10))))
            _participantes = [str(p).strip()[:40] for p in d.get("participantes", []) if str(p).strip()]
            _ganadores = list(d.get("ganadores", []))[-50:]
    except Exception:
        pass


_cargar()


def registrar_actividad(nombre, uid="", tipo="chat", comando_sorteo=False):
    """Registra la actividad de un espectador del directo para saber quién está activo."""
    nombre = str(nombre or "").strip()
    if not nombre:
        return
    ahora = time.time()
    with lock:
        prev = _activos.get(nombre, {})
        sorteo_val = bool(comando_sorteo or prev.get("sorteo", False))
        _activos[nombre] = {
            "ts": ahora,
            "uid": str(uid or prev.get("uid", "")),
            "tipo": tipo,
            "sorteo": sorteo_val
        }
        # Limpiar registros de más de 4 horas para no acumular memoria infinita
        if len(_activos) > 2000:
            limite = ahora - 14400
            for k in list(_activos.keys()):
                if _activos[k]["ts"] < limite:
                    _activos.pop(k, None)


def comando_sorteo(nombre, uid=""):
    """Llamado cuando alguien escribe !sorteo, !ruleta, !participar, !yo en el chat."""
    registrar_actividad(nombre, uid=uid, tipo="comando_sorteo", comando_sorteo=True)
    live.log(f"🎁 Sorteo: {nombre} se ha registrado para el sorteo")


def obtener_activos(minutos=10, solo_comando=False):
    """Devuelve la lista de nombres de espectadores activos en los últimos N minutos."""
    ahora = time.time()
    segundos = minutos * 60 if minutos > 0 else 14400  # 0 = todo el directo (hasta 4h)
    limite = ahora - segundos
    with lock:
        lista = []
        for nombre, datos in _activos.items():
            if datos["ts"] >= limite:
                if solo_comando and not datos.get("sorteo"):
                    continue
                lista.append(nombre)
        # Ordenar alfabéticamente para visualización ordenada
        return sorted(lista, key=lambda s: s.lower())


# ==============================================================
# RUTAS DE INTERFAZ (VISTAS)
# ==============================================================
@bp.route("/sorteo")
def overlay():
    """Overlay para OBS (fondo transparente con ruleta y animación de ganador)."""
    return render_template("sorteo.html")


@bp.route("/sorteo/panel")
def panel():
    """Panel de control del streamer con ruleta interactiva, sonido y gestión de participantes."""
    return render_template("sorteo_panel.html")


# ==============================================================
# API REST
# ==============================================================
@bp.route("/api/sorteo", methods=["GET"])
def api_estado():
    with lock:
        ahora = time.time()
        activos_5m = sum(1 for d in _activos.values() if ahora - d["ts"] <= 300)
        activos_10m = sum(1 for d in _activos.values() if ahora - d["ts"] <= 600)
        total_activos = len(_activos)
        total_sorteo_cmd = sum(1 for d in _activos.values() if d.get("sorteo"))

        return jsonify({
            "ok": True,
            "premio": cfg.get("premio", "Cofre de Streamloots"),
            "eliminar_ganador": cfg.get("eliminar_ganador", True),
            "tiempo_activos": cfg.get("tiempo_activos", 10),
            "participantes": list(_participantes),
            "ganadores": list(_ganadores)[-20:],
            "ultimo_ganador": _ultimo_ganador,
            "spin_id": _spin_id,
            "stats": {
                "activos_5m": activos_5m,
                "activos_10m": activos_10m,
                "total_activos": total_activos,
                "total_con_comando": total_sorteo_cmd
            }
        })


@bp.route("/api/sorteo/cargar_activos", methods=["POST"])
def api_cargar_activos():
    datos = request.get_json(silent=True) or {}
    minutos = int(datos.get("minutos", cfg.get("tiempo_activos", 10)))
    solo_cmd = bool(datos.get("solo_comando", False))
    reemplazar = bool(datos.get("reemplazar", False))

    activos = obtener_activos(minutos=minutos, solo_comando=solo_cmd)
    if not activos:
        return jsonify({"ok": False, "error": "No se encontraron espectadores activos en ese período de tiempo."}), 400

    global _participantes
    with lock:
        if reemplazar:
            _participantes = list(activos)
        else:
            # Añadir sin duplicar
            existentes = {p.lower() for p in _participantes}
            for nombre in activos:
                if nombre.lower() not in existentes:
                    _participantes.append(nombre)
                    existentes.add(nombre.lower())
        _guardar()
        lista = list(_participantes)

    live.log(f"🎁 Sorteo: Se cargaron {len(activos)} espectadores activos a la ruleta.")
    return jsonify({"ok": True, "participantes": lista, "agregados": len(activos)})


@bp.route("/api/sorteo/participantes", methods=["POST"])
def api_guardar_participantes():
    datos = request.get_json(silent=True) or {}
    lista_nueva = datos.get("participantes")
    if lista_nueva is None or not isinstance(lista_nueva, list):
        return jsonify({"ok": False, "error": "Formato de participantes inválido"}), 400

    global _participantes
    limpios = []
    vistos = set()
    for item in lista_nueva:
        nombre = str(item or "").strip()[:40]
        if nombre and nombre.lower() not in vistos:
            vistos.add(nombre.lower())
            limpios.append(nombre)

    with lock:
        _participantes = limpios[:300]
        _guardar()

    return jsonify({"ok": True, "participantes": list(_participantes)})


@bp.route("/api/sorteo/girar", methods=["POST"])
def api_girar():
    global _spin_id, _ultimo_ganador, _participantes
    with lock:
        if not _participantes:
            return jsonify({"ok": False, "error": "No hay participantes en la ruleta."}), 400

        indice_ganador = random.randrange(len(_participantes))
        ganador = _participantes[indice_ganador]
        _spin_id += 1
        _ultimo_ganador = ganador

        premio = cfg.get("premio", "Cofre de Streamloots")
        hora = time.strftime("%H:%M")
        registro_ganador = {"nombre": ganador, "hora": hora, "premio": premio}
        _ganadores.append(registro_ganador)

        # Si está configurado para eliminar al ganador de la ruleta tras ganar
        if cfg.get("eliminar_ganador", True):
            _participantes.pop(indice_ganador)

        _guardar()

        evento_spin = {
            "spin_id": _spin_id,
            "indice_ganador": indice_ganador,
            "ganador": ganador,
            "premio": premio,
            "participantes_conteo": len(_participantes) + (1 if cfg.get("eliminar_ganador") else 0),
            "eliminar_ganador": cfg.get("eliminar_ganador", True)
        }

    # Publicar en SSE para que OBS y el panel giren simultáneamente
    live.publicar("sorteo_girar", evento_spin)
    live.log(f"🎉 Sorteo: ¡Ganador del sorteo ({premio}): {ganador}!")

    return jsonify({
        "ok": True,
        "ganador": ganador,
        "indice_ganador": indice_ganador,
        "premio": premio,
        "spin_id": _spin_id,
        "participantes_restantes": list(_participantes)
    })


@bp.route("/api/sorteo/ajustes", methods=["POST"])
def api_ajustes():
    datos = request.get_json(silent=True) or {}
    with lock:
        if "premio" in datos:
            cfg["premio"] = str(datos["premio"]).strip()[:60]
        if "eliminar_ganador" in datos:
            cfg["eliminar_ganador"] = bool(datos["eliminar_ganador"])
        if "tiempo_activos" in datos:
            cfg["tiempo_activos"] = max(0, min(120, int(datos["tiempo_activos"])))
        _guardar()
    return jsonify({"ok": True, "cfg": cfg})


@bp.route("/api/sorteo/ganadores/limpiar", methods=["POST"])
def api_limpiar_ganadores():
    global _ganadores
    with lock:
        _ganadores.clear()
        _guardar()
    return jsonify({"ok": True})
