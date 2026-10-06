"""Lumino - app bureau.
Double-clic : couleur, luminosite, effet, Appliquer. La fenetre peut se fermer.
Un effet est joue en meme temps sur le GPU, la carte mere et le Corsair.
Le statique reste grave sur le GPU et la carte mere. Le hub Corsair oublie
sa couleur : corsair_keep.py reste en fond, renvoie le Corsair et pousse
les memes images au GPU et a la carte mere.

Lance :  python lumino_gui.py
"""
import json
import os
import re
import sys
import threading
import time
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox

import blackwell
import effects

BASE = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False) \
    else os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
os.chdir(BASE)

CONFIG_PATH = os.path.join(BASE, "config.json")
LOG_PATH = os.path.join(BASE, "lumino.log")
LCD_MEDIA = os.path.join(BASE, "lcd_media")

PRESETS = [
    "#ff0000", "#ff7f00", "#ffd60a", "#22c55e",
    "#00d5ff", "#2b6cff", "#7c3aed", "#ff2fb3",
    "#ffffff", "#111827",
]
MODE_CHOICES = tuple(effects.MODE_LABELS[m] for m in effects.MODES)


def _log(msg):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " " + msg + "\n")
    except OSError:
        pass


def store_lcd_copy(src):
    """Copie l'image dans lcd_media et ne garde que celle-ci."""
    import shutil
    src = os.path.abspath(src)
    name = os.path.basename(src)
    if not name or name in (".", ".."):
        raise OSError("nom de fichier invalide")
    os.makedirs(LCD_MEDIA, exist_ok=True)
    dest = os.path.join(LCD_MEDIA, name)
    if os.path.normcase(src) != os.path.normcase(dest):
        shutil.copy2(src, dest)
    for fn in os.listdir(LCD_MEDIA):
        other = os.path.join(LCD_MEDIA, fn)
        if os.path.isfile(other) and os.path.normcase(other) != os.path.normcase(dest):
            os.remove(other)
    return dest


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
            return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


_CFG_LOCK = threading.Lock()


def save_config(patch):
    """Ecriture atomique : le fond Corsair ne doit jamais lire un JSON coupe."""
    with _CFG_LOCK:
        cfg = load_config()
        cfg.update(patch)
        tmp = CONFIG_PATH + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, CONFIG_PATH)
        except OSError as e:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise RuntimeError(f"config.json inaccessible : {e}") from e


def _valid_geo(geo):
    """Valide 'LxH' ou 'LxH+X+Y' (evite de restaurer une geometrie absurde)."""
    if not isinstance(geo, str):
        return False
    m = re.fullmatch(r"(\d{3,4})x(\d{3,4})([+-]\d+[+-]\d+)?", geo)
    if not m:
        return False
    w, h = int(m.group(1)), int(m.group(2))
    # L'ancienne fenêtre étroite (480x900) ne convient plus au panneau large.
    return 860 <= w <= 2560 and 520 <= h <= 1600


def hex_to_rgb(h):
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_hex(r, g, b):
    return "#%02x%02x%02x" % (
        max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))


def fg_for(hx):
    """Texte lisible sur la pastille (clair sur fonce, fonce sur clair)."""
    try:
        r, g, b = hex_to_rgb(hx)
    except (ValueError, IndexError):
        return "#111827"
    return "#111827" if (0.299 * r + 0.587 * g + 0.114 * b) > 160 else "#f9fafb"


def stored_color(d, fb_hex, fb_bri):
    """(hex, luminosite) depuis un bloc config.
    Nouveau format : unscaled, le RGB est la teinte choisie.
    Ancien format : RGB deja attenue, on inverse le pourcentage."""
    if not isinstance(d, dict):
        return fb_hex, fb_bri
    try:
        bri = max(0, min(100, int(d.get("brightness", fb_bri))))
    except (TypeError, ValueError):
        bri = fb_bri
    try:
        rgb = (int(d["r"]), int(d["g"]), int(d["b"]))
    except (KeyError, TypeError, ValueError):
        return fb_hex, bri
    if d.get("unscaled") or bri <= 0:
        return rgb_to_hex(*rgb), bri
    return rgb_to_hex(*(min(255, round(c * 100 / bri)) for c in rgb)), bri


# --- backends directs (pas de serveur) ---------------------------------

def gpu_apply(r, g, b, brightness100, do_save=True):
    """Applique au GPU RTX 5080 (Blackwell 0x75). Bloquant ~1s.
    0 % envoie du noir (le cran materiel minimum est 1, pas 0)."""
    from nvapi import NvAPI
    nv = NvAPI()
    nv.initialize()
    gpus = nv.enum_gpus()
    if not gpus:
        raise RuntimeError("Aucun GPU NVIDIA trouvé")
    h = gpus[0]
    ok, detail = blackwell.probe(nv, h, port=1)
    if not ok:
        raise RuntimeError(f"Contrôleur LED 0x75 non détecté ({detail}). Redémarre le PC si besoin.")
    level = blackwell.level_from_percent(brightness100)
    sr, sg, sb = (0, 0, 0) if level <= 0 else (int(r), int(g), int(b))
    hw = 1 if level <= 0 else level
    last = None
    for _ in range(3):
        try:
            return blackwell.apply_static(nv, h, sr, sg, sb, hw, do_save)
        except RuntimeError as e:
            last = e
            time.sleep(0.5)
    raise last


