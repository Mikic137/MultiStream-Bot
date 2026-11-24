"""Alertas ligeras y locales para regalos, compartidos y nuevos seguidores de TikTok LIVE."""
import json, os, random, re, sys, time, uuid
from collections import deque
from pathlib import Path
from threading import Lock
from flask import Blueprint, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

BASE = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
ARCHIVO = BASE / "alertas.json"
VIDEOS = BASE / "videos_alertas"
SONIDOS = BASE / "sonidos_alertas"
VIDEO_MAX = 20 * 1024 * 1024
SONIDO_MAX = 10 * 1024 * 1024
VIDEO_EXT = {".mp4", ".webm"}
SONIDO_EXT = {".mp3", ".wav", ".ogg"}
try:
    VIDEOS.mkdir(exist_ok=True)
    SONIDOS.mkdir(exist_ok=True)
except OSError:
    pass
bp = Blueprint("alertas", __name__)
_lock = Lock()
_cola = deque()
_ult_social = {}
_descartadas = 0
MAX_COLA = 40

_DEF = {
    "regalo": {"activa": True, "min_diamantes": 1, "titulo": "¡NUEVO REGALO!",
               "texto": "{usuario} envió {cantidad}× {regalo} · {diamantes} diamantes",
               "color": "#ffd45e", "segundos": 5, "duracion_modo": "manual", "mostrar_tarjeta": True,
               "efecto": "energia", "video": "", "videos": [],
               "silenciar_video": False, "sonido": ""},
    "compartir": {"activa": True, "titulo": "¡GRACIAS POR COMPARTIR!",
                   "texto": "{usuario} compartió el directo",
                   "color": "#54dbed", "segundos": 3, "duracion_modo": "manual", "mostrar_tarjeta": True,
                   "efecto": "energia", "video": "", "videos": [],
                   "silenciar_video": False, "sonido": ""},
    "follow": {"activa": True, "titulo": "¡NUEVO SEGUIDOR!",
               "texto": "¡{usuario} te acaba de seguir!",
               "color": "#3df270", "segundos": 4, "duracion_modo": "manual", "mostrar_tarjeta": True,
               "efecto": "follower", "video": "", "videos": [],
               "silenciar_video": False, "sonido": ""},
    "kick_follow": {"activa": True, "titulo": "¡GRACIAS POR SEGUIRME!",
                    "texto": "¡{usuario} te acaba de seguir en Kick!",
                    "color": "#53fc18", "segundos": 5, "duracion_modo": "manual", "mostrar_tarjeta": True,
                    "efecto": "follower", "video": "", "videos": [],
                    "silenciar_video": True, "sonido": ""},
    "kick_sub": {"activa": True, "titulo": "¡NUEVA SUSCRIPCIÓN EN KICK!",
                 "texto": "¡{usuario} se ha suscrito a Kick! ({cantidad}×)",
                 "color": "#53fc18", "segundos": 5, "duracion_modo": "video", "mostrar_tarjeta": True,
                 "efecto": "energia", "video": "", "videos": [],
                 "silenciar_video": False, "sonido": ""},
    "kick_recompensa": {"activa": True, "titulo": "¡CANJE DE PUNTOS EN KICK!",
                        "texto": "¡{usuario} canjeó {regalo}!",
                        "color": "#53fc18", "segundos": 6, "duracion_modo": "video", "mostrar_tarjeta": False,
                        "efecto": "energia", "video": "", "videos": [],
                        "silenciar_video": False, "sonido": ""},
    "youtube_superchat": {"activa": True, "titulo": "¡SUPER CHAT EN YOUTUBE!",
                          "texto": "¡{usuario} envió un Super Chat de {regalo}!",
                          "color": "#ff0000", "segundos": 6, "duracion_modo": "manual", "mostrar_tarjeta": True,
                          "efecto": "energia", "video": "", "videos": [],
                          "silenciar_video": False, "sonido": ""},
    "youtube_miembro": {"activa": True, "titulo": "¡NUEVO MIEMBRO DEL CANAL!",
                        "texto": "¡{usuario} se unió como miembro en YouTube!",
                        "color": "#2ba640", "segundos": 5, "duracion_modo": "manual", "mostrar_tarjeta": True,
                        "efecto": "follower", "video": "", "videos": [],
                        "silenciar_video": False, "sonido": ""},
    "twitch_follow": {"activa": True, "titulo": "¡NUEVO SEGUIDOR EN TWITCH!",
                      "texto": "¡{usuario} te acaba de seguir en Twitch!",
                      "color": "#9146ff", "segundos": 4, "duracion_modo": "manual", "mostrar_tarjeta": True,
                      "efecto": "follower", "video": "", "videos": [],
                      "silenciar_video": True, "sonido": ""},
    "twitch_sub": {"activa": True, "titulo": "¡NUEVA SUSCRIPCIÓN EN TWITCH!",
                   "texto": "¡{usuario} se ha suscrito a Twitch! ({cantidad}×)",
                   "color": "#9146ff", "segundos": 5, "duracion_modo": "video", "mostrar_tarjeta": True,
                   "efecto": "energia", "video": "", "videos": [],
                   "silenciar_video": False, "sonido": ""}
}
_CFG = json.loads(json.dumps(_DEF))
_error_guardado = ""


