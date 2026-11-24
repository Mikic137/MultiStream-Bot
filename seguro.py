"""Seguro de recursos: vigila lo que consume el programa y el PC, y recorta funciones opcionales si se dispara."""
import json, os, sys, threading, time
from pathlib import Path
from flask import Blueprint, jsonify, render_template, request

BASE = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
ARCHIVO = BASE / "seguro.json"
NCPU = os.cpu_count() or 1
bp = Blueprint("seguro", __name__)
DEF = {"activo": True, "cpu_proc": 35, "ram_proc_mb": 400, "cpu_sis": 92, "ram_sis": 88, "seg": 15,
       "ram_proc_max": 1000, "ram_sis_max": 95, "recuperar_seg": 60, "cerrar_en_fuga": False}
LIM = {"cpu_proc": (5, 100), "ram_proc_mb": (50, 8000), "cpu_sis": (50, 100), "ram_sis": (50, 99), "seg": (5, 300),
       "ram_proc_max": (100, 16000), "ram_sis_max": (60, 99), "recuperar_seg": (10, 900)}
TXT = {"cpu_proc": "el programa usa mucha CPU", "ram_proc": "el programa usa mucha memoria", "cpu_sis": "el PC tiene la CPU al límite",
       "ram_sis": "al PC le queda poca memoria libre", "ram_proc_max": "el programa usa muchísima memoria",
       "ram_sis_max": "al PC se le acaba la memoria", "cpu_proc_max": "el programa usa casi toda la CPU"}


def _limpiar(d):
    r = {"activo": bool(d.get("activo", True)), "cerrar_en_fuga": bool(d.get("cerrar_en_fuga", False))}
    for k, (lo, hi) in LIM.items():
        try:
            r[k] = max(lo, min(hi, int(d.get(k, DEF[k]))))
        except (TypeError, ValueError):
            r[k] = DEF[k]
    return r


try:
    cfg = _limpiar(json.loads(ARCHIVO.read_text(encoding="utf-8")))
except (OSError, ValueError):
    cfg = _limpiar({})

# ---------- lectura de recursos (solo ctypes/stdlib, sin instalar nada) ----------
_ram_proc = _ram_sis = _tiempos = lambda: None
if os.name == "nt":
    try:
        import ctypes
        from ctypes import wintypes as wt
        _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class _MEM(ctypes.Structure):
            _fields_ = [("dwLength", wt.DWORD), ("dwMemoryLoad", wt.DWORD)] + [(n, ctypes.c_ulonglong) for n in (
                "ullTotalPhys", "ullAvailPhys", "ullTotalPageFile", "ullAvailPageFile", "ullTotalVirtual", "ullAvailVirtual", "ullAvailExtendedVirtual")]

        class _PMC(ctypes.Structure):
            _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD)] + [(n, ctypes.c_size_t) for n in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
        _k32.GetCurrentProcess.restype = wt.HANDLE
        _k32.K32GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.POINTER(_PMC), wt.DWORD]
        _k32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(_MEM)]
        _k32.GetSystemTimes.argtypes = [ctypes.POINTER(wt.FILETIME)] * 3

        def _ram_sis():
            m = _MEM()
            m.dwLength = ctypes.sizeof(m)
            return float(m.dwMemoryLoad) if _k32.GlobalMemoryStatusEx(ctypes.byref(m)) else None

        def _ram_proc():
            p = _PMC()
            p.cb = ctypes.sizeof(p)
            return p.WorkingSetSize / 1048576 if _k32.K32GetProcessMemoryInfo(_k32.GetCurrentProcess(), ctypes.byref(p), p.cb) else None

        def _tiempos():
            i, k, u = wt.FILETIME(), wt.FILETIME(), wt.FILETIME()
            if not _k32.GetSystemTimes(ctypes.byref(i), ctypes.byref(k), ctypes.byref(u)):
                return None
            f = lambda t: (t.dwHighDateTime << 32) | t.dwLowDateTime
            return f(k) + f(u), f(i)  # (total, inactivo): el tiempo de kernel incluye el inactivo
    except Exception:
        pass
