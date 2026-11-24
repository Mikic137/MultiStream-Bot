"""Módulo de Metas y Tops para OBS y el Stream:
1. Contador de Likes interactivo estilo TikFinity con Leaderboard (/likes).
2. Top Donadores LIVE en lista de Regalos y Dinero (/top_donador).
3. Máximo comprador de Streamloots con estética VIP (/top_streamloots).
4. Barra y paneles combinados multi-widget (/tops)."""
import json, re, sys, time
from pathlib import Path
from threading import RLock
from flask import Blueprint, jsonify, render_template, request
import live, stats

BASE = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
ARCHIVO = BASE / "tops.json"
bp = Blueprint("tops", __name__)
lock = RLock()

DEF = {
    "likes": {
        "activa": True,
        "meta": 5000,
        "titulo": "META DE LIKES",
        "color": "#ff0055",
        "mostrar_barra": True,
        "mostrar_lista": True,
        "top_n": 3,
        "auto_meta": True,
        "incremento": 2500,
        "estilo": "tikfinity"
    },
    "top_donador": {
        "activo": True,
        "titulo": "TOP DONADORES LIVE",
        "modo": "dual",  # "dual", "regalos", "dinero", "rotar"
        "top_n": 3,
        "fijar": False,
        "usuario_fijo": "",
        "diamantes_fijo": 0
    },
    "top_streamloots": {
        "activo": True,
        "usuario": "NexusLegends",
        "cofres": 25,
        "dinero": "$45.00 USD",
        "rango": "👑 SUPREMO VIP",
        "mensaje": "¡Máximo patrocinador del canal!",
        "color_aura": "dorado",
        "tamano": "normal"
    }
}


def _limpiar(d):
    if not isinstance(d, dict):
        d = {}

    # 1. Likes
    dl = d.get("likes") or {}
    try:
        meta_likes = max(100, int(dl.get("meta", 5000)))
    except (TypeError, ValueError):
        meta_likes = 5000
    try:
        inc_likes = max(100, int(dl.get("incremento", 2500)))
    except (TypeError, ValueError):
        inc_likes = 2500
    try:
        top_n_likes = max(1, min(10, int(dl.get("top_n", 3))))
    except (TypeError, ValueError):
        top_n_likes = 3

    color_likes = str(dl.get("color", "#ff0055")).strip()
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color_likes):
        color_likes = "#ff0055"

    likes_cfg = {
        "activa": bool(dl.get("activa", True)),
        "meta": meta_likes,
        "titulo": str(dl.get("titulo", "META DE LIKES")).strip()[:40] or "META DE LIKES",
        "color": color_likes,
        "mostrar_barra": bool(dl.get("mostrar_barra", True)),
        "mostrar_lista": bool(dl.get("mostrar_lista", True)),
        "top_n": top_n_likes,
        "auto_meta": bool(dl.get("auto_meta", True)),
        "incremento": inc_likes,
        "estilo": dl.get("estilo") if dl.get("estilo") in ("tikfinity", "neon", "minimal") else "tikfinity"
    }

    # 2. Top Donadores
    td = d.get("top_donador") or {}
    try:
        dia_fijo = max(0, int(td.get("diamantes_fijo", 0)))
    except (TypeError, ValueError):
        dia_fijo = 0
    try:
        top_n_don = max(1, min(10, int(td.get("top_n", 3))))
    except (TypeError, ValueError):
        top_n_don = 3

    modo_don = str(td.get("modo", "dual")).lower()
    if modo_don not in ("dual", "regalos", "dinero", "rotar"):
        modo_don = "dual"

    donador_cfg = {
        "activo": bool(td.get("activo", True)),
        "titulo": str(td.get("titulo", "TOP DONADORES LIVE")).strip()[:40] or "TOP DONADORES LIVE",
        "modo": modo_don,
        "top_n": top_n_don,
        "fijar": bool(td.get("fijar", False)),
        "usuario_fijo": str(td.get("usuario_fijo", "")).strip()[:40],
        "diamantes_fijo": dia_fijo
    }

    # 3. Top Streamloots VIP
    ts = d.get("top_streamloots") or {}
    try:
        cofres = max(0, int(ts.get("cofres", 0)))
    except (TypeError, ValueError):
        cofres = 0

    aura = ts.get("color_aura") if ts.get("color_aura") in ("dorado", "fuego", "neon", "morado", "esmeralda") else "dorado"
    tam = ts.get("tamano") if ts.get("tamano") in ("normal", "grande", "compacto") else "normal"

    streamloots_cfg = {
        "activo": bool(ts.get("activo", True)),
        "usuario": str(ts.get("usuario", "NexusLegends")).strip()[:40] or "NexusLegends",
        "cofres": cofres,
        "dinero": str(ts.get("dinero", "$45.00 USD")).strip()[:30] or "$45.00 USD",
        "rango": str(ts.get("rango", "👑 SUPREMO VIP")).strip()[:40] or "👑 SUPREMO VIP",
        "mensaje": str(ts.get("mensaje", "¡Máximo patrocinador del canal!")).strip()[:100],
        "color_aura": aura,
        "tamano": tam
    }

    return {
        "likes": likes_cfg,
        "top_donador": donador_cfg,
        "top_streamloots": streamloots_cfg
    }


