"""Bot de música híbrido: pedidos del chat (!play) + control del streamer.
Cola con aprobación -> descarga bajo demanda (yt-dlp en subproceso, sin ffmpeg)
-> reproducción en el navegador (<audio>), sin decodificar nada en Python."""
import json, os, random, subprocess, sys, time
from pathlib import Path
from queue import Queue
from threading import RLock, Thread
from flask import Blueprint, jsonify, request, render_template, send_from_directory
import live
import seguro

FROZEN = getattr(sys, "frozen", False)  # ejecutable creado con PyInstaller
BASE = Path(sys.executable).parent if FROZEN else Path(__file__).parent
YT = [str(BASE / "yt-dlp.exe")] if FROZEN else [sys.executable, "-m", "yt_dlp"]
CARPETA = BASE / "musica"
CARPETA.mkdir(exist_ok=True)
MAX_SEG, MAX_MB, MAX_POR_USUARIO, ESPERA_USUARIO = 600, 500, 2, 20
CF = 0x08004000 if os.name == "nt" else 0  # sin ventana + prioridad baja

bp = Blueprint("music", __name__)
ARCHIVO_CFG, ARCHIVO_BIBLIO = BASE / "musica_cfg.json", CARPETA / "biblioteca.json"
cfg_m = {"radio": False, "aprobar": False}  # reproducción automática
try:
    cfg_m.update({k: bool(v) for k, v in json.loads(ARCHIVO_CFG.read_text(encoding="utf-8")).items() if k in cfg_m})
except (OSError, ValueError):
    pass
try:
    _biblio = json.loads(ARCHIVO_BIBLIO.read_text(encoding="utf-8"))  # id -> {t: título, d: duración}
except (OSError, ValueError):
    _biblio = {}
_ultimo_radio = ""
_ultimo_avance = 0.0
lock = RLock()
cola, descargas = [], Queue()
actual, _id, _worker, _ult = None, 0, None, {}
pausado, volumen, pos_base, pos_ts = False, 0.5, 0.0, 0.0  # estado único para todos los reproductores
votos_skip = set()
SKIP_VOTOS_META = 3


def _yt(*args, timeout=60):
    return subprocess.run([*YT, "--no-warnings", *args],
                          capture_output=True, text=True, timeout=timeout, creationflags=CF)


def buscar(q, n=5):
    r = _yt(f"ytsearch{n}:{q}", "--flat-playlist", "--print",
            "%(id)s\t%(title)s\t%(duration)s", timeout=30)
    res = []
    for l in r.stdout.splitlines():
        p = l.split("\t")
        if len(p) == 3 and len(p[0]) == 11:
            try:
                d = int(float(p[2]))
            except ValueError:
                d = 0
            res.append({"vid": p[0], "titulo": p[1][:90], "dur": d})
    return res


def agregar(vid, titulo, dur, por):
    global _id
    with lock:
        _id += 1
        it = {"id": _id, "vid": vid, "titulo": titulo, "dur": dur, "por": por,
              "estado": "pendiente", "archivo": None}
        cola.append(it)
    live.log(f"Música: {por} pidió {titulo}")
    live.publicar("musica", {})
    return it


def aprobar(i):
    global _worker
    if seguro.ahorro():
        live.log("Música: modo ahorro activo, no se descargan canciones nuevas")
        return False
    with lock:
        it = next((x for x in cola if x["id"] == i and x["estado"] == "pendiente"), None)
        if it:
            it["estado"] = "descargando"
    if it:
        descargas.put(it)
        if not _worker or not _worker.is_alive():
            _worker = Thread(target=_trabajar, daemon=True)
            _worker.start()
        live.publicar("musica", {})
    return bool(it)


