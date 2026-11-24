import sorteo
"""Chat en vivo + canal SSE (una sola conexión abierta en vez de polling)."""
import asyncio, json, os, sys, time
from collections import deque
from pathlib import Path
from threading import Lock
from flask import Blueprint, jsonify, render_template, request
from TikTokLive import TikTokLiveClient
from TikTokLive.events import (CommentEvent, ConnectEvent, FollowEvent, GiftEvent,
                               JoinEvent, LikeEvent, RoomUserSeqEvent, ShareEvent, SocialEvent)
from TikTokLive.client.errors import UserOfflineError
from kick_client import KickClient
from twitch_client import TwitchClient
from youtube_client import YouTubeClient
import stats
import vips
import alertas
try:
    from TikTokLive.events import EmoteChatEvent  # mensajes que son solo un sticker
except ImportError:
    EmoteChatEvent = None

BASE = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
ARCHIVO_KICK = BASE / "kick_config.json"
ARCHIVO_TWITCH = BASE / "twitch_config.json"
ARCHIVO_YOUTUBE = BASE / "youtube_config.json"
ARCHIVO_PLATAFORMAS = BASE / "plataformas_config.json"

kick_estado = {"conectado": False, "conectando": False, "canal": "", "mensaje": "Desconectado", "viewers": 0}
twitch_estado = {"conectado": False, "conectando": False, "canal": "", "mensaje": "Desconectado", "viewers": 0, "en_directo": False}
youtube_estado = {"conectado": False, "conectando": False, "canal": "", "mensaje": "Desconectado", "viewers": 0, "en_directo": False}
tiktok_info = {"en_directo": False, "usuario": ""}
plataformas_cfg = {"tiktok": False, "kick": True, "twitch": True, "youtube": True, "ocultar_desconectadas": False, "mostrar_total": True}

kick_instancia = None
twitch_instancia = None
youtube_instancia = None
_procesador_comentario = None

try:
    if ARCHIVO_KICK.exists():
        _dk = json.loads(ARCHIVO_KICK.read_text(encoding="utf-8"))
        if _dk.get("canal"):
            kick_estado["canal"] = _dk["canal"]
except Exception:
    pass

try:
    if ARCHIVO_TWITCH.exists():
        _dt = json.loads(ARCHIVO_TWITCH.read_text(encoding="utf-8"))
        if _dt.get("canal"):
            twitch_estado["canal"] = _dt["canal"]
except Exception:
    pass

try:
    if ARCHIVO_YOUTUBE.exists():
        _dy = json.loads(ARCHIVO_YOUTUBE.read_text(encoding="utf-8"))
        if _dy.get("canal"):
            youtube_estado["canal"] = _dy["canal"]
except Exception:
    pass

try:
    if ARCHIVO_PLATAFORMAS.exists():
        _dp = json.loads(ARCHIVO_PLATAFORMAS.read_text(encoding="utf-8"))
        if isinstance(_dp, dict):
            plataformas_cfg.update(_dp)
except Exception:
    pass

bp = Blueprint("live", __name__)
historial = deque(maxlen=30)
_lock = Lock()
eventos, _seq = deque(maxlen=200), 0  # avisos para las páginas de OBS
presentes, _estado_f = {}, {}  # qué fuentes de OBS están dando señal
registro = deque(maxlen=100)
BOOT = str(int(time.time() * 1000))  # cambia en cada arranque


def log(msg):
    registro.append(time.strftime("%H:%M:%S ") + msg)


def publicar(tipo, datos):
    global _seq
    with _lock:
        _seq += 1
        eventos.append((_seq, tipo, datos))


def _partes(texto, emotes):
    """Mezcla texto y stickers en orden: [{'t': 'hola '}, {'i': 'https://...'}]."""
    partes, pos = [], 0
    for idx, url in sorted(emotes, key=lambda x: x[0]):
        idx = min(idx, len(texto))
        if idx > pos:
            partes.append({"t": texto[pos:idx]})
            pos = idx
        partes.append({"i": url})
    if pos < len(texto):
        partes.append({"t": texto[pos:]})
    return partes


def publicar_chat(nombre, texto, emotes=(), badge=None, plataforma="tiktok", uid=""):
    d = {"n": nombre, "u": uid, "t": texto, "p": _partes(texto, list(emotes)[:6]), "vip": badge,
         "estilo": vips.estilo_general_chat(), "plat": plataforma}
    historial.append(d)
    publicar("chat", d)


