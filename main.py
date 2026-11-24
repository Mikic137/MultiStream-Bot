
from flask import Flask, render_template, jsonify, request
from pathlib import Path
from threading import Lock, Thread
import json
import random
import re
import sys
import unicodedata
import time
import asyncio
from urllib.parse import urlsplit
import live
import music
import voz
import stats
import promo
import vips
import loots
import alertas
import obs
import seguro
import sorteo
import tops
import hk_spawner
import contador

VERSION = "1.18.2"
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 61 * 1024 * 1024
app.register_blueprint(live.bp)
app.register_blueprint(music.bp)
app.register_blueprint(voz.bp)
app.register_blueprint(stats.bp)
app.register_blueprint(promo.bp)
app.register_blueprint(vips.bp)
app.register_blueprint(loots.bp)
app.register_blueprint(alertas.bp)
app.register_blueprint(obs.bp)
app.register_blueprint(seguro.bp)
app.register_blueprint(sorteo.bp)
app.register_blueprint(tops.bp)
app.register_blueprint(hk_spawner.bp)
app.register_blueprint(contador.bp)


@app.before_request
def solo_panel_local():
    """Rechaza DNS rebinding y peticiones de otras webs al panel local."""
    raw_host = request.host or ""
    host = (raw_host.split("]:", 1)[0][1:] if raw_host.startswith("[") else raw_host.split(":", 1)[0]).lower()
    if host not in ("127.0.0.1", "localhost", "::1"):
        return jsonify({"error": "Este panel solo acepta conexiones locales."}), 403
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origen = request.headers.get("Origin")
        if origen:
            try:
                parsed = urlsplit(origen)
                if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1") or parsed.port != 5000:
                    return jsonify({"error": "Origen no permitido."}), 403
            except ValueError:
                return jsonify({"error": "Origen no permitido."}), 403
        if request.headers.get("Sec-Fetch-Site") == "cross-site":
            return jsonify({"error": "Origen no permitido."}), 403

# ==============================
# RETOS DE LA RULETA
# ==============================

retos = [
    "Gana sin usar Rising Rush",
    "Utiliza solo personajes Saiyan",
    "Gana usando un equipo poco habitual",
    "No uses personajes Ultra",
    "Gana una partida sin perder un personaje"
]

reto_actual = None
indice_ganador = None
spin_id = 0
lock = Lock()

BASE = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
ARCHIVO_RETOS = BASE / "retos.json"
ARCHIVO_BUDDIES = BASE / "buddies.json"
_BUDDY_DEF = {"activo": False, "personaje": "guerrero", "tam": 144, "pos": "derecha", "movimiento": "quieto"}
_BUDDY_PERSONAJES = ("guerrero", "guardian", "aleatorio")
ARCHIVO_META = BASE / "meta.json"
_META_DEF = {"activa": False, "titulo": "PC Gamer", "objetivo": 2000.0, "ahorrado": 0.0, "moneda": "USD"}
_MONEDAS_META = ("USD", "EUR", "MXN", "COP", "PEN")
_META_CFG = dict(_META_DEF)
try:
    _d = json.loads(ARCHIVO_META.read_text(encoding="utf-8"))
    if isinstance(_d, dict):
        _META_CFG.update(_d)
except (OSError, ValueError):
    pass
_BUDDY_CFG = dict(_BUDDY_DEF)
try:
    _d = json.loads(ARCHIVO_BUDDIES.read_text(encoding="utf-8"))
    if isinstance(_d, dict):
        _BUDDY_CFG.update(_d)
except (OSError, ValueError):
    pass


