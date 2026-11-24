"""Animaciones VIP: cuando un espectador de tu lista entra (o interactúa por primera vez), su alerta
se pone en una cola y el overlay /vip la pide cuando está libre."""
import json, re, sys, time
from collections import OrderedDict, deque
from pathlib import Path
from threading import Lock
from flask import Blueprint, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename
import live

BASE = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
ARCHIVO, VIDEOS = BASE / "vips.json", BASE / "videos_vip"
MAX_MB, EXT, MAX_VIPS = 60, (".mp4", ".webm"), 300
POS = ("arriba", "centro", "abajo")
ANIM = ("ninguna", "fade", "subir", "zoom", "rebote", "deslizar")
ESTILOS_CHAT = ("clasico", "pixel", "energia", "capsula")
bp = Blueprint("vips", __name__)
lock = Lock()
saludados, recientes, pendientes = {}, OrderedDict(), deque()
en_cola = set()  # evita que likes/comentarios repetidos dupliquen una alerta aún esperando
_indice, _ultimo, _ult_lleno = {}, 0.0, 0.0
try:
    VIDEOS.mkdir(exist_ok=True)
except OSError:
    pass


def _videos():
    try:
        return sorted(p.name for p in VIDEOS.iterdir() if p.suffix.lower() in EXT)
    except OSError:
        return []


def _color(x, por):
    return str(x) if re.fullmatch(r"#[0-9a-fA-F]{6}", str(x)) else por


def _limpiar(d):
    def num(k, lo, hi, por):
        try:
            return max(lo, min(hi, int(d.get(k, por))))
        except (TypeError, ValueError):
            return por

    def elegir(k, opciones, por):
        return d.get(k) if d.get(k) in opciones else por

    disponibles, vips, vistos = set(_videos()), [], set()
    for v in (d.get("vips") or [])[:MAX_VIPS]:
        if not isinstance(v, dict):
            continue
        u = re.sub(r"[^a-z0-9_.]", "", str(v.get("usuario", "")).lower())[:40]  # el @usuario TikTok
        u_kick = re.sub(r"[^a-z0-9_.]", "", str(v.get("usuario_kick", "")).lower())[:40]  # el @usuario Kick
        u_twitch = re.sub(r"[^a-z0-9_.]", "", str(v.get("usuario_twitch", "")).lower())[:40]  # el @usuario Twitch
        u_yt = str(v.get("usuario_youtube", "")).strip()[:50]  # usuario YouTube
        
        # Debe tener al menos algún usuario o nombre
        if not u and not u_kick and not u_twitch and not u_yt and not str(v.get("nombre", "")).strip():
            continue
        clave_unica = (u or "") + "::" + (u_kick or "") + "::" + (u_twitch or "") + "::" + (u_yt or "")
        if clave_unica in vistos:
            continue
        vistos.add(clave_unica)

        video = str(v.get("video", ""))
        rango = str(v.get("rango", "vip")) if v.get("rango") in ("vip", "mod", "top") else "vip"
        estandarte = str(v.get("estandarte", "none")) if v.get("estandarte") in ("none", "corona", "espada", "puño", "escudo", "estrella") else "none"
        animado = bool(v.get("animado", False))
        nombre = str(v.get("nombre", "")).strip()[:30]
        if not nombre:
            nombre = u or u_kick or u_twitch or u_yt or "VIP"

        vips.append({
            "usuario": u,
            "usuario_kick": u_kick,
            "usuario_twitch": u_twitch,
            "usuario_youtube": u_yt,
            "nombre": nombre,
            "video": video if video in disponibles else "",
            "activo": bool(v.get("activo", True)),
            "silenciar_video": bool(v.get("silenciar_video", False)),
            "mensaje": str(v.get("mensaje", "")).strip()[:100],
            "color": _color(v.get("color", ""), ""),
            "banner_chat": bool(v.get("banner_chat", False)),
            "rango": rango,
            "estandarte": estandarte,
            "animado": animado
        })

    return {
        "activa": bool(d.get("activa", True)), "volumen": num("volumen", 0, 100, 80), "tam": num("tam", 20, 100, 100),
        "pos": elegir("pos", POS, "centro"), "max": num("max", 3, 60, 15), "repetir_min": num("repetir_min", 0, 600, 0),
        "cola_max": MAX_VIPS, "caduca_seg": num("caduca_seg", 0, 3600, 0),
        "mensaje": str(d.get("mensaje", "¡Bienvenido al directo!")).strip()[:100],
        "mostrar_nombre": bool(d.get("mostrar_nombre", True)),
        "fuente": re.sub(r"[^\w \-]", "", str(d.get("fuente", "")))[:40],
        "color_nombre": _color(d.get("color_nombre"), "#ffd45e"), "color_mensaje": _color(d.get("color_mensaje"), "#ffffff"),
        "color_borde": _color(d.get("color_borde"), "#000000"), "color_fondo": _color(d.get("color_fondo"), "#000000"),
        "fondo": bool(d.get("fondo", False)), "tam_nombre": num("tam_nombre", 20, 150, 64), "tam_mensaje": num("tam_mensaje", 12, 100, 32),
        "animacion": elegir("animacion", ANIM, "zoom"), "brillo": bool(d.get("brillo", True)),
        "efecto_entrada": elegir("efecto_entrada", ("ninguno", "destello", "rayos", "explosion"), "destello"),
        "chat_estilo": elegir("chat_estilo", ESTILOS_CHAT, "clasico"),
        "silenciar_video": bool(d.get("silenciar_video", False)),
        "texto_pos": elegir("texto_pos", POS, "abajo"), "seg_texto": num("seg_texto", 2, 30, 6), "vips": vips
    }