def _nombre(e):
    u = getattr(e, "user", None)
    return str(getattr(u, "nickname", "") or getattr(u, "display_id", "") or getattr(u, "unique_id", "") or "?")[:30]


def _user_id(e):
    u = getattr(e, "user", None)
    return str(getattr(u, "unique_id", "") or getattr(u, "display_id", "") or getattr(u, "id_str", "") or "").lower().lstrip("@")[:40]


def _url(img):
    urls = getattr(img, "url_list", None) or []
    return str(urls[0]) if urls and str(urls[0]).startswith("https://") else None


def _emotes(e):
    """[(posición en el texto, url)] de los stickers dentro de un comentario."""
    r = []
    for ei in getattr(e, "emotes", None) or []:
        url = _url(getattr(getattr(ei, "emote", None), "image", None))
        if url:
            r.append((max(0, int(getattr(ei, "index", 0) or 0)), url))
    return r


def _stickers(e):
    """Stickers de un mensaje que es solo sticker (sin texto)."""
    return [(0, u) for u in (_url(getattr(em, "image", None)) for em in getattr(e, "emote_list", None) or []) if u]


@bp.route("/chat")
def chat():
    return render_template("chat.html")


@bp.route("/api/log")
def api_log():
    return jsonify(list(registro))


def _revisar():
    """Anota en el registro cuándo una fuente de OBS deja de dar señal o vuelve."""
    ahora = time.time()
    for n, ts in list(presentes.items()):
        ok = ahora - ts < 12
        if _estado_f.get(n) != ok:
            primera = n not in _estado_f
            _estado_f[n] = ok
            log(f"Fuente «{n}» " + ("conectada" if ok and primera else "reconectada" if ok
                                   else f"desconectada (sin señal hace {int(ahora - ts)} s)"))


@bp.route("/api/ev")
def api_ev():
    """Eventos nuevos desde la última consulta. Cada página lo pide cada segundo (consulta corta)."""
    n = request.args.get("n", "")[:20]
    if n:
        presentes[n] = time.time()
        _revisar()
    try:
        desde = int(request.args.get("desde", ""))
    except ValueError:
        desde = None
    tipos = {x for x in request.args.get("t", "").split(",") if x}
    with _lock:
        seq = _seq
        if desde is None:
            ev = [[0, "chat", d] for d in list(historial)[-8:]]  # al abrir: solo los últimos mensajes del chat
        elif desde > seq:
            ev = []  # el programa se reinició
        else:
            ev = [[s, t, d] for s, t, d in eventos if s > desde and (not tipos or t in tipos)]
    return jsonify({"seq": seq, "ev": ev, "boot": BOOT})


@bp.route("/api/fuentes")
def api_fuentes():
    _revisar()
    ahora = time.time()
    return jsonify([{"n": n, "ok": ahora - ts < 12} for n, ts in sorted(presentes.items())])


def _despachar_evento(e, tipo):
    vips.visto(e)
    try:
        u = getattr(e, "user", None)
        if u:
            nick = str(getattr(u, "nickname", "") or getattr(u, "display_id", "") or getattr(u, "unique_id", "") or "")
            uid = str(getattr(u, "unique_id", "") or getattr(u, "display_id", "") or getattr(u, "id_str", "") or "")
            if nick:
                sorteo.registrar_actividad(nick, uid=uid, tipo=tipo)
    except Exception:
        pass
    if tipo in ("regalo", "share", "follow"):
        try:
            alertas.evento(tipo, e)
        except Exception as err:
            log(f"Alerta {tipo}: no se pudo procesar ({str(err)[:100]})")
    if tipo == "regalo":
        try:
            import hk_spawner
            g = getattr(e, "gift", None)
            nom_regalo = str(getattr(g, "name", "") or "")
            diamantes = int(getattr(g, "diamond_count", 0) or 0)
            u = getattr(e, "user", None)
            nick_u = str(getattr(u, "nickname", "") or getattr(u, "unique_id", "") or "Espectador")
            hk_spawner.registrar_evento("tiktok", "regalo", nick_u, nom_regalo, diamantes)
        except Exception:
            pass
    msg = stats.evento(tipo, e)
    if msg:
        log(msg)


def _escuchar(cliente, evento, tipo):
    @cliente.on(evento)
    async def _h(e):
        _despachar_evento(e, tipo)


