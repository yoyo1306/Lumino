"""Lumino - app web locale (stdlib uniquement, sans pip).
Lance :  python app.py  ->  http://127.0.0.1:6420
API :
  GET  /api/status  -> GPUs + dernier état
  POST /api/scan    -> probe I2C (sans changer couleur)
  POST /api/apply   -> {"r":..,"g":..,"b":..,"brightness":0-100,"save":bool,"addr":optionnel}
  POST /api/off     -> éteint (noir, brightness 0)
"""
import json
import os
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
LOG_PATH = os.path.join(BASE, "lumino.log")


def _log(msg):
    try:
        with open(LOG_PATH, "a") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " " + msg + "\n")
    except OSError:
        pass

_state = {"last": None, "found": [], "error": None}
_lock = threading.Lock()
_aura_lock = threading.Lock()


def _load_nv():
    from nvapi import NvAPI
    nv = NvAPI()
    nv.initialize()
    return nv


def _status():
    with _lock:
        return dict(_state)


def api_scan():
    from nvapi import NvAPI
    import blackwell
    import gigabyte
    nv = NvAPI()
    nv.initialize()
    gpus = nv.enum_gpus()
    out = []
    for i, h in enumerate(gpus):
        try:
            pci = nv.get_pci(h)
        except Exception:
            pci = {}
        name = nv.get_name(h)
        bw_ok, bw_detail = blackwell.probe(nv, h, port=1)
        legacy = [] if bw_ok else gigabyte.scan_gpu(nv, h, port=1)
        out.append({"index": i, "name": name, "pci": pci,
                    "blackwell": {"addr": 0x75, "reply": bw_detail.get("reply")} if bw_ok else None,
                    "found": [{"addr": f["addr"], "port": f["port"]} for f in legacy]})
    with _lock:
        _state["found"] = out
        _state["error"] = None
    return out


def api_apply(payload):
    import blackwell
    r = max(0, min(255, int(payload.get("r", 255))))
    g = max(0, min(255, int(payload.get("g", 255))))
    b = max(0, min(255, int(payload.get("b", 255))))
    brightness = max(1, min(10, round(int(payload.get("brightness", 100)) / 10)))
    do_save = bool(payload.get("save", False))

    nv = _load_nv()
    gpus = nv.enum_gpus()
    if not gpus:
        raise RuntimeError("Aucun GPU NVIDIA trouvé")
    h = gpus[int(payload.get("gpu", 0))]

    ok, detail = blackwell.probe(nv, h, port=1)
    if not ok:
        raise RuntimeError(f"Blackwell non détecté à 0x75 ({detail}). Lance un Scan.")
    applied = blackwell.apply_static(nv, h, r, g, b, brightness, do_save)

    with _lock:
        _state["last"] = {"r": r, "g": g, "b": b, "brightness": brightness * 10,
                          "save": do_save, "detail": applied}
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
            if not isinstance(cfg, dict):
                cfg = {}
    except (OSError, ValueError):
        cfg = {}
    cfg.update(_state["last"])
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)
    except OSError:
        pass
    _log(f"GPU r={r} g={g} b={b} bri={brightness * 10}")
    return {"ok": True, "applied": _state["last"]}


