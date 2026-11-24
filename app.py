"""Lanzador: servidor + icono de bandeja. Cerrar la ventana NO detiene el bot."""
import logging, os, socket, subprocess, sys, threading, webbrowser
if sys.stdout is None:  # ejecutable sin consola
    try:
        sys.stdout = sys.stderr = open("crash.log", "w", encoding="utf-8")
    except Exception:
        sys.stdout = sys.stderr = open(os.devnull, "w")
from werkzeug.serving import make_server
import main

URL = "http://127.0.0.1:5000/"
logging.getLogger("werkzeug").setLevel(logging.ERROR)


def abrir():
    for p in (r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
              r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"):
        ruta = os.path.expandvars(p)
        if os.path.exists(ruta):  # ventana sin barras, usa el Edge ya instalado
            subprocess.Popen([ruta, f"--app={URL}", "--window-size=1100,720"])
            return
    webbrowser.open(URL)


def puerto_libre():
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", 5000)) != 0


def bandeja(servidor):
    try:
        import pystray
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (64, 64), "#10162b")
        ImageDraw.Draw(img).ellipse((10, 10, 54, 54), fill="#ffb52d")

        def salir(icono, _):
            icono.stop()
            servidor.shutdown()
            os._exit(0)

        pystray.Icon("DBLegendsBot", img, "DB Legends Bot", pystray.Menu(
            pystray.MenuItem("Abrir panel", lambda *_: abrir(), default=True),
            pystray.MenuItem("Salir", salir))).run()
    except Exception:
        pass


if __name__ == "__main__":
    try:
        if not puerto_libre():  # ya hay una instancia: solo abrir la ventana
            abrir()
            sys.exit()
        threading.Thread(target=main.monitor_tiktok, daemon=True).start()
        servidor = make_server("127.0.0.1", 5000, main.app, threaded=True)
        threading.Thread(target=servidor.serve_forever, daemon=True).start()
        abrir()
        try:
            bandeja(servidor)
        except Exception:
            pass
        threading.Event().wait()
    except Exception as exc:
        try:
            with open("crash.log", "a", encoding="utf-8") as f:
                import traceback
                f.write(f"ERROR: {exc}\n{traceback.format_exc()}\n")
        except Exception:
            pass
