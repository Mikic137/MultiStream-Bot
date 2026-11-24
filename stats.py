"""Estadísticas del directo: viewers, likes, seguidores, compartidos y regalos."""
import time
from collections import deque
from threading import RLock
from flask import Blueprint, jsonify, render_template

bp = Blueprint("stats", __name__)
lock = RLock()
regalos = deque(maxlen=40)
donantes = {}           # General (puntos combinados)
donantes_regalos = {}   # Específico regalos/diamantes (TikTok)
donantes_dinero = {}    # Específico dinero en USD (Kick / Tips)
likers = {}             # Específico likes por usuario


def _nuevo():
    return {
        "inicio": None, "ultimo": 0.0, "viewers": 0, "kick_viewers": 0, "twitch_viewers": 0, "pico": 0, "visitantes": 0,
        "likes": 0, "seguidores": 0, "compartidos": 0, "diamantes": 0, "regalos_n": 0
    }


s = _nuevo()


def reiniciar(forzar=False):
    """Al conectar: empieza de cero solo si pasaron >10 min sin actividad
    (así una caída corta de conexión no borra los números del directo)."""
    with lock:
        if forzar or time.time() - s["ultimo"] > 600:
            s.clear()
            s.update(_nuevo())
            regalos.clear()
            donantes.clear()
            donantes_regalos.clear()
            donantes_dinero.clear()
            likers.clear()
        s["inicio"] = s["inicio"] or time.time()
        s["ultimo"] = time.time()


def _num(e, campo):
    try:
        return int(getattr(e, campo, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _quien(e):
    u = getattr(e, "user", None)
    return str(getattr(u, "nickname", "") or getattr(u, "unique_id", "") or "?")[:30]


def actualizar_kick_viewers(cnt):
    with lock:
        s["kick_viewers"] = max(0, int(cnt or 0))


def actualizar_twitch_viewers(cnt):
    with lock:
        s["twitch_viewers"] = max(0, int(cnt or 0))


def registrar_like(quien, cantidad=1):
    """Registra likes de un usuario y emite actualización en tiempo real."""
    with lock:
        cantidad = max(1, int(cantidad or 1))
        s["likes"] += cantidad
        s["ultimo"] = time.time()
        if quien and quien != "?":
            likers[quien] = likers.get(quien, 0) + cantidad
        try:
            import live, tops
            live.publicar("likes", tops.get_likes_data())
        except Exception:
            pass


def registrar_donacion_dinero(quien, usd, fuente="Kick"):
    """Registra donaciones en dinero ($ USD) de Kick o Streamloots."""
    with lock:
        usd = float(usd or 0)
        if usd <= 0:
            return
        quien = str(quien or "Espectador")[:30]
        donantes_dinero[quien] = round(donantes_dinero.get(quien, 0.0) + usd, 2)
        # Equivalente en puntos/diamantes para ranking general (1 USD = 100 diamantes)
        puntos = int(usd * 100)
        donantes[quien] = donantes.get(quien, 0) + puntos
        s["ultimo"] = time.time()
        regalos.appendleft({
            "u": quien,
            "g": f"${usd:.2f} USD ({fuente})",
            "n": 1,
            "d": puntos,
            "h": time.strftime("%H:%M")
        })
        try:
            import live, tops
            live.publicar("top_donador", tops.get_top_donadores_data())
        except Exception:
            pass


def registrar_donacion(quien, diamantes, nombre_regalo="Regalo"):
    """Registra regalos de TikTok (diamantes)."""
    with lock:
        diamantes = int(diamantes or 0)
        quien = str(quien or "Espectador")[:30]
        s["diamantes"] += diamantes
        s["regalos_n"] += 1
        s["ultimo"] = time.time()
        donantes_regalos[quien] = donantes_regalos.get(quien, 0) + diamantes
        donantes[quien] = donantes.get(quien, 0) + diamantes
        regalos.appendleft({
            "u": quien,
            "g": nombre_regalo,
            "n": 1,
            "d": diamantes,
            "h": time.strftime("%H:%M")
        })
        try:
            import live, tops
            live.publicar("top_donador", tops.get_top_donadores_data())
        except Exception:
            pass


def evento(tipo, e):
    """Registra un evento de TikTok. Devuelve un texto para el registro (o None)."""
    msg = None
    with lock:
        s["ultimo"] = time.time()
        if tipo == "viewers":
            s["viewers"] = _num(e, "total")
            s["visitantes"] = max(s["visitantes"], _num(e, "total_user"))
            s["pico"] = max(s["pico"], s["viewers"])
            try:
                import live
                live.publicar("viewers", live.get_viewers_data())
            except Exception:
                pass
        elif tipo == "likes":
            total = _num(e, "total")
            cnt = _num(e, "count") or 1
            s["likes"] = max(s["likes"], total) if total else s["likes"] + cnt
            quien = _quien(e)
            if quien and quien != "?":
                likers[quien] = likers.get(quien, 0) + cnt
            try:
                import live, tops
                live.publicar("likes", tops.get_likes_data())
            except Exception:
                pass
        elif tipo == "follow":
            s["seguidores"] += 1
        elif tipo == "share":
            s["compartidos"] += 1
        elif tipo == "regalo":
            if getattr(e, "streaking", False):
                return None  # a mitad de racha: se cuenta el evento final
            g = getattr(e, "gift", None)
            nombre = str(getattr(g, "name", "") or "Regalo")
            n = max(1, _num(e, "repeat_count"))
            dia = n * _num(g, "diamond_count")
            quien = _quien(e)
            s["diamantes"] += dia
            s["regalos_n"] += n
            donantes_regalos[quien] = donantes_regalos.get(quien, 0) + dia
            donantes[quien] = donantes.get(quien, 0) + dia
            regalos.appendleft({
                "u": quien,
                "g": nombre,
                "n": n,
                "d": dia,
                "h": time.strftime("%H:%M")
            })
            msg = f"Regalo: {quien} envió {nombre} x{n} ({dia} diamantes)"
            try:
                import live, tops
                live.publicar("top_donador", tops.get_top_donadores_data())
            except Exception:
                pass
    return msg


@bp.route("/stats")
def pagina():
    return render_template("stats.html")


@bp.route("/api/stats")
def api_stats():
    with lock:
        top = sorted(donantes.items(), key=lambda x: -x[1])[:5]
        top_lik = sorted(likers.items(), key=lambda x: -x[1])[:5]
        top_reg = sorted(donantes_regalos.items(), key=lambda x: -x[1])[:5]
        top_din = sorted(donantes_dinero.items(), key=lambda x: -x[1])[:5]
        dur = int(time.time() - s["inicio"]) if s["inicio"] else 0
        return jsonify({
            **s,
            "duracion": dur,
            "regalos": list(regalos)[:15],
            "top": top,
            "top_likes": [{"usuario": u, "likes": l} for u, l in top_lik],
            "top_regalos": [{"usuario": u, "diamantes": d} for u, d in top_reg],
            "top_dinero": [{"usuario": u, "dinero": d} for u, d in top_din]
        })


@bp.route("/api/stats/reiniciar", methods=["POST"])
def api_reiniciar():
    reiniciar(forzar=True)
    try:
        import live, tops
        live.publicar("likes", tops.get_likes_data())
        live.publicar("top_donador", tops.get_top_donadores_data())
    except Exception:
        pass
    return jsonify({"ok": True})