def _limpiar_buddies(d):
    if not isinstance(d, dict):
        d = {}
    try:
        tam = max(72, min(280, int(d.get("tam", _BUDDY_DEF["tam"]))))
    except (TypeError, ValueError):
        tam = _BUDDY_DEF["tam"]
    return {"activo": bool(d.get("activo", False)),
            "personaje": d.get("personaje") if d.get("personaje") in _BUDDY_PERSONAJES else "guerrero",
            "tam": tam, "pos": d.get("pos") if d.get("pos") in ("izquierda", "derecha") else "derecha",
            "movimiento": d.get("movimiento") if d.get("movimiento") in ("quieto", "flotar") else "quieto"}


_BUDDY_CFG = _limpiar_buddies(_BUDDY_CFG)


def _limpiar_meta(d):
    import math
    if not isinstance(d, dict):
        d = {}
    def dinero(k, defecto):
        try:
            n = float(d.get(k, defecto))
            return round(max(0.01 if k == "objetivo" else 0, min(1000000000, n)), 2) if math.isfinite(n) else defecto
        except (TypeError, ValueError):
            return defecto
    return {"activa": bool(d.get("activa", False)),
            "titulo": str(d.get("titulo", _META_DEF["titulo"])).strip()[:50] or _META_DEF["titulo"],
            "objetivo": dinero("objetivo", _META_DEF["objetivo"]),
            "ahorrado": dinero("ahorrado", _META_DEF["ahorrado"]),
            "moneda": d.get("moneda") if d.get("moneda") in _MONEDAS_META else "USD"}


_META_CFG = _limpiar_meta(_META_CFG)


