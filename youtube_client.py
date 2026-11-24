"""Cliente en tiempo real para YouTube Live (Chat, Super Chats y Viewers) usando pytchat."""
import json
import re
import threading
import time
import urllib.request
from pathlib import Path
import pytchat


def _extraer_id_de_url_o_html(url):
    """Descarga la cabecera/HTML de una URL de YouTube e intenta extraer el videoId."""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Accept-Language": "es-ES,es;q=0.9,en;q=0.8"
            }
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            final_url = resp.geturl()
            m = re.search(r"(?:v=|/embed/|youtu\.be/)([a-zA-Z0-9_-]{11})", final_url)
            if m:
                return m.group(1)
            html = resp.read().decode("utf-8", errors="ignore")
            # 1. Buscar videoId directo en JSON interno
            target = '"videoId":"'
            idx = html.find(target)
            if idx != -1:
                vid = html[idx + len(target):idx + len(target) + 11]
                if re.fullmatch(r"[a-zA-Z0-9_-]{11}", vid):
                    return vid
            # 2. Buscar canonical o canonicalUrl
            m_can = re.search(r'"canonicalBaseUrl":"/watch\?v=([a-zA-Z0-9_-]{11})"', html)
            if m_can:
                return m_can.group(1)
    except Exception:
        pass
    return ""


def resolver_video_id_youtube(canal_o_url):
    """
    Resuelve el videoId de un directo de YouTube a partir de:
    - ID directo (11 caracteres): ej. 'UrJQdYq922U'
    - URL de video: ej. 'https://www.youtube.com/watch?v=...'
    - URL corta: ej. 'https://youtu.be/...'
    - Handle de canal: ej. '@dygetto', 'Dygettito137', 'https://www.youtube.com/@dygetto/live'
    """
    texto = str(canal_o_url or "").strip()
    if not texto:
        return ""

    # 1. ¿Es ya un videoId de 11 caracteres?
    if re.fullmatch(r"[a-zA-Z0-9_-]{11}", texto):
        return texto

    # 2. ¿Es URL con parámetro ?v= o embed o youtu.be?
    m = re.search(r"(?:v=|/embed/|youtu\.be/)([a-zA-Z0-9_-]{11})", texto)
    if m:
        return m.group(1)

    # 3. Limpiar prefijos de youtube.com
    canal = texto
    if "youtube.com/" in canal:
        canal = canal.split("youtube.com/")[-1].strip("/")

    # Intentos de URL para resolver el directo
    candidatos = []
    if canal.startswith("@"):
        candidatos.append(f"https://www.youtube.com/{canal.rstrip('/')}/live")
    elif canal.startswith("channel/") or canal.startswith("c/"):
        candidatos.append(f"https://www.youtube.com/{canal.rstrip('/')}/live")
    elif canal.startswith("UC") and len(canal) == 24:
        candidatos.append(f"https://www.youtube.com/channel/{canal}/live")
    else:
        # Si el usuario escribió el nombre sin @ (ej. 'Dygettito137'), probar @canal/live primero
        candidatos.append(f"https://www.youtube.com/@{canal.rstrip('/')}/live")
        candidatos.append(f"https://www.youtube.com/{canal.rstrip('/')}/live")

    for url in candidatos:
        vid = _extraer_id_de_url_o_html(url)
        if vid:
            return vid

    return ""