else:  # Linux/Mac: solo para poder probar
    def _ram_proc():
        try:
            return int(open("/proc/self/statm").read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1048576
        except Exception:
            return None

    def _ram_sis():
        try:
            d = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo")}
            return 100.0 * (1 - d["MemAvailable"] / d["MemTotal"])
        except Exception:
            return None

    def _tiempos():
        try:
            v = list(map(int, open("/proc/stat").readline().split()[1:]))
            return sum(v[:8]), v[3] + (v[4] if len(v) > 4 else 0)
        except Exception:
            return None

_prev = {"t": time.time(), "cpu": time.process_time(), "sis": _tiempos()}


def medir():
    ahora, cpu, sis = time.time(), time.process_time(), _tiempos()
    dt = max(ahora - _prev["t"], 1e-6)
    m = {"cpu_proc": round(100 * (cpu - _prev["cpu"]) / dt / NCPU, 1), "ram_proc_mb": _ram_proc(), "ram_sis": _ram_sis(), "cpu_sis": None}
    if sis and _prev["sis"]:
        dtot, dlibre = sis[0] - _prev["sis"][0], sis[1] - _prev["sis"][1]
        if dtot > 0:
            m["cpu_sis"] = round(100 * (dtot - dlibre) / dtot, 1)
    _prev.update(t=ahora, cpu=cpu, sis=sis)
    return m


# ---------- estado y reglas ----------
estado = {"nivel": "normal", "manual": False, "motivo": "", "desde": 0.0}
metricas, _cont, _subs, _ventanas, _calma = {}, {}, {}, [], 0.0


def _log(msg):
    import live  # import tardío: live también importa este módulo
    live.log(msg)


def suscribir(nivel, fn):
    _subs.setdefault(nivel, []).append(fn)


def registrar_ventana(proc):
    _ventanas.append(proc)


def cerrar_ventanas():
    for p in _ventanas:
        try:
            if p.poll() is None:
                p.terminate()
        except Exception:
            pass


def nivel():
    return estado["nivel"]


def ahorro():
    return estado["nivel"] != "normal"


def intervalo_ms():
    return 3000 if ahorro() else 0  # las páginas de OBS consultan menos


def _avisar(n):
    for fn in _subs.get(n, []):
        try:
            fn(n)
        except Exception as e:
            _log(f"🛡️ Seguro: error en una acción ({e})")


def _subir(n, motivo, fuga=False):
    estado.update(nivel=n, motivo=motivo, desde=time.time(), manual=False)
    _log(f"🛡️ Seguro: modo {n.upper()} — {motivo}")
    _avisar(n)
    if n == "emergencia":
        cerrar_ventanas()
        if fuga and cfg["cerrar_en_fuga"]:
            _log("🛡️ Seguro: el programa se cierra para proteger el PC. Vuelve a abrirlo cuando quieras.")
            threading.Timer(1.5, os._exit, args=(3,)).start()


def _bajar(por="el consumo volvió a la normalidad"):
    global _calma
    estado.update(nivel="normal", motivo="", manual=False)
    _calma = 0.0
    _cont.clear()
    _log(f"🛡️ Seguro: vuelve al modo normal ({por})")


def manual(on):
    if on:
        estado.update(nivel="ahorro", motivo="activado a mano", desde=time.time(), manual=True)
        _log("🛡️ Seguro: modo AHORRO activado a mano")
        _avisar("ahorro")
    else:
        _bajar("desactivado a mano")


def evaluar(m, dt):
    """Un paso del vigilante. Una regla solo cuenta si el valor se mantiene alto varios segundos."""
    global _calma
    metricas.update(m)
    if not cfg["activo"]:
        return
    c = cfg
    ahorro_r = {"cpu_proc": (m.get("cpu_proc"), c["cpu_proc"], c["seg"]), "ram_proc": (m.get("ram_proc_mb"), c["ram_proc_mb"], 6),
                "cpu_sis": (m.get("cpu_sis"), c["cpu_sis"], 30), "ram_sis": (m.get("ram_sis"), c["ram_sis"], 6)}
    emerg_r = {"ram_proc_max": (m.get("ram_proc_mb"), c["ram_proc_max"], 4), "ram_sis_max": (m.get("ram_sis"), c["ram_sis_max"], 6),
               "cpu_proc_max": (m.get("cpu_proc"), 80, 20)}

    def sostenida(n, r):
        valor, umbral, seg = r
        _cont[n] = _cont.get(n, 0) + dt if (valor is not None and valor >= umbral) else 0
        return _cont[n] >= seg
    dis_a = [n for n, r in ahorro_r.items() if sostenida(n, r)]
    dis_e = [n for n, r in emerg_r.items() if sostenida(n, r)]
    describir = lambda ns, rs: "; ".join(f"{TXT[n]} ({round(rs[n][0])})" for n in ns)
    if dis_e and estado["nivel"] != "emergencia":
        _subir("emergencia", describir(dis_e, emerg_r), fuga="ram_proc_max" in dis_e)
    elif dis_a and estado["nivel"] == "normal":
        _subir("ahorro", describir(dis_a, ahorro_r))
    elif estado["nivel"] != "normal" and not estado["manual"]:
        calmo = all(v is None or v < 0.8 * u for v, u, _ in ahorro_r.values())
        _calma = _calma + dt if calmo else 0
        if _calma >= c["recuperar_seg"]:
            _bajar()


def _bucle():
    while True:
        time.sleep(2)
        try:
            evaluar(medir(), 2.0)
        except Exception:
            pass


_hilo = None


def arrancar():
    global _hilo
    if not _hilo or not _hilo.is_alive():
        _hilo = threading.Thread(target=_bucle, daemon=True)
        _hilo.start()


@bp.route("/sistema")
def pagina():
    return render_template("sistema.html")


@bp.route("/api/seguro", methods=["GET", "POST"])
def api_seguro():
    global cfg
    if request.method == "POST":
        cfg = _limpiar({**cfg, **(request.get_json(silent=True) or {})})
        try:
            ARCHIVO.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
        except OSError:
            pass
    return jsonify({"cfg": cfg, "m": metricas, "estado": dict(estado), "ncpu": NCPU})


@bp.route("/api/seguro/manual", methods=["POST"])
def api_manual():
    manual(bool((request.get_json(silent=True) or {}).get("on")))
    return jsonify({"ok": True})