def _guardar_buddies():
    try:
        ARCHIVO_BUDDIES.write_text(json.dumps(_BUDDY_CFG, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def _guardar_meta():
    try:
        ARCHIVO_META.write_text(json.dumps(_META_CFG, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def guardar_retos():
    """Guarda la lista en disco para que sobreviva al cerrar el programa."""
    try:
        ARCHIVO_RETOS.write_text(json.dumps(retos, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


try:  # si ya hay una lista guardada, reemplaza a la de ejemplo
    _guardados = json.loads(ARCHIVO_RETOS.read_text(encoding="utf-8"))
    if isinstance(_guardados, list):
        retos[:] = [str(r).strip()[:100] for r in _guardados if str(r).strip()]
except (OSError, ValueError):
    pass

# ==============================
# ESTADO DE TIKTOK LIVE
# ==============================

TIKTOK_USUARIO = ""

tiktok_estado = {
    "usuario": TIKTOK_USUARIO,
    "estado": "desconectado",
    "en_directo": False,
    "ultimo_error": None
}

tiktok_lock = Lock()
_tiktok_hilo = None
_tiktok_detener = False
_tiktok_loop = None
_tiktok_cliente = None


def actualizar_estado(estado, en_directo, error=None):
    with tiktok_lock:
        cambio = tiktok_estado["estado"] != estado or tiktok_estado["en_directo"] != en_directo
        tiktok_estado["estado"] = estado
        tiktok_estado["en_directo"] = en_directo
        tiktok_estado["ultimo_error"] = error
    if cambio:
        live.log(f"TikTok: {estado}" + (f" ({error})" if error else ""))


MAX_RETOS = 25          # tope de retos en la ruleta (más segmentos = ilegible)
ESPERA_RETO = 30        # segundos entre retos del mismo espectador
_ult_reto = {}


INVISIBLES = re.compile("[\u200b-\u200f\u2060\ufeff]")
# "!reto juega ...", "! reto: juega ...", "!retos ..." y también "reto juega ..." sin "!"
RETO_RE = re.compile(r"^\s*(?:!\s*retos?\b|retos?\s+(?=juega\b))[\s:,\-–—]*(.*)$", re.I | re.S)


def comando_reto(nombre, reto):
    """Agrega un reto a la ruleta. Devuelve None si se agregó, o el motivo del rechazo."""
    reto = " ".join(reto.split())
    if not reto:
        return "vacío"
    if voz.cfg["reto_juega"] and not reto.lower().startswith("juega"):
        return "debe empezar con 'juega'"
    if len(reto) > 100:
        return "más de 100 caracteres"
    reto = reto[0].upper() + reto[1:]
    ahora = time.time()
    with lock:
        if ahora - _ult_reto.get(nombre, 0) < ESPERA_RETO:
            return f"espera {ESPERA_RETO} s entre retos"
        if len(retos) >= MAX_RETOS:
            return f"ruleta llena ({MAX_RETOS})"
        if reto.lower() in (r.lower() for r in retos):
            return "repetido"
        _ult_reto[nombre] = ahora
        retos.append(reto)
        guardar_retos()
    live.log(f"Reto de {nombre}: {reto}")
    return None


def procesar_comentario(nombre, texto, emotes=(), uid="", plataforma="tiktok"):
    try:
        sorteo.registrar_actividad(nombre, uid=uid, tipo="chat")
    except Exception:
        pass
    limpio = unicodedata.normalize("NFKC", INVISIBLES.sub("", texto)).strip()
    m = RETO_RE.match(limpio)
    if m:
        if voz.cfg["cmd_reto"]:
            texto_reto = m.group(1).strip()
            motivo = comando_reto(nombre, texto_reto)
            if motivo:
                live.log(f"Reto rechazado ({motivo}): {nombre}: {limpio[:60]}")
            else:
                try:
                    loots.agregar(carta=texto_reto, usuario=nombre, mensaje=f"Reto de {plataforma.capitalize()}", rareza="rara")
                except Exception:
                    pass
    elif limpio.startswith("!"):
        # 1. Comandos de Invocación de Hollow Knight (con cooldowns independientes)
        if hk_spawner.procesar_comando_chat(nombre, limpio, plataforma=plataforma):
            return
        partes = limpio.split(None, 1)
        cmd = partes[0].lower()
        if cmd in ("!sorteo", "!ruleta", "!participar", "!yo", "!entrar", "!cofre"):
            sorteo.comando_sorteo(nombre, uid=uid)
        elif cmd in ("!play", "!cancion") and voz.cfg["cmd_play"]:
            music.comando(nombre, limpio)
        elif cmd in ("!skip", "!saltar", "!next"):
            music.comando_skip(nombre, plataforma=plataforma)
        elif cmd in ("!cola", "!queue", "!canciones", "!proximas", "!siguientes"):
            music.mostrar_cola(nombre)
        elif cmd in ("!voces", "!infovoces", "!vocesia"):
            info_msg = voz.obtener_info_voces()
            live.log(f"Info Voces IA ({nombre}): {info_msg}")
            live.publicar_chat("🎙️ VOCES IA", info_msg, plataforma=plataforma)
        elif voz.buscar_voz_ia(cmd.lstrip("!")):
            v_ia = voz.buscar_voz_ia(cmd.lstrip("!"))
            nom_v = v_ia.get("nombre", "IA")
            emoj_v = v_ia.get("emoji", "🤖")
            if len(partes) > 1 and not cmd.startswith("!info"):
                puede, rol = voz.puede_usar_voz_ia(nombre, uid)
                if not puede:
                    live.log(f"Comando {cmd} ignorado ({nombre} no está en la lista de autorizados).")
                    live.publicar_chat(f"{emoj_v} {nom_v.upper()} IA", f"@{nombre} Las voces IA son exclusivas para los Top Donadores y el Streamer. Escribe !voces para más info.", plataforma=plataforma)
                    return
                restante = voz.tiempo_restante_voz_ia(nombre)
                if restante > 0:
                    live.log(f"Voz IA {nom_v}: @{nombre} debe esperar {restante}s de cooldown.")
                    live.publicar_chat(f"{emoj_v} {nom_v.upper()} IA", f"@{nombre} Por favor espera {restante}s antes de volver a usar una voz IA ⏳", plataforma=plataforma)
                    return
                voz.hablar_voz_ia(v_ia, nombre, partes[1])
            else:
                info_msg = voz.obtener_info_voces()
                live.log(f"Info Voz {nom_v} ({nombre}): {info_msg}")
                live.publicar_chat(f"{emoj_v} {nom_v.upper()} IA", info_msg, plataforma=plataforma)
        elif cmd == "!decir" and len(partes) > 1:
            voz.hablar(nombre, partes[1], comando=True, uid=uid)
        else:
            promo.comando(cmd[1:])
    else:
        if texto.strip() or emotes:
            badge_vip = vips.estilo_chat(uid) or vips.estilo_chat(nombre)
            live.publicar_chat(nombre, texto, emotes, badge_vip, plataforma=plataforma, uid=uid)
        if texto.strip():
            voz.hablar(nombre, texto, uid=uid)


# Conectar el procesador de mensajes para que Kick use el mismo motor
live.registrar_procesador(procesar_comentario)


def _bucle_tiktok(usuario):
    global _tiktok_detener, _tiktok_cliente, _tiktok_loop
    actualizar_estado("iniciando", False)
    while not _tiktok_detener:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            _tiktok_loop = loop

            async def _coro():
                global _tiktok_cliente
                cliente = live.TikTokLiveClient(unique_id=usuario)
                _tiktok_cliente = cliente

                @cliente.on(live.ConnectEvent)
                async def _conectado(e):
                    live.tiktok_info["en_directo"] = True
                    live.tiktok_info["usuario"] = str(usuario)
                    actualizar_estado("en_directo", True)
                    stats.reiniciar()
                    live.publicar("viewers", live.get_viewers_data())

                @cliente.on(live.CommentEvent)
                async def _comentario(e):
                    vips.visto(e)
                    procesar_comentario(live._nombre(e), str(e.comment)[:200], live._emotes(e), live._user_id(e), plataforma="tiktok")

                if getattr(live, "EmoteChatEvent", None) is not None:
                    @cliente.on(live.EmoteChatEvent)
                    async def _sticker(e):
                        r = live._stickers(e)
                        if r:
                            procesar_comentario(live._nombre(e), "", r, live._user_id(e), plataforma="tiktok")

                for ev, tipo in ((live.RoomUserSeqEvent, "viewers"), (live.LikeEvent, "likes"), (live.FollowEvent, "follow"),
                                 (live.ShareEvent, "share"), (live.GiftEvent, "regalo"), (live.JoinEvent, "join")):
                    live._escuchar(cliente, ev, tipo)

                @cliente.on(live.SocialEvent)
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
                        live._despachar_evento(e, "follow")
                    elif action == 1 or "share" in key:
                        live._despachar_evento(e, "share")

                try:
                    await cliente.connect()
                    actualizar_estado("desconectado", False)
                except live.UserOfflineError:
                    actualizar_estado("desconectado", False, "El canal no está transmitiendo en vivo actualmente")
                except Exception as err:
                    actualizar_estado("error", False, str(err))
                finally:
                    _tiktok_cliente = None

            loop.run_until_complete(_coro())
            try:
                loop.close()
            except Exception:
                pass
            _tiktok_loop = None
        except Exception as e:
            actualizar_estado("error", False, str(e))

        if _tiktok_detener:
            break
        for _ in range(25):
            if _tiktok_detener:
                break
            time.sleep(1)


def iniciar_monitor_tiktok(usuario=None):
    global _tiktok_hilo, _tiktok_detener, TIKTOK_USUARIO
    if usuario:
        TIKTOK_USUARIO = str(usuario).strip()
        if not TIKTOK_USUARIO.startswith("@"):
            TIKTOK_USUARIO = "@" + TIKTOK_USUARIO
        with tiktok_lock:
            tiktok_estado["usuario"] = TIKTOK_USUARIO
    _tiktok_detener = False
    if _tiktok_hilo and _tiktok_hilo.is_alive():
        return True, f"Monitor de TikTok ya está activo para {TIKTOK_USUARIO}"
    _tiktok_hilo = Thread(target=_bucle_tiktok, args=(TIKTOK_USUARIO,), daemon=True, name="TikTokWatcher")
    _tiktok_hilo.start()
    return True, f"Iniciando monitor para {TIKTOK_USUARIO}"


def detener_monitor_tiktok():
    global _tiktok_detener, _tiktok_cliente, _tiktok_loop
    _tiktok_detener = True
    if _tiktok_cliente and _tiktok_loop and _tiktok_loop.is_running():
        try:
            asyncio.run_coroutine_threadsafe(_tiktok_cliente.disconnect(), _tiktok_loop)
        except Exception:
            pass
    actualizar_estado("desconectado", False)
    live.tiktok_info["en_directo"] = False
    try:
        live.publicar("viewers", live.get_viewers_data())
    except Exception:
        pass
    return True, "TikTok desconectado"


def on_plataforma_cambio(cfg):
    if bool(cfg.get("tiktok", False)):
        iniciar_monitor_tiktok()
    else:
        detener_monitor_tiktok()


live.registrar_callback_plataformas(on_plataforma_cambio)


def iniciar_monitores_multistream():
    obs.arrancar()
    seguro.arrancar()
    loots.arrancar()  # escucha los canjes de Streamloots

    # 1. Kick (chat, viewers, subs, follows, puntos)
    try:
        if live.kick_estado.get("canal"):
            live.conectar_kick(live.kick_estado["canal"])
    except Exception as e:
        print("[Kick] Error al auto-conectar:", e)

    # 2. Twitch (chat en vivo, viewers, stream status)
    try:
        if getattr(live, "twitch_estado", {}).get("canal"):
            live.conectar_twitch(live.twitch_estado["canal"])
    except Exception as e:
        print("[Twitch] Error al auto-conectar:", e)

    # 3. YouTube (chat en vivo, Super Chats, viewers)
    try:
        if getattr(live, "youtube_estado", {}).get("canal"):
            live.conectar_youtube(live.youtube_estado["canal"])
    except Exception as e:
        print("[YouTube] Error al auto-conectar:", e)

    # 4. TikTok (opcional, solo si está activado)
    if live.plataformas_cfg.get("tiktok", False):
        try:
            iniciar_monitor_tiktok()
        except Exception as error:
            actualizar_estado("error", False, str(error))
            print("[TikTok] Error al auto-conectar:", error)

# Alias para compatibilidad con app.py
monitor_tiktok = iniciar_monitores_multistream


# ==============================
# PANEL Y OVERLAY
# ==============================

@app.route("/")
def inicio():
    return render_template("app.html")


@app.route("/panel")
def panel():
    return render_template("panel.html")


@app.route("/overlay")
def overlay():
    return render_template("overlay.html")


@app.route("/buddies")
def buddies_overlay():
    return render_template("buddies.html")


@app.route("/meta")
def meta_overlay():
    return render_template("meta.html")


@app.route("/api/buddies", methods=["GET", "POST"])
def api_buddies():
    if request.method == "POST":
        _BUDDY_CFG.update(_limpiar_buddies(request.get_json(silent=True) or {}))
        _guardar_buddies()
        live.publicar("buddy_cfg", dict(_BUDDY_CFG))
    return jsonify(dict(_BUDDY_CFG))


@app.route("/api/meta", methods=["GET", "POST"])
def api_meta():
    if request.method == "POST":
        _META_CFG.update(_limpiar_meta(request.get_json(silent=True) or {}))
        _guardar_meta()
        live.publicar("meta_cfg", dict(_META_CFG))
    return jsonify(dict(_META_CFG))


# ==============================
# API DE RETOS
# ==============================

@app.route("/api/retos", methods=["GET"])
def obtener_retos():
    with lock:
        return jsonify({
            "retos": retos.copy(),
            "reto_actual": reto_actual,
            "indice_ganador": indice_ganador,
            "spin_id": spin_id
        })


@app.route("/api/retos", methods=["POST"])
def agregar_reto():
    datos = request.get_json(silent=True) or {}
    texto = str(datos.get("reto", "")).strip()

    if not texto:
        return jsonify({"error": "Escribe un reto."}), 400

    if len(texto) > 100:
        return jsonify({"error": "Máximo 100 caracteres."}), 400

    with lock:
        retos.append(texto)
        guardar_retos()
        lista = retos.copy()

    return jsonify({"ok": True, "retos": lista})


@app.route("/api/girar", methods=["POST"])
def girar():
    global reto_actual, indice_ganador, spin_id

    with lock:
        if not retos:
            return jsonify({"error": "No hay retos disponibles."}), 400

        indice_ganador = random.randrange(len(retos))
        reto_actual = retos[indice_ganador]
        spin_id += 1

        return jsonify({
            "ok": True,
            "reto_actual": reto_actual,
            "indice_ganador": indice_ganador,
            "spin_id": spin_id
        })


@app.route("/api/retos/<int:indice>", methods=["DELETE"])
def eliminar_reto(indice):
    global reto_actual, indice_ganador

    with lock:
        if indice < 0 or indice >= len(retos):
            return jsonify({"error": "Reto no encontrado."}), 404

        eliminado = retos.pop(indice)
        guardar_retos()

        if not retos:
            reto_actual = None
            indice_ganador = None
        elif eliminado == reto_actual:
            reto_actual = None
            indice_ganador = None
        elif indice_ganador is not None and indice < indice_ganador:
            indice_ganador -= 1

        return jsonify({"ok": True, "retos": retos.copy()})


# ==============================
# API DE ESTADO DE TIKTOK
# ==============================

@app.route("/api/tiktok/estado", methods=["GET"])
def obtener_estado_tiktok():
    with tiktok_lock:
        res = tiktok_estado.copy()
    try:
        res["viewers"] = live.get_viewers_data().get("tiktok", 0)
    except Exception:
        res["viewers"] = 0
    return jsonify(res)


@app.route("/api/tiktok/conectar", methods=["POST"])
def api_tiktok_conectar():
    data = request.get_json(silent=True) or {}
    canal = data.get("canal") or data.get("usuario") or TIKTOK_USUARIO
    ok, msg = iniciar_monitor_tiktok(canal)
    with tiktok_lock:
        est = tiktok_estado.copy()
    return jsonify({"ok": ok, "mensaje": msg, "estado": est})


@app.route("/api/tiktok/desconectar", methods=["POST"])
def api_tiktok_desconectar():
    ok, msg = detener_monitor_tiktok()
    with tiktok_lock:
        est = tiktok_estado.copy()
    return jsonify({"ok": ok, "mensaje": msg, "estado": est})


# ==============================
# INICIAR APLICACIÓN
# ==============================


if __name__ == "__main__":
    print(f"\n=== DB LEGENDS MULTISTREAM BOT v{VERSION} ===")
    print("Kick + Twitch + YouTube Live")
    print("Control: http://127.0.0.1:5000/")
    print("Chat:    http://127.0.0.1:5000/chat")
    print("Viewers: http://127.0.0.1:5000/viewers")
    print("Voz:     http://127.0.0.1:5000/voz/player")
    print("VIPs:    http://127.0.0.1:5000/vip")
    print("Promo:   http://127.0.0.1:5000/promo")
    print("Retos:   http://127.0.0.1:5000/loots")
    print("Alertas: http://127.0.0.1:5000/alertas")
    print("Contador: http://127.0.0.1:5000/contador")
    print("================================\n")

    # Iniciar TikTok en un hilo independiente
    Thread(
        target=monitor_tiktok,
        daemon=True,
        name="TikTokMonitor"
    ).start()

    # Iniciar el servidor web
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=False,
        threaded=True
    )