def _trabajar():
    while True:
        it = descargas.get()
        ruta = ""
        try:
            en_cache = [p for p in CARPETA.glob(it["vid"] + ".*") if p.suffix not in (".part", ".ytdl")]
            if en_cache:
                ruta = str(en_cache[0])
            else:
                r = _yt(f"https://www.youtube.com/watch?v={it['vid']}", "--no-playlist",
                        "-f", "bestaudio[acodec=opus]/bestaudio[ext=m4a]/bestaudio",
                        "--max-filesize", "40M", "--match-filter", f"duration<={MAX_SEG}",
                        "-o", str(CARPETA / "%(id)s.%(ext)s"),
                        "--print", "after_move:filename", timeout=300)
                ruta = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
        except Exception:
            ruta = ""
        ok = bool(ruta) and os.path.exists(ruta)
        with lock:
            if it in cola:
                if ok:
                    it["estado"], it["archivo"] = "lista", os.path.basename(ruta)
                else:
                    cola.remove(it)
        if ok:
            _biblio[it["vid"]] = {"t": it["titulo"], "d": it["dur"]}
            _guardar_biblio()
            _limpiar()
            _avanzar()
        live.publicar("musica", {})


_oyentes = {}  # id de página -> (tipo, última señal)


def _oyentes_activos():
    ahora, r = time.time(), {"obs": 0, "panel": 0}
    for k, (t, ts) in list(_oyentes.items()):
        if ahora - ts > 15:
            _oyentes.pop(k, None)
        else:
            r[t] += 1
    return r


def _pos():
    """Segundo por el que va la canción (para que un reproductor recargado la retome)."""
    if not actual:
        return 0.0
    return pos_base if pausado else pos_base + (time.time() - pos_ts)


def _avanzar(forzar=False, id_actual=None):
    global actual, pos_base, pos_ts, pausado, _ultimo_avance
    ahora = time.time()
    with lock:
        if actual and not forzar:
            return
        if forzar:
            if id_actual is not None and (not actual or actual["id"] != id_actual):
                return  # otro reproductor ya avanzó: evita saltar dos canciones
            if ahora - _ultimo_avance < 1.0:
                return  # protección de salto rápido / bucle
            _ultimo_avance = ahora
        if actual in cola:
            cola.remove(actual)
        votos_skip.clear()
        actual = next((x for x in cola if x["estado"] == "lista"), None)
        if actual:
            actual["estado"] = "reproduciendo"
            pos_base, pos_ts = 0.0, time.time()
        else:
            pausado = False  # cola vacía: la próxima canción suena normal


def _limpiar():
    usados = {x["archivo"] for x in cola if x["archivo"]}
    arch = sorted((p for p in CARPETA.iterdir() if p.is_file()), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in arch)
    for p in arch:
        if total <= MAX_MB * 1048576:
            break
        if p.name not in usados:
            total -= p.stat().st_size
            p.unlink(missing_ok=True)


def comando(nombre, texto):
    partes = texto.split(None, 1)
    if seguro.ahorro() or partes[0].lower() not in ("!play", "!cancion") or len(partes) < 2:
        return
    ahora = time.time()
    with lock:
        if ahora - _ult.get(nombre, 0) < ESPERA_USUARIO:
            return
        if sum(1 for x in cola if x["por"] == nombre) >= MAX_POR_USUARIO:
            return
        _ult[nombre] = ahora
    Thread(target=_pedir_chat, args=(nombre, partes[1][:80]), daemon=True).start()


def _pedir_chat(nombre, q):
    try:
        r = buscar(q, 1)
    except Exception:
        return
    if r and r[0]["dur"] <= MAX_SEG:
        it = agregar(r[0]["vid"], r[0]["titulo"], r[0]["dur"], nombre)  # queda "pendiente"
        if cfg_m["aprobar"]:
            aprobar(it["id"])  # aprobación automática de los pedidos del chat


def es_streamer_nombre(nombre):
    if not nombre:
        return False
    nom = str(nombre).lower().strip().lstrip("@")
    if nom in ("streamer", "streamer_user"):
        return True
    try:
        import live
        if nom == str(live.kick_estado.get("canal", "")).lower().strip().lstrip("@"):
            return True
        if nom == str(live.tiktok_info.get("usuario", "")).lower().strip().lstrip("@"):
            return True
        import main
        if nom == str(getattr(main, "tiktok_estado", {}).get("usuario", "")).lower().strip().lstrip("@"):
            return True
    except Exception:
        pass
    try:
        import voz
        if nom == str(voz.cfg.get("streamer_user", "")).lower().strip().lstrip("@"):
            return True
    except Exception:
        pass
    return False


