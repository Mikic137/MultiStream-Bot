"""Conexión con OBS (obs-websocket v5, incluido en OBS 28 o superior).
Al arrancar, le pide a OBS «reiniciar el caché» de cada fuente de navegador que apunte a este programa."""
import asyncio, base64, hashlib, json, re, sys, uuid
from pathlib import Path
from threading import Event, Thread
from flask import Blueprint, jsonify, request
import live
try:
    from websockets.asyncio.client import connect
except ImportError:
    try:
        from websockets import connect
    except ImportError:
        connect = None

BASE = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
ARCHIVO, PUERTO = BASE / "obs.json", 5000
ESPERA_INICIAL = 3  # segundos: el servidor ya está arriba, da tiempo a que las páginas carguen
bp = Blueprint("obs", __name__)
estado = {"txt": "apagado", "error": "", "ultima": ""}
_pedido, _reintentar, _sesion, _hilo = Event(), Event(), {"refrescado": False}, None


def _limpiar(d):
    host = re.sub(r"[^\w.\-]", "", str(d.get("host", "127.0.0.1")))[:60] or "127.0.0.1"
    try:
        port = max(1, min(65535, int(d.get("port", 4455))))
    except (TypeError, ValueError):
        port = 4455
    return {"activo": bool(d.get("activo", True)), "auto": bool(d.get("auto", True)), "host": host, "port": port,
            "password": str(d.get("password", ""))[:100]}


try:
    cfg = _limpiar(json.loads(ARCHIVO.read_text(encoding="utf-8")))
except (OSError, ValueError):
    cfg = _limpiar({})


def auth(password, salt, challenge):
    """Cadena de autenticación de obs-websocket v5 (SHA256 + base64, dos veces)."""
    secreto = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secreto + challenge).encode()).digest()).decode()


async def _pedir(ws, tipo, datos=None):
    rid = uuid.uuid4().hex
    await ws.send(json.dumps({"op": 6, "d": {"requestType": tipo, "requestId": rid, "requestData": datos or {}}}))
    while True:
        m = json.loads(await asyncio.wait_for(ws.recv(), 10))
        if m.get("op") == 7 and m["d"].get("requestId") == rid:
            st = m["d"].get("requestStatus", {})
            if not st.get("result"):
                raise RuntimeError(st.get("comment") or f"código {st.get('code')}")
            return m["d"].get("responseData") or {}


async def _conectar():
    ws = await connect(f"ws://{cfg['host']}:{cfg['port']}", open_timeout=5, max_size=2 ** 22)
    try:
        hello = json.loads(await asyncio.wait_for(ws.recv(), 5))
        d = {"rpcVersion": 1, "eventSubscriptions": 0}  # sin eventos: casi sin tráfico
        a = hello["d"].get("authentication")
        if a:
            if not cfg["password"]:
                raise RuntimeError("OBS pide contraseña y no hay ninguna guardada")
            d["authentication"] = auth(cfg["password"], a["salt"], a["challenge"])
        await ws.send(json.dumps({"op": 1, "d": d}))
        m = json.loads(await asyncio.wait_for(ws.recv(), 5))
        if m.get("op") != 2:
            raise RuntimeError("OBS no aceptó la conexión")
    except BaseException:
        await ws.close()
        raise
    return ws


async def _refrescar(ws):
    r = await _pedir(ws, "GetInputList", {"inputKind": "browser_source"})
    hechas, fallidas = [], 0
    for inp in r.get("inputs", []):
        nombre = inp.get("inputName")
        try:
            s = (await _pedir(ws, "GetInputSettings", {"inputName": nombre})).get("inputSettings", {})
            url = str(s.get("url", ""))
            if s.get("is_local_file") or not any(h in url for h in (f"127.0.0.1:{PUERTO}", f"localhost:{PUERTO}")):
                continue  # solo las fuentes de este programa
            await _pedir(ws, "PressInputPropertiesButton", {"inputName": nombre, "propertyName": "refreshnocache"})
            hechas.append(nombre)
        except Exception:
            fallidas += 1
    return hechas, fallidas


def _amable(e):
    t = str(e)
    if "4009" in t or "uthentication" in t:
        return "Contraseña incorrecta."
    if isinstance(e, (ConnectionRefusedError, OSError, TimeoutError, asyncio.TimeoutError)) or "refused" in t.lower():
        return "No se pudo conectar: abre OBS y activa el servidor WebSocket (Herramientas → Ajustes del servidor WebSocket)."
    return t[:150] or e.__class__.__name__


async def _intentar():
    estado.update(txt="conectando", error="")
    try:
        ws = await _conectar()
    except Exception as e:
        estado.update(txt="sin_obs", error=_amable(e))
        return False
    try:
        if not _sesion["refrescado"]:
            await asyncio.sleep(ESPERA_INICIAL)
        hechas, fallidas = await _refrescar(ws)
        txt = f"{len(hechas)} fuente(s) actualizada(s)" + (f": {', '.join(hechas)}" if hechas else "")
        estado.update(txt="listo", error="", ultima=txt + (f" ({fallidas} con error)" if fallidas else ""))
        live.log("OBS: " + txt)
        return True
    except Exception as e:
        estado.update(txt="error", error=_amable(e))
        return False
    finally:
        try:
            await ws.close()
        except Exception:
            pass


async def _bucle():
    reintento = 0
    while True:
        if _reintentar.is_set():  # acaban de cambiar los datos de conexión: probar ya, sin esperar
            _reintentar.clear()
            reintento = 0
        if not cfg["activo"] or connect is None:
            estado.update(txt="apagado", error="" if connect else "Falta la librería websockets")
        elif _pedido.is_set() or (cfg["auto"] and not _sesion["refrescado"] and reintento <= 0):
            manual = _pedido.is_set()
            _pedido.clear()
            ok = await _intentar()
            if ok:
                _sesion["refrescado"] = True
            reintento = 0 if manual else 15  # si OBS aún no está abierto, reintenta cada 15 s
        else:
            reintento -= 1
            if not (cfg["auto"] and not _sesion["refrescado"]):
                estado["txt"] = estado["txt"] if estado["txt"] in ("listo", "error", "sin_obs") else "esperando"
        await asyncio.sleep(1)


def arrancar():
    global _hilo
    if not _hilo or not _hilo.is_alive():
        _hilo = Thread(target=lambda: asyncio.run(_bucle()), daemon=True)
        _hilo.start()


@bp.route("/api/obs", methods=["GET", "POST"])
def api_obs():
    global cfg
    if request.method == "POST":
        d = request.get_json(silent=True) or {}
        nuevo = {**cfg, **{k: v for k, v in d.items() if k != "password"}}
        if d.get("borrar_pass"):
            nuevo["password"] = ""
        elif d.get("password"):
            nuevo["password"] = d["password"]
        cfg = _limpiar(nuevo)
        _sesion["refrescado"] = False  # con datos nuevos, vuelve a intentarlo
        _reintentar.set()
        if cfg["activo"]:
            estado.update(txt="conectando", error="")
        try:
            ARCHIVO.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass
    return jsonify({"cfg": {**cfg, "password": ""}, "hay_pass": bool(cfg["password"]), "estado": dict(estado)})


@bp.route("/api/obs/actualizar", methods=["POST"])
def api_actualizar():
    _pedido.set()
    estado.update(txt="conectando", error="")
    return jsonify({"ok": True})