def _reindexar():
    """Búsqueda instantánea por @usuario de TikTok, Kick, Twitch, YouTube y nombre para vincular todas las plataformas."""
    global _indice
    _indice = {}
    for v in cfg.get("vips", []):
        if not v.get("activo", True):
            continue
        u_tt = str(v.get("usuario") or "").lower().lstrip("@").strip()
        u_kick = str(v.get("usuario_kick") or "").lower().lstrip("@").strip()
        u_tw = str(v.get("usuario_twitch") or "").lower().lstrip("@").strip()
        u_yt = str(v.get("usuario_youtube") or "").lower().lstrip("@").strip()
        nom = str(v.get("nombre") or "").lower().strip()
        if u_tt:
            _indice[u_tt] = v
        if u_kick:
            _indice[u_kick] = v
        if u_tw:
            _indice[u_tw] = v
        if u_yt:
            _indice[u_yt] = v
        if nom:
            _indice[nom] = v


def es_vip(uid_o_nombre):
    """Devuelve True si el usuario o identificador está en la lista de VIPs activos."""
    if not uid_o_nombre:
        return False
    clean = str(uid_o_nombre).lower().lstrip("@").strip()
    return clean in _indice


def estilo_chat(uid):
    """Devuelve la insignia/etiqueta VIP para el chat overlay tanto si comenta en TikTok como en Kick."""
    clean_uid = str(uid or "").lower().lstrip("@").strip()
    v = _indice.get(clean_uid)
    if v and v.get("banner_chat"):
        return {
            "etiqueta": v.get("nombre") or "VIP",
            "color": v.get("color") or "#bd7cff",
            "rango": v.get("rango", "vip"),
            "estandarte": v.get("estandarte", "none"),
            "animado": bool(v.get("animado", False))
        }
    return None


def estilo_general_chat():
    return cfg["chat_estilo"]


try:
    cfg = _limpiar(json.loads(ARCHIVO.read_text(encoding="utf-8")))
except (OSError, ValueError):
    cfg = _limpiar({})
_reindexar()


def _guardar():
    try:
        ARCHIVO.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def _alerta(v, nick="", forzar=False):
    nombre = v.get("nombre") or nick or v.get("usuario") or v.get("usuario_kick") or v.get("usuario_twitch") or v.get("usuario_youtube") or "VIP"
    plantilla = v.get("mensaje") or cfg["mensaje"]
    user_tag = "@" + (v.get("usuario") or v.get("usuario_kick") or v.get("usuario_twitch") or v.get("usuario_youtube") or "vip")
    msg = plantilla.replace("{nombre}", nombre).replace("{usuario}", user_tag)[:120]
    canonical_id = v.get("usuario") or v.get("usuario_kick") or v.get("usuario_twitch") or v.get("usuario_youtube") or "vip"
    silenciado = bool(cfg.get("silenciar_video") or v.get("silenciar_video"))
    vol = 0.0 if silenciado else cfg["volumen"] / 100
    return {"u": "/vip/video/" + v["video"] if v.get("video") else "", "n": nombre, "m": msg, "c": v.get("color", ""),
            "v": vol, "silenciar_video": silenciado, "f": forzar, "uid": canonical_id, "ts": time.time()}