def comando_skip(nombre, plataforma="kick"):
    global votos_skip
    with lock:
        if not actual:
            live.log(f"Música: {nombre} intentó saltar pero no hay canción sonando.")
            live.publicar_chat("🎵 MÚSICA", f"@{nombre} No hay ninguna canción reproduciéndose actualmente.", plataforma=plataforma)
            return

        titulo = str(actual.get("titulo", "canción actual"))
        dueno = str(actual.get("por", "")).lower().strip().lstrip("@")
        nom_limpio = str(nombre).lower().strip().lstrip("@")
        es_streamer = es_streamer_nombre(nombre)
        es_dueno = (nom_limpio == dueno and dueno != "radio")

        # Si pide el streamer o quien pidió la canción -> skip directo e instantáneo
        if es_streamer or es_dueno:
            votos_skip.clear()
            _avanzar(True)
            motivo = "el streamer" if es_streamer else "quien la pidió"
            live.log(f"Música: {nombre} ({motivo}) saltó la canción '{titulo}' inmediatamente.")
            _radio(forzar_oyente=True)
            live.publicar_chat("🎵 MÚSICA", f"⏭️ @{nombre} saltó la canción: {titulo}", plataforma=plataforma)
            live.publicar("musica", {})
            return

        # Voto de la comunidad
        if nom_limpio in votos_skip:
            live.publicar_chat("🎵 MÚSICA", f"@{nombre} Ya habías votado para saltar esta canción ({len(votos_skip)}/{SKIP_VOTOS_META}) ⏳", plataforma=plataforma)
            return

        votos_skip.add(nom_limpio)
        total = len(votos_skip)
        live.log(f"Música: {nombre} votó para saltar ({total}/{SKIP_VOTOS_META})")
        if total >= SKIP_VOTOS_META:
            votos_skip.clear()
            _avanzar(True)
            live.log(f"Música: Canción saltada por votos del chat ({total} votos).")
            _radio(forzar_oyente=True)
            live.publicar_chat("🎵 MÚSICA", f"⏭️ Canción saltada por votos del chat ({total}/{SKIP_VOTOS_META}): {titulo}", plataforma=plataforma)
        else:
            faltan = SKIP_VOTOS_META - total
            live.publicar_chat("🎵 MÚSICA", f"⏭️ @{nombre} votó para saltar ({total}/{SKIP_VOTOS_META}). Faltan {faltan} voto{'s' if faltan > 1 else ''} (!skip)", plataforma=plataforma)
        live.publicar("musica", {})


def mostrar_cola(nombre=""):
    with lock:
        proximas = [{"titulo": x["titulo"], "por": x["por"], "dur": x["dur"]}
                    for x in cola if x["estado"] in ("lista", "pendiente", "descargando") and x != actual][:4]
    live.log(f"Música: Solicitud de mostrar cola ({nombre or 'panel'})")
    live.publicar("musica_cola", {"proximas": proximas, "por": nombre})


@bp.route("/api/musica/mostrar_cola", methods=["POST"])
def api_mostrar_cola():
    mostrar_cola("streamer")
    return jsonify({"ok": True})


@bp.route("/musica")
def pagina():
    return render_template("musica.html")


@bp.route("/musica/archivo/<nombre>")
def archivo(nombre):
    return send_from_directory(CARPETA, nombre)


@bp.route("/api/musica/cola")
def api_cola():
    with lock:
        return jsonify({"cola": list(cola), "pausado": pausado, "volumen": volumen, "pos": round(_pos(), 1),
                        "oyentes": _oyentes_activos(), "auto": cfg_m,
                        "skip": {"votos": len(votos_skip), "meta": SKIP_VOTOS_META}})


@bp.route("/api/musica/buscar")
def api_buscar():
    q = request.args.get("q", "").strip()[:80]
    if seguro.ahorro():
        return jsonify({"error": "Modo ahorro activo: las búsquedas están en pausa."}), 503
    if not q:
        return jsonify([])
    try:
        return jsonify(buscar(q))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/musica/pedir", methods=["POST"])
def api_pedir():
    d = request.get_json(silent=True) or {}
    vid = str(d.get("vid", ""))
    if len(vid) != 11 or not vid.replace("-", "").replace("_", "").isalnum():
        return jsonify({"error": "id inválido"}), 400
    it = agregar(vid, str(d.get("titulo", ""))[:90], int(d.get("dur") or 0), "streamer")
    aprobar(it["id"])  # lo eligió el streamer: ya está revisado
    return jsonify({"ok": True})


