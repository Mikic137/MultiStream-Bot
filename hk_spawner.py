"""Módulo Spawner de Jefes y Enemigos de Hollow Knight para TikTok y Kick."""
from flask import Blueprint, render_template, jsonify, request
from pathlib import Path
from threading import RLock
import json
import time

bp = Blueprint("hk", __name__)

BASE = Path(__file__).parent
ARCHIVO_CONFIG = BASE / "hk_config.json"
_lock = RLock()

_CONFIG_DEF = {
    "activo": True,
    "cooldown_segundos": 20,
    "ultimo_spawn": 0,
    "reglas": {
        "grimm": {
            "nombre": "Pesadilla Grimm",
            "prefab_key": "Nightmare Grimm Boss",
            "cantidad": 1,
            "modo": "kick_sub",
            "descripcion": "SOLO por Suscripción en Kick"
        },
        "vasija_pura": {
            "nombre": "Vasija Pura",
            "prefab_key": "HK Prime",
            "cantidad": 1,
            "modo": "kick_puntos_o_tiktok_alto",
            "tiktok_min_diamantes": 500,
            "tiktok_regalos": ["León", "Galaxia", "Avión", "Ballena", "Gorra y bigote"],
            "kick_puntos_min": 10000,
            "kick_recompensa_palabra": "vasija",
            "descripcion": "Regalo Alto en TikTok o Puntos de Canal en Kick"
        },
        "destello_absoluto": {
            "nombre": "Destello Absoluto",
            "prefab_key": "Absolute Radiance",
            "cantidad": 1,
            "modo": "kick_10_o_tiktok_caro",
            "tiktok_min_diamantes": 1000,
            "tiktok_regalos": ["TikTok Universe", "Falcon", "Fénix", "León", "Castillo de fantasía"],
            "kick_donacion_min": 10.0,
            "descripcion": "Regalo Caro en TikTok o Donación de $10 en Kick"
        },
        "enemigos_medianos": {
            "nombre": "Enemigos Medianos",
            "prefab_key": "Moss Knight",
            "cantidad": 2,
            "modo": "tiktok_mediano_o_kick_medio",
            "tiktok_min_diamantes": 50,
            "tiktok_regalos": ["Dona", "Pato", "Dinero volando", "Corazón de papel", "Rosa de cristal"],
            "kick_donacion_min": 3.0,
            "descripcion": "Regalos Medianos en TikTok o Donación de $3-$5 en Kick"
        },
        "enemigos_debiles": {
            "nombre": "Enemigos Débiles (Áspid / Mosca)",
            "prefab_key": "Spitter",
            "cantidad": 3,
            "modo": "tiktok_menor_o_kick_bajo",
            "tiktok_min_diamantes": 1,
            "tiktok_regalos": ["Rosa", "GG", "Corazón", "Café", "Mando", "Dedo arriba"],
            "kick_donacion_min": 1.0,
            "descripcion": "Regalos Menores en TikTok o Donación de $1 en Kick"
        }
    }
}

_config = dict(_CONFIG_DEF)
_cola = []
_historial = []
_ultimo_ping_juego = 0
_ultimo_spawn = 0
_contador_id = 0
_ultimos_spawns = {}