try:
    cfg = _limpiar(json.loads(ARCHIVO.read_text(encoding="utf-8")))
except (OSError, ValueError):
    cfg = _limpiar(DEF)


def _guardar():
    try:
        ARCHIVO.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def get_likes_data():
    """Devuelve datos dinámicos de likes (con lista de top likers)."""
    with lock:
        c = dict(cfg.get("likes", {}))

    likes_actuales = int(stats.s.get("likes", 0))
    meta = max(100, int(c.get("meta", 5000)))

    # Auto-meta incremento si se supera
    if c.get("auto_meta", True) and likes_actuales >= meta:
        inc = max(100, int(c.get("incremento", 2500)))
        while meta <= likes_actuales:
            meta += inc
        c["meta"] = meta

    pct = min(100.0, round((likes_actuales / meta) * 100, 1)) if meta > 0 else 0.0

    # Top likers
    top_n = max(1, min(10, int(c.get("top_n", 3))))
    raw_likers = sorted(stats.likers.items(), key=lambda x: -x[1])[:top_n]
    top_likers = [{"pos": i, "usuario": u, "likes": l} for i, (u, l) in enumerate(raw_likers, 1)]

    return {
        **c,
        "actuales": likes_actuales,
        "meta": meta,
        "porcentaje": pct,
        "top_likers": top_likers
    }


def get_top_donadores_data():
    """Devuelve datos dinámicos de donadores (en lista de regalos y dinero)."""
    with lock:
        c = dict(cfg.get("top_donador", {}))

    top_n = max(1, min(10, int(c.get("top_n", 3))))

    # Top regalos (TikTok diamantes)
    raw_regalos = sorted(stats.donantes_regalos.items(), key=lambda x: -x[1])[:top_n]
    top_regalos = [{"pos": i, "usuario": u, "diamantes": d} for i, (u, d) in enumerate(raw_regalos, 1)]

    # Top dinero (Kick / USD)
    raw_dinero = sorted(stats.donantes_dinero.items(), key=lambda x: -x[1])[:top_n]
    top_dinero = [
        {"pos": i, "usuario": u, "dinero": d, "dinero_txt": f"${d:.2f} USD"}
        for i, (u, d) in enumerate(raw_dinero, 1)
    ]

    # Top general (todos los donantes combinados)
    raw_general = sorted(stats.donantes.items(), key=lambda x: -x[1])[:top_n]
    top_general = [{"pos": i, "usuario": u, "diamantes": d} for i, (u, d) in enumerate(raw_general, 1)]

    # Compatibilidad para #1
    if c.get("fijar") and c.get("usuario_fijo"):
        donador_nombre = c["usuario_fijo"]
        donador_diamantes = c["diamantes_fijo"]
    else:
        donador_nombre = top_general[0]["usuario"] if top_general else ""
        donador_diamantes = top_general[0]["diamantes"] if top_general else 0

    return {
        **c,
        "usuario": donador_nombre,
        "diamantes": donador_diamantes,
        "top_regalos": top_regalos,
        "top_dinero": top_dinero,
        "top_general": top_general
    }