def visto(e, nickname=None):
    """Llamar con cada evento de un espectador (entrar, comentar, like, regalo...), ya sea en TikTok o Kick."""
    global _ultimo, _ult_lleno
    if isinstance(e, str):
        uid = e.lower().lstrip("@").strip()
        nick = str(nickname or uid).strip()[:30]
    else:
        u = getattr(e, "user", None)
        uid = str(getattr(u, "unique_id", "") or (getattr(u, "display_id", "") or (u if isinstance(u, str) else ""))).lower().lstrip("@").strip()
        nick = str(getattr(u, "nickname", "") or nickname or uid).strip()[:30]
    if not uid:
        return
    ahora, alerta = time.time(), None
    with lock:
        if ahora - _ultimo > 600:  # más de 10 min sin actividad: directo nuevo
            saludados.clear()
            pendientes.clear()
            en_cola.clear()
        _ultimo = ahora
        recientes[uid] = nick
        recientes.move_to_end(uid)
        while len(recientes) > 20:
            recientes.popitem(last=False)
        v = _indice.get(uid) if cfg["activa"] else None
        if not v:
            return

        vip_key = v.get("usuario") or v.get("usuario_kick") or uid
        previo = saludados.get(vip_key)
        if previo is not None and (cfg["repetir_min"] <= 0 or ahora - previo < cfg["repetir_min"] * 60):
            return
        if vip_key in en_cola:
            return
        if len(pendientes) >= MAX_VIPS:
            if ahora - _ult_lleno > 30:
                _ult_lleno = ahora
                live.log(f"VIP: cola llena ({cfg['cola_max']}), se omiten alertas hasta que haya sitio")
            return
        saludados[vip_key] = ahora
        alerta = _alerta(v, nick)
        pendientes.append(alerta)
        en_cola.add(vip_key)
    live.log(f"VIP: {alerta['n']} ({uid}) → {v['video'] or 'solo tarjeta'}")
    live.publicar("vip", {})  # aviso para que el overlay pida la alerta


@bp.route("/vip")
def overlay():
    return render_template("vip.html")


@bp.route("/vip/panel")
def panel():
    return render_template("vip_panel.html")


@bp.route("/vip/video/<nombre>")
def video(nombre):
    return send_from_directory(VIDEOS, nombre, conditional=True)


def _recientes():
    with lock:
        return [[k, v] for k, v in reversed(recientes.items())][:12], len(pendientes)


@bp.route("/api/vip", methods=["GET", "POST"])
def api_vip():
    global cfg
    if request.method == "POST":
        with lock:
            cfg = _limpiar(request.get_json(silent=True) or {})
            _reindexar()
        _guardar()
        live.publicar("vip_cfg", {})
    if request.args.get("solo") == "cfg":  # el overlay solo necesita el estilo
        return jsonify({k: v for k, v in cfg.items() if k != "vips"})
    rec, n = _recientes()
    if request.args.get("rec"):  # consulta ligera del panel
        return jsonify({"recientes": rec, "pendientes": n})
    vids = [{"n": x, "mb": round((VIDEOS / x).stat().st_size / 1048576, 1)} for x in _videos()]
    return jsonify({**cfg, "videos": vids, "recientes": rec, "pendientes": n})


@bp.route("/api/vip/siguiente", methods=["POST"])
def siguiente():
    """El overlay pide la próxima alerta cuando está libre. Se descartan las que esperaron demasiado."""
    ahora, a = time.time(), None
    with lock:
        while pendientes:
            x = pendientes.popleft()
            en_cola.discard(x["uid"])
            if x["f"] or not cfg["caduca_seg"] or ahora - x["ts"] <= cfg["caduca_seg"]:
                a = {k: x[k] for k in ("u", "n", "m", "c", "v", "f")}
                break
            saludados.pop(x["uid"], None)  # caducó: podrá volver a saludarse
        n = len(pendientes)
    return jsonify({"alerta": a, "pendientes": n})


