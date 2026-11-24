"""Tarjetas de promoción para OBS (/promo): rotan solas o salen con un comando (!comandos, !discord, !youtube...).
Incluye carrusel de imágenes, redes sociales y contador integrado de viewers."""
import json, re, sys, time
from pathlib import Path
from threading import Lock
from flask import Blueprint, jsonify, render_template, request
import live

BASE = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
ARCHIVO = BASE / "promo.json"
bp = Blueprint("promo", __name__)
lock = Lock()
_ultimo = 0.0


def _limpiar(d):
    def num(k, lo, hi, por):
        try:
            return max(lo, min(hi, int(d.get(k, por))))
        except (TypeError, ValueError):
            return por

    tarjetas = []
    for t in (d.get("tarjetas") or [])[:25]:
        if not isinstance(t, dict):
            continue
        lineas = [str(x).strip()[:100] for x in (t.get("lineas") or []) if str(x).strip()][:8]
        titulo = str(t.get("titulo", "")).strip()[:40]
        red = re.sub(r"[^a-z0-9_\-]", "", str(t.get("red", "")).lower())[:20]
        usuario = str(t.get("usuario", "")).strip()[:50]
        cmd = re.sub(r"[^a-z0-9_]", "", str(t.get("comando", "")).lower())[:20]
        dur = num("duracion", 0, 60, 0)

        # Manejo de imágenes (única o carrusel de varias)
        imgs = []
        raw_imgs = t.get("imagenes")
        if isinstance(raw_imgs, list):
            imgs = [str(u).strip() for u in raw_imgs if str(u).strip()][:10]
        elif t.get("imagen"):
            imgs = [str(t.get("imagen")).strip()]

        if not lineas and not titulo and not imgs and not usuario:
            continue

        tarjetas.append({
            "titulo": titulo,
            "lineas": lineas,
            "comando": cmd,
            "auto": bool(t.get("auto")),
            "red": red,
            "imagen": imgs[0] if imgs else "",
            "imagenes": imgs,
            "usuario": usuario,
            "duracion": dur
        })

    pos = str(d.get("posicion", "abajo_izquierda"))
    if pos not in ("abajo_izquierda", "abajo_derecha", "arriba_izquierda", "arriba_derecha", "arriba_centro"):
        pos = "abajo_izquierda"

    return {
        "activa": bool(d.get("activa", True)),
        "segundos": num("segundos", 3, 60, 10),
        "pausa": num("pausa", 5, 600, 35),
        "espera": num("espera", 0, 300, 20),
        "fuente": re.sub(r"[^\w \-]", "", str(d.get("fuente", "")))[:40],
        "mostrar_viewers": bool(d.get("mostrar_viewers", False)),
        "posicion": pos,
        "tarjetas": tarjetas
    }


DEF = {
    "activa": True,
    "segundos": 10,
    "pausa": 35,
    "espera": 20,
    "fuente": "",
    "mostrar_viewers": False,
    "posicion": "abajo_izquierda",
    "tarjetas": [
        {
            "titulo": "COMANDOS DEL CHAT",
            "comando": "comandos",
            "auto": True,
            "red": "",
            "usuario": "",
            "imagen": "",
            "imagenes": [],
            "duracion": 0,
            "lineas": ["!play nombre de la canción", "!reto juega + tu reto para la ruleta"]
        },
        {
            "titulo": "SÍGUEME EN REDES",
            "comando": "redes",
            "auto": True,
            "red": "youtube",
            "usuario": "@tu_usuario",
            "imagen": "",
            "imagenes": [],
            "duracion": 0,
            "lineas": ["Suscríbete para más contenido", "Directos y videos todas las semanas"]
        }
    ]
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


def mostrar(tarjeta_o_titulo, lineas=None, seg=None, forzar=False):
    if isinstance(tarjeta_o_titulo, dict):
        t = tarjeta_o_titulo
        s_dur = seg or t.get("duracion") or max(cfg.get("segundos", 10), 5)
        titulo = t.get("titulo", "")
        lineas_arr = t.get("lineas", [])
        imgs_arr = t.get("imagenes", []) or ([t.get("imagen")] if t.get("imagen") else [])
        img_principal = t.get("imagen", "") or (imgs_arr[0] if imgs_arr else "")
        usuario = t.get("usuario", "")
        red = t.get("red", "")
        live.publicar("promo", {
            "t": titulo,
            "titulo": titulo,
            "l": lineas_arr,
            "lineas": lineas_arr,
            "s": s_dur,
            "duracion": s_dur,
            "red": red,
            "img": img_principal,
            "imagen": img_principal,
            "imgs": imgs_arr,
            "imagenes": imgs_arr,
            "u": usuario,
            "usuario": usuario,
            "f": forzar
        })
    else:
        titulo = tarjeta_o_titulo or ""
        lineas_arr = lineas or []
        s_dur = seg or max(cfg.get("segundos", 10), 10)
        live.publicar("promo", {
            "t": titulo,
            "titulo": titulo,
            "l": lineas_arr,
            "lineas": lineas_arr,
            "s": s_dur,
            "duracion": s_dur,
            "red": "",
            "img": "",
            "imagen": "",
            "imgs": [],
            "imagenes": [],
            "u": "",
            "usuario": "",
            "f": forzar
        })


def comando(cmd):
    """Un espectador escribió !cmd: muestra la tarjeta que tenga ese comando (con espera global)."""
    global _ultimo
    if not cfg.get("activa") or not cmd:
        return
    with lock:
        t = next((x for x in cfg.get("tarjetas", []) if x.get("comando") == cmd), None)
        ahora = time.time()
        if not t or ahora - _ultimo < cfg.get("espera", 20):
            return
        _ultimo = ahora
    mostrar(t, forzar=True)


@bp.route("/promo")
def overlay():
    return render_template("promo.html")


@bp.route("/promo/panel")
def panel():
    return render_template("promo_panel.html")


@bp.route("/api/promo", methods=["GET", "POST"])
def api_promo():
    global cfg
    if request.method == "POST":
        with lock:
            cfg = _limpiar(request.get_json(silent=True) or {})
        _guardar()
        live.publicar("promo_cfg", {})
    return jsonify(cfg)


@bp.route("/api/promo/mostrar", methods=["POST"])
def api_mostrar():
    req_data = request.get_json(silent=True) or {}
    t_list = _limpiar({"tarjetas": [req_data]}).get("tarjetas", [])
    if t_list:
        mostrar(t_list[0], forzar=True)
        return jsonify({"ok": True})
    return jsonify({"ok": False})


@bp.route("/api/promo/subir_imagen", methods=["POST"])
def api_subir_imagen():
    if "imagen" not in request.files:
        return jsonify({"ok": False, "error": "No se envió ningún archivo de imagen."}), 400
    archivo = request.files["imagen"]
    if not archivo or not archivo.filename:
        return jsonify({"ok": False, "error": "Archivo vacío."}), 400

    ext = Path(archivo.filename).suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"):
        return jsonify({"ok": False, "error": "Formato no permitido. Usa PNG, JPG, WEBP, GIF o SVG."}), 400

    dir_upload = BASE / "static" / "uploads" / "promos"
    dir_upload.mkdir(parents=True, exist_ok=True)

    stem_clean = re.sub(r"[^a-zA-Z0-9_\-]", "", Path(archivo.filename).stem)[:25]
    nombre_seguro = f"promo_{int(time.time())}_{stem_clean}{ext}"
    destino = dir_upload / nombre_seguro
    archivo.save(str(destino))

    url = f"/static/uploads/promos/{nombre_seguro}"
    return jsonify({"ok": True, "url": url})