def aura_apply(parts, r, g, b, brightness100):
    """Applique a la CM (static persiste). Le retour garde la teinte brute."""
    import aura
    bri = max(0, min(100, int(brightness100)))
    r, g, b = (max(0, min(255, int(c))) for c in (r, g, b))
    rs, gs, bs = (c * bri // 100 for c in (r, g, b))
    a = aura.Aura()
    try:
        if parts == ("cm",):
            a.set_cm(rs, gs, bs)
        else:
            raise RuntimeError(f"Canal Aura inconnu : {parts}")
    finally:
        a.close()
    return {"r": r, "g": g, "b": b, "brightness": bri, "unscaled": True}


def _saved_color(r, g, b, bri, mode, speed):
    return {"r": r, "g": g, "b": b, "brightness": bri,
            "mode": mode, "speed": speed, "unscaled": True}


def corsair_apply(mode="static", speed=4, block=None, fans=None):
    """Sauve le waterblock et/ou les deux RX, puis le fond rejoue.

    block et fans sont (r, g, b, luminosite), ou None pour ne pas y toucher.
    Si le waterblock change et que les ventilos n'ont pas encore de couleur
    a eux, ils gardent l'ancienne teinte du bloc.
    """
    import corsair_link
    import corsair_keep
    if block is None and fans is None:
        raise RuntimeError("aucune couleur Corsair")
    if mode not in effects.MODES:
        mode = "static"
    try:
        speed = max(1, min(10, int(speed)))
    except (ValueError, TypeError):
        speed = 4
    patch = {"effect": {"mode": mode, "speed": speed}}
    cfg = load_config()
    if block is not None:
        r, g, b, bri = block
        bri = max(0, min(100, int(bri)))
        r, g, b = (max(0, min(255, int(c))) for c in (r, g, b))
        patch["corsair_last"] = _saved_color(r, g, b, bri, mode, speed)
        if fans is None and not isinstance(cfg.get("corsair_fans_last"), dict):
            prev = cfg.get("corsair_last")
            if isinstance(prev, dict) and "r" in prev:
                frozen = dict(prev)
                frozen["mode"] = mode
                frozen["speed"] = speed
                frozen["unscaled"] = True
                patch["corsair_fans_last"] = frozen
    if fans is not None:
        r, g, b, bri = fans
        bri = max(0, min(100, int(bri)))
        r, g, b = (max(0, min(255, int(c))) for c in (r, g, b))
        patch["corsair_fans_last"] = _saved_color(r, g, b, bri, mode, speed)
    save_config(patch)
    res = {"mode": mode, "speed": speed}
    try:
        if corsair_keep.is_running():
            res["detail"] = {"leds": 60, "mode": "fond-relais"}
        else:
            c = corsair_link.CorsairLink()
            try:
                fresh = corsair_keep.read_setup() or {}
                wb = fresh.get("corsair")
                fx = fresh.get("fans")
                c.set_segments(corsair_keep.render_link(
                    c.led_spans(), mode, time.monotonic(), wb, fx, speed))
                res["detail"] = {"leds": c.total_leds, "mode": "direct-volatile"}
            finally:
                c.close()
    except Exception as e:  # noqa: BLE001
        res["live_error"] = str(e)
    try:
        alive = corsair_keep.ensure_running()
    except Exception as e:  # noqa: BLE001
        alive = False
        res["live_error"] = (res.get("live_error", "") + f" | fond : {e}").strip(" |")
    res["keepalive"] = alive
    return res


def saved_effect(mode, speed):
    if mode not in effects.MODES:
        mode = "static"
    try:
        speed = max(1, min(10, int(speed)))
    except (TypeError, ValueError):
        speed = 4
    return {"mode": mode, "speed": speed}


def effect_mode_in_config():
    cfg = load_config()
    fx = cfg.get("effect") if isinstance(cfg.get("effect"), dict) else {}
    last = cfg.get("corsair_last") if isinstance(cfg.get("corsair_last"), dict) else {}
    mode = str(fx.get("mode", last.get("mode", "static")))
    return mode if mode in effects.MODES else "static"


def hardware_taken_by_sync(prev_mode, new_mode):
    """True si le fond anime le GPU et la carte mere, ou vient de les figer."""
    import corsair_keep
    if new_mode != "static":
        try:
            return bool(corsair_keep.ensure_running())
        except Exception as e:  # noqa: BLE001
            _log(f"fond: {e}")
            return False
    if prev_mode != "static" and corsair_keep.is_running():
        return corsair_keep.wait_static(4.0)
    return False


# --- interface : panneau large ------------------------------------------

THEMES = {
    "dark": {
        "bg": "#101114",
        "side": "#16181d",
        "card": "#1c1f27",
        "card_on": "#2a2440",
        "text": "#f4f4f5",
        "muted": "#a1a1aa",
        "line": "#2e323c",
        "accent": "#8b5cf6",
        "on_accent": "#ffffff",
        "ok": "#4ade80",
        "err": "#f87171",
        "chip": "#2a2d36",
        "input": "#12141a",
    },
    "light": {
        "bg": "#f3f4f6",
        "side": "#ffffff",
        "card": "#ffffff",
        "card_on": "#ede9fe",
        "text": "#111827",
        "muted": "#6b7280",
        "line": "#e5e7eb",
        "accent": "#6d28d9",
        "on_accent": "#ffffff",
        "ok": "#166534",
        "err": "#b91c1c",
        "chip": "#ffffff",
        "input": "#f9fafb",
    },
}

NAV = (
    ("all", "Tout"),
    ("gpu", "GPU"),
    ("cm", "Carte mère"),
    ("corsair", "Waterblock"),
    ("fans", "Ventilos"),
    ("lcd", "Image"),
)
TITLES = {
    "all": ("Tout synchroniser",
            "Même couleur et même effet, en même temps, sur le GPU, la carte mère, le waterblock et les ventilos."),
    "gpu": ("Carte graphique",
            "Même effet que les autres. En statique, la couleur est gravée sur la carte."),
    "cm": ("Carte mère",
           "Même effet que les autres. En statique, la couleur est gravée sur la carte."),
    "corsair": ("Waterblock",
               "Pompe Titan et anneau. Les deux RX ont leur page. iCUE fermé."),
    "fans": ("Ventilos Corsair",
             "Les deux RX ensemble. Même effet que le waterblock, couleur et luminosité à part."),
}
APPLY_LABELS = {
    "all": "Tout appliquer",
    "gpu": "Appliquer le GPU",
    "cm": "Appliquer la CM",
    "corsair": "Appliquer le waterblock",
    "fans": "Appliquer les ventilos",
}


class App(tk.Tk):
    DEFAULT_GEO = "1080x720"

    def __init__(self):
        super().__init__()
        self.title("Lumino")
        self.minsize(920, 620)
        self._geo_after = None
        self._last_geo = None
        self._busy = False
        self._loading = False
        self._status_kind = "info"
        self._status_text = "Prêt"
        self._fond_on = False
        self.lcd_on = False
        self.sel = "all"
        try:
            ico = os.path.join(BASE, "lumino.ico")
            if os.path.isfile(ico):
                self.iconbitmap(default=ico)
        except tk.TclError:
            pass

        cfg = load_config()
        theme = cfg.get("theme")
        self.theme_name = theme if theme in THEMES else "dark"
        self.pal = THEMES[self.theme_name]
        self.lcd_on = bool(cfg.get("lcd_temp"))
        path = cfg.get("lcd_image_path")
        self.lcd_image_path = path if isinstance(path, str) else ""
        try:
            self.lcd_scale = max(25, min(200, int(cfg.get("lcd_image_scale", 100))))
        except (TypeError, ValueError):
            self.lcd_scale = 100
        try:
            self.lcd_brightness = max(0, min(100, int(cfg.get("lcd_brightness", 100))))
        except (TypeError, ValueError):
            self.lcd_brightness = 100
        self.lcd_image_on = bool(cfg.get("lcd_image_on")) and bool(self.lcd_image_path)
        self._lcd_frames = None
        self._lcd_durs = None
        self._lcd_gif_i = 0
        self._lcd_src_path = None
        self._lcd_photo = None
        self._load_devices(cfg)

        geo = cfg.get("win_geo")
        self.geometry(geo if _valid_geo(geo) else self.DEFAULT_GEO)
        self._build()
        self._paint()
        self._load_editor()
        self.update_idletasks()
        self._last_geo = self.geometry()
        self.bind("<Configure>", self._on_configure)
        self._read_fond()
        self.after(2000, self._poll_fond)
        self._preview_t0 = time.monotonic()
        self.after(200, self._tick_preview)

    def _load_devices(self, cfg):
        last = cfg if "r" in cfg else {}
        cm_hex, cm_bri = stored_color(cfg.get("cm_last") or {}, "#00aaff", 100)
        co = cfg.get("corsair_last") or {}
        co_hex, co_bri = stored_color(co, "#ffeb00", 100)
        fan = cfg.get("corsair_fans_last") if isinstance(cfg.get("corsair_fans_last"), dict) else co
        fan_hex, fan_bri = stored_color(fan, co_hex, co_bri)

        def hx(d, fb):
            try:
                return rgb_to_hex(int(d["r"]), int(d["g"]), int(d["b"]))
            except (KeyError, TypeError, ValueError):
                return fb

        try:
            gpu_bri = max(0, min(100, int(last.get("brightness", 100))))
        except (TypeError, ValueError):
            gpu_bri = 100
        gpu_hex = hx(last, "#00aaff")
        fx = cfg.get("effect") if isinstance(cfg.get("effect"), dict) else {}
        mode = str(fx.get("mode", co.get("mode", "static")))
        if mode not in effects.MODES:
            mode = "static"
        try:
            speed = max(1, min(10, int(fx.get("speed", co.get("speed", 4)))))
        except (TypeError, ValueError):
            speed = 4
        self.dev = {
            "all": {"hex": gpu_hex, "bri": gpu_bri},
            "gpu": {"hex": gpu_hex, "bri": gpu_bri},
            "cm": {"hex": cm_hex, "bri": cm_bri},
            "corsair": {"hex": co_hex, "bri": co_bri, "mode": mode, "speed": speed},
            "fans": {"hex": fan_hex, "bri": fan_bri},
        }

    def _build(self):
        self.shell = tk.Frame(self)
        self.shell.pack(fill="both", expand=True)
        self.side = tk.Frame(self.shell, width=236)
        self.side.pack(side="left", fill="y")
        self.side.pack_propagate(False)
        self.sep = tk.Frame(self.shell, width=1)
        self.sep.pack(side="left", fill="y")
        self.editor = tk.Frame(self.shell)
        self.editor.pack(side="left", fill="both", expand=True)

        inn = tk.Frame(self.side)
        inn.pack(fill="both", expand=True, padx=14, pady=16)
        self.side_in = inn
        self.brand = tk.Label(inn, text="Lumino", font=("Segoe UI", 18, "bold"), anchor="w")
        self.brand.pack(fill="x")
        self.fond_fr = tk.Frame(inn)
        self.fond_fr.pack(fill="x", pady=(6, 12))
        self.fond_dot = tk.Canvas(self.fond_fr, width=14, height=14, highlightthickness=0, bd=0)
        self.fond_dot.pack(side="left")
        self.fond_lbl = tk.Label(self.fond_fr, text="fond arrêté", font=("Segoe UI", 9), anchor="w")
        self.fond_lbl.pack(side="left", padx=(4, 0))

        self.theme_fr = tk.Frame(inn)
        self.theme_fr.pack(fill="x", pady=(0, 14))
        self.btn_dark = tk.Button(self.theme_fr, text="Sombre", command=lambda: self._set_theme("dark"),
                                  relief="flat", bd=0, cursor="hand2", font=("Segoe UI", 9))
        self.btn_light = tk.Button(self.theme_fr, text="Clair", command=lambda: self._set_theme("light"),
                                   relief="flat", bd=0, cursor="hand2", font=("Segoe UI", 9))
        self.btn_dark.pack(side="left", expand=True, fill="x", padx=(0, 4))
        self.btn_light.pack(side="left", expand=True, fill="x", padx=(4, 0))

        self.btn_stop = tk.Button(inn, text="Arrêter le fond", command=self.do_stop_corsair,
                                  relief="flat", bd=0, cursor="hand2", font=("Segoe UI", 9))
        self.btn_off_all = tk.Button(inn, text="Tout éteindre", command=self.do_off,
                                     relief="flat", bd=0, cursor="hand2", font=("Segoe UI", 10))
        self.btn_lcd = tk.Button(inn, text="Écran LCD", command=self._toggle_lcd,
                                 relief="flat", bd=0, cursor="hand2", font=("Segoe UI", 9))
        self.btn_stop.pack(side="bottom", fill="x", ipady=6)
        self.btn_off_all.pack(side="bottom", fill="x", ipady=6, pady=(0, 8))
        self.btn_lcd.pack(side="bottom", fill="x", ipady=6, pady=(0, 8))

        self.nav = {}
        for key, name in NAV:
            fr = tk.Frame(inn, cursor="hand2")
            fr.pack(fill="x", pady=3)
            dot = tk.Canvas(fr, width=22, height=22, highlightthickness=0, bd=0)
            dot.pack(side="left", padx=(10, 8), pady=8)
            lab = tk.Label(fr, text=name, font=("Segoe UI", 10), anchor="w")
            lab.pack(side="left")
            pct = tk.Label(fr, font=("Segoe UI", 9), anchor="e")
            pct.pack(side="right", padx=10)
            for w in (fr, dot, lab, pct):
                w.bind("<Button-1>", lambda _e, k=key: self._select(k))
            self.nav[key] = {"frame": fr, "dot": dot, "name": lab, "pct": pct}

        ed = tk.Frame(self.editor)
        ed.pack(fill="both", expand=True, padx=28, pady=22)
        self.editor_in = ed
        self.title_lbl = tk.Label(ed, font=("Segoe UI", 20, "bold"), anchor="w")
        self.title_lbl.pack(fill="x")
        self.sub_lbl = tk.Label(ed, font=("Segoe UI", 9), anchor="w", justify="left")
        self.sub_lbl.pack(fill="x", pady=(2, 12))
        self.preview = tk.Canvas(ed, height=108, highlightthickness=1, bd=0)
        self.preview.pack(fill="x")

        self.color_lbl = tk.Label(ed, text="Couleur", font=("Segoe UI", 9), anchor="w")
        self.color_lbl.pack(fill="x", pady=(14, 6))
        self.crow = tk.Frame(ed)
        self.crow.pack(fill="x")
        self.presets_fr = tk.Frame(self.crow)
        self.presets_fr.pack(side="left")
        self.preset_dots = []
        for col in PRESETS:
            c = tk.Canvas(self.presets_fr, width=28, height=28, highlightthickness=0, bd=0, cursor="hand2")
            c.pack(side="left", padx=2)
            oid = c.create_oval(4, 4, 24, 24, width=2)
            c.bind("<Button-1>", lambda _e, hx=col: self._set_hex(hx))
            self.preset_dots.append((col, c, oid))
        self.hex_var = tk.StringVar()
        tools = tk.Frame(self.crow)
        tools.pack(side="right")
        self.color_tools = tools
        self.pick_btn = tk.Button(tools, text="Choisir…", command=self._pick,
                                  relief="flat", bd=0, cursor="hand2", font=("Segoe UI", 9))
        self.pick_btn.pack(side="left", padx=(12, 6), ipady=2)
        self.hex_entry = tk.Entry(tools, textvariable=self.hex_var, width=8,
                                  font=("Consolas", 11), relief="flat", justify="center")
        self.hex_entry.pack(side="left", ipady=4)
        self.hex_entry.bind("<Return>", self._commit_hex)
        self.hex_entry.bind("<FocusOut>", self._commit_hex)

        self.brow = tk.Frame(ed)
        self.brow.pack(fill="x", pady=(16, 0))
        self.bri_lbl = tk.Label(self.brow, text="Luminosité", font=("Segoe UI", 9), anchor="w")
        self.bri_lbl.pack(side="left")
        self.bri_val = tk.Label(self.brow, font=("Segoe UI", 10, "bold"), anchor="e")
        self.bri_val.pack(side="right")
        self.bri_bar = tk.Canvas(ed, height=28, highlightthickness=0, bd=0, cursor="hand2")
        self.bri_bar.pack(fill="x", pady=(4, 0))
        self.bri_bar.bind("<Button-1>", self._bri_click)
        self.bri_bar.bind("<B1-Motion>", self._bri_click)
        self.bri_bar.bind("<Configure>", lambda _e: self._paint_bri())

        self.lcd_panel = tk.Frame(ed)
        self.lcd_pick = tk.Button(self.lcd_panel, text="Choisir une image…",
                                  command=self._pick_lcd_image, relief="flat", bd=0,
                                  cursor="hand2", font=("Segoe UI", 10))
        self.lcd_pick.pack(anchor="w", ipady=4, ipadx=10)
        self.lcd_name = tk.Label(self.lcd_panel, font=("Segoe UI", 9), anchor="w")
        self.lcd_name.pack(fill="x", pady=(8, 0))
        self.lcd_size_row = tk.Frame(self.lcd_panel)
        self.lcd_size_row.pack(fill="x", pady=(14, 0))
        self.lcd_size_lbl = tk.Label(self.lcd_size_row, text="Taille", font=("Segoe UI", 9), anchor="w")
        self.lcd_size_lbl.pack(side="left")
        self.lcd_size_val = tk.Label(self.lcd_size_row, font=("Segoe UI", 10, "bold"), anchor="e")
        self.lcd_size_val.pack(side="right")
        self.lcd_scale_var = tk.IntVar(value=self.lcd_scale)
        self.lcd_scale_w = tk.Scale(
            self.lcd_panel, from_=25, to=200, orient="horizontal", showvalue=False,
            variable=self.lcd_scale_var, command=self._lcd_scale_moved, resolution=1,
            highlightthickness=0, bd=0, sliderlength=18)
        self.lcd_scale_w.pack(fill="x", pady=(4, 0))
        self.lcd_scale_w.bind("<ButtonRelease-1>", self._lcd_scale_commit)
        self.lcd_bri_row = tk.Frame(self.lcd_panel)
        self.lcd_bri_row.pack(fill="x", pady=(14, 0))
        self.lcd_bri_lbl = tk.Label(self.lcd_bri_row, text="Luminosité", font=("Segoe UI", 9), anchor="w")
        self.lcd_bri_lbl.pack(side="left")
        self.lcd_bri_val = tk.Label(self.lcd_bri_row, font=("Segoe UI", 10, "bold"), anchor="e")
        self.lcd_bri_val.pack(side="right")
        self.lcd_bri_var = tk.IntVar(value=self.lcd_brightness)
        self.lcd_bri_w = tk.Scale(
            self.lcd_panel, from_=0, to=100, orient="horizontal", showvalue=False,
            variable=self.lcd_bri_var, command=self._lcd_bri_moved, resolution=1,
            highlightthickness=0, bd=0, sliderlength=18)
        self.lcd_bri_w.pack(fill="x", pady=(4, 0))
        self.lcd_bri_w.bind("<ButtonRelease-1>", self._lcd_bri_commit)
        self.lcd_view = tk.Canvas(self.lcd_panel, width=240, height=240, highlightthickness=0, bd=0)
        self.lcd_view.pack(pady=(16, 0))

        self.fx = tk.Frame(ed)
        self.mode_lbl = tk.Label(self.fx, text="Mode", font=("Segoe UI", 9), anchor="w")
        self.mode_lbl.pack(fill="x", pady=(0, 6))
        self.mode_grid = tk.Frame(self.fx)
        self.mode_grid.pack(fill="x")
        self.mode_btns = {}
        for i, code in enumerate(effects.MODES):
            label = effects.MODE_LABELS[code]
            b = tk.Button(self.mode_grid, text=label, relief="flat", bd=0, cursor="hand2",
                          font=("Segoe UI", 9),
                          command=lambda c=code: self._set_mode(c))
            b.grid(row=i // 3, column=i % 3, sticky="ew", padx=3, pady=3, ipady=3)
            self.mode_btns[code] = b
        for col in range(3):
            self.mode_grid.columnconfigure(col, weight=1)
        self.speed_row = tk.Frame(self.fx)
        self.speed_row.pack(fill="x", pady=(10, 0))
        self.speed_lbl = tk.Label(self.speed_row, text="Vitesse", font=("Segoe UI", 9), anchor="w")
        self.speed_lbl.pack(side="left", padx=(0, 8))
        self.speed_btns = {}
        for n in range(1, 11):
            b = tk.Button(self.speed_row, text=str(n), width=2, relief="flat", bd=0, cursor="hand2",
                          font=("Segoe UI", 9), command=lambda v=n: self._set_speed(v))
            b.pack(side="left", expand=True, fill="x", padx=2, ipady=2)
            self.speed_btns[n] = b

        self.actions = tk.Frame(ed)
        self.actions.pack(fill="x", pady=(16, 0))
        self.apply_btn = tk.Button(self.actions, text="Appliquer", command=self.do_apply,
                                   relief="flat", bd=0, cursor="hand2",
                                   font=("Segoe UI", 11, "bold"))
        self.apply_btn.pack(side="left", expand=True, fill="x", ipady=8, padx=(0, 6))
        self.off_btn = tk.Button(self.actions, text="Éteindre", command=self.do_selected_off,
                                 relief="flat", bd=0, cursor="hand2", font=("Segoe UI", 11))
        self.off_btn.pack(side="left", expand=True, fill="x", ipady=8, padx=(6, 0))
        self.status = tk.Label(ed, font=("Segoe UI", 9), anchor="w", justify="left")
        self.status.pack(fill="x", pady=(12, 0))

        self._busy_widgets = [self.apply_btn, self.off_btn, self.btn_off_all, self.btn_stop]
        self._fx_shown = None

    def _paint_lcd(self, side_btn=None):
        if side_btn is None:
            side_btn = "#2a2d36" if self.theme_name == "dark" else "#f3f4f6"
        if self.lcd_on:
            self.btn_lcd.configure(text="Écran : temp. GPU", highlightthickness=0)
            self._style_accent(self.btn_lcd)
        else:
            self.btn_lcd.configure(text="Écran LCD")
            self._style_ghost(self.btn_lcd, side_btn)

    def _paint_lcd_widgets(self):
        p = self.pal
        self.lcd_size_row.configure(bg=p["bg"])
        self.lcd_size_lbl.configure(bg=p["bg"], fg=p["muted"])
        self.lcd_size_val.configure(bg=p["bg"], fg=p["text"], text=f"{self.lcd_scale} %")
        self.lcd_bri_row.configure(bg=p["bg"])
        self.lcd_bri_lbl.configure(bg=p["bg"], fg=p["muted"])
        self.lcd_bri_val.configure(bg=p["bg"], fg=p["text"], text=f"{self.lcd_brightness} %")
        self.lcd_name.configure(bg=p["bg"], fg=p["muted"])
        self.lcd_view.configure(bg=p["bg"])
        self._style_ghost(self.lcd_pick)
        trough = "#2a2d36" if self.theme_name == "dark" else "#e5e7eb"
        self.lcd_scale_w.configure(bg=p["bg"], fg=p["text"], troughcolor=trough,
                                   activebackground=p["accent"], highlightthickness=0)
        self.lcd_bri_w.configure(bg=p["bg"], fg=p["text"], troughcolor=trough,
                                 activebackground=p["accent"], highlightthickness=0)

    def _show_lcd_editor(self):
        for w in (self.preview, self.color_lbl, self.crow, self.brow, self.bri_bar, self.fx):
            w.pack_forget()
        self.lcd_panel.pack(fill="both", expand=True, before=self.actions)
        self.apply_btn.configure(text="Afficher")
        self.off_btn.configure(text="Fond enregistré")
        self._paint_lcd_widgets()
        self._paint_lcd_preview()

    def _show_led_editor(self):
        self.lcd_panel.pack_forget()
        self.preview.pack(fill="x", before=self.actions)
        self.color_lbl.pack(fill="x", pady=(14, 6), before=self.actions)
        self.crow.pack(fill="x", before=self.actions)
        self.brow.pack(fill="x", pady=(16, 0), before=self.actions)
        self.bri_bar.pack(fill="x", pady=(4, 0), before=self.actions)
        self.off_btn.configure(text="Éteindre")
        if self._fx_shown:
            self.fx.pack(fill="x", pady=(16, 0), before=self.actions)

    def _pick_lcd_image(self):
        path = filedialog.askopenfilename(
            parent=self, title="Image de l'écran",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.bmp *.gif"),
                       ("Tous les fichiers", "*.*")])
        if not path:
            return
        try:
            path = store_lcd_copy(path)
        except OSError as e:
            self.done(f"Copie impossible : {e}", "err")
            return
        self.lcd_image_path = path
        self._lcd_frames = None
        self._lcd_src_path = None
        self._lcd_gif_i = 0
        self._paint_lcd_preview()

    def _lcd_scale_moved(self, _v=None):
        try:
            self.lcd_scale = max(25, min(200, int(float(self.lcd_scale_var.get()))))
        except (TypeError, ValueError, tk.TclError):
            return
        try:
            self.lcd_size_val.configure(text=f"{self.lcd_scale} %")
        except tk.TclError:
            return
        if self.sel == "lcd":
            self._paint_lcd_preview()

    def _lcd_scale_commit(self, _e=None):
        if not self.lcd_image_on or not self.lcd_image_path:
            return
        try:
            save_config({"lcd_image_scale": self.lcd_scale})
        except RuntimeError as e:
            self.done(str(e), "err")

    def _lcd_bri_moved(self, _v=None):
        try:
            self.lcd_brightness = max(0, min(100, int(float(self.lcd_bri_var.get()))))
        except (TypeError, ValueError, tk.TclError):
            return
        try:
            self.lcd_bri_val.configure(text=f"{self.lcd_brightness} %")
        except tk.TclError:
            return

    def _lcd_bri_commit(self, _e=None):
        try:
            save_config({"lcd_brightness": int(self.lcd_brightness)})
        except RuntimeError as e:
            self.done(str(e), "err")

    def _paint_lcd_preview(self):
        from PIL import Image, ImageDraw, ImageTk
        import corsair_lcd
        size = 240
        try:
            br, bgc, bb = hex_to_rgb(self.pal["bg"])
        except (ValueError, IndexError):
            br, bgc, bb = 0, 0, 0
        path = self.lcd_image_path
        frame = None
        if path and os.path.isfile(path):
            try:
                mt = os.path.getmtime(path)
                if self._lcd_frames is None or self._lcd_src_path != (path, mt):
                    self._lcd_frames, self._lcd_durs = corsair_lcd.read_preview_frames(path)
                    self._lcd_gif_i = 0
                    self._lcd_src_path = (path, mt)
                src = self._lcd_frames[self._lcd_gif_i % len(self._lcd_frames)]
                frame = corsair_lcd.compose_image(src, self.lcd_scale, size)
            except Exception as e:  # noqa: BLE001
                self._lcd_frames = None
                self._lcd_src_path = None
                self.done(f"Image illisible : {e}", "err")
        if frame is None:
            frame = Image.new("RGB", (size, size), (br, bgc, bb))
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((1, 1, size - 2, size - 2), fill=255)
        rgba = frame.convert("RGBA")
        rgba.putalpha(mask)
        self._lcd_photo = ImageTk.PhotoImage(rgba)
        self.lcd_view.delete("all")
        self.lcd_view.create_image(size // 2, size // 2, image=self._lcd_photo)
        self.lcd_name.configure(text=os.path.basename(path) if path else "Aucune image")

    def _apply_lcd_image(self):
        path = self.lcd_image_path
        scale = int(self.lcd_scale)
        if not path or not os.path.isfile(path):
            self.done("Choisis une image.", "err")
            return

        def _fn():
            import corsair_keep
            try:
                stored = store_lcd_copy(path)
            except OSError as e:
                return (f"Copie impossible : {e}", None, "err")
            save_config({
                "lcd_image_path": stored,
                "lcd_image_scale": scale,
                "lcd_image_on": True,
                "lcd_temp": False,
            })
            if not corsair_keep.ensure_running():
                return ("Image enregistrée, fond non démarré", None, "err")

            def ui():
                self.lcd_image_path = stored
                self.lcd_image_on = True
                self.lcd_on = False
                self._paint_lcd()
                self._paint_nav()

            return ("Image affichée. HydroScreen et iCUE fermés.", ui, "ok")

        self.run_bg(_fn)

    def _off_lcd_image(self):
        def _fn():
            import corsair_keep
            save_config({"lcd_image_on": False})
            if not corsair_keep.is_running():
                import corsair_lcd
                corsair_lcd.release_to_hardware()

            def ui():
                self.lcd_image_on = False
                self._paint_nav()

            return ("Écran : retour au fond enregistré.", ui, "ok")

        self.run_bg(_fn)

    def _toggle_lcd(self):
        nxt = not self.lcd_on
        try:
            patch = {"lcd_temp": nxt}
            if nxt:
                patch["lcd_image_on"] = False
            save_config(patch)
        except RuntimeError as e:
            self.done(str(e), "err")
            return
        self.lcd_on = nxt
        if nxt:
            self.lcd_image_on = False
        self._paint_lcd()
        self._paint_nav()
        if not nxt:
            self.done("Écran : retour au fond enregistré.")

            def _off():
                import corsair_keep
                if corsair_keep.is_running():
                    return
                try:
                    import corsair_lcd
                    corsair_lcd.release_to_hardware()
                except Exception:  # noqa: BLE001
                    pass

            threading.Thread(target=_off, daemon=True).start()
            return
        self.done("Écran : envoi de la température…")

        def _t():
            import corsair_keep
            try:
                ok = bool(corsair_keep.ensure_running())
            except Exception:  # noqa: BLE001
                ok = False

            def ui():
                if ok:
                    self.done("Écran : température GPU. HydroScreen et iCUE fermés.")
                else:
                    self.done("Écran enregistré, fond non démarré", "err")

            try:
                self.after(0, ui)
            except tk.TclError:
                pass

        threading.Thread(target=_t, daemon=True).start()

    def _set_theme(self, name, persist=True):
        if name not in THEMES:
            return
        self.theme_name = name
        self.pal = THEMES[name]
        self._paint()
        if persist:
            try:
                save_config({"theme": name})
            except RuntimeError:
                pass

    def _paint(self):
        p = self.pal
        self.configure(bg=p["bg"])
        for w in (self.shell, self.editor, self.editor_in, self.actions, self.fx, self.lcd_panel):
            w.configure(bg=p["bg"])
        self.side.configure(bg=p["side"])
        self.side_in.configure(bg=p["side"])
        self.sep.configure(bg=p["line"])
        self.brand.configure(bg=p["side"], fg=p["text"])
        self.fond_fr.configure(bg=p["side"])
        self.theme_fr.configure(bg=p["side"])
        self.fond_dot.configure(bg=p["side"])
        self.fond_lbl.configure(bg=p["side"], fg=p["muted"])
        self._style_choice(self.btn_dark, self.theme_name == "dark")
        self._style_choice(self.btn_light, self.theme_name == "light")
        side_btn = "#2a2d36" if self.theme_name == "dark" else "#f3f4f6"
        self._style_ghost(self.btn_off_all, side_btn)
        self._style_ghost(self.btn_stop, side_btn)
        self._paint_lcd(side_btn)
        self.title_lbl.configure(bg=p["bg"], fg=p["text"])
        self.sub_lbl.configure(bg=p["bg"], fg=p["muted"])
        self.color_lbl.configure(bg=p["bg"], fg=p["muted"])
        self.crow.configure(bg=p["bg"])
        self.presets_fr.configure(bg=p["bg"])
        self.color_tools.configure(bg=p["bg"])
        self.brow.configure(bg=p["bg"])
        self.bri_lbl.configure(bg=p["bg"], fg=p["muted"])
        self.bri_val.configure(bg=p["bg"], fg=p["text"])
        self.mode_grid.configure(bg=p["bg"])
        self.speed_row.configure(bg=p["bg"])
        self.mode_lbl.configure(bg=p["bg"], fg=p["muted"])
        self.speed_lbl.configure(bg=p["bg"], fg=p["muted"])
        self.hex_entry.configure(bg=p["input"], fg=p["text"], insertbackground=p["text"],
                                 highlightthickness=1, highlightbackground=p["line"],
                                 highlightcolor=p["accent"])
        self.pick_btn.configure(bg=p["chip"], fg=p["text"], activebackground=p["line"],
                                activeforeground=p["text"])
        self._paint_bri()
        self._style_accent(self.apply_btn)
        self._style_ghost(self.off_btn)
        self.status.configure(bg=p["bg"], fg=self._status_color(), text=self._status_text)
        try:
            wrap = max(280, self.editor.winfo_width() - 56)
            self.sub_lbl.configure(wraplength=wrap)
            self.status.configure(wraplength=wrap)
        except tk.TclError:
            pass
        self._paint_nav()
        self._paint_presets()
        self._paint_modes()
        self._paint_speeds()
        self._paint_fond()
        self._paint_lcd_widgets()
        self._refresh_preview()
        if self.sel == "lcd":
            self._paint_lcd_preview()

    def _style_accent(self, btn):
        p = self.pal
        btn.configure(bg=p["accent"], fg=p["on_accent"],
                      activebackground=p["accent"], activeforeground=p["on_accent"])

    def _style_ghost(self, btn, bg=None):
        p = self.pal
        btn.configure(bg=bg or p["chip"], fg=p["text"],
                      activebackground=p["line"], activeforeground=p["text"],
                      highlightthickness=1, highlightbackground=p["line"],
                      highlightcolor=p["line"])

    def _style_choice(self, btn, on):
        p = self.pal
        if on:
            btn.configure(bg=p["accent"], fg=p["on_accent"],
                          activebackground=p["accent"], activeforeground=p["on_accent"])
        else:
            btn.configure(bg=p["chip"], fg=p["text"],
                          activebackground=p["line"], activeforeground=p["text"])

    def _style_chip(self, btn, on):
        p = self.pal
        if on:
            btn.configure(bg=p["accent"], fg=p["on_accent"],
                          activebackground=p["accent"], activeforeground=p["on_accent"],
                          highlightthickness=0)
        else:
            btn.configure(bg=p["chip"], fg=p["text"],
                          activebackground=p["line"], activeforeground=p["text"],
                          highlightthickness=1, highlightbackground=p["line"],
                          highlightcolor=p["line"])

    def _status_color(self):
        p = self.pal
        return {"ok": p["ok"], "err": p["err"]}.get(self._status_kind, p["muted"])

    def _shown_hex(self, key):
        d = self.dev[key]
        try:
            r, g, b = hex_to_rgb(d["hex"])
        except (ValueError, IndexError):
            return "#000000"
        bri = int(d["bri"])
        if key == "gpu":
            level = blackwell.level_from_percent(bri)
            if level <= 0:
                return "#000000"
            f = level / 10
        else:
            f = max(0, min(100, bri)) / 100
        return rgb_to_hex(round(r * f), round(g * f), round(b * f))

    def _paint_nav(self):
        p = self.pal
        for key, row in self.nav.items():
            on = key == self.sel
            bg = p["card_on"] if on else p["side"]
            row["frame"].configure(bg=bg)
            row["name"].configure(bg=bg, fg=p["text"])
            row["dot"].configure(bg=bg)
            row["dot"].delete("all")
            if key == "lcd":
                row["pct"].configure(bg=bg, fg=p["muted"], text="")
                fill = p["accent"] if self.lcd_image_on else p["line"]
                row["dot"].create_oval(4, 4, 18, 18, fill=fill, outline="")
                continue
            row["pct"].configure(bg=bg, fg=p["muted"], text=f"{int(self.dev[key]['bri'])} %")
            row["dot"].create_oval(4, 4, 18, 18, fill=self.dev[key]["hex"], outline="")

    def _paint_presets(self):
        p = self.pal
        cur = self.dev[self.sel]["hex"].lower()
        for col, canvas, oid in self.preset_dots:
            canvas.configure(bg=p["bg"])
            on = col.lower() == cur
            canvas.itemconfigure(oid, fill=col, outline=p["accent"] if on else p["bg"],
                                 width=3 if on else 0)

    def _paint_modes(self):
        cur = self.dev["corsair"]["mode"]
        for code, btn in self.mode_btns.items():
            self._style_chip(btn, code == cur)

    def _paint_speeds(self):
        cur = int(self.dev["corsair"]["speed"])
        for n, btn in self.speed_btns.items():
            self._style_chip(btn, n == cur)

    def _paint_fond(self):
        p = self.pal
        self.fond_dot.delete("all")
        color = p["ok"] if self._fond_on else p["muted"]
        self.fond_dot.create_oval(3, 3, 11, 11, fill=color, outline="")
        self.fond_lbl.configure(text="fond actif" if self._fond_on else "fond arrêté")

    def _refresh_preview(self):
        if self.sel == "lcd":
            return
        p = self.pal
        shown = self._shown_hex(self.sel)
        self.preview.configure(bg=shown, highlightbackground=p["line"])
        self.bri_val.configure(text=f"{int(self.dev[self.sel]['bri'])} %")

    def _sync_fx(self):
        if self._fx_shown:
            return
        self._fx_shown = True
        self.fx.pack(fill="x", pady=(16, 0), before=self.actions)

    def _tick_preview(self):
        try:
            if not self.winfo_exists():
                return
            if self.sel == "lcd":
                delay = 200
                frames = self._lcd_frames or []
                if len(frames) > 1:
                    self._lcd_gif_i = (self._lcd_gif_i + 1) % len(frames)
                    self._paint_lcd_preview()
                    dur = (self._lcd_durs or [None])[self._lcd_gif_i]
                    delay = 100 if not dur else max(40, min(int(dur), 500))
                self.after(delay, self._tick_preview)
                return
            mode = self.dev["corsair"]["mode"]
            if mode == "static":
                shown = self._shown_hex(self.sel)
            else:
                r, g, b, bri = self._rgb(self.sel)
                col = effects.render(
                    1, mode, time.monotonic() - self._preview_t0,
                    (r, g, b), bri, self.dev["corsair"]["speed"])[0]
                shown = rgb_to_hex(*col)
            self.preview.configure(bg=shown, highlightbackground=self.pal["line"])
        except tk.TclError:
            return
        self.after(200, self._tick_preview)

    def _load_editor(self):
        if self.sel == "lcd":
            self.title_lbl.configure(text="Image")
            self.sub_lbl.configure(
                text="Image ou GIF sur l'écran du Titan. 100 % couvre l'écran. iCUE et HydroScreen fermés.")
            self._show_lcd_editor()
            self._paint_nav()
            return
        self._show_led_editor()
        d = self.dev[self.sel]
        title, sub = TITLES[self.sel]
        self.title_lbl.configure(text=title)
        self.sub_lbl.configure(text=sub)
        self.apply_btn.configure(text=APPLY_LABELS[self.sel])
        self._loading = True
        try:
            self.hex_var.set(d["hex"])
        except tk.TclError:
            pass
        self._loading = False
        self._paint_bri()
        self._sync_fx()
        self._paint_nav()
        self._paint_presets()
        self._paint_modes()
        self._paint_speeds()
        self._refresh_preview()

    def _select(self, key):
        if self._busy or (key not in self.dev and key != "lcd"):
            return
        if self.sel in self.dev:
            self._commit_hex()
        self.sel = key
        self._load_editor()

    def _set_hex(self, hx):
        self.dev[self.sel]["hex"] = hx.lower()
        self.hex_var.set(hx.lower())
        self._paint_presets()
        self._paint_nav()
        self._refresh_preview()

    def _commit_hex(self, _e=None):
        if self.sel not in self.dev:
            return
        raw = self.hex_var.get().strip()
        if not raw.startswith("#"):
            raw = "#" + raw
        try:
            if len(raw) != 7:
                raise ValueError
            hex_to_rgb(raw)
        except (ValueError, IndexError):
            self.hex_var.set(self.dev[self.sel]["hex"])
            return
        self._set_hex(raw.lower())

    def _pick(self):
        _rgb, hx = colorchooser.askcolor(color=self.dev[self.sel]["hex"],
                                        parent=self, title="Choisir une couleur")
        if hx:
            self._set_hex(hx)

    _BRI_KNOB = 7

    def _bri_track(self):
        """Marge = rayon du curseur, pour qu'il ne soit pas coupe a 0 % et 100 %."""
        width = max(1, self.bri_bar.winfo_width())
        margin = self._BRI_KNOB + 1
        span = max(1, width - 2 * margin)
        return width, margin, span

    def _bri_click(self, event):
        if self._loading:
            return
        _width, margin, span = self._bri_track()
        bri = int(round(max(0.0, min(1.0, (event.x - margin) / span)) * 100))
        self.dev[self.sel]["bri"] = bri
        self._paint_bri()
        self._paint_nav()
        self._refresh_preview()

    def _paint_bri(self):
        if getattr(self, "_painting_bri", False):
            return
        self._painting_bri = True
        bar = self.bri_bar
        p = self.pal
        try:
            bar.configure(bg=p["bg"])
            bar.delete("all")
            width, margin, span = self._bri_track()
            if width < 2 * margin + 2:
                return
            bri = int(self.dev[self.sel]["bri"])
            x = margin + int(round(span * bri / 100))
            bar.create_rectangle(margin, 12, width - margin, 16, fill=p["chip"], outline="")
            if x > margin:
                bar.create_rectangle(margin, 12, x, 16, fill=p["accent"], outline="")
            r = self._BRI_KNOB
            knob = "#ffffff" if self.theme_name == "dark" else p["accent"]
            bar.create_oval(x - r, 5, x + r, 5 + 2 * r, fill=knob, outline=p["accent"])
        except tk.TclError:
            pass
        finally:
            self._painting_bri = False

    def _set_mode(self, code):
        if code not in effects.MODES:
            return
        self.dev["corsair"]["mode"] = code
        self._paint_modes()

    def _set_speed(self, n):
        self.dev["corsair"]["speed"] = max(1, min(10, int(n)))
        self._paint_speeds()

    def _assign(self, key, hx, bri, mode=None, speed=None):
        d = self.dev[key]
        d["hex"] = hx
        d["bri"] = int(bri)
        if mode is not None and key == "corsair":
            d["mode"] = mode
        if speed is not None and key == "corsair":
            d["speed"] = int(speed)

    def _rgb(self, key):
        d = self.dev[key]
        r, g, b = hex_to_rgb(d["hex"])
        return r, g, b, int(d["bri"])

    def _on_configure(self, event):
        if event.widget is not self:
            return
        try:
            wrap = max(280, event.width - 300)
            self.sub_lbl.configure(wraplength=wrap)
            self.status.configure(wraplength=wrap)
        except tk.TclError:
            pass
        if self._geo_after is not None:
            self.after_cancel(self._geo_after)
        self._geo_after = self.after(600, self._save_geo)

    def _save_geo(self):
        self._geo_after = None
        try:
            geo = self.geometry()
        except tk.TclError:
            return
        if geo == self._last_geo:
            return
        self._last_geo = geo
        try:
            save_config({"win_geo": geo})
        except RuntimeError:
            pass

    def busy(self, msg):
        self._status_kind = "info"
        self._status_text = msg
        self.status.configure(text=msg, fg=self._status_color())
        self.update_idletasks()

    def done(self, msg, kind="ok"):
        self._status_kind = kind
        self._status_text = msg
        self.status.configure(text=msg, fg=self._status_color())

    def _set_locked(self, locked):
        state = tk.DISABLED if locked else tk.NORMAL
        for w in self._busy_widgets:
            try:
                w.configure(state=state)
            except tk.TclError:
                pass

    def _clear_busy(self):
        self._busy = False
        self._set_locked(False)

    def run_bg(self, fn):
        if self._busy:
            return
        self._busy = True
        self._set_locked(True)
        self.busy("Application…")

        def _t():
            ui = None
            kind = "ok"
            try:
                result = fn()
                if isinstance(result, tuple):
                    if len(result) == 3:
                        msg, ui, kind = result
                    else:
                        msg, ui = result
                else:
                    msg = result
                err = None
            except Exception as e:  # noqa: BLE001
                msg, err = str(e), e

            def _finish():
                try:
                    if err is None and ui:
                        ui()
                    if err is None:
                        self.done(msg, kind)
                    else:
                        self.done(msg, "err")
                        messagebox.showerror("Lumino", msg, parent=self)
                except tk.TclError:
                    pass
                finally:
                    self._clear_busy()

            try:
                self.after(0, _finish)
            except tk.TclError:
                pass

        threading.Thread(target=_t, daemon=True).start()

    def _refresh_after_apply(self):
        self._load_editor()
        self._read_fond()

    def do_apply(self):
        if self.sel == "lcd":
            self._apply_lcd_image()
            return
        self._commit_hex()
        key = self.sel
        if key == "all":
            r, g, b, bri = self._rgb("all")
            mode, speed = self.dev["corsair"]["mode"], self.dev["corsair"]["speed"]
            self.run_bg(lambda: self._apply_all(r, g, b, bri, mode, speed))
        elif key == "gpu":
            self._apply_gpu(*self._rgb("gpu"))
        elif key == "cm":
            self._apply_cm(*self._rgb("cm"))
        elif key == "fans":
            r, g, b, bri = self._rgb("fans")
            self._apply_fans(r, g, b, bri, self.dev["corsair"]["mode"], self.dev["corsair"]["speed"])
        else:
            r, g, b, bri = self._rgb("corsair")
            self._apply_corsair(r, g, b, bri, self.dev["corsair"]["mode"], self.dev["corsair"]["speed"])

    def _apply_all(self, r, g, b, bri, mode, speed):
        import corsair_keep
        errors = []
        hx = rgb_to_hex(r, g, b)
        gpu_ok = cm_ok = co_ok = False
        prev = effect_mode_in_config()
        save_config({
            "r": r, "g": g, "b": b, "brightness": bri,
            "cm_last": {"r": r, "g": g, "b": b, "brightness": bri, "unscaled": True},
            "effect": saved_effect(mode, speed),
        })
        taken = hardware_taken_by_sync(prev, mode)
        if taken:
            _log(f"SYNC {mode} r={r} g={g} b={b} bri={bri} v={speed}")
            gpu_ok = cm_ok = True
        else:
            try:
                with corsair_keep.hw_hold():
                    det = gpu_apply(r, g, b, bri, do_save=True)
                _log(f"GPU r={r} g={g} b={b} bri={bri}")
                save_config({"save": True, "detail": det})
                gpu_ok = True
            except Exception as e:  # noqa: BLE001
                errors.append(f"GPU : {e}")
                _log(f"GPU erreur: {e}")
            try:
                with corsair_keep.hw_hold():
                    aura_apply(("cm",), r, g, b, bri)
                _log(f"AURA cm r={r} g={g} b={b} bri={bri}")
                cm_ok = True
            except Exception as e:  # noqa: BLE001
                errors.append(f"Carte mère : {e}")
                _log(f"AURA erreur: {e}")
            if mode != "static":
                errors.append("Effet non démarré, couleur statique envoyée")
        try:
            cres = corsair_apply(mode, speed, block=(r, g, b, bri), fans=(r, g, b, bri))
            _log(f"CORSAIR {mode} r={r} g={g} b={b} bri={bri} v={speed}")
            co_ok = True
            if cres.get("live_error"):
                errors.append(f"Corsair enregistré, direct : {cres['live_error']}")
            elif not cres.get("keepalive"):
                errors.append("Corsair enregistré, fond non démarré")
        except Exception as e:  # noqa: BLE001
            errors.append(f"Corsair : {e}")
            _log(f"CORSAIR erreur: {e}")
        if not (gpu_ok or cm_ok or co_ok):
            raise RuntimeError(" ".join(errors))

        def ui():
            self._assign("all", hx, bri)
            if gpu_ok:
                self._assign("gpu", hx, bri)
            if cm_ok:
                self._assign("cm", hx, bri)
            if co_ok:
                self._assign("corsair", hx, bri)
                self._assign("fans", hx, bri)
            self._refresh_after_apply()

        msg = f"Tout appliqué {r},{g},{b} à {bri} % ({effects.MODE_LABELS.get(mode, mode)})"
        if errors:
            msg += " — " + " ".join(errors)
        return msg, ui, ("err" if errors else "ok")

    def _apply_gpu(self, r, g, b, bri):
        hx = rgb_to_hex(r, g, b)
        mode, speed = self.dev["corsair"]["mode"], self.dev["corsair"]["speed"]

        def _fn():
            import corsair_keep
            prev = effect_mode_in_config()
            save_config({"r": r, "g": g, "b": b, "brightness": bri,
                         "effect": saved_effect(mode, speed)})
            taken = hardware_taken_by_sync(prev, mode)
            if not taken:
                with corsair_keep.hw_hold():
                    det = gpu_apply(r, g, b, bri, do_save=True)
                save_config({"save": True, "detail": det})
            _log(f"GPU r={r} g={g} b={b} bri={bri} mode={mode} fond={taken}")

            def ui():
                self._assign("gpu", hx, bri)
                self._refresh_after_apply()

            label = effects.MODE_LABELS.get(mode, mode)
            msg = f"GPU {label} à {bri} %"
            if mode != "static" and not taken:
                msg += " — fond non démarré"
                return msg, ui, "err"
            return msg, ui

        self.run_bg(_fn)

    def _apply_cm(self, r, g, b, bri):
        hx = rgb_to_hex(r, g, b)
        mode, speed = self.dev["corsair"]["mode"], self.dev["corsair"]["speed"]

        def _fn():
            import corsair_keep
            prev = effect_mode_in_config()
            res = {"r": r, "g": g, "b": b, "brightness": bri, "unscaled": True}
            save_config({"cm_last": res, "effect": saved_effect(mode, speed)})
            taken = hardware_taken_by_sync(prev, mode)
            if not taken:
                with corsair_keep.hw_hold():
                    aura_apply(("cm",), r, g, b, bri)
            _log(f"AURA cm r={r} g={g} b={b} bri={bri} mode={mode} fond={taken}")

            def ui():
                self._assign("cm", hx, bri)
                self._refresh_after_apply()

            label = effects.MODE_LABELS.get(mode, mode)
            msg = f"Carte mère {label} à {bri} %"
            if mode != "static" and not taken:
                msg += " — fond non démarré"
                return msg, ui, "err"
            return msg, ui

        self.run_bg(_fn)

    def _apply_corsair(self, r, g, b, bri, mode, speed):
        hx = rgb_to_hex(r, g, b)

        def _fn():
            res = corsair_apply(mode, speed, block=(r, g, b, bri))
            _log(f"CORSAIR bloc {mode} r={r} g={g} b={b} bri={bri} v={speed}")
            msg = f"Waterblock {effects.MODE_LABELS.get(mode, mode)} à {bri} %"
            kind = "ok"
            if res.get("live_error"):
                msg += f" — direct : {res['live_error']} (iCUE ouvert ?)"
                kind = "err"
            if not res.get("keepalive"):
                msg += " — fond non démarré"
                kind = "err"

            def ui():
                self._assign("corsair", hx, bri, mode, speed)
                self._refresh_after_apply()

            return msg, ui, kind

        self.run_bg(_fn)

    def _apply_fans(self, r, g, b, bri, mode, speed):
        hx = rgb_to_hex(r, g, b)

        def _fn():
            res = corsair_apply(mode, speed, fans=(r, g, b, bri))
            _log(f"CORSAIR fans {mode} r={r} g={g} b={b} bri={bri} v={speed}")
            msg = f"Ventilos {effects.MODE_LABELS.get(mode, mode)} à {bri} %"
            kind = "ok"
            if res.get("live_error"):
                msg += f" — direct : {res['live_error']} (iCUE ouvert ?)"
                kind = "err"
            if not res.get("keepalive"):
                msg += " — fond non démarré"
                kind = "err"

            def ui():
                self._assign("fans", hx, bri)
                self.dev["corsair"]["mode"] = mode
                self.dev["corsair"]["speed"] = speed
                self._refresh_after_apply()

            return msg, ui, kind

        self.run_bg(_fn)

    def do_selected_off(self):
        if self.sel == "lcd":
            self._off_lcd_image()
            return
        if self.sel == "all":
            self.do_off()
            return
        self._commit_hex()
        if self.sel == "gpu":
            self._off_gpu()
        elif self.sel == "cm":
            self._off_cm()
        elif self.sel == "fans":
            self._off_fans()
        else:
            self._off_corsair()

    def _off_gpu(self):
        r, g, b, _bri = self._rgb("gpu")
        hx = self.dev["gpu"]["hex"]
        mode, speed = self.dev["corsair"]["mode"], self.dev["corsair"]["speed"]

        def _fn():
            import corsair_keep
            prev = effect_mode_in_config()
            save_config({"r": r, "g": g, "b": b, "brightness": 0,
                         "effect": saved_effect(mode, speed)})
            if not hardware_taken_by_sync(prev, mode):
                with corsair_keep.hw_hold():
                    det = gpu_apply(r, g, b, 0, do_save=True)
                save_config({"save": True, "detail": det})
            _log(f"GPU off teinte={r},{g},{b}")

            def ui():
                self._assign("gpu", hx, 0)
                self._refresh_after_apply()

            return "GPU éteint", ui

        self.run_bg(_fn)

    def _off_cm(self):
        r, g, b, _bri = self._rgb("cm")
        hx = self.dev["cm"]["hex"]
        mode, speed = self.dev["corsair"]["mode"], self.dev["corsair"]["speed"]

        def _fn():
            import corsair_keep
            prev = effect_mode_in_config()
            res = {"r": r, "g": g, "b": b, "brightness": 0, "unscaled": True}
            save_config({"cm_last": res, "effect": saved_effect(mode, speed)})
            if not hardware_taken_by_sync(prev, mode):
                with corsair_keep.hw_hold():
                    aura_apply(("cm",), r, g, b, 0)
            _log("AURA cm off")

            def ui():
                self._assign("cm", hx, 0)
                self._refresh_after_apply()

            return "Carte mère éteinte", ui

        self.run_bg(_fn)

    def _off_corsair(self):
        r, g, b, _bri = self._rgb("corsair")
        hx = self.dev["corsair"]["hex"]
        mode, speed = self.dev["corsair"]["mode"], self.dev["corsair"]["speed"]

        def _fn():
            res = corsair_apply(mode, speed, block=(r, g, b, 0))
            _log(f"CORSAIR bloc off mode={mode}")
            msg, kind = "Waterblock éteint", "ok"
            if res.get("live_error"):
                msg += f" — {res['live_error']}"
                kind = "err"
            if not res.get("keepalive"):
                msg += " — fond non démarré"
                kind = "err"

            def ui():
                self._assign("corsair", hx, 0, mode, speed)
                self._refresh_after_apply()

            return msg, ui, kind

        self.run_bg(_fn)

    def _off_fans(self):
        r, g, b, _bri = self._rgb("fans")
        hx = self.dev["fans"]["hex"]
        mode, speed = self.dev["corsair"]["mode"], self.dev["corsair"]["speed"]

        def _fn():
            res = corsair_apply(mode, speed, fans=(r, g, b, 0))
            _log(f"CORSAIR fans off mode={mode}")
            msg, kind = "Ventilos éteints", "ok"
            if res.get("live_error"):
                msg += f" — {res['live_error']}"
                kind = "err"
            if not res.get("keepalive"):
                msg += " — fond non démarré"
                kind = "err"

            def ui():
                self._assign("fans", hx, 0)
                self._refresh_after_apply()

            return msg, ui, kind

        self.run_bg(_fn)

    def do_off(self):
        if self._busy:
            return
        if not messagebox.askyesno(
                "Lumino",
                "Éteindre le GPU, la carte mère, le waterblock et les ventilos ?\n\n"
                "Les couleurs et le mode restent en mémoire, "
                "la luminosité passe à 0. Au prochain démarrage les LEDs "
                "resteront éteintes tant que tu n'auras pas réappliqué.",
                parent=self):
            return
        self._commit_hex()
        gpu_r, gpu_g, gpu_b, _b = self._rgb("gpu")
        cm_r, cm_g, cm_b, _b = self._rgb("cm")
        co_r, co_g, co_b, _b = self._rgb("corsair")
        fan_r, fan_g, fan_b, _b = self._rgb("fans")
        mode, speed = self.dev["corsair"]["mode"], self.dev["corsair"]["speed"]
        gpu_hx, cm_hx = self.dev["gpu"]["hex"], self.dev["cm"]["hex"]
        co_hx, all_hx = self.dev["corsair"]["hex"], self.dev["all"]["hex"]
        fan_hx = self.dev["fans"]["hex"]
        self.run_bg(lambda: self._all_off(
            gpu_r, gpu_g, gpu_b, gpu_hx, cm_r, cm_g, cm_b, cm_hx,
            co_r, co_g, co_b, co_hx, fan_r, fan_g, fan_b, fan_hx,
            mode, speed, all_hx))

    def _all_off(self, gpu_r, gpu_g, gpu_b, gpu_hx, cm_r, cm_g, cm_b, cm_hx,
                 co_r, co_g, co_b, co_hx, fan_r, fan_g, fan_b, fan_hx,
                 mode, speed, all_hx):
        import corsair_keep
        errors = []
        gpu_ok = cm_ok = co_ok = False
        prev = effect_mode_in_config()
        save_config({
            "r": gpu_r, "g": gpu_g, "b": gpu_b, "brightness": 0,
            "cm_last": {"r": cm_r, "g": cm_g, "b": cm_b, "brightness": 0, "unscaled": True},
            "effect": saved_effect(mode, speed),
        })
        if hardware_taken_by_sync(prev, mode):
            _log("OFF gpu+cm via fond")
            gpu_ok = cm_ok = True
        else:
            try:
                with corsair_keep.hw_hold():
                    det = gpu_apply(gpu_r, gpu_g, gpu_b, 0, do_save=True)
                save_config({"save": True, "detail": det})
                _log("OFF gpu")
                gpu_ok = True
            except Exception as e:  # noqa: BLE001
                errors.append(f"GPU : {e}")
                _log(f"OFF gpu erreur: {e}")
            try:
                with corsair_keep.hw_hold():
                    aura_apply(("cm",), cm_r, cm_g, cm_b, 0)
                _log("OFF cm")
                cm_ok = True
            except Exception as e:  # noqa: BLE001
                errors.append(f"Carte mère : {e}")
                _log(f"OFF cm erreur: {e}")
        try:
            cres = corsair_apply(mode, speed, block=(co_r, co_g, co_b, 0),
                                 fans=(fan_r, fan_g, fan_b, 0))
            _log("OFF corsair")
            co_ok = True
            if cres.get("live_error"):
                errors.append(f"Corsair : {cres['live_error']}")
            elif not cres.get("keepalive"):
                errors.append("Corsair : fond non démarré")
        except Exception as e:  # noqa: BLE001
            errors.append(f"Corsair : {e}")
            _log(f"OFF corsair erreur: {e}")
        if not (gpu_ok or cm_ok or co_ok):
            raise RuntimeError(" ".join(errors))

        def ui():
            if gpu_ok:
                self._assign("gpu", gpu_hx, 0)
            if cm_ok:
                self._assign("cm", cm_hx, 0)
            if co_ok:
                self._assign("corsair", co_hx, 0, mode, speed)
                self._assign("fans", fan_hx, 0)
            if gpu_ok and cm_ok and co_ok:
                self._assign("all", all_hx, 0)
            self._refresh_after_apply()

        if gpu_ok and cm_ok and co_ok and not errors:
            return "Tout éteint. Couleurs gardées, luminosité à 0.", ui, "ok"
        return "Éteint en partie — " + " ".join(errors), ui, "err"

    def do_stop_corsair(self):
        if self._busy:
            return
        if not messagebox.askyesno(
                "Lumino",
                "Arrêter le fond ?\n\n"
                "Le GPU et la carte mère reviennent à leur couleur enregistrée. "
                "Le hub Corsair repasse sur les couleurs iCUE (Device Memory) "
                "tant que Lumino ne réapplique pas.",
                parent=self):
            return

        def _fn():
            import corsair_keep
            stopped = corsair_keep.stop_running()
            msg = "Fond arrêté" if stopped else "Aucun fond en cours"

            def ui():
                self._read_fond()

            return msg, ui, "info"

        self.run_bg(_fn)

    def _read_fond(self):
        try:
            import corsair_keep
            self._fond_on = bool(corsair_keep.is_running())
        except Exception:
            self._fond_on = False
        try:
            self._paint_fond()
        except tk.TclError:
            pass

    def _poll_fond(self):
        try:
            if not int(self.winfo_exists()):
                return
        except tk.TclError:
            return
        self._read_fond()
        try:
            self.after(2000, self._poll_fond)
        except tk.TclError:
            pass


def main():
    if "--boot" in sys.argv:
        sys.argv.remove("--boot")
        import apply_boot
        apply_boot.main()
        return
    if "--corsair-keepalive" in sys.argv:
        import corsair_keep
        corsair_keep.keepalive_loop()
        return
    if "--corsair-stop" in sys.argv:
        import corsair_keep
        print("stop:", corsair_keep.stop_running())
        return
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