@bp.route("/api/musica/<int:i>/aprobar", methods=["POST"])
def api_aprobar(i):
    return jsonify({"ok": aprobar(i)})


@bp.route("/api/musica/<int:i>", methods=["DELETE"])
def api_quitar(i):
    with lock:
        it = next((x for x in cola if x["id"] == i), None)
        if it:
            cola.remove(it)
        era_actual = it is not None and it is actual
    if era_actual:
        _avanzar(True)
    live.publicar("musica", {})
    return jsonify({"ok": True})


@bp.route("/api/musica/siguiente", methods=["POST"])
def api_siguiente():
    i = (request.get_json(silent=True) or {}).get("id")
    _avanzar(True, i if isinstance(i, int) else None)
    _radio()
    live.publicar("musica", {})
    return jsonify({"ok": True})


@bp.route("/musica/player")
def player():
    return render_template("musica_player.html")


@bp.route("/api/musica/pausa", methods=["POST"])
def api_pausa():
    global pausado, pos_base, pos_ts
    d = request.get_json(silent=True) or {}
    with lock:
        nuevo = bool(d["pausado"]) if "pausado" in d else not pausado
        if nuevo != pausado:
            if nuevo:
                pos_base = _pos()
            else:
                pos_ts = time.time()
            pausado = nuevo
    live.publicar("musica", {})
    return jsonify({"ok": True, "pausado": pausado})


@bp.route("/api/musica/volumen", methods=["POST"])
def api_volumen():
    global volumen
    try:
        volumen = max(0.0, min(1.0, float((request.get_json(silent=True) or {}).get("v", volumen))))
    except (TypeError, ValueError):
        pass
    live.publicar("musica", {})
    return jsonify({"ok": True})


@bp.route("/api/musica/latido", methods=["POST"])
def api_latido():
    d = request.get_json(silent=True) or {}
    c, t = str(d.get("c", ""))[:20], d.get("t")
    if c and t in ("obs", "panel"):
        _oyentes[c] = (t, time.time())
        _radio()
    return jsonify({"ok": True})


def _guardar_biblio():
    try:
        ARCHIVO_BIBLIO.write_text(json.dumps(_biblio, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def pausar(nuevo):
    global pausado, pos_base, pos_ts
    with lock:
        if nuevo != pausado:
            if nuevo:
                pos_base = _pos()
            else:
                pos_ts = time.time()
            pausado = nuevo
    live.publicar("musica", {})


def _radio(forzar_oyente=False):
    """Modo radio: si no hay nada en cola, suena una canción guardada al azar (solo con un reproductor conectado)."""
    global _id, _ultimo_radio
    if not cfg_m["radio"] or seguro.nivel() == "emergencia":
        return
    o = _oyentes_activos()
    if not forzar_oyente and not (o["obs"] or o["panel"]):
        return
    with lock:
        if actual or any(x["estado"] in ("lista", "descargando") for x in cola):
            return
        try:
            archivos = [p for p in CARPETA.iterdir() if p.is_file() and p.suffix.lower() in (".webm", ".m4a", ".mp4", ".opus", ".mp3", ".ogg")]
        except OSError:
            return
        if not archivos:
            return
        p = random.choice([x for x in archivos if x.stem != _ultimo_radio] or archivos)
        _ultimo_radio = p.stem
        info = _biblio.get(p.stem, {})
        _id += 1
        cola.append({"id": _id, "vid": p.stem, "titulo": info.get("t") or "Canción guardada", "dur": int(info.get("d") or 0),
                     "por": "radio", "estado": "lista", "archivo": p.name})
    _avanzar()
    live.publicar("musica", {})


@bp.route("/api/musica/auto", methods=["POST"])
def api_auto():
    d = request.get_json(silent=True) or {}
    for k in cfg_m:
        if k in d:
            cfg_m[k] = bool(d[k])
    try:
        ARCHIVO_CFG.write_text(json.dumps(cfg_m), encoding="utf-8")
    except OSError:
        pass
    _radio()
    live.publicar("musica", {})
    return jsonify({"ok": True, "auto": cfg_m})