async def vigilar_tiktok(usuario, estado, on_comentario):
    """Reemplaza el sondeo is_live cada 60 s: una conexión que escucha el chat."""
    while True:
        cliente = TikTokLiveClient(unique_id=usuario)

        @cliente.on(ConnectEvent)
        async def _conectado(e):
            tiktok_info["en_directo"] = True
            tiktok_info["usuario"] = str(usuario)
            estado("en_directo", True)
            stats.reiniciar()

        @cliente.on(CommentEvent)
        async def _comentario(e):
            vips.visto(e)
            on_comentario(_nombre(e), str(e.comment)[:200], _emotes(e), _user_id(e))

        if EmoteChatEvent is not None:
            @cliente.on(EmoteChatEvent)
            async def _sticker(e):
                r = _stickers(e)
                if r:
                    on_comentario(_nombre(e), "", r, _user_id(e))

        for ev, tipo in ((RoomUserSeqEvent, "viewers"), (LikeEvent, "likes"), (FollowEvent, "follow"),
                         (ShareEvent, "share"), (GiftEvent, "regalo"), (JoinEvent, "join")):
            _escuchar(cliente, ev, tipo)

        # Captura de SocialEvent general (cubre seguidores cuando TikTokLive no emite FollowEvent)
        @cliente.on(SocialEvent)
        async def _social(e):
            action = getattr(e, "action", 0)
            follow_type = getattr(e, "follow_type", 0)
            key = ""
            try:
                c = getattr(e, "common", None)
                if c and getattr(c, "display_text", None):
                    key = (getattr(c.display_text, "key", "") or "").lower()
            except Exception:
                pass
            if action == 3 or follow_type > 0 or "follow" in key:
                _despachar_evento(e, "follow")
            elif action == 1 or "share" in key:
                _despachar_evento(e, "share")

        try:
            await cliente.connect()
            estado("desconectado", False)
        except UserOfflineError:
            estado("desconectado", False)
        except Exception as err:
            estado("error", False, str(err))
        await asyncio.sleep(60)


def registrar_procesador(fn):
    global _procesador_comentario
    _procesador_comentario = fn


# ---------------- Callbacks de eventos de Kick ----------------
_ultimas_subs_kick = {}


def on_kick_message(username, content, emotes, uid, badges):
    # Detección de donaciones para Hollow Knight Spawner, alertas y estadísticas
    try:
        import hk_spawner
        cont_low = str(content or "").lower()
        if any(w in cont_low for w in ("$", "donó", "dono", "kicks", "propina", "usd")):
            import re
            m = re.search(r"(\d+(?:\.\d+)?)", content)
            val = float(m.group(1)) if m else 0
            if val > 0:
                hk_spawner.registrar_evento("kick", "donacion", username, content, val)
                stats.registrar_donacion_dinero(username, val, fuente="Kick")
                desc = f"${val:.2f}" if "$" in content else f"{val:g} Kicks"
                try:
                    alertas.evento("kick_sub", username, regalo=f"Donación {desc}", cantidad=max(1, int(val)), diamantes=int(val * 100), plataforma="kick")
                except Exception as e:
                    log(f"Error alerta Kick donación: {e}")

        # Detección de anuncios de subs regaladas en chat como respaldo
        elif any(w in cont_low for w in ("ha regalado", "regaló", "regalo", "gifted")) and any(s in cont_low for s in ("suscri", "sub")):
            import re
            m_cnt = re.search(r"(\d+)\s*(?:suscri|subs)", cont_low)
            cnt = int(m_cnt.group(1)) if m_cnt else 1
            m_user = re.search(r"^([a-zA-Z0-9_\-]+)\s+ha\s+regalado", content, re.IGNORECASE) or re.search(r"^([a-zA-Z0-9_\-]+)\s+gifted", content, re.IGNORECASE)
            gifter = m_user.group(1) if m_user else username
            on_kick_sub(gifter, cnt)
    except Exception:
        pass

    # 1. VIP entrance check (y registro en recientes del panel VIP)
    try:
        vips.visto(username, nickname=username)
    except Exception as e:
        log(f"VIP Kick error: {e}")

    # 2. Procesamiento completo (ruleta de retos, sorteos, canciones, comandos)
    if _procesador_comentario:
        try:
            _procesador_comentario(username, content, emotes=emotes, uid=uid, plataforma="kick")
            return
        except Exception as e:
            log(f"Error procesando mensaje Kick: {e}")

    # Fallback si no hay procesador
    try:
        sorteo.registrar_actividad(username, uid=uid, tipo="chat")
        badge_vip = vips.estilo_chat(username) or vips.estilo_chat(uid)
        publicar_chat(username, content, emotes=emotes, badge=badge_vip, plataforma="kick", uid=uid)
    except Exception:
        pass