@bp.route("/api/vip/vaciar", methods=["POST"])
def vaciar():
    with lock:
        for x in pendientes:
            saludados.pop(x["uid"], None)
        pendientes.clear()
        en_cola.clear()
    return jsonify({"ok": True})


@bp.route("/api/vip/subir", methods=["POST"])
def subir():
    f = request.files.get("archivo")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "No llegó ningún archivo."}), 400
    nombre = secure_filename(f.filename)
    suf = Path(nombre).suffix.lower()
    if suf not in EXT:
        return jsonify({"ok": False, "error": "Solo se aceptan videos .mp4 o .webm."}), 400
    if (request.content_length or 0) > MAX_MB * 1048576:
        return jsonify({"ok": False, "error": f"El video pesa más de {MAX_MB} MB."}), 413
    base, n = Path(nombre).stem or "video", 1
    destino = VIDEOS / (base + suf)
    while destino.exists():
        destino = VIDEOS / f"{base}_{n}{suf}"
        n += 1
    limite = MAX_MB * 1048576
    total = 0
    try:
        with destino.open("xb") as salida:
            while bloque := f.stream.read(1024 * 1024):
                total += len(bloque)
                if total > limite:
                    raise ValueError("límite excedido")
                salida.write(bloque)
    except ValueError:
        destino.unlink(missing_ok=True)
        return jsonify({"ok": False, "error": f"El video pesa más de {MAX_MB} MB."}), 413
    except OSError:
        destino.unlink(missing_ok=True)
        return jsonify({"ok": False, "error": "No se pudo guardar el video."}), 500
    return jsonify({"ok": True, "nombre": destino.name})


@bp.route("/api/vip/video/<nombre>", methods=["DELETE"])
def borrar(nombre):
    if nombre in _videos():
        (VIDEOS / nombre).unlink(missing_ok=True)
        with lock:
            for v in cfg["vips"]:
                if v["video"] == nombre:
                    v["video"] = ""
        _guardar()
    return jsonify({"ok": True})


@bp.route("/api/vip/probar", methods=["POST"])
def probar():
    d = request.get_json(silent=True) or {}
    d["usuario"] = "prueba"
    v = _limpiar({"vips": [d]})["vips"][0]  # reutiliza la validación (video, colores, largos)
    with lock:
        if len(pendientes) >= MAX_VIPS:
            return jsonify({"ok": False, "error": "La cola está llena; la prueba puede hacerse cuando se libere un espacio."}), 429
        test = _alerta(v, "NombreVIP", forzar=True)
        pendientes.appendleft(test)  # pruebas urgentes pasan por delante; cantidad acotada
        en_cola.add("prueba")
    live.publicar("vip", {})
    return jsonify({"ok": True})


@bp.route("/api/vip/chat/prueba", methods=["POST"])
def probar_chat():
    d = request.get_json(silent=True) or {}
    if not d.get("usuario"):
        d["usuario"] = "prueba"
    limpiados = _limpiar({"vips": [d]}).get("vips", [])
    v = limpiados[0] if limpiados else {}
    nombre = v.get("nombre") or d.get("nombre") or "VIP de prueba"
    texto = "¡Gracias por apoyar el stream! Toda la tarjeta está animada en estilo pixel art."
    live.publicar("chat", {"n": nombre, "t": texto, "p": [{"t": texto}],
                            "vip": {"etiqueta": nombre, "color": v.get("color") or d.get("color") or "#ffd45e",
                                    "rango": v.get("rango") or d.get("rango") or "top",
                                    "estandarte": v.get("estandarte") or d.get("estandarte") or "corona",
                                    "animado": bool(d.get("animado", v.get("animado", False)))},
                            "estilo": cfg["chat_estilo"], "prueba": True})
    return jsonify({"ok": True})

@bp.route("/api/vip/reiniciar", methods=["POST"])
def reiniciar():
    with lock:
        saludados.clear()
    return jsonify({"ok": True})