def get_tops_data():
    """Devuelve el estado dinámico global de metas y tops para OBS."""
    return {
        "likes": get_likes_data(),
        "top_donador": get_top_donadores_data(),
        "top_streamloots": cfg.get("top_streamloots", {})
    }


@bp.route("/api/tops", methods=["GET", "POST"])
def api_tops():
    global cfg
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        with lock:
            cfg = _limpiar(data)
        _guardar()
        # Notificar a los overlays por SSE / WebSockets
        live.publicar("tops_cfg", get_tops_data())
    return jsonify(get_tops_data())


@bp.route("/api/tops/simular_like", methods=["POST"])
def api_simular_like():
    data = request.get_json(silent=True) or {}
    usuario = str(data.get("usuario", "FanDeTikTok")).strip() or "FanDeTikTok"
    cantidad = int(data.get("cantidad", 100))
    stats.registrar_like(usuario, cantidad)
    return jsonify({"ok": True, "likes": stats.s.get("likes", 0)})


@bp.route("/api/tops/simular_regalo", methods=["POST"])
def api_simular_regalo():
    data = request.get_json(silent=True) or {}
    usuario = str(data.get("usuario", "SuperDonador")).strip() or "SuperDonador"
    diamantes = int(data.get("diamantes", 500))
    regalo = str(data.get("regalo", "Galaxia"))
    stats.registrar_donacion(usuario, diamantes, nombre_regalo=regalo)
    return jsonify({"ok": True, "top_donador": get_top_donadores_data()})


@bp.route("/api/tops/simular_dinero", methods=["POST"])
def api_simular_dinero():
    data = request.get_json(silent=True) or {}
    usuario = str(data.get("usuario", "PatrocinadorKick")).strip() or "PatrocinadorKick"
    usd = float(data.get("usd", 10.0))
    stats.registrar_donacion_dinero(usuario, usd, fuente="Kick")
    return jsonify({"ok": True, "top_donador": get_top_donadores_data()})


@bp.route("/api/tops/reiniciar", methods=["POST"])
def api_reiniciar_tops():
    stats.reiniciar(forzar=True)
    live.publicar("tops_cfg", get_tops_data())
    return jsonify({"ok": True})


@bp.route("/api/tops/streamloots_auto")
def api_streamloots_auto():
    archivo_loots = BASE / "loots.json"
    if not archivo_loots.exists():
        return jsonify({"ok": False, "error": "No existe loots.json"})

    try:
        d = json.loads(archivo_loots.read_text(encoding="utf-8"))
        items = d.get("items", [])
        conteo = {}
        for it in items:
            u = str(it.get("usuario", "")).strip()
            if u and u.lower() != "anonymous":
                conteo[u] = conteo.get(u, 0) + 1

        if not conteo:
            return jsonify({"ok": False, "error": "No hay cartas registradas todavía."})

        top_user = sorted(conteo.items(), key=lambda x: -x[1])[0]
        cartas = top_user[1]
        cofres_estimados = max(1, round(cartas / 3))

        return jsonify({
            "ok": True,
            "usuario": top_user[0],
            "cartas": cartas,
            "cofres": cofres_estimados
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ---------------- Rutas Overlays OBS ----------------
@bp.route("/likes")
def overlay_likes():
    return render_template("likes.html")


@bp.route("/top_donador")
def overlay_top_donador():
    return render_template("top_donador.html")


@bp.route("/top_streamloots")
def overlay_top_streamloots():
    return render_template("top_streamloots.html")


@bp.route("/tops")
def overlay_tops():
    return render_template("tops_multitask.html")


@bp.route("/tops/panel")
def panel_tops():
    return render_template("tops_panel.html")