def on_kick_follow(username):
    log(f"💖 Nuevo seguidor en Kick: {username}")
    try:
        alertas.evento("follow", username, plataforma="kick")
    except Exception as e:
        log(f"Error alerta Kick follow: {e}")
    try:
        sorteo.registrar_actividad(username, tipo="follow")
    except Exception:
        pass


def on_kick_sub(username, count=1):
    ahora = time.time()
    count = max(1, int(count or 1))
    u_key = (str(username or "").lower().strip(), count)
    if ahora - _ultimas_subs_kick.get(u_key, 0) < 5:
        log(f"⭐ Sub Kick duplicada omitida (<5s): {username} (x{count})")
        return
    _ultimas_subs_kick[u_key] = ahora

    nombre_evento = "Suscripción" if count == 1 else f"{count} Suscripciones Regaladas"
    log(f"⭐ Suscripción/Regalo en Kick: {username} ({nombre_evento})")
    try:
        import hk_spawner
        hk_spawner.registrar_evento("kick", "sub", username, nombre_evento, count)
    except Exception:
        pass
    try:
        alertas.evento("kick_sub", username, regalo=nombre_evento, cantidad=count, diamantes=count * 100, plataforma="kick")
    except Exception as e:
        log(f"Error alerta Kick sub: {e}")
    try:
        sorteo.registrar_actividad(username, tipo="regalo")
        stats.registrar_donacion(username, count * 100, nombre_evento)
        stats.registrar_donacion_dinero(username, count * 5.0, fuente="Kick Sub")
    except Exception:
        pass
    try:
        badge_sub = "⭐ ✦ KICK SUB ✦ ⭐" if count == 1 else f"🎁 ✦ REGALÓ {count} SUBS ✦ 🎁"
        texto_chat = "⭐ ¡Se ha suscrito a Kick!" if count == 1 else f"🎁 ¡Ha regalado {count} suscripciones a la comunidad de Kick!"
        publicar_chat(username, texto_chat, badge=badge_sub, plataforma="kick", uid=username)
    except Exception:
        pass


def on_kick_reward(username, reward_title, cost=0, user_input=""):
    log(f"🎁 Canje de Puntos de Kick: {username} canjeó «{reward_title}»" + (f" (Nota: {user_input})" if user_input else ""))
    try:
        import hk_spawner
        hk_spawner.registrar_evento("kick", "recompensa", username, reward_title, cost)
    except Exception:
        pass

    # 1. Si es un reto para la ruleta y para la lista de retos en pantalla
    if "reto" in reward_title.lower() or "reto" in user_input.lower():
        texto_reto = user_input.strip() if user_input.strip() else reward_title
        if _procesador_comentario:
            try:
                _procesador_comentario(username, f"!reto {texto_reto}", uid=username, plataforma="kick")
            except Exception as e:
                log(f"Error procesando reto por puntos: {e}")
        try:
            import loots
            loots.agregar(carta=reward_title, usuario=username, mensaje=user_input, rareza="epica")
            log(f"🟢 Kick: Reto por Puntos añadido a la lista de retos: {username} -> «{reward_title}»")
        except Exception as e:
            log(f"Error agregando canje Kick a retos: {e}")

    # 2. Registrar en sorteo
    try:
        sorteo.registrar_actividad(username, tipo="recompensa")
    except Exception:
        pass

    # 3. Disparar alerta en OBS (/alertas)
    try:
        texto_mostrar = f"{reward_title}" + (f": {user_input}" if user_input else "")
        alertas.evento("kick_recompensa", username, regalo=texto_mostrar, cantidad=cost, diamantes=cost, plataforma="kick")
    except Exception as e:
        log(f"Error alerta Kick recompensa: {e}")

    # 4. Mostrar en chat overlay (/chat)
    try:
        badge_vip = vips.estilo_chat(username)
        msg_chat = f"🎁 Canjeó con puntos: {reward_title}" + (f" ({user_input})" if user_input else "")
        publicar_chat(username, msg_chat, badge=badge_vip, plataforma="kick", uid=username)
    except Exception:
        pass