class Handler(BaseHTTPRequestHandler):
    server_version = "Lumino/0.1"

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, rel, ctype):
        p = os.path.join(BASE, rel)
        if not os.path.isfile(p):
            self.send_error(404)
            return
        with open(p, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self._file(os.path.join("static", "index.html"), "text/html; charset=utf-8")
        if u.path == "/api/health":
            return self._json({"ok": True})
        if u.path == "/api/status":
            return self._json({"state": _status()})
        self.send_error(404)

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            payload = json.loads(raw.decode() or "{}")
        except Exception:
            payload = {}
        try:
            if u.path == "/api/scan":
                return self._json({"ok": True, "gpus": api_scan()})
            if u.path == "/api/apply":
                return self._json(api_apply(payload))
            if u.path == "/api/off":
                payload.update({"r": 0, "g": 0, "b": 0, "brightness": 0})
                return self._json(api_apply(payload))
            if u.path == "/api/all_off":
                import blackwell as _bw
                import aura as _aura2
                t1 = threading.Thread(target=lambda: api_apply(
                    {"r": 0, "g": 0, "b": 0, "brightness": 0, "save": True}))
                def _aura_off():
                    with _aura_lock:
                        a = _aura2.Aura()
                        try:
                            a.set_cm(0, 0, 0)
                            a.set_wc(0, 0, 0)
                        finally:
                            a.close()
                    with _lock:
                        _state["cm_last"] = {"r": 0, "g": 0, "b": 0, "brightness": 0}
                        _state["wc_last"] = {"r": 0, "g": 0, "b": 0, "brightness": 0}
                t2 = threading.Thread(target=_aura_off)
                t1.start(); t2.start(); t1.join(); t2.join()
                try:
                    with open(CONFIG_PATH) as f:
                        cfg = json.load(f)
                        if not isinstance(cfg, dict):
                            cfg = {}
                except (OSError, ValueError):
                    cfg = {}
                cfg.update({"cm_last": _state.get("cm_last"), "wc_last": _state.get("wc_last")})
                try:
                    with open(CONFIG_PATH, "w") as f:
                        json.dump(cfg, f, indent=2)
                except OSError:
                    pass
                _log("ALL_OFF gpu+cm+wc")
                return self._json({"ok": True})
            if u.path == "/api/aura_scan":
                import aura as _aura
                with _aura_lock:
                    a = _aura.Aura()
                    try:
                        return self._json({"ok": True, "aura": a.describe()})
                    finally:
                        a.close()
            if u.path in ("/api/cm_apply", "/api/wc_apply", "/api/cw_apply"):
                import aura as _aura
                if u.path == "/api/cm_apply":
                    parts = ("cm",)
                elif u.path == "/api/wc_apply":
                    parts = ("wc",)
                else:
                    parts = ("cm", "wc")
                r = max(0, min(255, int(payload.get("r", 255))))
                g = max(0, min(255, int(payload.get("g", 255))))
                b = max(0, min(255, int(payload.get("b", 255))))
                bri = max(0, min(100, int(payload.get("brightness", 100))))
                r, g, b = (c * bri // 100 for c in (r, g, b))
                with _aura_lock:
                    a = _aura.Aura()
                    try:
                        if parts == ("cm", "wc"):
                            a.set_both(r, g, b)
                            parts_done = ("cm", "wc")
                        else:
                            for part in parts:
                                if part == "cm":
                                    a.set_cm(r, g, b)
                                else:
                                    a.set_wc(r, g, b)
                            parts_done = parts
                    finally:
                        a.close()
                with _lock:
                    for part in parts_done:
                        _state[part + "_last"] = {"r": r, "g": g, "b": b, "brightness": bri}
                try:
                    with open(CONFIG_PATH) as f:
                        cfg = json.load(f)
                        if not isinstance(cfg, dict):
                            cfg = {}
                except (OSError, ValueError):
                    cfg = dict(_state.get("last") or {})
                for part in parts_done:
                    cfg[part + "_last"] = _state[part + "_last"]
                try:
                    with open(CONFIG_PATH, "w") as f:
                        json.dump(cfg, f, indent=2)
                except OSError:
                    pass
                _log(f"AURA {','.join(parts_done)} r={r} g={g} b={b} bri={bri}")
                return self._json({"ok": True, "applied": _state[parts[-1] + "_last"]})
            if u.path == "/api/gv_scan":
                import gv_official
                return self._json({"ok": True, "gv": gv_official.enum_devices()})
            if u.path == "/api/gv_apply":
                import gv_official
                r = max(0, min(255, int(payload.get("r", 255))))
                g = max(0, min(255, int(payload.get("g", 255))))
                b = max(0, min(255, int(payload.get("b", 255))))
                bri10 = round(max(0, min(100, int(payload.get("brightness", 100)))) / 10)
                return self._json({"ok": True, "gv": gv_official.apply_static_all(
                    r, g, b, bri10, bool(payload.get("save", False)))})
        except Exception as e:  # noqa: BLE001
            return self._json({"ok": False, "error": str(e)}, code=500)
        self.send_error(404)

    def log_message(self, fmt, *args):
        print("lumino:", fmt % args)


def _wait_and_open(port, timeout=15):
    """Ouvre le navigateur seulement quand /api/health répond (meilleure ouverture)."""
    import http.client

    def _run():
        for _ in range(int(timeout * 2)):
            try:
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                c.request("GET", "/api/health")
                r = c.getresponse()
                if r.status == 200:
                    break
            except Exception:
                pass
            time.sleep(0.5)
        try:
            webbrowser.open(f"http://127.0.0.1:{port}")
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Lumino - controle RGB local")
    ap.add_argument("--port", type=int, default=6420)
    ap.add_argument("--no-browser", action="store_true",
                    help="ne pas ouvrir le navigateur automatiquement")
    args = ap.parse_args()
    os.makedirs(os.path.join(BASE, "static"), exist_ok=True)
    try:
        with open(CONFIG_PATH) as f:
            saved = json.load(f)
            if isinstance(saved, dict):
                if "aura_last" in saved and "wc_last" not in saved:
                    saved["wc_last"] = saved["aura_last"]
                for key in ("cm_last", "wc_last"):
                    if key in saved:
                        _state[key] = saved[key]
                gpu = {k: v for k, v in saved.items() if k != "aura_last"}
                if "r" in gpu:
                    _state["last"] = gpu
    except (OSError, ValueError):
        pass
    try:
        srv = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    except OSError:
        print(f"Port {args.port} déjà utilisé : ouverture du site existant.")
        if not args.no_browser:
            try:
                webbrowser.open(f"http://127.0.0.1:{args.port}")
            except Exception:
                pass
        return
    try:
        import socket
        lan = socket.gethostbyname(socket.gethostname())
    except Exception:
        lan = "IP-du-PC"
    print(f"Lumino local  -> http://127.0.0.1:{args.port}")
    print(f"Lumino réseau -> http://{lan}:{args.port}  (téléphone sur le même WiFi)")
    if not args.no_browser:
        _wait_and_open(args.port)
    srv.serve_forever()


if __name__ == "__main__":
    main()
