"""Retos de Streamloots: escucha las cartas canjeadas, las guarda en una lista y las muestra en /loots.
Se conecta a la misma URL de alertas que usan Mix It Up o Streamer.bot (tu ID de alertas es privado)."""
import json, re, sys, time, urllib.request
from pathlib import Path
from threading import Event, Lock, Thread
from flask import Blueprint, jsonify, render_template, request
import live

try:
    import winreg
except ImportError:
    winreg = None

BASE = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
ARCHIVO = BASE / "loots.json"
URL = "https://widgets.streamloots.com/alerts/{}/media-stream"
POS = ("arriba-izq", "arriba-der", "abajo-izq", "abajo-der")
RAREZAS = ("comun", "rara", "epica", "legendaria")
bp = Blueprint("loots", __name__)
lock, _despertar = Lock(), Event()
estado = {"txt": "apagado", "error": ""}
_resp, _hilo, _raros = None, None, 0
_error_guardado = ""


def _extraer_id(t):
    t = str(t or "").strip()
    m = re.search(r"alerts/([A-Za-z0-9_-]{8,64})", t) or re.fullmatch(r"([A-Za-z0-9_-]{8,64})", t)
    return m.group(1) if m else ""


def _color(x, por):
    return str(x) if re.fullmatch(r"#[0-9a-fA-F]{6}", str(x)) else por


def _normalizar_rareza(r):
    val = str(r or "comun").lower().strip()
    if any(k in val for k in ("legend", "oro", "gold")):
        return "legendaria"
    if any(k in val for k in ("epic", "épic", "morad", "purple")):
        return "epica"
    if any(k in val for k in ("rar", "azul", "blue")):
        return "rara"
    return "comun"


def _limpiar_cfg(d):
    def num(k, lo, hi, por):
        try:
            return max(lo, min(hi, int(d.get(k, por))))
        except (TypeError, ValueError):
            return por
    return {
        "activa": bool(d.get("activa", True)),
        "id": _extraer_id(d.get("id", "")),
        "fuente": re.sub(r"[^\w \-]", "", str(d.get("fuente", "")))[:60],
        "color_titulo": _color(d.get("color_titulo"), "#ffd45e"),
        "color_texto": _color(d.get("color_texto"), "#ffffff"),
        "color_actual": _color(d.get("color_actual"), "#ffd45e"),
        "tam": num("tam", 14, 80, 26),
        "fondo": bool(d.get("fondo", True)),
        "max_visible": num("max_visible", 1, 20, 8),
        "mostrar_mensaje": bool(d.get("mostrar_mensaje", False)),
        "mostrar_rareza": bool(d.get("mostrar_rareza", True)),
        "pos": d.get("pos") if d.get("pos") in POS else "abajo-izq",
        "ocultar_vacio": bool(d.get("ocultar_vacio", True))
    }


def _item(x):
    return {
        "id": int(x["id"]),
        "carta": str(x.get("carta", ""))[:120],
        "usuario": str(x.get("usuario", ""))[:40],
        "mensaje": str(x.get("mensaje", ""))[:200],
        "estado": x.get("estado") if x.get("estado") in ("pendiente", "curso", "hecho") else "pendiente",
        "rareza": _normalizar_rareza(x.get("rareza")),
        "hora": str(x.get("hora", ""))[:5]
    }


try:
    _d = json.loads(ARCHIVO.read_text(encoding="utf-8"))
    cfg = _limpiar_cfg(_d.get("cfg") or {})
    items = [_item(x) for x in _d.get("items", []) if isinstance(x, dict) and "id" in x][:300]
except (OSError, ValueError, TypeError, KeyError):
    cfg, items = _limpiar_cfg({}), []
_sig = max([x["id"] for x in items], default=0)