def on_kick_status(tipo, val):
    if tipo == "en_directo":
        kick_estado["conectado"] = True
        kick_estado["conectando"] = False
        kick_estado["mensaje"] = "Conectado al chat en vivo"
        publicar("viewers", get_viewers_data())
    elif tipo == "conectando":
        kick_estado["conectado"] = False
        kick_estado["conectando"] = True
        kick_estado["mensaje"] = "Conectando..."
    elif tipo == "desconectado":
        kick_estado["conectado"] = False
        kick_estado["conectando"] = False
        kick_estado["mensaje"] = "Desconectado"
        kick_estado["viewers"] = 0
        try:
            import stats
            stats.actualizar_kick_viewers(0)
        except Exception:
            pass
        publicar("viewers", get_viewers_data())
    elif tipo == "viewers":
        kick_estado["viewers"] = max(0, int(val or 0))
        try:
            import stats
            stats.actualizar_kick_viewers(kick_estado["viewers"])
        except Exception:
            pass
        publicar("viewers", get_viewers_data())
    elif tipo == "log":
        log(str(val))


def get_viewers_data():
    tt_viewers = 0
    try:
        import stats
        tt_viewers = int(stats.s.get("viewers", 0))
    except Exception:
        pass
    kk_viewers = int(kick_estado.get("viewers", 0))
    tw_viewers = int(twitch_estado.get("viewers", 0))
    yt_viewers = int(youtube_estado.get("viewers", 0))

    tt_activa = bool(plataformas_cfg.get("tiktok", False))
    kk_activa = bool(plataformas_cfg.get("kick", True))
    tw_activa = bool(plataformas_cfg.get("twitch", True))
    yt_activa = bool(plataformas_cfg.get("youtube", True))

    total = 0
    if tt_activa:
        total += tt_viewers
    if kk_activa:
        total += kk_viewers
    if tw_activa:
        total += tw_viewers
    if yt_activa:
        total += yt_viewers

    tt_conectado = False
    tt_usuario = ""
    try:
        import main
        tt_conectado = bool(main.tiktok_estado.get("en_directo"))
        tt_usuario = str(main.tiktok_estado.get("usuario", ""))
    except Exception:
        tt_conectado = bool(tiktok_info.get("en_directo"))
        tt_usuario = str(tiktok_info.get("usuario", ""))

    return {
        "tiktok": tt_viewers,
        "kick": kk_viewers,
        "twitch": tw_viewers,
        "youtube": yt_viewers,
        "total": total,
        "tiktok_conectado": tt_conectado,
        "kick_conectado": bool(kick_estado.get("conectado")),
        "twitch_conectado": bool(twitch_estado.get("conectado")),
        "youtube_conectado": bool(youtube_estado.get("conectado")),
        "tiktok_en_directo": tt_conectado,
        "kick_en_directo": bool(kick_estado.get("conectado")),
        "twitch_en_directo": bool(twitch_estado.get("en_directo")),
        "youtube_en_directo": bool(youtube_estado.get("en_directo")),
        "tiktok_usuario": tt_usuario,
        "kick_canal": kick_estado.get("canal", ""),
        "twitch_canal": twitch_estado.get("canal", ""),
        "youtube_canal": youtube_estado.get("canal", ""),
        "plataformas": {
            "tiktok": tt_activa,
            "kick": kk_activa,
            "twitch": tw_activa,
            "youtube": yt_activa,
            "ocultar_desconectadas": bool(plataformas_cfg.get("ocultar_desconectadas", False)),
            "mostrar_total": bool(plataformas_cfg.get("mostrar_total", True))
        }
    }


def on_twitch_message(nombre, texto, uid="", badge=None, color="", custom_reward_id="", tags=None):
    # 1. Si es un canje de Puntos de Canal de Twitch
    if custom_reward_id:
        log(f"🟣 Canje de Puntos Twitch: {nombre} -> «{texto}» (ID: {custom_reward_id})")
        try:
            import loots
            texto_limpio = texto.strip()
            carta = texto_limpio if texto_limpio else "Reto de Puntos Twitch"
            mensaje = "Canje de Puntos en Twitch"
            if len(carta) > 60:
                carta = "Reto de Puntos Twitch"
                mensaje = texto_limpio
            loots.agregar(carta=carta, usuario=nombre, mensaje=mensaje, rareza="epica")
            log(f"🟣 Twitch: Reto por Puntos de Canal añadido a lista de retos: {nombre} -> «{carta}»")
        except Exception as e:
            log(f"Error agregando canje Twitch a retos: {e}")

        try:
            import alertas
            alertas.evento("twitch_sub", nombre, regalo=f"Canje: {texto}", cantidad=1, diamantes=100, plataforma="twitch")
        except Exception:
            pass

        try:
            badge_vip = vips.estilo_chat(nombre) or "🎁 PUNTOS TWITCH"
            publicar_chat(nombre, f"🟣 Canje de Puntos: {texto}", badge=badge_vip, plataforma="twitch", uid=uid)
        except Exception:
            pass
    else:
        try:
            sorteo.registrar_actividad(nombre, uid=uid, tipo="chat")
            vips.visto(nombre, nickname=nombre)
        except Exception:
            pass

    if _procesador_comentario:
        try:
            _procesador_comentario(nombre, texto, uid=uid, plataforma="twitch")
        except Exception as e:
            log(f"Error procesando mensaje Twitch: {e}")
    else:
        try:
            badge_vip = vips.estilo_chat(nombre) or vips.estilo_chat(uid) or badge
            publicar_chat(nombre, texto, badge=badge_vip, plataforma="twitch", uid=uid)
        except Exception:
            pass


