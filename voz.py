"""Lectura de comentarios en voz alta con la voz de Windows (System.Speech) y voces neurales (Edge TTS).
Usa un PowerShell persistente o generación asíncrona de audio."""
import asyncio, base64, json, os, re, subprocess, sys, time
from collections import deque
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Lock, Thread
from flask import Blueprint, jsonify, render_template, request, send_from_directory
import live
import seguro

BASE = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
ARCHIVO = BASE / "config_voz.json"
AUDIOS = BASE / "voz_audio"  # temporales (se borran al sonar)
CF = 0x08004000 if os.name == "nt" else 0  # sin ventana + prioridad baja
bp = Blueprint("voz", __name__)
lock = Lock()

VOCES_EDGE = [
    {"id": "edge:es-MX-DaliaNeural", "nombre": "🇲🇽 Dalia (México - Femenina, Juvenil)"},
    {"id": "edge:es-MX-JorgeNeural", "nombre": "🇲🇽 Jorge (México - Masculina, Streamer)"},
    {"id": "edge:es-ES-AlvaroNeural", "nombre": "🇪🇸 Álvaro (España - Masculina, Enérgica)"},
    {"id": "edge:es-ES-ElviraNeural", "nombre": "🇪🇸 Elvira (España - Femenina, Cálida)"},
    {"id": "edge:es-CO-GonzaloNeural", "nombre": "🇨🇴 Gonzalo (Colombia - Masculina, Amable)"},
    {"id": "edge:es-CO-SalomeNeural", "nombre": "🇨🇴 Salomé (Colombia - Femenina, Expresiva)"},
    {"id": "edge:es-AR-TomasNeural", "nombre": "🇦🇷 Tomás (Argentina - Masculina)"},
    {"id": "edge:es-AR-ElenaNeural", "nombre": "🇦🇷 Elena (Argentina - Femenina)"},
    {"id": "edge:es-CL-LorenzoNeural", "nombre": "🇨🇱 Lorenzo (Chile - Masculina)"},
    {"id": "edge:es-CL-CatalinaNeural", "nombre": "🇨🇱 Catalina (Chile - Femenina)"},
    {"id": "edge:es-PE-AlexNeural", "nombre": "🇵🇪 Alex (Perú - Masculina)"},
    {"id": "edge:es-PE-CamilaNeural", "nombre": "🇵🇪 Camila (Perú - Femenina)"},
    {"id": "edge:es-EC-LuisNeural", "nombre": "🇪🇨 Luis (Ecuador - Masculina)"},
    {"id": "edge:es-EC-AndreaNeural", "nombre": "🇪🇨 Andrea (Ecuador - Femenina)"},
    {"id": "edge:es-US-AlonsoNeural", "nombre": "🇺🇸 Alonso (EE.UU. - Latino)"},
    {"id": "edge:es-US-PalomaNeural", "nombre": "🇺🇸 Paloma (EE.UU. - Latina)"},
    {"id": "edge:en-US-AnaNeural", "nombre": "🎭 Ana (Anime / Infantil)"},
    {"id": "edge:en-US-GuyNeural", "nombre": "🇺🇸 Guy (Inglés - Masculina)"},
    {"id": "edge:en-US-JennyNeural", "nombre": "🇺🇸 Jenny (Inglés - Femenina)"},
]

VOCES_IA_DEF = [
    {
        "id": "bulma",
        "nombre": "Bulma",
        "comando": "b",
        "comandos_extra": ["bulma"],
        "model_id": "392155b6ab2a47c483b0f463f0b8e0d9",
        "emoji": "🌸",
        "color": "#00e5ff",
        "voz_respaldo": "es-MX-DaliaNeural",
        "pitch": "+14Hz",
        "rate": "+8%",
        "activo": True
    },
    {
        "id": "goku",
        "nombre": "Goku",
        "comando": "g",
        "comandos_extra": ["goku"],
        "model_id": "9f850ee9ada24b20a6866825eaefd3f8",
        "emoji": "🥋",
        "color": "#ff7700",
        "voz_respaldo": "es-MX-JorgeNeural",
        "pitch": "+2Hz",
        "rate": "0%",
        "activo": True
    }
]

DEF = {"activa": False, "modo": "todos", "voz": "edge:es-MX-DaliaNeural", "velocidad": 0, "volumen": 80,
       "max_chars": 120, "espera": 8, "max_cola": 5, "leer_nombre": True,
       "sin_links": True, "bloqueadas": "", "silenciados": [],
       "cmd_reto": True, "cmd_play": True, "motor": "obs", "voz_ventana": "", "reto_juega": True,
       # Voces IA de personajes para VIPs y Top Donadores (Fish Audio)
       "bulma_activa": True, "bulma_voz": "edge:es-MX-DaliaNeural", "bulma_pitch": "+14Hz",
       "bulma_rate": "+8%", "bulma_volumen": 100, "bulma_leer_nombre": False, "bulma_max_chars": 160,
       "bulma_motor": "fish_audio", "fish_audio_key": "",
       "fish_audio_model_id": "392155b6ab2a47c483b0f463f0b8e0d9",
       "fish_audio_model_name": "s2.1-pro-free",
       "bulma_modo_acceso": "vips_y_autorizados",
       "bulma_usuarios_permitidos": [],
       "bulma_cooldown": 120,
       "voces_ia": list(VOCES_IA_DEF)}
