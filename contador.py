"""Módulo de Contador Regresivo para Dragon Ball Legends Festival y Eventos Especiales (/contador o /festival).
Permite mostrar un widget animado en OBS con estética de Dragon Ball Legends (Golden Ki, Ultra Ki, etc.)
o como cuenta regresiva para iniciar stream (Starting Soon).
"""
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from flask import Blueprint, jsonify, render_template, request
import live

BASE = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
ARCHIVO = BASE / "contador.json"
bp = Blueprint("contador", __name__)
lock = Lock()

_DEF_CONFIG = {
    "activa": True,
    "badge": "LEGENDS FESTIVAL",
    "titulo": "LEGENDS FESTIVAL",
    "subtitulo": "DRAGON BALL LEGENDS",
    "fecha_objetivo": "2024-11-21T13:00",
    "mensaje_final": "¡EL LEGENDS FESTIVAL HA COMENZADO!",
    "estilo": "festival",  # "festival", "compacto", "ultra"
    "color_primario": "#ffb703",
    "color_secundario": "#fb8500",
    "mostrar_dias": True,
    "mostrar_horas": True,
    "mostrar_segundos": True,
    "mostrar_aura": True,
    "tamano": 100  # porcentaje de escala (60-160)
}

_CFG = dict(_DEF_CONFIG)


def _color(v, default):
    if isinstance(v, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", v):
        return v
    return default


def _limpiar(d):
    if not isinstance(d, dict):
        d = {}

    badge_def = _CFG.get("badge", _DEF_CONFIG["badge"])
    if "badge" in d:
        badge = str(d.get("badge") or "").strip()[:40]
    else:
        badge = badge_def

    titulo = str(d.get("titulo") or _CFG.get("titulo", _DEF_CONFIG["titulo"])).strip()[:60]
    subtitulo = str(d.get("subtitulo") or _CFG.get("subtitulo", _DEF_CONFIG["subtitulo"])).strip()[:80]
    mensaje_final = str(d.get("mensaje_final") or _CFG.get("mensaje_final", _DEF_CONFIG["mensaje_final"])).strip()[:100]
    fecha_obj = str(d.get("fecha_objetivo") or _CFG.get("fecha_objetivo", _DEF_CONFIG["fecha_objetivo"])).strip()[:30]

    estilo = str(d.get("estilo") or _CFG.get("estilo", "festival")).lower()
    if estilo not in ("festival", "compacto", "ultra"):
        estilo = "festival"

    try:
        tam = max(60, min(160, int(d.get("tamano", _CFG.get("tamano", 100)))))
    except (TypeError, ValueError):
        tam = 100

    def_primario = _CFG.get("color_primario", _DEF_CONFIG["color_primario"])
    def_secundario = _CFG.get("color_secundario", _DEF_CONFIG["color_secundario"])

    return {
        "activa": bool(d.get("activa", _CFG.get("activa", True))),
        "badge": badge,
        "titulo": titulo,
        "subtitulo": subtitulo,
        "fecha_objetivo": fecha_obj,
        "mensaje_final": mensaje_final,
        "estilo": estilo,
        "color_primario": _color(d.get("color_primario"), def_primario),
        "color_secundario": _color(d.get("color_secundario"), def_secundario),
        "mostrar_dias": bool(d.get("mostrar_dias", _CFG.get("mostrar_dias", True))),
        "mostrar_horas": bool(d.get("mostrar_horas", _CFG.get("mostrar_horas", True))),
        "mostrar_segundos": bool(d.get("mostrar_segundos", _CFG.get("mostrar_segundos", True))),
        "mostrar_aura": bool(d.get("mostrar_aura", _CFG.get("mostrar_aura", True))),
        "tamano": tam
    }


def _cargar():
    global _CFG
    try:
        if ARCHIVO.exists():
            datos = json.loads(ARCHIVO.read_text(encoding="utf-8"))
            _CFG.update(_limpiar(datos))
    except (OSError, ValueError):
        pass


def _guardar():
    try:
        ARCHIVO.write_text(json.dumps(_CFG, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except OSError:
        return False


_cargar()


@bp.route("/contador")
@bp.route("/festival")
def overlay():
    """Overlay transparente de OBS para el contador regresivo."""
    return render_template("contador.html")


@bp.route("/contador/panel")
@bp.route("/festival/panel")
def panel():
    """Panel de configuración web del contador."""
    return render_template("contador_panel.html")


@bp.route("/api/contador", methods=["GET", "POST"])
def api_contador():
    global _CFG
    if request.method == "POST":
        datos = request.get_json(silent=True) or {}
        with lock:
            _CFG = _limpiar(datos)
            _guardar()
        # Notificar en tiempo real a todos los overlays de OBS conectados
        try:
            live.publicar("contador_cfg", _CFG)
        except Exception:
            pass
        return jsonify({"ok": True, "cfg": _CFG})

    with lock:
        return jsonify({"ok": True, "cfg": _CFG})