def on_twitch_status(tipo, val, sender=None):
    if sender and twitch_instancia and sender is not twitch_instancia:
        return
    if tipo == "en_directo":
        twitch_estado["conectado"] = True
        twitch_estado["conectando"] = False
        twitch_estado["en_directo"] = True
        twitch_estado["mensaje"] = "En directo"
        publicar("viewers", get_viewers_data())
    elif tipo == "offline":
        twitch_estado["conectado"] = True
        twitch_estado["conectando"] = False
        twitch_estado["en_directo"] = False
        twitch_estado["mensaje"] = "Canal offline"
        twitch_estado["viewers"] = 0
        try:
            import stats
            stats.actualizar_twitch_viewers(0)
        except Exception:
            pass
        publicar("viewers", get_viewers_data())
    elif tipo == "conectando":
        twitch_estado["conectado"] = False
        twitch_estado["conectando"] = True
        twitch_estado["mensaje"] = "Conectando..."
    elif tipo == "desconectado":
        twitch_estado["conectado"] = False
        twitch_estado["conectando"] = False
        twitch_estado["en_directo"] = False
        twitch_estado["mensaje"] = "Desconectado"
        twitch_estado["viewers"] = 0
        try:
            import stats
            stats.actualizar_twitch_viewers(0)
        except Exception:
            pass
        publicar("viewers", get_viewers_data())
    elif tipo == "viewers":
        twitch_estado["viewers"] = max(0, int(val or 0))
        try:
            import stats
            stats.actualizar_twitch_viewers(twitch_estado["viewers"])
        except Exception:
            pass
        publicar("viewers", get_viewers_data())
    elif tipo == "log":
        log(str(val))


def conectar_twitch(canal):
    global twitch_instancia
    canal = str(canal or "").strip().lower().lstrip("@")
    if "twitch.tv/" in canal:
        canal = canal.split("twitch.tv/")[-1].strip("/").split("?")[0]
    if not canal:
        return False, "Escribe el nombre de tu canal de Twitch."
    desconectar_twitch()
    twitch_estado["canal"] = canal
    try:
        ARCHIVO_TWITCH.write_text(json.dumps({"canal": canal}, indent=2), encoding="utf-8")
    except Exception:
        pass

    twitch_instancia = TwitchClient(
        username=canal,
        on_message=on_twitch_message,
        on_status=on_twitch_status,
        poll_interval=15
    )
    twitch_instancia.start()
    return True, f"Iniciando monitoreo y chat con @{canal}"


def desconectar_twitch():
    global twitch_instancia
    if twitch_instancia:
        try:
            twitch_instancia.stop()
        except Exception:
            pass
        twitch_instancia = None
    twitch_estado["conectado"] = False
    twitch_estado["conectando"] = False
    twitch_estado["en_directo"] = False
    twitch_estado["mensaje"] = "Desconectado"
    twitch_estado["viewers"] = 0
    try:
        import stats
        stats.actualizar_twitch_viewers(0)
    except Exception:
        pass
    publicar("viewers", get_viewers_data())


# ---------------- Conexión y Callbacks de YouTube ----------------
def on_youtube_message(nombre, texto, uid="", badge=None):
    try:
        sorteo.registrar_actividad(nombre, uid=uid, tipo="chat")
        vips.visto(nombre, nickname=nombre)
    except Exception:
        pass
    if _procesador_comentario:
        try:
            _procesador_comentario(nombre, texto, uid=uid, plataforma="youtube")
        except Exception as e:
            log(f"Error procesando mensaje YouTube: {e}")
    else:
        try:
            badge_vip = vips.estilo_chat(nombre) or vips.estilo_chat(uid) or badge
            publicar_chat(nombre, texto, badge=badge_vip, plataforma="youtube", uid=uid)
        except Exception:
            pass