def _guardar():
    global _error_guardado
    temporal = ARCHIVO.with_name(ARCHIVO.name + ".tmp")
    try:
        temporal.write_text(json.dumps({"cfg": cfg, "items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
        temporal.replace(ARCHIVO)  # reemplazo atómico para evitar JSON truncado
        _error_guardado = ""
        return True
    except OSError as exc:
        _error_guardado = str(exc)[:180]
        try:
            temporal.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def _recortar():
    for x in [x for x in items if x["estado"] == "hecho"][:-30]:
        items.remove(x)


def agregar(carta, usuario="", mensaje="", rareza="comun"):
    global _sig
    with lock:
        _sig += 1
        it = _item({
            "id": _sig,
            "carta": str(carta).strip() or "Carta",
            "usuario": str(usuario).strip(),
            "mensaje": str(mensaje).strip(),
            "estado": "pendiente",
            "rareza": _normalizar_rareza(rareza),
            "hora": time.strftime("%H:%M")
        })
        items.append(it)
        _recortar()
        _guardar()
    live.log(f"Streamloots ({it['rareza'].upper()}): {it['usuario'] or '?'} canjeó «{it['carta']}»")
    live.publicar("loots", {})


def _detectar_rareza(niveles, f):
    """Inspecciona el paquete JSON de Streamloots para extraer si la carta es común, rara, épica o legendaria."""
    # 1. Buscar en fields del evento
    for k in ("rarity", "cardRarity", "rareza", "tier", "card_rarity"):
        val = str(f.get(k) or "")
        if val:
            return _normalizar_rareza(val)
    # 2. Buscar en niveles anidados de "data"
    for n in niveles:
        for k in ("cardRarity", "rarity", "rareza", "card_rarity"):
            v = str(n.get(k) or "")
            if v:
                return _normalizar_rareza(v)
        c = n.get("card")
        if isinstance(c, dict):
            for k in ("rarity", "cardRarity", "rareza"):
                v = str(c.get(k) or "")
                if v:
                    return _normalizar_rareza(v)
    return "comun"


def _procesar(obj):
    """Interpreta un paquete de alertas. Devuelve False si no lo reconoce."""
    niveles, o = [], obj
    while isinstance(o, dict):  # la información viene anidada en "data" dentro de "data"
        niveles.append(o)
        o = o.get("data")
    if not niveles:
        return False

    def buscar(k, inverso=False):
        for n in (reversed(niveles) if inverso else niveles):
            if n.get(k) not in (None, ""):
                return n[k]
    tipo, carta = str(buscar("type", True) or "").lower(), buscar("cardName")
    campos = buscar("fields") or []
    f = {str(x.get("name")): str(x.get("value", "")) for x in campos if isinstance(x, dict)}
    if carta:
        rareza = _detectar_rareza(niveles, f)
        agregar(carta, f.get("username", ""), f.get("message") or f.get("longMessage", ""), rareza=rareza)
        return True
    return "purchase" in tipo  # compras de cofres: se reconocen pero no se muestran


def _no_reconocido(obj):
    global _raros
    if _raros < 5:
        _raros += 1
        live.log("Streamloots: paquete sin reconocer: " + json.dumps(obj, ensure_ascii=False)[:160])


def _leer(resp):
    buf = ""
    for raw in resp:
        linea = raw.decode("utf-8", "replace").strip()
        if linea.startswith("data:"):
            linea = linea[5:].strip()
        elif not linea.startswith(("{", '"')) and not buf:
            continue  # event:, id:, retry:, comentarios
        if not linea:
            continue
        buf += linea
        obj = None
        for intento in (buf, "{" + buf + "}"):
            try:
                obj = json.loads(intento)
                break
            except ValueError:
                pass
        if obj is None:
            if len(buf) > 200_000:
                buf = ""
            continue
        buf = ""
        if not _procesar(obj):
            _no_reconocido(obj)
        if not cfg["activa"] or not cfg["id"]:
            return


def _trabajar():
    global _resp
    espera = 5
    while True:
        if not cfg["activa"] or not cfg["id"]:
            estado.update(txt="apagado", error="")
            _despertar.wait(3)
            _despertar.clear()
            continue
        try:
            estado.update(txt="conectando", error="")
            req = urllib.request.Request(URL.format(cfg["id"]), headers={"Accept": "text/event-stream", "User-Agent": "DBLegendsBot"})
            with urllib.request.urlopen(req, timeout=300) as r:
                _resp = r
                estado.update(txt="conectado", error="")
                espera = 5
                _leer(r)
        except Exception as e:
            if cfg["activa"] and cfg["id"] and "timed out" not in str(e):
                estado.update(txt="error", error=str(e)[:120])
                time.sleep(espera)
                espera = min(espera * 2, 120)
        finally:
            _resp = None
        live.publicar("loots", {})


def arrancar():
    global _hilo
    if not _hilo or not _hilo.is_alive():
        _hilo = Thread(target=_trabajar, daemon=True)
        _hilo.start()


def _reconectar():
    _despertar.set()
    r = _resp
    if r:
        try:
            r.close()
        except Exception:
            pass


@bp.route("/loots")
def overlay():
    return render_template("loots.html")


@bp.route("/loots/panel")
def panel():
    return render_template("loots_panel.html")


@bp.route("/api/loots", methods=["GET", "POST"])
def api_loots():
    global cfg
    if request.method == "POST":
        d = request.get_json(silent=True) or {}
        nuevo = {**cfg, **{k: v for k, v in d.items() if k != "id"}}
        if d.get("borrar_id"):
            nuevo["id"] = ""
        elif d.get("id"):
            nuevo["id"] = _extraer_id(d["id"])
            if not nuevo["id"]:
                return jsonify({"ok": False, "error": "No encontré el ID. Pega la URL de alertas completa."})
        antes = (cfg["id"], cfg["activa"])
        with lock:
            cfg = _limpiar_cfg(nuevo)
            _guardar()
        if antes != (cfg["id"], cfg["activa"]):
            estado.update(txt="conectando" if cfg["id"] and cfg["activa"] else "apagado", error="")
            _reconectar()
        live.publicar("loots", {})
    with lock:
        return jsonify({"ok": True, "cfg": {**cfg, "id": ""}, "hay_id": bool(cfg["id"]), "id_fin": cfg["id"][-4:],
                        "estado": dict(estado), "items": list(items), "guardado": not bool(_error_guardado),
                        "error_guardado": _error_guardado})


@bp.route("/api/loots/agregar", methods=["POST"])
def api_agregar():
    d = request.get_json(silent=True) or {}
    if not str(d.get("carta", "")).strip():
        return jsonify({"ok": False, "error": "Escribe el reto o la carta."})
    rareza = _normalizar_rareza(d.get("rareza", "comun"))
    agregar(str(d["carta"])[:120], str(d.get("usuario", ""))[:40], str(d.get("mensaje", ""))[:200], rareza=rareza)
    return jsonify({"ok": not bool(_error_guardado), "error_guardado": _error_guardado})


@bp.route("/api/loots/<int:i>/<accion>", methods=["POST"])
def api_accion(i, accion):
    with lock:
        it = next((x for x in items if x["id"] == i), None)
        if not it or accion not in ("curso", "hecho", "pendiente", "quitar"):
            return jsonify({"ok": False}), 404
        if accion == "quitar":
            items.remove(it)
        else:
            # PERMITE MÚLTIPLES RETOS EN CURSO: No desmarcamos las demás cartas cuando accion == 'curso'
            it["estado"] = accion
        _recortar()
        _guardar()
    live.publicar("loots", {})
    return jsonify({"ok": not bool(_error_guardado), "error_guardado": _error_guardado})


@bp.route("/api/loots/<int:i>/rareza", methods=["POST"])
def api_rareza(i):
    d = request.get_json(silent=True) or {}
    nueva_rareza = _normalizar_rareza(d.get("rareza"))
    with lock:
        it = next((x for x in items if x["id"] == i), None)
        if not it:
            return jsonify({"ok": False, "error": "Reto no encontrado"}), 404
        it["rareza"] = nueva_rareza
        _guardar()
    live.publicar("loots", {})
    return jsonify({"ok": True})


@bp.route("/api/loots/fuentes")
def api_fuentes():
    fuentes = set()
    destacadas = [
        "Minecraft Medium",
        "Minecraft",
        "Press Start 2P",
        "Rajdhani",
        "Impact",
        "Arial Black",
        "Segoe UI",
        "Bebas Neue",
        "Montserrat",
        "Consolas"
    ]
    if winreg:
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts") as k:
                    i = 0
                    while True:
                        try:
                            nom, _, _ = winreg.EnumValue(k, i)
                            limpio = re.sub(r"\s*\([^)]*\)$", "", nom).strip()
                            if limpio:
                                fuentes.add(limpio)
                            i += 1
                        except OSError:
                            break
            except OSError:
                pass
    fuentes.add("Minecraft")
    fuentes.add("Minecraft Medium")
    todas = sorted(list(fuentes), key=lambda s: s.lower())
    return jsonify({
        "destacadas": destacadas,
        "fuentes": todas
    })