LIM = {"velocidad": (-10, 10), "volumen": (0, 100), "max_chars": (20, 300),
       "espera": (0, 120), "max_cola": (1, 20), "bulma_volumen": (0, 100),
       "bulma_max_chars": (20, 300), "bulma_cooldown": (5, 600)}
cfg = dict(DEF)
try:
    with open(ARCHIVO, encoding="utf-8") as f:
        cfg.update({k: v for k, v in json.load(f).items() if k in DEF and type(v) == type(DEF[k])})
except Exception:
    pass
cfg["activa"] = bool(cfg.get("activa", True))  # respeta la configuración guardada

cola, recientes, _ult = Queue(), deque(maxlen=15), {}
_proc, _reiniciar, _worker, _voces = None, False, None, None
_corte = False  # True si cerramos PowerShell a propósito (Saltar)
_latidos, _fin = {"ventana": 0.0, "obs": 0.0}, Event()
try:
    AUDIOS.mkdir(exist_ok=True)
    for _f in AUDIOS.glob("*.*"):
        if _f.suffix.lower() in (".wav", ".mp3"):
            _f.unlink()
except OSError:
    pass
URL = re.compile(r"https?://\S+|www\.\S+", re.I)

SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try {
  Add-Type -AssemblyName System.Speech
  $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
  if ($env:VOZ_NOMBRE) { try { $s.SelectVoice($env:VOZ_NOMBRE) } catch {} }
  $s.Rate = [int]$env:VOZ_VELOCIDAD
  $s.Volume = [int]$env:VOZ_VOLUMEN
  [Console]::Out.WriteLine("LISTA"); [Console]::Out.Flush()
} catch { [Console]::Out.WriteLine("ERR " + $_.Exception.Message); [Console]::Out.Flush(); exit 1 }
while (($l = [Console]::In.ReadLine()) -ne $null) {
  $p = $l.Split([char]9)
  try {
    $t = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($p[1]))
    if ($p[0]) { $s.SetOutputToWaveFile([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($p[0]))) }
    $s.Speak($t)
    [Console]::Out.WriteLine("OK")
  } catch { [Console]::Out.WriteLine("ERR " + $_.Exception.Message) }
  finally { if ($p[0]) { try { $s.SetOutputToDefaultAudioDevice() } catch {} } }
  [Console]::Out.Flush()
}
'''


def _guardar():
    try:
        ARCHIVO.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def ventana_abierta():
    """¿Está conectado el reproductor del motor elegido? (avisa cada 5 s)"""
    return time.time() - _latidos.get(cfg["motor"], 0) < 20


def es_vip_o_autorizado(nombre, uid=""):
    """Comprueba si el usuario es VIP registrado, donador autorizado o el streamer."""
    streamer_users = {
        "streamer",
        str(cfg.get("streamer_user", "")).lower().lstrip("@").strip(),
        str(live.kick_estado.get("canal", "")).lower().lstrip("@").strip(),
        str(live.twitch_estado.get("canal", "")).lower().lstrip("@").strip(),
        str(live.youtube_estado.get("canal", "")).lower().lstrip("@").strip(),
        str(live.tiktok_info.get("usuario", "")).lower().lstrip("@").strip()
    }
    streamer_users = {x for x in streamer_users if x}
    clean_nombre = str(nombre or "").lower().lstrip("@").strip()
    clean_uid = str(uid or "").lower().lstrip("@").strip()
    if (clean_nombre and clean_nombre in streamer_users) or (clean_uid and clean_uid in streamer_users):
        return True
    try:
        import vips
        if (clean_nombre and vips.es_vip(clean_nombre)) or (clean_uid and vips.es_vip(clean_uid)):
            return True
    except Exception:
        pass
    permitidos = {str(x).lower().lstrip("@").strip() for x in cfg.get("bulma_usuarios_permitidos", []) if str(x).strip()}
    if (clean_nombre and clean_nombre in permitidos) or (clean_uid and clean_uid in permitidos):
        return True
    return False


def hablar(nombre, texto, comando=False, uid=""):
    """Filtra y encola un comentario. comando=True si vino de !decir."""
    if seguro.ahorro() or not cfg["activa"]:
        return
    modo = str(cfg.get("modo", "todos")).lower()
    if modo == "comando" and not comando:
        return
    if modo in ("vips", "solo_vips"):
        if not es_vip_o_autorizado(nombre, uid):
            return
    if nombre in cfg["silenciados"]:
        return
    t = URL.sub("", texto) if cfg["sin_links"] else texto
    t = " ".join(t.split())[:cfg["max_chars"]]
    if not re.search(r"\w", t):
        return
    malas = [w.strip().lower() for w in cfg["bloqueadas"].split(",") if w.strip()]
    if any(w in t.lower() for w in malas):
        return
    ahora = time.time()
    with lock:
        if ahora - _ult.get(nombre, 0) < cfg["espera"] or cola.qsize() >= cfg["max_cola"]:
            return
        _ult[nombre] = ahora
    cola.put((nombre, t))
    _asegurar()


def puede_usar_bulma(nombre, uid=""):
    """Comprueba si el usuario tiene permiso para usar la voz IA de personajes (!b, !g, etc.)."""
    modo = cfg.get("bulma_modo_acceso", "vips_y_autorizados")
    streamer_users = {
        "streamer",
        str(cfg.get("streamer_user", "")).lower().lstrip("@"),
        str(live.kick_estado.get("canal", "")).lower().lstrip("@"),
        str(live.twitch_estado.get("canal", "")).lower().lstrip("@"),
        str(live.youtube_estado.get("canal", "")).lower().lstrip("@"),
        str(live.tiktok_info.get("usuario", "")).lower().lstrip("@")
    }
    limpio_nom = str(nombre or "").lower().lstrip("@").strip()
    limpio_uid = str(uid or "").lower().lstrip("@").strip()
    if limpio_nom in streamer_users or limpio_uid in streamer_users:
        return True, "streamer"

    if modo == "todos":
        return True, "todos"

    import vips
    es_vip = vips.es_vip(limpio_uid) or vips.es_vip(limpio_nom)

    # Si el modo incluye VIPs
    if modo in ("vips", "vips_y_autorizados") and es_vip:
        return True, "vip"

    permitidos = {str(x).lower().lstrip("@").strip() for x in cfg.get("bulma_usuarios_permitidos", []) if str(x).strip()}
    if limpio_nom in permitidos or limpio_uid in permitidos:
        return True, "autorizado"

    # Si el usuario es VIP y en permitidos se agregó su display name (ej: "NEXUS" o "KEVIIN")
    if es_vip:
        v_data = vips._indice.get(limpio_nom) or vips._indice.get(limpio_uid)
        if v_data:
            nombres_vip = {
                str(v_data.get("nombre", "")).lower().strip(),
                str(v_data.get("usuario", "")).lower().strip(),
                str(v_data.get("usuario_kick", "")).lower().strip()
            }
            if nombres_vip & permitidos:
                return True, "autorizado"

    return False, "no_autorizado"


def tiempo_restante_bulma(nombre):
    """Devuelve los segundos de cooldown que debe esperar el usuario antes de volver a usar !b (0 si ya puede)."""
    limpio = str(nombre or "").lower().lstrip("@").strip()
    streamer_users = {
        "streamer",
        str(cfg.get("streamer_user", "")).lower().lstrip("@"),
        str(live.kick_estado.get("canal", "")).lower().lstrip("@")
    }
    if limpio in streamer_users:
        return 0  # El streamer no tiene cooldown
    ahora = time.time()
    cooldown = int(cfg.get("bulma_cooldown", 120))
    ultimo = _ult.get(f"b_{limpio}", 0)
    pasado = ahora - ultimo
    if pasado < cooldown:
        return int(cooldown - pasado)
    return 0


def obtener_info_bulma():
    return obtener_info_voces()


def extraer_model_id(val):
    """Extrae el hash de 32 caracteres de un enlace o ID de Fish Audio."""
    val = str(val or "").strip()
    m = re.search(r"([a-f0-9]{32})", val.lower())
    return m.group(1) if m else val


def obtener_voces_ia():
    """Devuelve la lista de voces IA activas."""
    voces = cfg.get("voces_ia")
    if not isinstance(voces, list) or not voces:
        return list(VOCES_IA_DEF)
    return voces


def buscar_voz_ia(cmd_o_id):
    """Encuentra una voz IA por comando (ej: 'b', 'bulma', 'g', 'goku') o por ID."""
    limpio = str(cmd_o_id or "").lower().lstrip("!").strip()
    for v in obtener_voces_ia():
        if not v.get("activo", True):
            continue
        c_principal = str(v.get("comando", "")).lower().lstrip("!").strip()
        c_extras = [str(x).lower().lstrip("!").strip() for x in v.get("comandos_extra", [])]
        v_id = str(v.get("id", "")).lower().strip()
        if limpio == c_principal or limpio in c_extras or limpio == v_id:
            return v
    if limpio in ("b", "bulma"):
        return VOCES_IA_DEF[0]
    return None


def puede_usar_voz_ia(nombre, uid=""):
    return puede_usar_bulma(nombre, uid)


def tiempo_restante_voz_ia(nombre):
    return tiempo_restante_bulma(nombre)


def obtener_info_voces():
    cd = int(cfg.get("bulma_cooldown", 120))
    voces = [v for v in obtener_voces_ia() if v.get("activo", True)]
    ejemplos = ", ".join(f"!{v.get('comando')} ({v.get('nombre')} {v.get('emoji', '🤖')})" for v in voces)
    permitidos = [str(x).lstrip("@") for x in cfg.get("bulma_usuarios_permitidos", []) if str(x).strip()]
    nombres_txt = " / ".join(f"@{u}" for u in permitidos[:3]) if permitidos else "Top Donadores"
    return f"🎙️ Voces IA disponibles: {ejemplos}. Exclusivo para {nombres_txt} y el Streamer. Cooldown: {cd}s."


def hablar_voz_ia(voz_obj, nombre, texto, forzar=False):
    """Filtra y encola un mensaje con la voz IA del personaje seleccionado."""
    if seguro.ahorro() or not cfg.get("bulma_activa", True):
        return
    if nombre in cfg.get("silenciados", []):
        return
    t = URL.sub("", texto) if cfg.get("sin_links", True) else texto
    max_c = cfg.get("bulma_max_chars", 160)
    t = " ".join(t.split())[:max_c]
    if not re.search(r"\w", t):
        return
    malas = [w.strip().lower() for w in cfg.get("bloqueadas", "").split(",") if w.strip()]
    if any(w in t.lower() for w in malas):
        return
    limpio = str(nombre or "").lower().lstrip("@").strip()
    ahora = time.time()
    with lock:
        _ult[f"b_{limpio}"] = ahora
    frase = f"{nombre} dice: {t}" if cfg.get("bulma_leer_nombre", False) else t
    cola.put(("__VOZ_IA__", (voz_obj, nombre, t, frase)))
    _asegurar()
    live.log(f"{voz_obj.get('emoji', '🤖')} {voz_obj.get('nombre', 'IA')} ({nombre}): «{t}»")


def hablar_bulma(nombre, texto, forzar=False):
    """Filtra y encola un mensaje con la voz IA de Bulma (!b)."""
    v = buscar_voz_ia("bulma") or VOCES_IA_DEF[0]
    hablar_voz_ia(v, nombre, texto, forzar=forzar)


def _asegurar():
    global _worker
    if not _worker or not _worker.is_alive():
        _worker = Thread(target=_trabajar, daemon=True)
        _worker.start()


def _iniciar():
    env = dict(os.environ, VOZ_NOMBRE=cfg["voz"], VOZ_VELOCIDAD=str(cfg["velocidad"]),
               VOZ_VOLUMEN=str(100 if cfg["motor"] == "obs" else cfg["volumen"]))
    codigo = base64.b64encode(SCRIPT.encode("utf-16-le")).decode()
    p = subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", codigo],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         env=env, creationflags=CF)
    linea = p.stdout.readline().decode("utf-8", "replace").strip()
    if linea != "LISTA":
        try:
            p.wait(2)
        except subprocess.TimeoutExpired:
            p.kill()
        extra = p.stderr.read(300).decode("utf-8", "replace").strip()
        raise RuntimeError((linea or extra or "PowerShell no arrancó")[:200])
    return p


def _matar():
    global _proc
    p, _proc = _proc, None
    if p:
        try:
            p.kill()
        except Exception:
            pass


def _trabajar():
    global _proc, _reiniciar, _corte
    while True:
        try:
            nombre, texto = cola.get(timeout=60)
        except Empty:
            _matar()  # sin uso: libera la memoria de PowerShell
            return
        try:
            # 0. Voces IA de Personajes (!b, !g, etc.)
            if nombre in ("__BULMA__", "__VOZ_IA__"):
                if nombre == "__BULMA__":
                    vip_nombre, texto_limpio, frase = texto
                    voz_obj = buscar_voz_ia("bulma") or VOCES_IA_DEF[0]
                else:
                    voz_obj, vip_nombre, texto_limpio, frase = texto

                nom_pers = voz_obj.get("nombre", "Bulma")
                emoji_pers = voz_obj.get("emoji", "🌸")
                color_pers = voz_obj.get("color", "#00e5ff")
                model_id = str(voz_obj.get("model_id") or cfg.get("fish_audio_model_id", "392155b6ab2a47c483b0f463f0b8e0d9")).strip()

                recientes.appendleft({"n": f"{nom_pers} ({vip_nombre})", "t": texto_limpio})
                motor_bulma = cfg.get("bulma_motor", "fish_audio")
                fish_key = str(cfg.get("fish_audio_key", "")).strip()

                vol_val = max(0.0, min(1.0, cfg.get("bulma_volumen", 100) / 100.0))
                audio_file = AUDIOS / f"{voz_obj.get('id', 'ia')}_{int(time.time() * 1000)}.mp3"
                sintetizado = False
                # 1. Intentar primero con Fish Audio (Voz IA real del personaje) si hay API Key configurada
                if motor_bulma == "fish_audio" and fish_key and model_id:
                    try:
                        import urllib.request, json
                        payload = json.dumps({
                            "text": frase,
                            "reference_id": model_id,
                            "format": "mp3",
                            "latency": "normal"
                        }).encode("utf-8")
                        req = urllib.request.Request(
                            "https://api.fish.audio/v1/tts",
                            data=payload,
                            headers={
                                "Authorization": f"Bearer {fish_key}",
                                "Content-Type": "application/json",
                                "model": str(cfg.get("fish_audio_model_name", "s2.1-pro-free")).strip(),
                                "User-Agent": "DBLegendsBot/1.0"
                            }
                        )
                        with urllib.request.urlopen(req, timeout=18) as resp:
                            if resp.status == 200:
                                with open(audio_file, "wb") as f_out:
                                    f_out.write(resp.read())
                                sintetizado = audio_file.exists() and audio_file.stat().st_size > 500
                                if sintetizado:
                                    live.log(f"Fish Audio: Voz IA de {nom_pers} generada con éxito ({len(frase)} chars).")
                    except Exception as err:
                        live.log(f"Fish Audio error en {nom_pers} ({err}), usando voz de respaldo...")
                        audio_file.unlink(missing_ok=True)

                # 2. Si no hay API Key de Fish Audio o falló, usar respaldo Edge TTS
                if not sintetizado:
                    voz_id = voz_obj.get("voz_respaldo") or cfg.get("bulma_voz", "es-MX-DaliaNeural")
                    if str(voz_id).startswith("edge:"):
                        voz_id = voz_id.split(":", 1)[1]
                    pitch_str = str(voz_obj.get("pitch") or cfg.get("bulma_pitch", "0Hz"))
                    rate_str = str(voz_obj.get("rate") or cfg.get("bulma_rate", "0%"))
                    try:
                        import edge_tts
                        async def _gen_ia():
                            com = edge_tts.Communicate(frase, voz_id, pitch=pitch_str, rate=rate_str)
                            await com.save(str(audio_file))
                        asyncio.run(_gen_ia())
                    except Exception as ex:
                        live.log(f"Voz {nom_pers} respaldo error ({ex})")
                        audio_file.unlink(missing_ok=True)

                if audio_file.exists():
                    payload_pub = {
                        "u": "/voz/archivo/" + audio_file.name,
                        "v": vol_val,
                        "bulma": True,
                        "voz_ia": True,
                        "nombre": vip_nombre,
                        "texto": texto_limpio,
                        "personaje": nom_pers,
                        "emoji": emoji_pers,
                        "color": color_pers,
                        "tag": f"{emoji_pers} {nom_pers.upper()} (VOZ IA)"
                    }
                    if cfg["motor"] == "obs" and ventana_abierta():
                        _fin.clear()
                        live.publicar("voz_audio", payload_pub)
                        _fin.wait(60)
                        audio_file.unlink(missing_ok=True)
                        continue
                    elif cfg["motor"] in ("sistema", "obs"):
                        try:
                            payload_pub["v"] = 0
                            live.publicar("voz_audio", payload_pub)
                            subprocess.run([
                                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                                f"Add-Type -AssemblyName presentationCore; $m = New-Object System.Windows.Media.MediaPlayer; $m.Open([System.Uri]'{audio_file.resolve()}'); $m.Volume = {vol_val}; $m.Play(); Start-Sleep -Milliseconds 400; $dur = if ($m.NaturalDuration.HasTimeSpan) {{ $m.NaturalDuration.TimeSpan.TotalSeconds }} else {{ 4 }}; Start-Sleep -Seconds $dur; $m.Close()"
                            ], creationflags=CF, timeout=60)
                        except Exception:
                            pass
                        audio_file.unlink(missing_ok=True)
                        continue
                    elif cfg["motor"] == "ventana":
                        _fin.clear()
                        live.publicar("voz_audio", payload_pub)
                        _fin.wait(60)
                        audio_file.unlink(missing_ok=True)
                        continue
                continue

            frase = f"{nombre} dice: {texto}" if cfg["leer_nombre"] else texto
            recientes.appendleft({"n": nombre, "t": texto})
            
            # 1. Si usa voces neurales de Edge TTS (independiente de si es OBS o Sistema)
            if str(cfg.get("voz", "")).startswith("edge:"):
                voz_id = cfg["voz"].split(":", 1)[1]
                rate_val = int(cfg["velocidad"]) * 5
                rate_str = f"{rate_val:+d}%"
                audio_file = AUDIOS / f"{int(time.time() * 1000)}.mp3"
                
                try:
                    import edge_tts
                    async def _gen():
                        com = edge_tts.Communicate(frase, voz_id, rate=rate_str)
                        await com.save(str(audio_file))
                    asyncio.run(_gen())
                except Exception as ex:
                    live.log(f"Voz edge-tts: error ({ex})")
                    audio_file.unlink(missing_ok=True)
                
                if audio_file.exists():
                    if cfg["motor"] == "obs" and ventana_abierta():
                        _fin.clear()
                        live.publicar("voz_audio", {"u": "/voz/archivo/" + audio_file.name, "v": cfg["volumen"] / 100})
                        _fin.wait(60)
                        audio_file.unlink(missing_ok=True)
                        continue
                    elif cfg["motor"] in ("sistema", "obs"):
                        try:
                            # Reproducir por audio del sistema (directo o fallback si OBS no está conectado)
                            subprocess.run([
                                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                                f"Add-Type -AssemblyName presentationCore; $m = New-Object System.Windows.Media.MediaPlayer; $m.Open([System.Uri]'{audio_file.resolve()}'); $m.Volume = {cfg['volumen']/100}; $m.Play(); Start-Sleep -Milliseconds 400; $dur = if ($m.NaturalDuration.HasTimeSpan) {{ $m.NaturalDuration.TimeSpan.TotalSeconds }} else {{ 4 }}; Start-Sleep -Seconds $dur; $m.Close()"
                            ], creationflags=CF, timeout=60)
                        except Exception:
                            pass
                        audio_file.unlink(missing_ok=True)
                        continue
                        audio_file.unlink(missing_ok=True)
                        continue
                    elif cfg["motor"] == "ventana":
                        _fin.clear()
                        live.publicar("voz_audio", {"u": "/voz/archivo/" + audio_file.name, "v": cfg["volumen"] / 100})
                        _fin.wait(60)
                        audio_file.unlink(missing_ok=True)
                        continue

            # 2. Modo ventana con síntesis de navegador
            if cfg["motor"] == "ventana":
                _fin.clear()
                live.publicar("voz", {"t": frase, "voz": cfg["voz_ventana"],
                                      "r": round(2 ** (cfg["velocidad"] / 10), 2), "v": cfg["volumen"] / 100})
                _fin.wait(25)  # la ventana avisa al terminar de hablar
                continue

            # 3. Voces de Windows (SAPI System.Speech)
            if _reiniciar:
                _matar()
                _reiniciar = False
            if _proc is None or _proc.poll() is not None:
                _proc = _iniciar()
            p = _proc
            obs = cfg["motor"] == "obs"
            wav = AUDIOS / f"{int(time.time() * 1000)}.wav"
            ruta = base64.b64encode(str(wav).encode("utf-8")) if obs else b""
            p.stdin.write(ruta + b"\t" + base64.b64encode(frase.encode("utf-8")) + b"\n")
            p.stdin.flush()
            resp = p.stdout.readline().decode("utf-8", "replace").strip()
            if not resp:
                if _corte:  # lo cerramos nosotros al pulsar Saltar
                    _corte = False
                    continue
                raise RuntimeError("PowerShell se cerró")
            if resp.startswith("ERR"):
                raise RuntimeError(resp[4:] or "error de voz")
            if obs and not wav.exists():
                raise RuntimeError("no se generó el archivo de audio")
            if obs:
                _fin.clear()
                live.publicar("voz_audio", {"u": "/voz/archivo/" + wav.name, "v": cfg["volumen"] / 100})
                _fin.wait(60)  # la fuente de OBS avisa al terminar de sonar
                wav.unlink(missing_ok=True)
        except Exception as e:
            live.log(f"Voz: error ({e})")
            _matar()


@bp.route("/voz")
def pagina():
    return render_template("voz.html")


def obtener_candidatos_donadores():
    """Recopila donadores de Kick, TikTok, Streamloots y VIPs para agregarlos con un clic."""
    candidatos = []
    vistos = set()
    permitidos = {str(x).lower().lstrip("@").strip() for x in cfg.get("bulma_usuarios_permitidos", []) if str(x).strip()}

    # 1. Top Streamloots
    try:
        import tops
        c_tops = tops.cargar_cfg()
        st = c_tops.get("top_streamloots", {})
        u_st = str(st.get("usuario", "")).strip()
        if u_st and u_st.lower() not in vistos:
            vistos.add(u_st.lower())
            cof = st.get("cofres", 0)
            din = st.get("dinero", "")
            candidatos.append({
                "usuario": u_st,
                "nombre": u_st,
                "origen": "Streamloots",
                "badge_color": "#ffd700",
                "detalle": f"Top Cofres: {cof} ({din})",
                "icono": "👑",
                "ya_autorizado": u_st.lower() in permitidos
            })
    except Exception:
        pass

    # 2. Donadores en Vivo de esta sesión (stats.py)
    try:
        import stats
        for user, dias in sorted(stats.donantes.items(), key=lambda x: -x[1])[:10]:
            u_clean = str(user).strip()
            if u_clean and u_clean.lower() not in vistos:
                vistos.add(u_clean.lower())
                candidatos.append({
                    "usuario": u_clean,
                    "nombre": u_clean,
                    "origen": "Donador LIVE",
                    "badge_color": "#00e5ff",
                    "detalle": f"{dias} diamantes donados",
                    "icono": "💎",
                    "ya_autorizado": u_clean.lower() in permitidos
                })
    except Exception:
        pass

    # 3. Top Donador Fijo si existe en tops.py
    try:
        td = c_tops.get("top_donador", {})
        u_td = str(td.get("usuario_fijo", "")).strip()
        if u_td and u_td.lower() not in vistos:
            vistos.add(u_td.lower())
            candidatos.append({
                "usuario": u_td,
                "nombre": u_td,
                "origen": "Top Donador LIVE",
                "badge_color": "#00e5ff",
                "detalle": f"{td.get('diamantes_fijo', 0)} diamantes",
                "icono": "💎",
                "ya_autorizado": u_td.lower() in permitidos
            })
    except Exception:
        pass

    # 4. VIPs registrados en el bot (vips.json)
    try:
        import vips
        for v in vips.cfg.get("vips", []):
            if not v.get("activo", True):
                continue
            u_tt = str(v.get("usuario", "")).strip()
            u_kick = str(v.get("usuario_kick", "")).strip()
            nom = str(v.get("nombre", "")).strip()
            u_clave = (u_tt or u_kick or nom).lower()
            if not u_clave or u_clave in vistos:
                continue
            vistos.add(u_clave)
            
            u_autorizar = u_tt or u_kick or nom
            origen = "VIP Kick" if (u_kick and not u_tt) else ("VIP MultiStream" if (u_tt and u_kick) else "VIP TikTok")
            badge_col = "#53fc18" if "Kick" in origen else "#a855f7"
            ya_aut = (u_autorizar.lower() in permitidos) or (nom.lower() in permitidos) or (u_tt.lower() in permitidos) or (u_kick.lower() in permitidos)
            
            candidatos.append({
                "usuario": u_autorizar,
                "usuario_tt": u_tt,
                "usuario_kick": u_kick,
                "nombre": nom or u_autorizar,
                "origen": origen,
                "badge_color": badge_col,
                "detalle": f"Rango: {v.get('rango', 'VIP').upper()}",
                "icono": "⭐",
                "ya_autorizado": ya_aut
            })
    except Exception:
        pass

    return candidatos


@bp.route("/api/voz/candidatos", methods=["GET"])
def api_candidatos():
    return jsonify({"candidatos": obtener_candidatos_donadores()})


@bp.route("/api/voz/agregar_autorizado", methods=["POST"])
def api_agregar_autorizado():
    d = request.get_json(silent=True) or {}
    user = str(d.get("usuario", "")).strip().lstrip("@")
    if not user:
        return jsonify({"ok": False, "error": "Usuario inválido"}), 400
    with lock:
        permitidos = list(cfg.get("bulma_usuarios_permitidos", []))
        if not any(x.lower() == user.lower() for x in permitidos):
            permitidos.append(user)
            cfg["bulma_usuarios_permitidos"] = permitidos
            _guardar()
    return jsonify({"ok": True, "permitidos": cfg.get("bulma_usuarios_permitidos", [])})


@bp.route("/api/voz/quitar_autorizado", methods=["POST"])
def api_quitar_autorizado():
    d = request.get_json(silent=True) or {}
    user = str(d.get("usuario", "")).strip().lstrip("@").lower()
    with lock:
        permitidos = [x for x in cfg.get("bulma_usuarios_permitidos", []) if x.strip().lstrip("@").lower() != user]
        cfg["bulma_usuarios_permitidos"] = permitidos
        _guardar()
    return jsonify({"ok": True, "permitidos": cfg.get("bulma_usuarios_permitidos", [])})


@bp.route("/api/voz/agregar_todos_candidatos", methods=["POST"])
def api_agregar_todos_candidatos():
    candidatos = obtener_candidatos_donadores()
    with lock:
        permitidos = list(cfg.get("bulma_usuarios_permitidos", []))
        existentes_low = {x.lower() for x in permitidos}
        for c in candidatos:
            u = c.get("usuario", "").strip().lstrip("@")
            if u and u.lower() not in existentes_low:
                permitidos.append(u)
                existentes_low.add(u.lower())
        cfg["bulma_usuarios_permitidos"] = permitidos
        _guardar()
    return jsonify({"ok": True, "permitidos": cfg.get("bulma_usuarios_permitidos", [])})


@bp.route("/api/voz", methods=["GET", "POST"])
def api_cfg():
    global _reiniciar
    if request.method == "POST":
        d = request.get_json(silent=True) or {}
        with lock:
            for k, v in d.items():
                if k == "voces_ia" and isinstance(v, list):
                    cfg["voces_ia"] = v
                elif k == "bulma_usuarios_permitidos":
                    if isinstance(v, str):
                        cfg[k] = [x.strip().lstrip("@") for x in v.split(",") if x.strip()]
                    elif isinstance(v, list):
                        cfg[k] = [str(x).strip().lstrip("@") for x in v if str(x).strip()]
                elif k in DEF and type(v) == type(DEF[k]):
                    if k in LIM:
                        v = max(LIM[k][0], min(LIM[k][1], v))
                    cfg[k] = v
        if any(k in d for k in ("voz", "velocidad", "volumen", "motor")):
            _reiniciar = True
        _guardar()
    return jsonify({"cfg": cfg, "cola": cola.qsize(), "recientes": list(recientes), "ventana": ventana_abierta(), "voces_ia": obtener_voces_ia(), "candidatos": obtener_candidatos_donadores()})


@bp.route("/api/voz/voces_ia", methods=["GET", "POST"])
def api_voces_ia():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        voces = data.get("voces_ia")
        if isinstance(voces, list):
            limpias = []
            for v in voces:
                if not isinstance(v, dict):
                    continue
                nom = str(v.get("nombre", "")).strip()[:40] or "Voz IA"
                cmd = re.sub(r"[^a-zA-Z0-9_]", "", str(v.get("comando", "")).lower().lstrip("!"))[:20]
                v_id = re.sub(r"[^a-zA-Z0-9_\-]", "", str(v.get("id", "")))[:30] or cmd or f"voz_{int(time.time()*1000)}"
                m_id = extraer_model_id(v.get("model_id", ""))
                color = str(v.get("color", "#00e5ff")).strip()[:10]
                emoji = str(v.get("emoji", "🤖")).strip()[:6]
                if not cmd or not m_id:
                    continue
                limpias.append({
                    "id": v_id,
                    "nombre": nom,
                    "comando": cmd,
                    "comandos_extra": [str(x).lower().lstrip("!") for x in v.get("comandos_extra", []) if str(x).strip()][:5],
                    "model_id": m_id,
                    "emoji": emoji,
                    "color": color,
                    "voz_respaldo": str(v.get("voz_respaldo", "es-MX-DaliaNeural")).strip(),
                    "pitch": str(v.get("pitch", "0Hz")).strip(),
                    "rate": str(v.get("rate", "0%")).strip(),
                    "activo": bool(v.get("activo", True))
                })
            with lock:
                cfg["voces_ia"] = limpias
            _guardar()
    return jsonify({"voces_ia": obtener_voces_ia()})


@bp.route("/api/voz/probar_ia", methods=["POST"])
def api_probar_ia():
    if cfg["motor"] in ("ventana", "obs") and not ventana_abierta():
        return jsonify({"ok": False, "error": "La fuente de OBS (/voz/player) no está conectada." if cfg["motor"] == "obs" else "Primero abre la ventana de sonido."})
    data = request.get_json(silent=True) or {}
    voz_id = str(data.get("voz_id") or data.get("id") or "bulma").strip()
    v_ia = buscar_voz_ia(voz_id) or VOCES_IA_DEF[0]
    frase_prueba = f"¡Hola! Soy {v_ia.get('nombre')}. ¡Activaste mi voz IA para el directo!"
    hablar_voz_ia(v_ia, "VIP", frase_prueba, forzar=True)
    return jsonify({"ok": True})


@bp.route("/api/voz/decir_ia", methods=["POST"])
def api_decir_ia():
    if cfg["motor"] in ("ventana", "obs") and not ventana_abierta():
        return jsonify({"ok": False, "error": "La fuente de OBS (/voz/player) no está conectada." if cfg["motor"] == "obs" else "Primero abre la ventana de sonido."})
    data = request.get_json(silent=True) or {}
    texto = str(data.get("texto", "")).strip()
    voz_id = str(data.get("voz_id") or data.get("id") or "bulma").strip()
    v_ia = buscar_voz_ia(voz_id) or VOCES_IA_DEF[0]
    if not texto:
        return jsonify({"ok": False, "error": f"Escribe un mensaje para que {v_ia.get('nombre')} lo diga."}), 400
    hablar_voz_ia(v_ia, "Streamer", texto, forzar=True)
    return jsonify({"ok": True})


@bp.route("/api/voz/saltar", methods=["POST"])
def saltar():
    global _corte
    _corte = _proc is not None and cfg["motor"] == "sistema"
    _matar()
    live.publicar("voz_stop", {})
    _fin.set()
    return jsonify({"ok": True})


@bp.route("/api/voz/vaciar", methods=["POST"])
def vaciar():
    global _corte
    _corte = _proc is not None and cfg["motor"] == "sistema"
    while True:
        try:
            cola.get_nowait()
        except Empty:
            break
    _matar()
    live.publicar("voz_stop", {})
    _fin.set()
    return jsonify({"ok": True})


@bp.route("/api/voz/probar", methods=["POST"])
def probar():
    if cfg["motor"] in ("ventana", "obs") and not ventana_abierta():
        return jsonify({"ok": False, "error": "La fuente de OBS no está conectada." if cfg["motor"] == "obs" else "Primero abre la ventana de sonido."})
    cola.put(("Prueba", "Hola, así suena mi voz en el directo"))
    _asegurar()
    return jsonify({"ok": True})


@bp.route("/api/voz/probar_bulma", methods=["POST"])
def probar_bulma():
    if cfg["motor"] in ("ventana", "obs") and not ventana_abierta():
        return jsonify({"ok": False, "error": "La fuente de OBS (/voz/player) no está conectada." if cfg["motor"] == "obs" else "Primero abre la ventana de sonido."})
    hablar_bulma("VIP", "¡Hola! Soy Bulma. ¡Activaste la voz VIP para el directo!", forzar=True)
    return jsonify({"ok": True})


@bp.route("/api/voz/decir_bulma", methods=["POST"])
def api_decir_bulma():
    if cfg["motor"] in ("ventana", "obs") and not ventana_abierta():
        return jsonify({"ok": False, "error": "La fuente de OBS (/voz/player) no está conectada." if cfg["motor"] == "obs" else "Primero abre la ventana de sonido."})
    data = request.get_json(silent=True) or {}
    texto = str(data.get("texto", "")).strip()
    if not texto:
        return jsonify({"ok": False, "error": "Escribe un mensaje para que Bulma lo diga."}), 400
    hablar_bulma("Streamer", texto, forzar=True)
    return jsonify({"ok": True})


@bp.route("/api/voz/silenciar", methods=["POST"])
def silenciar():
    n = str((request.get_json(silent=True) or {}).get("n", ""))[:40]
    with lock:
        if n and n not in cfg["silenciados"]:
            cfg["silenciados"].append(n)
    _guardar()
    return jsonify({"ok": True})


@bp.route("/api/voz/voces")
def voces():
    global _voces
    res = list(VOCES_EDGE)
    if _voces is None:
        _voces = []
        if os.name == "nt":
            try:
                r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                    "Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis."
                    "SpeechSynthesizer).GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }"],
                    capture_output=True, text=True, errors="replace", timeout=20, creationflags=CF)
                _voces = [l.strip() for l in r.stdout.splitlines() if l.strip()]
            except Exception:
                pass
    for v in _voces:
        res.append({"id": v, "nombre": f"💻 {v} (Windows)"})
    return jsonify(res)


@bp.route("/vozplayer")
def vozplayer():
    return render_template("vozplayer.html")


@bp.route("/api/voz/latido", methods=["POST"])
def latido():
    p = request.args.get("p", "ventana")
    if p in _latidos:
        _latidos[p] = time.time()
    return jsonify({"ok": True})


@bp.route("/api/voz/fin", methods=["POST"])
def fin():
    _fin.set()
    return jsonify({"ok": True})


@bp.route("/api/voz/ventana", methods=["POST"])
def ventana():
    """Abre la ventana de sonido en un Edge aparte (perfil propio) para poder
    capturar SOLO su audio con 'Captura de audio de aplicación' de OBS."""
    if ventana_abierta():
        return jsonify({"ok": True})
    for p in (r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
              r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"):
        ruta = os.path.expandvars(p)
        if os.path.exists(ruta):
            subprocess.Popen([ruta, "--app=http://127.0.0.1:5000/vozplayer",
                              f"--user-data-dir={BASE / 'edge_voz'}", "--no-first-run",
                              "--autoplay-policy=no-user-gesture-required",
                              "--disable-background-timer-throttling",
                              "--disable-renderer-backgrounding",
                              "--disable-backgrounding-occluded-windows",
                              "--window-size=420,260", "--disable-gpu"])
            return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "No se encontró Microsoft Edge."})


@bp.route("/voz/player")
def player():
    return render_template("voz_player.html")


@bp.route("/voz/archivo/<nombre>")
def archivo(nombre):
    m = "audio/mpeg" if nombre.lower().endswith(".mp3") else "audio/wav"
    return send_from_directory(AUDIOS, nombre, mimetype=m)


def vaciar_todo():
    """Para el modo ahorro: calla la voz y descarta lo que esté esperando."""
    global _corte
    _corte = _proc is not None and cfg["motor"] == "sistema"
    while True:
        try:
            cola.get_nowait()
        except Empty:
            break
    _matar()
    live.publicar("voz_stop", {})
    _fin.set()