def on_youtube_superchat(nombre, amount, texto, uid=""):
    import stats, alertas, re
    log(f"🔴 Super Chat YouTube: {nombre} ({amount})" + (f" - {texto}" if texto else ""))
    nums = re.findall(r"[\d.,]+", amount)
    val = float(nums[0].replace(",", ".")) if nums else 5.0
    try:
        stats.registrar_donacion(nombre, int(val * 100), f"Super Chat {amount}")
        stats.registrar_donacion_dinero(nombre, val, fuente="YouTube SuperChat")
    except Exception:
        pass
    try:
        alertas.evento("regalo", nombre, regalo=f"Super Chat {amount}", cantidad=1, diamantes=int(val * 100), plataforma="youtube")
    except Exception as e:
        log(f"Error alerta YouTube SuperChat: {e}")
    try:
        sorteo.registrar_actividad(nombre, uid=uid, tipo="regalo")
    except Exception:
        pass
    try:
        msg_sc = f"🔴 ¡Super Chat de {amount}!: {texto}" if texto else f"🔴 ¡Super Chat de {amount}!"
        publicar_chat(nombre, msg_sc, badge="🎁 ✦ YOUTUBE SUPER CHAT ✦ 🎁", plataforma="youtube", uid=uid)
    except Exception:
        pass


def on_youtube_status(tipo, val, sender=None):
    if sender and youtube_instancia and sender is not youtube_instancia:
        return
    if tipo == "en_directo":
        youtube_estado["conectado"] = True
        youtube_estado["conectando"] = False
        youtube_estado["en_directo"] = True
        youtube_estado["mensaje"] = "En directo"
        publicar("viewers", get_viewers_data())
    elif tipo == "offline":
        youtube_estado["conectado"] = True
        youtube_estado["conectando"] = False
        youtube_estado["en_directo"] = False
        youtube_estado["mensaje"] = "Canal offline"
        youtube_estado["viewers"] = 0
        publicar("viewers", get_viewers_data())
    elif tipo == "conectando":
        youtube_estado["conectado"] = False
        youtube_estado["conectando"] = True
        youtube_estado["mensaje"] = "Conectando..."
    elif tipo == "desconectado":
        youtube_estado["conectado"] = False
        youtube_estado["conectando"] = False
        youtube_estado["en_directo"] = False
        youtube_estado["mensaje"] = "Desconectado"
        youtube_estado["viewers"] = 0
        publicar("viewers", get_viewers_data())
    elif tipo == "viewers":
        youtube_estado["viewers"] = max(0, int(val or 0))
        publicar("viewers", get_viewers_data())
    elif tipo == "log":
        log(str(val))


def conectar_youtube(canal):
    global youtube_instancia
    canal = str(canal or "").strip()
    if not canal:
        return False, "Escribe el canal (@usuario) o enlace del directo de YouTube."
    
    # Auto-completar @ si es un identificador sin @
    if not canal.startswith("@") and "channel/" not in canal and not canal.startswith("UC") and "youtube.com" not in canal and not re.fullmatch(r"[a-zA-Z0-9_-]{11}", canal):
        canal = f"@{canal}"

    desconectar_youtube()
    youtube_estado["canal"] = canal
    try:
        ARCHIVO_YOUTUBE.write_text(json.dumps({"canal": canal}, indent=2), encoding="utf-8")
    except Exception:
        pass

    youtube_instancia = YouTubeClient(
        target=canal,
        on_message=on_youtube_message,
        on_superchat=on_youtube_superchat,
        on_status=on_youtube_status
    )
    youtube_instancia.start()
    return True, f"Iniciando conexión con YouTube ({canal})"


def desconectar_youtube():
    global youtube_instancia
    if youtube_instancia:
        try:
            youtube_instancia.stop()
        except Exception:
            pass
        youtube_instancia = None
    youtube_estado["conectado"] = False
    youtube_estado["conectando"] = False
    youtube_estado["en_directo"] = False
    youtube_estado["mensaje"] = "Desconectado"
    youtube_estado["viewers"] = 0
    publicar("viewers", get_viewers_data())


_on_plataforma_cambio = None


def registrar_callback_plataformas(fn):
    global _on_plataforma_cambio
    _on_plataforma_cambio = fn


