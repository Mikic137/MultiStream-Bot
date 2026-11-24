"""Cliente en tiempo real para Twitch: viewers, estado en directo y chat en vivo por IRC WebSocket (sin OAuth)."""
import asyncio
import json
import re
import threading
import time
import urllib.request
import websockets


def parse_irc_tags(tag_str):
    tags = {}
    for item in tag_str.split(";"):
        if "=" in item:
            k, v = item.split("=", 1)
            tags[k] = v
    return tags


class TwitchClient:
    def __init__(self, username, on_message=None, on_status=None, poll_interval=15):
        self.username = str(username or "").strip().lower().lstrip("@")
        self.on_message = on_message
        self.on_status = on_status
        self.poll_interval = poll_interval
        self.running = False
        self.thread_poll = None
        self.thread_chat = None
        self.chat_ws = None
        self.chat_loop = None
        self.viewers = 0
        self.is_live = False
        self.primera_vez = True

    def _log(self, msg):
        print(f"[Twitch] {msg}")
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

    def _poll_once(self):
        """Consulta el estado del canal mediante la API pública GQL de Twitch."""
        if not self.username:
            return

        # 1. Intentar con GQL oficial de Twitch
        try:
            url = "https://gql.twitch.tv/gql"
            query = {
                "query": "query($login: String!) { user(login: $login) { id stream { id viewersCount } } }",
                "variables": {"login": self.username}
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(query).encode("utf-8"),
                headers={
                    "Client-Id": "kimne78kx3ncx6brgo4mv6wki5h1ko",
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                }
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode())
                    user_data = data.get("data", {}).get("user")
                    if user_data:
                        stream = user_data.get("stream")
                        if stream and isinstance(stream, dict):
                            cnt = int(stream.get("viewersCount", 0))
                            self._set_state(True, cnt)
                            return
                        else:
                            self._set_state(False, 0)
                            return
        except Exception:
            pass

        # 2. Respaldo secundario si falla GQL
        try:
            url = f"https://unttv.vercel.app/streams/{self.username}"
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode())
                    cnt = data.get("viewerCount")
                    if cnt is not None and str(cnt).isdigit():
                        self._set_state(True, int(cnt))
                        return
                    else:
                        self._set_state(False, 0)
                        return
        except Exception:
            pass

    def _set_state(self, live, cnt):
        cambio_estado = (self.is_live != live) or self.primera_vez
        cambio_viewers = (self.viewers != cnt) or self.primera_vez
        self.primera_vez = False

        self.is_live = live
        self.viewers = max(0, int(cnt))

        if cambio_estado:
            self._log(f"Canal @{self.username} {'¡EN DIRECTO!' if live else 'Offline'}")
            self._notify("en_directo" if live else "offline", live)
        if cambio_viewers or cambio_estado:
            self._notify("viewers", self.viewers)

    def _loop_poll(self):
        self._log(f"Monitoreando Twitch: @{self.username}...")
        self._notify("conectando", True)

        while self.running:
            try:
                self._poll_once()
            except Exception as e:
                self._log(f"Error consultando viewers de Twitch: {e}")

            for _ in range(self.poll_interval):
                if not self.running:
                    break
                time.sleep(1)

        self._log("Monitoreo de viewers de Twitch detenido.")
        self._notify("desconectado", False)

    async def _async_chat_loop(self):
        ws_url = "wss://irc-ws.chat.twitch.tv:443"
        anon_nick = f"justinfan{int(time.time() % 89999 + 10000)}"

        while self.running:
            try:
                self._log(f"Conectando al chat de Twitch: #{self.username}...")
                async with websockets.connect(ws_url, open_timeout=8, ping_interval=30) as ws:
                    self.chat_ws = ws
                    # Registrarse con soporte de tags y comandos
                    await ws.send("CAP REQ :twitch.tv/tags twitch.tv/commands")
                    await ws.send("PASS oauth:none")
                    await ws.send(f"NICK {anon_nick}")
                    await ws.send(f"JOIN #{self.username}")

                    self._log(f"¡Conectado exitosamente al chat en vivo de Twitch! (#{self.username})")

                    async for raw_msg in ws:
                        if not self.running:
                            break
                        lines = raw_msg.strip().split("\r\n")
                        for line in lines:
                            if not line:
                                continue
                            if line.startswith("PING"):
                                await ws.send("PONG :tmi.twitch.tv")
                                continue

                            # Procesar PRIVMSG y USERNOTICE
                            if "PRIVMSG" in line or "USERNOTICE" in line:
                                self._parsear_mensaje_irc(line)

            except Exception as e:
                if self.running:
                    self._log(f"Conexión con chat de Twitch reiniciándose ({e})...")
                    await asyncio.sleep(4)

    def _parsear_mensaje_irc(self, raw_line):
        try:
            line = raw_line.strip()
            tags = {}
            if line.startswith("@"):
                partes = line.split(" ", 1)
                tags = parse_irc_tags(partes[0][1:])
                line = partes[1] if len(partes) > 1 else ""

            if " PRIVMSG " not in line and " USERNOTICE " not in line:
                return

            prefix = ""
            if line.startswith(":"):
                partes = line[1:].split(" ", 1)
                prefix = partes[0]
                resto = partes[1] if len(partes) > 1 else ""
            else:
                resto = line

            texto = ""
            if " :" in resto:
                _, texto = resto.split(" :", 1)
            elif resto.endswith(" :"):
                texto = ""

            if not texto:
                texto = tags.get("system-msg", "").replace(r"\s", " ")

            usuario_raw = prefix.split("!", 1)[0] if "!" in prefix else prefix
            display_name = tags.get("display-name") or (usuario_raw if usuario_raw != "tmi.twitch.tv" else "Twitch")
            uid = tags.get("user-id") or display_name.lower()
            badges_str = tags.get("badges") or ""

            badge = None
            if "broadcaster" in badges_str:
                badge = "streamer"
            elif "moderator" in badges_str:
                badge = "moderador"
            elif "vip" in badges_str:
                badge = "vip"
            elif "subscriber" in badges_str:
                badge = "sub"

            color = tags.get("color") or ""
            custom_reward_id = tags.get("custom-reward-id") or ""

            if self.on_message and texto:
                try:
                    self.on_message(display_name, texto, uid=uid, badge=badge, color=color, custom_reward_id=custom_reward_id, tags=tags)
                except TypeError:
                    self.on_message(display_name, texto, uid=uid, badge=badge, color=color)
        except Exception as ex:
            self._log(f"Error parseando mensaje IRC: {ex}")

    def _run_chat_loop(self):
        self.chat_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.chat_loop)
        try:
            self.chat_loop.run_until_complete(self._async_chat_loop())
        finally:
            try:
                self.chat_loop.close()
            except Exception:
                pass

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread_poll = threading.Thread(target=self._loop_poll, daemon=True)
        self.thread_poll.start()

        if self.on_message:
            self.thread_chat = threading.Thread(target=self._run_chat_loop, daemon=True)
            self.thread_chat.start()

    def stop(self):
        self.running = False
        if self.chat_ws and self.chat_loop and self.chat_loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(self.chat_ws.close(), self.chat_loop)
            except Exception:
                pass