class YouTubeClient:
    def __init__(self, target, on_message=None, on_superchat=None, on_status=None):
        self.target = str(target or "").strip()
        self.on_message = on_message
        self.on_superchat = on_superchat
        self.on_status = on_status

        self.running = False
        self.thread = None
        self.video_id = ""
        self.chat = None
        self.viewers = 0
        self.is_live = False

    def _log(self, msg):
        print(f"[YouTube] {msg}")
        if self.on_status:
            try:
                self.on_status("log", msg, self)
            except TypeError:
                self.on_status("log", msg)

    def _notify(self, tipo, val):
        if self.on_status:
            try:
                self.on_status(tipo, val, self)
            except TypeError:
                self.on_status(tipo, val)

    def _consultar_viewers_directo(self, video_id):
        """Consulta el recuento aproximado de espectadores concurrentes de YouTube."""
        if not video_id:
            return 0
        try:
            url = f"https://www.youtube.com/watch?v={video_id}"
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8"
                }
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                html = resp.read().decode("utf-8", errors="ignore")

                # 1. Buscar texto en runs: ej "1 persona mirando ahora", "25 espectadores", "150 watching now"
                m = re.search(r'"text":\s*"([0-9.,\s\xa0]+)\s*(?:personas?\s+mirando|espectador|watching|viendo|mirando)', html, re.I)
                if m:
                    raw = re.sub(r"[^\d]", "", m.group(1))
                    if raw.isdigit():
                        return int(raw)

                # 2. Buscar originalViewCount
                m_orig = re.search(r'"originalViewCount":\s*"(\d+)"', html)
                if m_orig:
                    return int(m_orig.group(1))

                # 3. Buscar concurrentViewers
                m_conc = re.search(r'"concurrentViewers":\s*"(\d+)"', html)
                if m_conc:
                    return int(m_conc.group(1))

                # 4. Buscar en viewCountText o shortViewCount
                m_vc = re.search(r'"viewCountText":\{[^\}]*"text":\s*"([0-9.,\s\xa0]+)', html)
                if m_vc:
                    raw = re.sub(r"[^\d]", "", m_vc.group(1))
                    if raw.isdigit():
                        return int(raw)
        except Exception:
            pass
        return 0

    def _loop(self):
        self._log(f"Iniciando conexión con YouTube: {self.target}...")
        self._notify("conectando", True)

        reintentos_resolver = 0
        while self.running:
            # 1. Resolver el videoId si es canal o URL
            if not self.video_id:
                vid = resolver_video_id_youtube(self.target)
                if not vid:
                    reintentos_resolver += 1
                    if reintentos_resolver % 6 == 1:
                        self._log(f"Esperando directo en YouTube ({self.target})...")
                    self._notify("offline", False)
                    for _ in range(10):
                        if not self.running:
                            break
                        time.sleep(1)
                    continue
                self.video_id = vid
                self._log(f"Directo de YouTube detectado: https://youtu.be/{self.video_id}")

            # 2. Conectar pytchat
            try:
                self.chat = pytchat.create(video_id=self.video_id, interruptable=False)
                self.is_live = True
                self._notify("en_directo", True)
                self._log(f"¡Conectado al chat en vivo de YouTube! (video: {self.video_id})")

                # Consultar viewers de inmediato al conectar
                try:
                    self.viewers = self._consultar_viewers_directo(self.video_id)
                    self._notify("viewers", self.viewers)
                except Exception:
                    pass

                last_viewers_check = time.time()

                while self.running and self.chat and self.chat.is_alive():
                    for item in self.chat.get().sync_items():
                        if not self.running:
                            break
                        # Mensaje normal
                        autor = getattr(item.author, "name", "Anon") or "Anon"
                        uid = getattr(item.author, "channelId", "") or autor
                        badge = "moderador" if getattr(item.author, "isChatModerator", False) else ("sponsor" if getattr(item.author, "isChatSponsor", False) else None)
                        texto = getattr(item, "message", "") or ""

                        # Super Chat o Super Sticker
                        amount = getattr(item, "amountString", "") or ""
                        if amount and self.on_superchat:
                            try:
                                self.on_superchat(autor, amount, texto, uid=uid)
                            except Exception as e:
                                self._log(f"Error procesando SuperChat: {e}")

                        # Despachar mensaje de chat
                        if self.on_message and texto:
                            try:
                                self.on_message(autor, texto, uid=uid, badge=badge)
                            except Exception as e:
                                self._log(f"Error procesando comentario de YouTube: {e}")

                    # Consultar viewers cada 20 segundos
                    ahora = time.time()
                    if ahora - last_viewers_check > 20:
                        last_viewers_check = ahora
                        vw = self._consultar_viewers_directo(self.video_id)
                        if vw != self.viewers:
                            self.viewers = vw
                            self._notify("viewers", self.viewers)

                    time.sleep(1)

                if self.running:
                    self._log("El directo de YouTube finalizó o se interrumpió.")
                    self.video_id = ""
                    self.is_live = False
                    self._notify("offline", False)
                    time.sleep(5)

            except Exception as e:
                self._log(f"Error en chat de YouTube: {e}")
                self.video_id = ""
                self.is_live = False
                self._notify("offline", False)
                for _ in range(10):
                    if not self.running:
                        break
                    time.sleep(1)

        self._log("Monitoreo de YouTube detenido.")
        self._notify("desconectado", False)

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.chat:
            try:
                self.chat.terminate()
            except Exception:
                pass
            self.chat = None