def guardar_plataformas_cfg(cfg):
    global plataformas_cfg
    if not isinstance(cfg, dict):
        return
    plataformas_cfg["tiktok"] = bool(cfg.get("tiktok", False))
    plataformas_cfg["kick"] = bool(cfg.get("kick", True))
    plataformas_cfg["twitch"] = bool(cfg.get("twitch", True))
    plataformas_cfg["youtube"] = bool(cfg.get("youtube", True))
    plataformas_cfg["ocultar_desconectadas"] = bool(cfg.get("ocultar_desconectadas", False))
    plataformas_cfg["mostrar_total"] = bool(cfg.get("mostrar_total", True))
    try:
        ARCHIVO_PLATAFORMAS.write_text(json.dumps(plataformas_cfg, indent=2), encoding="utf-8")
    except Exception:
        pass
    if _on_plataforma_cambio:
        try:
            _on_plataforma_cambio(plataformas_cfg)
        except Exception as e:
            print("[Plataformas] Callback error:", e)
    publicar("viewers", get_viewers_data())


def conectar_kick(canal):
    global kick_instancia
    canal = str(canal or "").strip().lower().lstrip("@")
    if not canal:
        return False, "Escribe el nombre de tu canal de Kick."
    desconectar_kick()
    kick_estado["canal"] = canal
    try:
        ARCHIVO_KICK.write_text(json.dumps({"canal": canal}, indent=2), encoding="utf-8")
    except Exception:
        pass

    kick_instancia = KickClient(
        username=canal,
        on_message=on_kick_message,
        on_follow=on_kick_follow,
        on_sub=on_kick_sub,
        on_reward=on_kick_reward,
        on_status=on_kick_status
    )
    kick_instancia.start()
    return True, f"Iniciando conexión con @{canal}"


def desconectar_kick():
    global kick_instancia
    if kick_instancia:
        try:
            kick_instancia.stop()
        except Exception:
            pass
        kick_instancia = None
    kick_estado["conectado"] = False
    kick_estado["conectando"] = False
    kick_estado["mensaje"] = "Desconectado"


# ---------------- Rutas Web de Kick ----------------
@bp.route("/api/kick/estado")
def api_kick_estado():
    return jsonify({
        **kick_estado,
        "registros": list(registro)[-20:]
    })


@bp.route("/api/kick/conectar", methods=["POST"])
def api_kick_conectar():
    data = request.get_json(silent=True) or {}
    canal = data.get("canal", "")
    ok, msg = conectar_kick(canal)
    return jsonify({"ok": ok, "mensaje": msg, "estado": kick_estado})


@bp.route("/api/kick/desconectar", methods=["POST"])
def api_kick_desconectar():
    desconectar_kick()
    return jsonify({"ok": True, "estado": kick_estado})


# ---------------- Rutas Web de Twitch ----------------
@bp.route("/api/twitch/estado")
def api_twitch_estado():
    return jsonify({
        **twitch_estado
    })


@bp.route("/api/twitch/conectar", methods=["POST"])
def api_twitch_conectar():
    data = request.get_json(silent=True) or {}
    canal = data.get("canal", "")
    ok, msg = conectar_twitch(canal)
    return jsonify({"ok": ok, "mensaje": msg, "estado": twitch_estado})


@bp.route("/api/twitch/desconectar", methods=["POST"])
def api_twitch_desconectar():
    desconectar_twitch()
    return jsonify({"ok": True, "estado": twitch_estado})


# ---------------- Rutas Web de YouTube ----------------
@bp.route("/api/youtube/estado")
def api_youtube_estado():
    return jsonify({
        **youtube_estado
    })


@bp.route("/api/youtube/conectar", methods=["POST"])
def api_youtube_conectar():
    data = request.get_json(silent=True) or {}
    canal = data.get("canal", "")
    ok, msg = conectar_youtube(canal)
    return jsonify({"ok": ok, "mensaje": msg, "estado": youtube_estado})


@bp.route("/api/youtube/desconectar", methods=["POST"])
def api_youtube_desconectar():
    desconectar_youtube()
    return jsonify({"ok": True, "estado": youtube_estado})


# ---------------- Configuración de Plataformas Activas ----------------
@bp.route("/api/plataformas", methods=["GET", "POST"])
def api_plataformas():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        guardar_plataformas_cfg(data)
        return jsonify({"ok": True, "plataformas": plataformas_cfg, "viewers": get_viewers_data()})
    return jsonify({"ok": True, "plataformas": plataformas_cfg, "viewers": get_viewers_data()})


@bp.route("/api/viewers")
def api_viewers():
    return jsonify(get_viewers_data())


@bp.route("/viewers")
def overlay_viewers():
    return render_template("viewers.html")
