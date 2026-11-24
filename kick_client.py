"""Cliente en tiempo real para Kick.com usando Centrifugo WebSockets."""
import asyncio
import json
import re
import threading
import time
import urllib.request
import websockets

# Limpiar texto de emotes tipo [emote:12345:nombre] o [emoji:1234:nombre] -> nombre
EMOTE_RE = re.compile(r"\[(?:emote|emoji):\d+:([a-zA-Z0-9_\-]+)\]")

class KickClient:
    def __init__(self, username, on_message=None, on_follow=None, on_sub=None, on_reward=None, on_status=None):
        self.username = str(username or "").strip().lower().lstrip("@")
        self.on_message = on_message
        self.on_follow = on_follow
        self.on_sub = on_sub
        self.on_reward = on_reward
        self.on_status = on_status
        self.running = False
        self.ws = None
        self.thread = None
        self.chatroom_id = None
        self.channel_id = None
        self.token = None
        self.loop = None
        self.viewers = 0

    def _log(self, msg):
        print(f"[Kick] {msg}")
        if self.on_status:
            self.on_status("log", msg)

    def resolve_channel(self):
        """Obtiene chatroom_id y channel_id de la API pública de Kick."""
        if not self.username:
            return False
        url = f"https://kick.com/api/v1/channels/{self.username}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json"
        })
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                self.channel_id = data.get("id")
                self.chatroom_id = data.get("chatroom", {}).get("id")
                return bool(self.chatroom_id)
        except Exception as e:
            self._log(f"No se pudo encontrar el canal Kick '@{self.username}': {e}")
            return False

    def get_token(self):
        """Obtiene el token de autenticación para la conexión WebSocket con Centrifugo."""
        url = "https://web.kick.com/api/v1/realtime/auth/connection"
        payload = json.dumps({"client_id": f"kick-lite-{int(time.time())}"}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Content-Type": "application/json",
            "Accept": "application/json"
        })
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                self.token = data.get("data", {}).get("token")
                return bool(self.token)
        except Exception as e:
            self._log(f"Error al obtener token de realtime: {e}")
            return False

    async def _loop(self):
        self.loop = asyncio.get_running_loop()
        ws_url = "wss://realtime.us-west-2.platform.kick.com/connection/websocket"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Origin": "https://kick.com"
        }

        if self.on_status:
            self.on_status("conectando", True)

        while self.running:
            try:
                if not self.chatroom_id:
                    if not self.resolve_channel():
                        await asyncio.sleep(5)
                        continue

                if not self.token:
                    if not self.get_token():
                        await asyncio.sleep(5)
                        continue

                self._log(f"Conectando al chat de Kick: @{self.username} (chatroom: {self.chatroom_id})...")
                if self.on_status:
                    self.on_status("conectando", True)

                # Desactivar ping RFC6455 porque Centrifugo maneja heartbeat JSON ({})
                connect_kwargs = {
                    "ping_interval": None,
                    "ping_timeout": None,
                    "close_timeout": 5,
                }
                import inspect
                sig = inspect.signature(websockets.connect)
                if "additional_headers" in sig.parameters:
                    connect_kwargs["additional_headers"] = headers
                    connect_kwargs["user_agent_header"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    connect_kwargs["origin"] = "https://kick.com"
                else:
                    connect_kwargs["extra_headers"] = headers

                async with websockets.connect(ws_url, **connect_kwargs) as ws:
                    self.ws = ws

                    # 1. Handshake Connect con token
                    await ws.send(json.dumps({"connect": {"token": self.token}, "id": 1}))

                    # Esperar confirmación
                    while self.running:
                        init_raw = await ws.recv()
                        if not init_raw or init_raw.strip() == "{}":
                            await ws.send("{}")
                            continue
                        break

                    # 2. Suscribirse a los canales de chatroom y canal
                    chat_channel = f"chatrooms.{self.chatroom_id}.v2"
                    await ws.send(json.dumps({"subscribe": {"channel": chat_channel}, "id": 2}))

                    if self.channel_id:
                        ch_channel = f"channel.{self.channel_id}"
                        await ws.send(json.dumps({"subscribe": {"channel": ch_channel}, "id": 3}))

                    self._log(f"¡Conectado exitosamente al chat de Kick en vivo! (@{self.username})")
                    if self.on_status:
                        self.on_status("en_directo", True)

                    # Tarea de Keep-Alive activo: envía {} cada 20s para que proxies/Kick nunca cierren la sesión
                    async def _heartbeat():
                        while self.running:
                            try:
                                await asyncio.sleep(20)
                                if ws and not ws.closed:
                                    await ws.send("{}")
                            except Exception:
                                break

                    # Sondeo de viewers de Kick cada 15s
                    async def _poll_viewers():
                        while self.running:
                            try:
                                if self.username:
                                    url = f"https://kick.com/api/v1/channels/{self.username}"
                                    req = urllib.request.Request(url, headers={
                                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                                        "Accept": "application/json"
                                    })
                                    cur_loop = asyncio.get_running_loop()
                                    def _fetch():
                                        try:
                                            with urllib.request.urlopen(req, timeout=8) as r:
                                                return json.loads(r.read().decode())
                                        except Exception:
                                            return None
                                    data = await cur_loop.run_in_executor(None, _fetch)
                                    if data:
                                        ls = data.get("livestream")
                                        cnt = int((ls.get("viewer_count") or ls.get("viewers") or 0) if isinstance(ls, dict) else 0)
                                        if cnt != self.viewers:
                                            self.viewers = cnt
                                            if self.on_status:
                                                self.on_status("viewers", self.viewers)
                            except Exception:
                                pass
                            await asyncio.sleep(15)

                    hb_task = asyncio.create_task(_heartbeat())
                    vp_task = asyncio.create_task(_poll_viewers())

                    try:
                        while self.running:
                            raw = await ws.recv()
                            if not raw:
                                continue

                            # Heartbeat de Centrifugo: el servidor envía {} como ping y espera {} como pong
                            raw_str = raw.strip() if isinstance(raw, str) else ""
                            if raw_str == "{}":
                                await ws.send("{}")
                                continue

                            try:
                                msg = json.loads(raw)
                            except Exception:
                                continue

                            # Objeto vacío: responder con pong
                            if not msg:
                                await ws.send("{}")
                                continue

                            # Procesar mensajes push
                            if "push" in msg:
                                push = msg.get("push", {})
                                pub = push.get("pub", {})
                                data_wrapper = pub.get("data", {})

                                if isinstance(data_wrapper, dict):
                                    event = data_wrapper.get("event")
                                    event_data_raw = data_wrapper.get("data")
                                    event_data = json.loads(event_data_raw) if isinstance(event_data_raw, str) else (event_data_raw or {})
                                else:
                                    event = None
                                    event_data = data_wrapper if isinstance(data_wrapper, dict) else {}

                                # 0. Actualización de viewers o livestream
                                if (event in ("App\\Events\\LivestreamUpdated", "App\\Events\\StreamerIsLive") or 
                                    "livestream" in event_data):
                                    ls_obj = event_data.get("livestream") or event_data
                                    if isinstance(ls_obj, dict):
                                        v_cnt = ls_obj.get("viewer_count") or ls_obj.get("viewers")
                                        if v_cnt is not None:
                                            try:
                                                self.viewers = int(v_cnt)
                                                if self.on_status:
                                                    self.on_status("viewers", self.viewers)
                                            except (TypeError, ValueError):
                                                pass

                                # 0.1 Detección de canje de puntos de canal (Kick Channel Points)
                                event_lower = str(event or "").lower()
                                msg_obj = event_data.get("message") if isinstance(event_data.get("message"), dict) else {}
                                reward_obj = event_data.get("reward") or msg_obj.get("reward")
                                is_reward_ev = (any(k in event_lower for k in ("reward", "redemption", "channelpoint")) or
                                                isinstance(reward_obj, dict) or
                                                str(event_data.get("type", "")).lower() in ("reward", "redemption", "channel_points"))

                                if is_reward_ev and (reward_obj or "reward" in event_data or event_data.get("user") or event_data.get("sender")):
                                    reward_title = ""
                                    reward_cost = 0
                                    if isinstance(reward_obj, dict):
                                        reward_title = reward_obj.get("title") or reward_obj.get("name") or ""
                                        reward_cost = reward_obj.get("cost") or reward_obj.get("points") or 0
                                    if not reward_title:
                                        reward_title = (event_data.get("reward_title") or 
                                                        event_data.get("title") or 
                                                        event_data.get("reward_name") or 
                                                        "Recompensa de Puntos")
                                    if not reward_cost:
                                        reward_cost = event_data.get("cost") or event_data.get("points") or 0

                                    sender = event_data.get("sender") or event_data.get("user") or msg_obj.get("sender") or {}
                                    username = (sender.get("username") or 
                                                sender.get("slug") or 
                                                event_data.get("username") or 
                                                event_data.get("user_name") or 
                                                "Espectador")

                                    content = (event_data.get("content") or 
                                               msg_obj.get("content") or 
                                               event_data.get("user_input") or 
                                               event_data.get("input") or "")
                                    content_limpio = EMOTE_RE.sub(r"\1", str(content)).strip() if content else ""

                                    self._log(f"🎁 Canje de puntos de Kick detectado: {username} -> «{reward_title}» ({reward_cost} pts)" + (f": {content_limpio}" if content_limpio else ""))

                                    if self.on_reward:
                                        self.on_reward(username, reward_title, reward_cost, content_limpio)

                                # 1. Detección de Suscripción / Subs regaladas / Regalos (Kick Subs & Gifts)
                                is_sub_ev = (
                                    any(k in event_lower for k in ("subscription", "giftedsubscription", "giftsub", "sub_gift", "luckyusers", "gift")) or
                                    "total_gifted" in event_data or
                                    "gifted_quantity" in event_data or
                                    "gifted_usernames" in event_data or
                                    isinstance(event_data.get("gifter"), dict) or
                                    str(event_data.get("type", "")).lower() in ("subscription", "sub", "gift_sub", "gift")
                                )

                                if is_sub_ev:
                                    gifter_obj = event_data.get("gifter") if isinstance(event_data.get("gifter"), dict) else {}
                                    user_obj = event_data.get("user") if isinstance(event_data.get("user"), dict) else {}
                                    sender_obj = event_data.get("sender") if isinstance(event_data.get("sender"), dict) else {}
                                    sub_name = (
                                        gifter_obj.get("username") or
                                        gifter_obj.get("slug") or
                                        event_data.get("gifter_username") or
                                        user_obj.get("username") or
                                        user_obj.get("slug") or
                                        event_data.get("username") or
                                        sender_obj.get("username") or
                                        event_data.get("user_name") or
                                        "Suscriptor"
                                    )
                                    count = (
                                        event_data.get("count") or
                                        event_data.get("total_gifted") or
                                        event_data.get("total") or
                                        event_data.get("gifted_quantity") or
                                        event_data.get("quantity") or
                                        (len(event_data.get("gifted_usernames")) if isinstance(event_data.get("gifted_usernames"), list) else None) or
                                        (len(event_data.get("user_ids")) if isinstance(event_data.get("user_ids"), list) else None) or
                                        1
                                    )
                                    try:
                                        count = max(1, int(count))
                                    except (TypeError, ValueError):
                                        count = 1

                                    self._log(f"⭐ Suscripción/Regalo detectado en Kick: {sub_name} (x{count})")
                                    if self.on_sub:
                                        self.on_sub(sub_name, count)

                                # 2. Nuevo seguidor
                                elif any(k in event_lower for k in ("followevent", "followersupdated", "follow")):
                                    follower_name = (event_data.get("username") or 
                                                     event_data.get("user", {}).get("username") or 
                                                     event_data.get("follower_username") or "Nuevo Seguidor")
                                    if self.on_follow:
                                        self.on_follow(follower_name)

                                # 3. Mensaje de chat (ChatMessageEvent o ChatMessageSentEvent)
                                elif (event in ("App\\Events\\ChatMessageEvent", "App\\Events\\ChatMessageSentEvent") or
                                      "content" in event_data or "message" in event_data):
                                    msg_obj = event_data.get("message") if isinstance(event_data.get("message"), dict) else {}
                                    content = event_data.get("content") or msg_obj.get("content") or ""
                                    sender = event_data.get("sender") or event_data.get("user") or msg_obj.get("sender") or {}
                                    username = sender.get("username") or sender.get("slug") or "Anónimo"
                                    uid = str(sender.get("id") or username)
                                    emotes = []
                                    badges = sender.get("identity", {}).get("badges", []) if isinstance(sender.get("identity"), dict) else []

                                    if content:
                                        content_limpio = EMOTE_RE.sub(r"\1", str(content)).strip()
                                        if self.on_message and content_limpio:
                                            self.on_message(username, content_limpio, emotes, uid, badges)

                                elif any(k in str(event) for k in ("LivestreamUpdated", "StreamViewersChanged", "ViewersUpdated")):
                                    ls = event_data.get("livestream") or event_data
                                    cnt = int((ls.get("viewer_count") or ls.get("viewers") or 0) if isinstance(ls, dict) else 0)
                                    if cnt != self.viewers:
                                        self.viewers = cnt
                                        if self.on_status:
                                            self.on_status("viewers", self.viewers)

                                elif event and event not in ("App\\Events\\ChatMessageEvent", "App\\Events\\ChatMessageSentEvent", "App\\Events\\FollowEvent", "App\\Events\\FollowersUpdated", "App\\Events\\SubscriptionEvent", "App\\Events\\GiftedSubscriptionsEvent"):
                                    self._log(f"Evento Kick recibido [{event}]: {str(event_data)[:200]}")

                    finally:
                        hb_task.cancel()
                        vp_task.cancel()

            except Exception as e:
                if self.running:
                    self._log(f"Conexión con Kick reiniciándose ({e})...")
                    if self.on_status:
                        self.on_status("desconectado", False)
                    self.token = None  # Refrescar token
                    await asyncio.sleep(4)

    def start(self):
        if self.running:
            return
        self.running = True
        def _run():
            asyncio.run(self._loop())
        self.thread = threading.Thread(target=_run, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        ws = self.ws
        if ws and self.loop and self.loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(ws.close(), self.loop)
            except Exception:
                pass
        self._log("Desconectado de Kick.")
        if self.on_status:
            self.on_status("desconectado", False)