def _color(v, default):
    if isinstance(v, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", v) and v.lower() != "#000000":
        return v
    return default


def _limpiar(datos):
    if not isinstance(datos, dict):
        datos = {}
    res = {}
    for tipo, base in _DEF.items():
        d = datos.get(tipo) if isinstance(datos.get(tipo), dict) else {}
        try:
            segundos = max(2, min(30, int(d.get("segundos", base["segundos"]))))
        except (TypeError, ValueError):
            segundos = base["segundos"]
        item = {"activa": bool(d.get("activa", base["activa"])),
                "mostrar_tarjeta": bool(d.get("mostrar_tarjeta", base.get("mostrar_tarjeta", True))),
                "duracion_modo": str(d.get("duracion_modo", base.get("duracion_modo", "manual"))) if str(d.get("duracion_modo", "")) in ("manual", "video") else base.get("duracion_modo", "manual"),
                "titulo": str(d.get("titulo", base["titulo"])).strip()[:60] or base["titulo"],
                "texto": str(d.get("texto", base["texto"])).strip()[:180] or base["texto"],
                "color": _color(d.get("color"), base["color"]), "segundos": segundos,
                "efecto": d.get("efecto") if d.get("efecto") in ("ninguno", "energia", "pixel", "follower") else base["efecto"]}
        video = str(d.get("video", ""))
        item["video"] = video if Path(video).name == video and Path(video).suffix.lower() in VIDEO_EXT else ""
        videos_raw = d.get("videos")
        if isinstance(videos_raw, list):
            item["videos"] = [Path(v).name for v in videos_raw if Path(v).name == v and Path(v).suffix.lower() in VIDEO_EXT]
        elif item["video"]:
            item["videos"] = [item["video"]]
        elif _CFG.get(tipo, {}).get("videos") and "videos" not in d:
            item["videos"] = list(_CFG[tipo]["videos"])
        else:
            item["videos"] = []
        if item["video"] and item["video"] not in item["videos"]:
            item["videos"].insert(0, item["video"])
        if item["videos"] and not item["video"]:
            item["video"] = item["videos"][0]
        item["silenciar_video"] = bool(d.get("silenciar_video", base.get("silenciar_video", False)))
        sonido = str(d.get("sonido", "")).strip()
        if sonido and Path(sonido).name == sonido and Path(sonido).suffix.lower() in SONIDO_EXT:
            item["sonido"] = sonido
        elif "sonido" in d and d["sonido"] == "":
            item["sonido"] = ""
        elif _CFG.get(tipo, {}).get("sonido"):
            item["sonido"] = _CFG[tipo]["sonido"]
        else:
            item["sonido"] = ""
        if tipo == "regalo":
            try:
                item["min_diamantes"] = max(0, min(1_000_000_000, int(d.get("min_diamantes", base["min_diamantes"]))))
            except (TypeError, ValueError):
                item["min_diamantes"] = base["min_diamantes"]
        res[tipo] = item

    # Alertas personalizadas individuales (cada una con su propio video, duración, tarjeta y sonido)
    alertas_pers = datos.get("alertas_personalizadas")
    if isinstance(alertas_pers, list):
        limpias_pers = []
        for p in alertas_pers:
            if not isinstance(p, dict):
                continue
            p_id = str(p.get("id") or f"custom_{uuid.uuid4().hex[:8]}")
            p_nombre = str(p.get("nombre") or "Alerta Personalizada").strip()[:60]
            p_trigger = str(p.get("trigger") or "").strip().lower()[:60]
            p_tipo = str(p.get("tipo") or "kick_recompensa")
            p_plat = str(p.get("plataforma") or ("kick" if p_tipo.startswith("kick") else "tiktok"))
            p_activa = bool(p.get("activa", True))
            p_tarjeta = bool(p.get("mostrar_tarjeta", False if p_tipo in ("kick_recompensa", "kick_reward") else True))
            p_duracion_modo = str(p.get("duracion_modo", "video"))
            try:
                p_seg = max(1, min(60, int(p.get("segundos", 10))))
            except (TypeError, ValueError):
                p_seg = 10
            p_video = str(p.get("video") or "").strip()
            if p_video and (Path(p_video).name != p_video or Path(p_video).suffix.lower() not in VIDEO_EXT):
                p_video = ""
            p_silenciar = bool(p.get("silenciar_video", False))
            p_sonido = str(p.get("sonido") or "").strip()
            if p_sonido and (Path(p_sonido).name != p_sonido or Path(p_sonido).suffix.lower() not in SONIDO_EXT):
                p_sonido = ""
            p_titulo = str(p.get("titulo") or "¡CANJE DE PUNTOS!").strip()[:60]
            p_texto = str(p.get("texto") or "¡{usuario} canjeó {regalo}!").strip()[:180]
            p_color = _color(p.get("color"), "#53fc18" if p_plat == "kick" else "#ffd45e")
            p_efecto = p.get("efecto") if p.get("efecto") in ("ninguno", "energia", "pixel", "follower") else "energia"

            limpias_pers.append({
                "id": p_id,
                "nombre": p_nombre,
                "trigger": p_trigger,
                "tipo": p_tipo,
                "plataforma": p_plat,
                "activa": p_activa,
                "mostrar_tarjeta": p_tarjeta,
                "duracion_modo": p_duracion_modo,
                "segundos": p_seg,
                "video": p_video,
                "silenciar_video": p_silenciar,
                "sonido": p_sonido,
                "titulo": p_titulo,
                "texto": p_texto,
                "color": p_color,
                "efecto": p_efecto
            })
        res["alertas_personalizadas"] = limpias_pers
    elif "alertas_personalizadas" in _CFG:
        res["alertas_personalizadas"] = _CFG["alertas_personalizadas"]
    else:
        res["alertas_personalizadas"] = []

    return res


try:
    _CFG.update(_limpiar(json.loads(ARCHIVO.read_text(encoding="utf-8"))))
except (OSError, ValueError, TypeError):
    pass


def _guardar():
    global _error_guardado
    tmp = ARCHIVO.with_name(ARCHIVO.name + ".tmp")
    try:
        tmp.write_text(json.dumps(_CFG, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(ARCHIVO)
        _error_guardado = ""
        return True
    except OSError as exc:
        _error_guardado = str(exc)[:180]
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def _quien(evento):
    if isinstance(evento, str):
        return evento[:40]
    u = getattr(evento, "user", None)
    if not u:
        return "Alguien"
    return str(
        getattr(u, "nickname", "") or
        getattr(u, "display_id", "") or
        getattr(u, "unique_id", "") or
        getattr(u, "id_str", "") or
        "Alguien"
    ).strip()[:40] or "Alguien"


def _crear(tipo, usuario, regalo="Regalo", cantidad=1, diamantes=0, plataforma="tiktok", custom_id=None):
    # 1. Búsqueda de alerta personalizada (si se especificó custom_id o por coincidencia de trigger/canje)
    alerta_p = None
    pers_list = _CFG.get("alertas_personalizadas", [])
    if custom_id:
        for p in pers_list:
            if str(p.get("id")) == str(custom_id):
                alerta_p = p
                break
    elif tipo in ("kick_recompensa", "kick_reward") or (tipo == "recompensa" and plataforma == "kick"):
        candidatos_trigger = []
        candidatos_generales = []
        texto_busqueda = (regalo or "").lower().strip()
        for p in pers_list:
            if not p.get("activa", True):
                continue
            if p.get("plataforma", "kick") != "kick":
                continue
            trig = (p.get("trigger") or "").lower().strip()
            nom = (p.get("nombre") or "").lower().strip()
            if trig and (trig in texto_busqueda or texto_busqueda in trig):
                candidatos_trigger.append(p)
            elif nom and nom in texto_busqueda:
                candidatos_trigger.append(p)
            elif not trig:
                candidatos_generales.append(p)
        if candidatos_trigger:
            alerta_p = candidatos_trigger[0]
        elif candidatos_generales:
            alerta_p = random.choice(candidatos_generales)
    elif tipo in ("kick_sub", "kick_donacion", "kick_regalo") or (tipo in ("sub", "regalo", "donacion") and plataforma == "kick"):
        texto_busqueda = (regalo or "").lower().strip()
        for p in pers_list:
            if not p.get("activa", True) or p.get("plataforma") != "kick":
                continue
            trig = (p.get("trigger") or "").lower().strip()
            nom = (p.get("nombre") or "").lower().strip()
            if trig and (trig in texto_busqueda or texto_busqueda in trig):
                alerta_p = p
                break
            elif nom and nom in texto_busqueda:
                alerta_p = p
                break
    elif tipo in ("youtube_superchat", "youtube_sc", "superchat") or (tipo in ("regalo", "donacion") and plataforma == "youtube"):
        texto_busqueda = (regalo or "").lower().strip()
        for p in pers_list:
            if not p.get("activa", True) or p.get("plataforma") != "youtube":
                continue
            trig = (p.get("trigger") or "").lower().strip()
            nom = (p.get("nombre") or "").lower().strip()
            if trig and (trig in texto_busqueda or texto_busqueda in trig):
                alerta_p = p
                break
            elif nom and nom in texto_busqueda:
                alerta_p = p
                break
    elif tipo in ("twitch_sub", "twitch_donacion", "twitch_follow") or (tipo in ("sub", "regalo", "follow") and plataforma == "twitch"):
        texto_busqueda = (regalo or "").lower().strip()
        for p in pers_list:
            if not p.get("activa", True) or p.get("plataforma") != "twitch":
                continue
            trig = (p.get("trigger") or "").lower().strip()
            nom = (p.get("nombre") or "").lower().strip()
            if trig and (trig in texto_busqueda or texto_busqueda in trig):
                alerta_p = p
                break
            elif nom and nom in texto_busqueda:
                alerta_p = p
                break
    elif tipo == "regalo" and plataforma == "tiktok":
        texto_busqueda = (regalo or "").lower().strip()
        for p in pers_list:
            if not p.get("activa", True) or p.get("plataforma") != "tiktok":
                continue
            trig = (p.get("trigger") or "").lower().strip()
            if trig and (trig in texto_busqueda or texto_busqueda in trig):
                alerta_p = p
                break

    # Si se encontró una alerta personalizada individual con sus propios segundos, tarjeta y video:
    if alerta_p:
        valores = {"usuario": usuario, "regalo": regalo, "cantidad": cantidad, "diamantes": diamantes}
        plantilla_texto = alerta_p.get("texto") or "¡{usuario} canjeó {regalo}!"
        try:
            mensaje = plantilla_texto.format(**valores)
        except (KeyError, ValueError, IndexError):
            mensaje = plantilla_texto

        video_url = ""
        v_nombre = alerta_p.get("video", "")
        if v_nombre and (VIDEOS / v_nombre).exists():
            video_url = f"/alertas/archivo/{v_nombre}"

        sonido_url = ""
        s_nombre = alerta_p.get("sonido", "")
        if s_nombre and (SONIDOS / s_nombre).exists():
            sonido_url = f"/alertas/archivo_sonido/{s_nombre}"

        return {
            "id": alerta_p.get("id"),
            "tipo": tipo,
            "titulo": alerta_p.get("titulo", "¡CANJE DE PUNTOS!"),
            "mensaje": mensaje[:220],
            "color": alerta_p.get("color", "#53fc18"),
            "segundos": alerta_p.get("segundos", 10),
            "efecto": alerta_p.get("efecto", "energia"),
            "mostrar_tarjeta": bool(alerta_p.get("mostrar_tarjeta", False)),
            "duracion_modo": alerta_p.get("duracion_modo", "video"),
            "video": video_url,
            "sonido": sonido_url,
            "silenciar_video": bool(alerta_p.get("silenciar_video", False)),
            "plataforma": alerta_p.get("plataforma", plataforma),
            **valores
        }

    # 2. Configuración estándar para alertas base
    c = _CFG.get(tipo) or _DEF.get(tipo) or _DEF["follow"]
    valores = {"usuario": usuario, "regalo": regalo, "cantidad": cantidad, "diamantes": diamantes}
    try:
        mensaje = c["texto"].format(**valores)
    except (KeyError, ValueError, IndexError):
        mensaje = c["texto"]

    titulo = c["titulo"]
    color = c["color"]

    # Ajuste dinámico de texto para donaciones o subs múltiples en Kick y YouTube
    if tipo == "kick_sub" or (tipo == "regalo" and plataforma == "kick"):
        if "donaci" in str(regalo).lower() or "$" in str(regalo) or "kicks" in str(regalo).lower():
            titulo = "¡DONACIÓN EN KICK!"
            mensaje = f"¡{usuario} envió {regalo}!"
        elif cantidad > 1 and "REGAL" not in titulo.upper():
            titulo = f"¡REGALÓ {cantidad} SUBS EN KICK!"
            mensaje = f"¡{usuario} ha regalado {cantidad} suscripciones a Kick!"
    elif tipo == "regalo" and plataforma == "youtube":
        titulo = "¡SUPER CHAT EN YOUTUBE!"
        color = "#ff0000"
        mensaje = f"¡{usuario} envió {regalo}!"

    # Selección de video (soporta lista múltiple o selección aleatoria / coincidencia)
    video_url = ""
    lista_videos = [v for v in c.get("videos", []) if v and (VIDEOS / v).exists()]
    if not lista_videos and c.get("video") and (VIDEOS / c["video"]).exists():
        lista_videos = [c["video"]]

    if lista_videos:
        elegido = None
        texto_busqueda = f"{regalo} {mensaje}".lower()
        for v in lista_videos:
            stem = Path(v).stem.lower().replace("_", " ")
            palabras = [w for w in re.split(r"[\s\-_]+", stem) if len(w) > 3 and w not in ("kick", "tiktok", "recompensa", "regalo", "video", "follow", "sub")]
            if palabras and any(p in texto_busqueda for p in palabras):
                elegido = v
                break
        if not elegido:
            elegido = random.choice(lista_videos)
        video_url = f"/alertas/archivo/{elegido}"
    elif tipo == "kick_follow" and _CFG.get("follow", {}).get("video"):
        video_url = "/alertas/video/follow"
    elif tipo in ("kick_sub", "kick_recompensa") and _CFG.get("regalo", {}).get("video"):
        video_url = "/alertas/video/regalo"

    sonido_url = ""
    if c.get("sonido") and (SONIDOS / c["sonido"]).exists():
        sonido_url = f"/alertas/sonido/{tipo}"
    else:
        try:
            archivos_sfx = list(SONIDOS.glob(f"sfx_{tipo}_*"))
            if archivos_sfx:
                sonido_url = f"/alertas/archivo_sonido/{archivos_sfx[0].name}"
        except OSError:
            pass
    silenciar = bool(c.get("silenciar_video", False))
    mostrar_tarjeta = bool(c.get("mostrar_tarjeta", False if tipo in ("kick_recompensa", "kick_reward") else True))
    duracion_modo = str(c.get("duracion_modo", "video" if tipo in ("kick_sub", "kick_recompensa") else "manual"))

    return {"tipo": tipo, "titulo": titulo, "mensaje": mensaje[:220], "color": color,
            "segundos": c["segundos"], "efecto": c["efecto"],
            "mostrar_tarjeta": mostrar_tarjeta,
            "duracion_modo": duracion_modo,
            "video": video_url,
            "sonido": sonido_url,
            "silenciar_video": silenciar,
            "plataforma": plataforma,
            **valores}


def _encolar(alerta):
    global _descartadas
    tipo_alerta = alerta.get("tipo", "")
    plat = alerta.get("plataforma", "tiktok")
    u_nom = alerta.get("usuario", "")
    r_nom = alerta.get("regalo", "")
    key = (tipo_alerta, u_nom.casefold(), r_nom.casefold(), plat)
    with _lock:
        if tipo_alerta in ("regalo", "kick_sub"):
            # Regalos y subs repetidos durante una alerta pendiente se agrupan
            for actual in reversed(_cola):
                if (actual["tipo"], actual["usuario"].casefold(), actual.get("regalo", "").casefold(), actual.get("plataforma", "")) == key:
                    actual["cantidad"] += alerta["cantidad"]
                    actual["diamantes"] += alerta["diamantes"]
                    actual.update({"mensaje": _crear(actual["tipo"], actual["usuario"], actual.get("regalo", "Regalo"),
                                                       actual["cantidad"], actual["diamantes"], actual.get("plataforma", plat))["mensaje"]})
                    break
            else:
                if len(_cola) >= MAX_COLA:
                    _cola.popleft()
                    _descartadas += 1
                _cola.append(alerta)
        elif tipo_alerta in ("kick_recompensa", "kick_reward", "recompensa"):
            # Canjes de recompensas por puntos de canal: cada uno se ejecuta individualmente
            if len(_cola) >= MAX_COLA:
                _cola.popleft()
                _descartadas += 1
            _cola.append(alerta)
        else:
            # Para follow o compartir: si ya está en cola o sonó hace menos de 8 s, no duplicar
            ahora = time.time()
            u_key = (tipo_alerta, u_nom.casefold(), plat)
            if ahora - _ult_social.get(u_key, 0) < 8:
                return
            for actual in _cola:
                if actual["tipo"] == tipo_alerta and actual["usuario"].casefold() == u_nom.casefold() and actual.get("plataforma") == plat:
                    return
            _ult_social[u_key] = ahora
            if len(_cola) >= MAX_COLA:
                _cola.popleft()
                _descartadas += 1
            _cola.append(alerta)
    import live
    live.publicar("stream_alert", {})


def evento(tipo, e, regalo="Regalo", cantidad=1, diamantes=0, plataforma="tiktok"):
    if tipo in ("share", "compartir"):
        c = _CFG.get("compartir")
        if c and c["activa"]:
            _encolar(_crear("compartir", _quien(e), plataforma="tiktok"))
    elif tipo == "follow" and plataforma == "tiktok":
        c = _CFG.get("follow")
        if c and c["activa"]:
            _encolar(_crear("follow", _quien(e), plataforma="tiktok"))
    elif (tipo == "kick_follow" or (tipo == "follow" and plataforma == "kick")):
        c = _CFG.get("kick_follow")
        if c and c["activa"]:
            _encolar(_crear("kick_follow", _quien(e), plataforma="kick"))
    elif (tipo in ("kick_sub", "kick_donacion", "kick_regalo") or (tipo in ("sub", "regalo", "donacion") and plataforma == "kick")):
        c = _CFG.get("kick_sub")
        if c and c["activa"]:
            _encolar(_crear("kick_sub", _quien(e), regalo=regalo, cantidad=cantidad, diamantes=diamantes, plataforma="kick"))
    elif (tipo in ("kick_recompensa", "kick_reward") or (tipo == "recompensa" and plataforma == "kick")):
        c = _CFG.get("kick_recompensa")
        if c and c["activa"]:
            _encolar(_crear("kick_recompensa", _quien(e), regalo=regalo, cantidad=cantidad, diamantes=diamantes, plataforma="kick"))
    elif (tipo in ("youtube_superchat", "youtube_sc") or (tipo in ("regalo", "donacion") and plataforma == "youtube")):
        c = _CFG.get("youtube_superchat")
        if c and c["activa"]:
            _encolar(_crear("youtube_superchat", _quien(e), regalo=regalo, cantidad=cantidad, diamantes=diamantes, plataforma="youtube"))
    elif (tipo in ("youtube_miembro", "youtube_sub") or (tipo == "sub" and plataforma == "youtube")):
        c = _CFG.get("youtube_miembro")
        if c and c["activa"]:
            _encolar(_crear("youtube_miembro", _quien(e), regalo=regalo, cantidad=cantidad, diamantes=diamantes, plataforma="youtube"))
    elif (tipo == "twitch_follow" or (tipo == "follow" and plataforma == "twitch")):
        c = _CFG.get("twitch_follow")
        if c and c["activa"]:
            _encolar(_crear("twitch_follow", _quien(e), plataforma="twitch"))
    elif (tipo in ("twitch_sub", "twitch_donacion") or (tipo in ("sub", "regalo") and plataforma == "twitch")):
        c = _CFG.get("twitch_sub")
        if c and c["activa"]:
            _encolar(_crear("twitch_sub", _quien(e), regalo=regalo, cantidad=cantidad, diamantes=diamantes, plataforma="twitch"))
    elif tipo == "regalo" and plataforma == "tiktok":
        if isinstance(e, str):
            c = _CFG.get("regalo")
            if c and c["activa"]:
                _encolar(_crear("regalo", e, regalo, cantidad, diamantes, plataforma="tiktok"))
            return
        if getattr(e, "streaking", False):
            return
        g = getattr(e, "gift", None)
        nombre = str(getattr(g, "name", "") or "Regalo")[:60]
        try:
            cantidad = max(1, min(1_000_000, int(getattr(e, "repeat_count", 1) or 1)))
            valor = max(0, min(1_000_000_000, int(getattr(g, "diamond_count", 0) or 0)))
        except (TypeError, ValueError, OverflowError):
            cantidad, valor = 1, 0
        diamantes = cantidad * valor
        c = _CFG.get("regalo")
        if c and c["activa"] and diamantes >= c["min_diamantes"]:
            _encolar(_crear("regalo", _quien(e), nombre, cantidad, diamantes, plataforma="tiktok"))


@bp.route("/alertas")
def overlay():
    return render_template("alertas.html")


@bp.route("/alertas/panel")
def panel():
    return render_template("alertas_panel.html")


@bp.route("/api/alertas", methods=["GET", "POST"])
def api_alertas():
    global _CFG
    if request.method == "POST":
        with _lock:
            _CFG = _limpiar(request.get_json(silent=True) or {})
            guardado = _guardar()
            pendientes = len(_cola)
            descartadas = _descartadas
        import live
        live.publicar("alertas_cfg", {})
        return jsonify({"ok": guardado, "error": _error_guardado, "cfg": _CFG,
                        "pendientes": pendientes, "descartadas": descartadas})
    with _lock:
        return jsonify({"ok": not bool(_error_guardado), "error": _error_guardado, "cfg": _CFG,
                        "pendientes": len(_cola), "descartadas": _descartadas})


@bp.route("/api/alertas/siguiente", methods=["POST"])
def siguiente():
    with _lock:
        a = _cola.popleft() if _cola else None
    return jsonify({"alerta": a})


@bp.route("/api/alertas/prueba", methods=["POST"])
def prueba():
    d = request.get_json(silent=True) or {}
    if not isinstance(d, dict):
        d = {}
    custom_id = d.get("id")
    tipo = d.get("tipo") if d.get("tipo") in _DEF else ("kick_recompensa" if custom_id else "regalo")
    plat = d.get("plataforma")
    if not plat:
        if tipo.startswith("kick_"): plat = "kick"
        elif tipo.startswith("youtube_"): plat = "youtube"
        elif tipo.startswith("twitch_"): plat = "twitch"
        elif custom_id: plat = "kick"
        else: plat = "tiktok"
    reg = d.get("regalo") or ("Suscripción" if "sub" in tipo else "Super Chat $5.00" if "superchat" in tipo else "Miembro del Canal" if "miembro" in tipo else "Reto Legendario" if "recompensa" in tipo else "Rosa")
    cant = max(1, int(d.get("cantidad") or 1))
    dia = max(0, int(d.get("diamantes") or (cant * 100)))
    a = _crear(tipo, "NEXUS", reg, cant, dia, plataforma=plat, custom_id=custom_id)
    import live
    live.publicar("alertas_prueba", a)
    return jsonify({"ok": True, "alerta": a})


@bp.route("/api/alertas/archivos", methods=["GET"])
def api_archivos():
    videos = []
    try:
        for f in VIDEOS.iterdir():
            if f.is_file() and f.suffix.lower() in VIDEO_EXT:
                clean_name = re.sub(r"^[^_]+_", "", f.stem)
                clean_name = re.sub(r"_[a-f0-9]{6,}$", "", clean_name).replace("_", " ").strip()
                videos.append({"archivo": f.name, "nombre": clean_name or f.name, "url": f"/alertas/archivo/{f.name}"})
    except OSError:
        pass
    sonidos = []
    try:
        for f in SONIDOS.iterdir():
            if f.is_file() and f.suffix.lower() in SONIDO_EXT:
                clean_name = re.sub(r"^sfx_[^_]+_", "", f.stem)
                clean_name = re.sub(r"_[a-f0-9]{6,}$", "", clean_name).replace("_", " ").strip()
                sonidos.append({"archivo": f.name, "nombre": clean_name or f.name, "url": f"/alertas/archivo_sonido/{f.name}"})
    except OSError:
        pass
    videos.sort(key=lambda x: x["nombre"].lower())
    sonidos.sort(key=lambda x: x["nombre"].lower())
    return jsonify({"ok": True, "videos": videos, "sonidos": sonidos})


@bp.route("/api/alertas/subir_clip", methods=["POST"])
def api_subir_clip():
    archivo = request.files.get("archivo")
    if not archivo or not archivo.filename:
        return jsonify({"ok": False, "error": "Selecciona un video."}), 400
    clean_stem = re.sub(r"[^\w\-]", "_", Path(secure_filename(archivo.filename)).stem)[:35]
    ext = Path(secure_filename(archivo.filename)).suffix.lower()
    if ext not in VIDEO_EXT:
        return jsonify({"ok": False, "error": "Usa un video MP4 o WebM."}), 400
    if request.content_length and request.content_length > VIDEO_MAX + 1024 * 1024:
        return jsonify({"ok": False, "error": "El video no puede superar 20 MB."}), 413
    nombre = f"clip_{clean_stem}_{uuid.uuid4().hex[:6]}{ext}"
    destino, temporal = VIDEOS / nombre, VIDEOS / (nombre + ".tmp")
    try:
        archivo.save(temporal)
        temporal.replace(destino)
    except OSError as e:
        try:
            temporal.unlink(missing_ok=True)
        except OSError:
            pass
        return jsonify({"ok": False, "error": f"Error al guardar: {e}"}), 500
    return jsonify({"ok": True, "archivo": nombre, "url": f"/alertas/archivo/{nombre}"})


@bp.route("/api/alertas/subir_audio", methods=["POST"])
def api_subir_audio():
    archivo = request.files.get("archivo")
    if not archivo or not archivo.filename:
        return jsonify({"ok": False, "error": "Selecciona un audio."}), 400
    clean_stem = re.sub(r"[^\w\-]", "_", Path(secure_filename(archivo.filename)).stem)[:35]
    ext = Path(secure_filename(archivo.filename)).suffix.lower()
    if ext not in SONIDO_EXT:
        return jsonify({"ok": False, "error": "Usa un audio MP3, WAV o OGG."}), 400
    if request.content_length and request.content_length > SONIDO_MAX + 512 * 1024:
        return jsonify({"ok": False, "error": "El audio no puede superar 10 MB."}), 413
    nombre = f"sfx_{clean_stem}_{uuid.uuid4().hex[:6]}{ext}"
    destino = SONIDOS / nombre
    try:
        archivo.save(destino)
    except OSError as e:
        return jsonify({"ok": False, "error": f"Error al guardar: {e}"}), 500
    return jsonify({"ok": True, "archivo": nombre, "url": f"/alertas/archivo_sonido/{nombre}"})


@bp.route("/alertas/archivo/<path:nombre>")
def archivo_video(nombre):
    nombre_limpio = Path(nombre).name
    if not nombre_limpio or Path(nombre_limpio).suffix.lower() not in VIDEO_EXT:
        return "", 404
    return send_from_directory(VIDEOS, nombre_limpio, conditional=True, max_age=0)


@bp.route("/alertas/archivo_sonido/<path:nombre>")
def archivo_sonido(nombre):
    nombre_limpio = Path(nombre).name
    if not nombre_limpio or Path(nombre_limpio).suffix.lower() not in SONIDO_EXT:
        return "", 404
    return send_from_directory(SONIDOS, nombre_limpio, conditional=True, max_age=0)


@bp.route("/api/alertas/video/<tipo>", methods=["POST", "DELETE"])
def api_video(tipo):
    global _CFG
    if tipo not in _DEF:
        return jsonify({"ok": False, "error": "Tipo de alerta no válido."}), 404

    if request.method == "DELETE":
        archivo_del = (request.args.get("archivo") or "").strip()
        with _lock:
            if "videos" not in _CFG[tipo] or not isinstance(_CFG[tipo]["videos"], list):
                _CFG[tipo]["videos"] = [_CFG[tipo]["video"]] if _CFG[tipo].get("video") else []

            if archivo_del:
                _CFG[tipo]["videos"] = [v for v in _CFG[tipo]["videos"] if v != archivo_del]
                _CFG[tipo]["video"] = _CFG[tipo]["videos"][0] if _CFG[tipo]["videos"] else ""
                ok = _guardar()
                try:
                    (VIDEOS / archivo_del).unlink(missing_ok=True)
                except OSError:
                    pass
            else:
                antiguos = list(_CFG[tipo].get("videos", []))
                _CFG[tipo]["videos"] = []
                _CFG[tipo]["video"] = ""
                ok = _guardar()
                for a in antiguos:
                    try:
                        (VIDEOS / a).unlink(missing_ok=True)
                    except OSError:
                        pass
        import live
        live.publicar("alertas_cfg", {})
        return jsonify({"ok": True, "video": _CFG[tipo].get("video", ""), "videos": _CFG[tipo].get("videos", [])})

    else:
        archivo = request.files.get("archivo")
        if not archivo or not archivo.filename:
            return jsonify({"ok": False, "error": "Selecciona un video."}), 400
        clean_stem = re.sub(r"[^\w\-]", "_", Path(secure_filename(archivo.filename)).stem)[:30]
        ext = Path(secure_filename(archivo.filename)).suffix.lower()
        if ext not in VIDEO_EXT:
            return jsonify({"ok": False, "error": "Usa un video MP4 o WebM."}), 400
        if request.content_length and request.content_length > VIDEO_MAX + 1024 * 1024:
            return jsonify({"ok": False, "error": "El video no puede superar 20 MB."}), 413
        nombre = f"{tipo}_{clean_stem}_{uuid.uuid4().hex[:6]}{ext}"
        destino, temporal = VIDEOS / nombre, VIDEOS / (nombre + ".tmp")
        total = 0
        try:
            with temporal.open("xb") as salida:
                while True:
                    bloque = archivo.stream.read(1024 * 1024)
                    if not bloque:
                        break
                    total += len(bloque)
                    if total > VIDEO_MAX:
                        raise ValueError("El video no puede superar 20 MB.")
                    salida.write(bloque)
            if not total:
                raise ValueError("El archivo de video está vacío.")
            temporal.replace(destino)
        except ValueError as exc:
            try:
                temporal.unlink(missing_ok=True)
            except OSError:
                pass
            return jsonify({"ok": False, "error": str(exc)}), 413
        except OSError:
            try:
                temporal.unlink(missing_ok=True)
            except OSError:
                pass
            return jsonify({"ok": False, "error": "No se pudo guardar el video."}), 500

        with _lock:
            if "videos" not in _CFG[tipo] or not isinstance(_CFG[tipo]["videos"], list):
                _CFG[tipo]["videos"] = [_CFG[tipo]["video"]] if _CFG[tipo].get("video") else []
            if nombre not in _CFG[tipo]["videos"]:
                _CFG[tipo]["videos"].append(nombre)
            _CFG[tipo]["video"] = _CFG[tipo]["videos"][0]
            ok = _guardar()

        if not ok:
            try:
                destino.unlink(missing_ok=True)
            except OSError:
                pass
            return jsonify({"ok": False, "error": _error_guardado}), 500

        import live
        live.publicar("alertas_cfg", {})
        return jsonify({"ok": True, "video": _CFG[tipo].get("video", ""), "videos": _CFG[tipo].get("videos", [])})


@bp.route("/alertas/video/<tipo>")
def video(tipo):
    if tipo not in _DEF:
        return "", 404
    nombre = _CFG[tipo].get("video", "")
    if not nombre:
        return "", 404
    return send_from_directory(VIDEOS, nombre, conditional=True, max_age=0)


@bp.route("/alertas/sonido/<tipo>")
def sonido(tipo):
    if tipo not in _DEF:
        return "", 404
    nombre = _CFG[tipo].get("sonido", "")
    if not nombre:
        return "", 404
    return send_from_directory(SONIDOS, nombre, conditional=True, max_age=0)


@bp.route("/api/alertas/sonido/<tipo>", methods=["POST", "DELETE"])
def api_sonido(tipo):
    global _CFG
    if tipo not in _DEF:
        return jsonify({"ok": False, "error": "Tipo de alerta no válido."}), 404
    anterior = _CFG[tipo].get("sonido", "")
    if request.method == "DELETE":
        with _lock:
            _CFG[tipo]["sonido"] = ""
            ok = _guardar()
            if not ok:
                _CFG[tipo]["sonido"] = anterior
        if not ok:
            return jsonify({"ok": False, "error": _error_guardado}), 500
        if anterior:
            try:
                (SONIDOS / anterior).unlink(missing_ok=True)
            except OSError:
                pass
        import live
        live.publicar("alertas_cfg", {})
        return jsonify({"ok": True, "sonido": ""})
    else:
        archivo = request.files.get("archivo")
        if not archivo or not archivo.filename:
            return jsonify({"ok": False, "error": "Selecciona un archivo de audio."}), 400
        ext = Path(secure_filename(archivo.filename)).suffix.lower()
        if ext not in SONIDO_EXT:
            return jsonify({"ok": False, "error": "Usa un audio MP3, WAV o OGG."}), 400
        if request.content_length and request.content_length > SONIDO_MAX + 512 * 1024:
            return jsonify({"ok": False, "error": "El audio no puede superar 10 MB."}), 413
        nombre = f"sfx_{tipo}_{uuid.uuid4().hex[:10]}{ext}"
        destino = SONIDOS / nombre
        try:
            archivo.save(destino)
        except OSError as e:
            return jsonify({"ok": False, "error": f"Error al guardar: {e}"}), 500
        with _lock:
            _CFG[tipo]["sonido"] = nombre
            ok = _guardar()
            if not ok:
                _CFG[tipo]["sonido"] = anterior
        if not ok:
            destino.unlink(missing_ok=True)
            return jsonify({"ok": False, "error": _error_guardado}), 500
        if anterior and anterior != nombre:
            try:
                (SONIDOS / anterior).unlink(missing_ok=True)
            except OSError:
                pass
        import live
        live.publicar("alertas_cfg", {})
        return jsonify({"ok": True, "sonido": nombre})