def _cargar_config():
    global _config
    try:
        if ARCHIVO_CONFIG.exists():
            data = json.loads(ARCHIVO_CONFIG.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _config.update(data)
    except Exception as e:
        print(f"[HK] Error cargando hk_config.json: {e}")


def _guardar_config():
    try:
        ARCHIVO_CONFIG.write_text(json.dumps(_config, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"[HK] Error guardando hk_config.json: {e}")


_cargar_config()


def encolar_spawn(clave_enemigo, solicitante="Stream", fuente="manual", cantidad=None, inmediato=False):
    """Añade un enemigo o jefe a la cola de invocación en Hollow Knight."""
    global _contador_id, _ultimo_spawn
    with _lock:
        regla = _config.get("reglas", {}).get(clave_enemigo)
        if not regla:
            return False, f"Enemigo no reconocido: {clave_enemigo}"

        cant = int(cantidad if cantidad is not None else regla.get("cantidad", 1))
        cant = max(1, min(cant, 10))

        _contador_id += 1
        item = {
            "id": _contador_id,
            "clave": clave_enemigo,
            "nombre": regla.get("nombre", clave_enemigo),
            "prefab_key": regla.get("prefab_key", clave_enemigo),
            "cantidad": cant,
            "solicitante": str(solicitante or "Espectador"),
            "fuente": str(fuente or "Stream"),
            "timestamp": time.time(),
            "inmediato": bool(inmediato)
        }

        if inmediato:
            _ultimo_spawn = 0  # reinicia cooldown para tests instantáneos
            _cola.insert(0, item)
        else:
            _cola.append(item)

        _ultimos_spawns[clave_enemigo] = time.time()

        # Limitar tamaño de cola
        if len(_cola) > 50:
            _cola.pop(0)

        print(f"[HK Spawner] Encolado: {item['nombre']} x{cant} por {solicitante} ({fuente})")
        return True, f"Encolado {item['nombre']} x{cant}"


def procesar_comando_chat(usuario, texto, plataforma="tiktok"):
    """
    Procesa comandos de chat (TikTok / Kick) para invocar enemigos en Hollow Knight.
    Aplica cooldown INDEPENDIENTE para cada enemigo.
    """
    if not _config.get("activo", True):
        return False

    txt = (texto or "").strip().lower()
    if not txt.startswith("!"):
        return False

    txt_norm = " ".join(txt.split())
    reglas = _config.get("reglas", {})
    ahora = time.time()

    for clave, regla in reglas.items():
        comandos = [c.lower() for c in regla.get("comandos", [])]
        if txt_norm in comandos:
            cd_total = float(regla.get("cooldown_segundos", 30))
            ult = _ultimos_spawns.get(clave, 0)
            restante = cd_total - (ahora - ult)

            import live
            nombre_enemigo = regla.get("nombre", clave)

            if restante > 0:
                seg_espera = int(restante) + 1
                msg_cd = f"⏳ @{usuario} ¡{nombre_enemigo} está en recarga! Espera {seg_espera}s."
                live.log(f"[HK Cooldown] @{usuario} intentó {texto} pero {nombre_enemigo} está en recarga ({seg_espera}s restantes)")
                try:
                    live.publicar_chat("🎮 HOLLOW KNIGHT", msg_cd, plataforma=plataforma)
                except Exception:
                    pass
                return True

            # Cooldown superado: ejecutar spawn
            _ultimos_spawns[clave] = ahora
            cant = regla.get("cantidad", 1)
            fuente_desc = f"Chat {plataforma.capitalize()} ({texto})"
            encolar_spawn(clave, solicitante=usuario, fuente=fuente_desc, cantidad=cant)

            msg_exito = f"⚔️ @{usuario} invocó a {nombre_enemigo} (x{cant})! Cooldown: {int(cd_total)}s"
            live.log(f"[HK Spawn] Invocado {nombre_enemigo} x{cant} por @{usuario} ({fuente_desc})")
            try:
                live.publicar_chat("🎮 HOLLOW KNIGHT", msg_exito, plataforma=plataforma)
            except Exception:
                pass
            return True

    return False


def registrar_evento(plataforma, tipo_evento, usuario, detalle, valor=0):
    """
    Hook de eventos desde live.py (Kick y TikTok).
    Aplica las reglas estrictas configuradas por el usuario.
    """
    if not _config.get("activo", True):
        return

    plataforma = str(plataforma or "").lower()
    tipo_evento = str(tipo_evento or "").lower()
    usuario = str(usuario or "Espectador")
    detalle = str(detalle or "")
    val = float(valor or 0)

    reglas = _config.get("reglas", {})

    # 1. PESADILLA GRIMM: SOLO por una Sub en Kick
    if plataforma == "kick" and tipo_evento in ("sub", "subscription"):
        encolar_spawn("grimm", solicitante=usuario, fuente=f"⭐ Sub en Kick ({detalle})")
        return

    # 2. DESTELLO ABSOLUTO: Regalo Caro en TikTok o Donación de $10 en Kick
    regla_destello = reglas.get("destello_absoluto", {})
    if plataforma == "kick":
        min_donacion = float(regla_destello.get("kick_donacion_min", 10.0))
        # Detectar si es donación de $10 USD o equivalente en Kicks
        if (tipo_evento in ("donacion", "tip", "kicks") and val >= min_donacion) or ("$10" in detalle or "10$" in detalle or "10 usd" in detalle.lower()):
            encolar_spawn("destello_absoluto", solicitante=usuario, fuente=f"☀️ Donación Kick ${val or 10}")
            return
    elif plataforma == "tiktok" and tipo_evento == "regalo":
        min_diamantes = int(regla_destello.get("tiktok_min_diamantes", 1000))
        regalos_caros = [r.lower() for r in regla_destello.get("tiktok_regalos", [])]
        if val >= min_diamantes or detalle.lower() in regalos_caros:
            encolar_spawn("destello_absoluto", solicitante=usuario, fuente=f"☀️ Regalo Caro TikTok ({detalle})")
            return

    # 3. VASIJA PURA: Regalo Alto en TikTok o Puntos en el canal de Kick
    regla_vasija = reglas.get("vasija_pura", {})
    if plataforma == "kick" and tipo_evento in ("recompensa", "reward", "channel_points"):
        min_puntos = int(regla_vasija.get("kick_puntos_min", 10000))
        palabra_clave = str(regla_vasija.get("kick_recompensa_palabra", "vasija")).lower()
        if palabra_clave in detalle.lower() or val >= min_puntos:
            encolar_spawn("vasija_pura", solicitante=usuario, fuente=f"⚔️ Puntos Kick ({detalle})")
            return
    elif plataforma == "tiktok" and tipo_evento == "regalo":
        min_diamantes = int(regla_vasija.get("tiktok_min_diamantes", 500))
        regalos_altos = [r.lower() for r in regla_vasija.get("tiktok_regalos", [])]
        if val >= min_diamantes or detalle.lower() in regalos_altos:
            encolar_spawn("vasija_pura", solicitante=usuario, fuente=f"⚔️ Regalo Alto TikTok ({detalle})")
            return

    # 4. ENEMIGOS MEDIANOS: Regalos medianos en TikTok o Donación de $3-$5 en Kick
    regla_medianos = reglas.get("enemigos_medianos", {})
    if plataforma == "kick":
        min_donacion = float(regla_medianos.get("kick_donacion_min", 3.0))
        if (tipo_evento in ("donacion", "tip", "kicks") and val >= min_donacion) or ("$3" in detalle or "$5" in detalle):
            encolar_spawn("enemigos_medianos", solicitante=usuario, fuente=f"🌿 Donación Kick ${val or 3}")
            return
    elif plataforma == "tiktok" and tipo_evento == "regalo":
        min_diamantes = int(regla_medianos.get("tiktok_min_diamantes", 50))
        regalos_med = [r.lower() for r in regla_medianos.get("tiktok_regalos", [])]
        if val >= min_diamantes or detalle.lower() in regalos_med:
            encolar_spawn("enemigos_medianos", solicitante=usuario, fuente=f"🌿 Regalo Mediano TikTok ({detalle})")
            return

    # 5. ENEMIGOS DÉBILES: Regalos menores en TikTok o Donación de $1 en Kick
    regla_debiles = reglas.get("enemigos_debiles", {})
    if plataforma == "kick":
        min_donacion = float(regla_debiles.get("kick_donacion_min", 1.0))
        if (tipo_evento in ("donacion", "tip", "kicks") and val >= min_donacion) or "$1" in detalle:
            encolar_spawn("enemigos_debiles", solicitante=usuario, fuente=f"🦟 Donación Kick ${val or 1}")
            return
    elif plataforma == "tiktok" and tipo_evento == "regalo":
        min_diamantes = int(regla_debiles.get("tiktok_min_diamantes", 1))
        regalos_deb = [r.lower() for r in regla_debiles.get("tiktok_regalos", [])]
        if val >= min_diamantes or detalle.lower() in regalos_deb:
            encolar_spawn("enemigos_debiles", solicitante=usuario, fuente=f"🦟 Regalo TikTok ({detalle})")
            return


# ==========================================================
# RUTAS WEB Y API PARA EL PANEL Y PARA EL JUEGO (MOD HOLLOW KNIGHT)
# ==========================================================

@bp.route("/hk/panel")
def panel():
    return render_template("hk_panel.html")


@bp.route("/hk/overlay")
def overlay():
    return render_template("hk_overlay.html")


@bp.route("/api/hk/config", methods=["GET", "POST"])
def api_config():
    global _config
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        with _lock:
            if "activo" in data:
                _config["activo"] = bool(data["activo"])
            if "cooldown_despacho_segundos" in data:
                try:
                    _config["cooldown_despacho_segundos"] = max(0.5, min(float(data["cooldown_despacho_segundos"]), 30.0))
                except (ValueError, TypeError):
                    pass
            if "reglas" in data and isinstance(data["reglas"], dict):
                for k, v in data["reglas"].items():
                    if k in _config.get("reglas", {}) and isinstance(v, dict):
                        _config["reglas"][k].update(v)
            _guardar_config()
        return jsonify({"ok": True, "config": _config})
    return jsonify(_config)


@bp.route("/api/hk/cola", methods=["GET"])
def api_cola():
    """
    Endpoint de alta velocidad consultado por el mod en C# de Hollow Knight (cada 0.5s - 1.0s).
    Controla el delay de despacho seguro entre apariciones para proteger la partida de lag spikes.
    """
    global _ultimo_ping_juego, _ultimo_spawn
    ahora = time.time()
    _ultimo_ping_juego = ahora

    cooldown_despacho = max(0.5, float(_config.get("cooldown_despacho_segundos", 2.0)))
    diferencia = ahora - _ultimo_spawn

    with _lock:
        if not _config.get("activo", True):
            return jsonify({
                "spawn": False,
                "motivo": "pausado",
                "cooldown_restante": 0,
                "en_cola": len(_cola)
            })

        if not _cola:
            return jsonify({
                "spawn": False,
                "motivo": "vacio",
                "cooldown_restante": max(0.0, round(cooldown_despacho - diferencia, 1)),
                "en_cola": 0
            })

        siguiente = _cola[0]
        # Si es un test inmediato o ya pasó el cooldown mínimo de despacho:
        if siguiente.get("inmediato") or diferencia >= cooldown_despacho:
            item = _cola.pop(0)
            _ultimo_spawn = ahora
            item["despachado_a"] = ahora
            _historial.insert(0, item)
            if len(_historial) > 50:
                _historial.pop()

            return jsonify({
                "spawn": True,
                "id": item["id"],
                "clave": item["clave"],
                "enemigo": item["prefab_key"],
                "nombre": item["nombre"],
                "cantidad": item["cantidad"],
                "solicitante": item["solicitante"],
                "fuente": item["fuente"]
            })

        # Sigue en cooldown de despacho
        return jsonify({
            "spawn": False,
            "motivo": "cooldown",
            "cooldown_restante": max(0.0, round(cooldown_despacho - diferencia, 1)),
            "en_cola": len(_cola)
        })


@bp.route("/api/hk/test_spawn", methods=["POST"])
def api_test_spawn():
    data = request.get_json(silent=True) or {}
    clave = data.get("clave", "grimm")
    cantidad = data.get("cantidad")
    inmediato = bool(data.get("inmediato", True))
    ok, msg = encolar_spawn(clave, solicitante="Streamer (Test)", fuente="Panel Web", cantidad=cantidad, inmediato=inmediato)
    return jsonify({"ok": ok, "mensaje": msg})


@bp.route("/api/hk/limpiar_cola", methods=["POST"])
def api_limpiar_cola():
    with _lock:
        _cola.clear()
    return jsonify({"ok": True, "mensaje": "Cola de Hollow Knight vaciada."})


@bp.route("/api/hk/estado")
def api_estado():
    ahora = time.time()
    conectado = (ahora - _ultimo_ping_juego) < 6.0
    cooldown_despacho = max(0.5, float(_config.get("cooldown_despacho_segundos", 2.0)))
    restante_despacho = max(0.0, round(cooldown_despacho - (ahora - _ultimo_spawn), 1))

    # Cooldowns individuales por cada enemigo
    reglas = _config.get("reglas", {})
    cooldowns_enemigos = {}
    for clave, regla in reglas.items():
        cd_total = float(regla.get("cooldown_segundos", 30))
        ult = _ultimos_spawns.get(clave, 0)
        rest = max(0.0, round(cd_total - (ahora - ult), 1))
        cooldowns_enemigos[clave] = {
            "nombre": regla.get("nombre", clave),
            "cooldown_total": int(cd_total),
            "restante": rest,
            "listo": rest <= 0.0,
            "porcentaje": min(100, int(((cd_total - rest) / cd_total) * 100)) if cd_total > 0 else 100,
            "comandos": regla.get("comandos", []),
            "cantidad": regla.get("cantidad", 1)
        }

    return jsonify({
        "activo": _config.get("activo", True),
        "cooldown_despacho": cooldown_despacho,
        "cooldown_restante": restante_despacho,
        "juego_conectado": conectado,
        "ultimo_ping_hace": round(ahora - _ultimo_ping_juego, 1) if _ultimo_ping_juego > 0 else None,
        "en_cola": len(_cola),
        "cola": list(_cola),
        "historial": list(_historial[:15]),
        "reglas": reglas,
        "cooldowns": cooldowns_enemigos
    })

