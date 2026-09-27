"""Lumino - app bureau (sans serveur, sans tache de fond).
Double-clic : choisis la couleur -> Appliquer -> l'app se ferme.
La config est sauvegardee dans config.json et re-appliquee UNE fois
au demarrage Windows par apply_boot.py (aucun processus resident).

Lance :  python lumino_gui.py
"""
import json
import os
import re
import sys
import threading
import time
import tkinter as tk
from tkinter import colorchooser, messagebox, ttk

BASE = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False) \
    else os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
os.chdir(BASE)

CONFIG_PATH = os.path.join(BASE, "config.json")
LOG_PATH = os.path.join(BASE, "lumino.log")

PRESETS = [
    "#ff0000", "#ff7f00", "#ffd60a", "#22c55e",
    "#00d5ff", "#2b6cff", "#7c3aed", "#ff2fb3",
    "#ffffff", "#111827",
]


def _log(msg):
    try:
        with open(LOG_PATH, "a") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " " + msg + "\n")
    except OSError:
        pass


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
            return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


_CFG_LOCK = threading.Lock()


def save_config(patch):
    with _CFG_LOCK:
        cfg = load_config()
        cfg.update(patch)
        try:
            with open(CONFIG_PATH, "w") as f:
                json.dump(cfg, f, indent=2)
        except OSError as e:
            raise RuntimeError(f"config.json inscriptible : {e}")


def _valid_geo(geo):
    """Valide 'LxH' ou 'LxH+X+Y' (evite de restaurer une geometrie absurde)."""
    if not isinstance(geo, str):
        return False
    m = re.fullmatch(r"(\d{3,4})x(\d{3,4})([+-]\d+[+-]\d+)?", geo)
    if not m:
        return False
    return 300 <= int(m.group(1)) <= 1600 and 400 <= int(m.group(2)) <= 1600


def hex_to_rgb(h):
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_hex(r, g, b):
    return "#%02x%02x%02x" % (
        max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))


# --- backends directs (pas de serveur) ---------------------------------

def gpu_apply(r, g, b, brightness100, do_save=True):
    """Applique au GPU RTX 5080 (Blackwell 0x75). Bloquant ~1s."""
    from nvapi import NvAPI
    import blackwell
    nv = NvAPI()
    nv.initialize()
    gpus = nv.enum_gpus()
    if not gpus:
        raise RuntimeError("Aucun GPU NVIDIA trouve")
    h = gpus[0]
    ok, detail = blackwell.probe(nv, h, port=1)
    if not ok:
        raise RuntimeError(f"Controleur LED 0x75 non detecte ({detail}). Redemarre le PC si besoin.")
    bri10 = max(1, min(10, round(int(brightness100) / 10)))
    # 3 essais (le controleur se coince parfois apres un reboot/driver)
    last = None
    for _ in range(3):
        try:
            return blackwell.apply_static(nv, h, r, g, b, bri10, do_save)
        except RuntimeError as e:
            last = e
            time.sleep(0.5)
    raise last


def aura_apply(parts, r, g, b, brightness100):
    """Applique a la CM (static persiste) et/ou WC (direct volatile)."""
    import aura
    bri = max(0, min(100, int(brightness100)))
    rs, gs, bs = (c * bri // 100 for c in (r, g, b))
    a = aura.Aura()
    try:
        if set(parts) == {"cm", "wc"}:
            a.set_both(rs, gs, bs)
        elif parts == ("cm",):
            a.set_cm(rs, gs, bs)
        else:
            a.set_wc(rs, gs, bs)
    finally:
        a.close()
    return {"r": rs, "g": gs, "b": bs, "brightness": bri}


def gpu_off():
    return gpu_apply(0, 0, 0, 10, do_save=True)


def scan_all():
    """Detection lecture seule (ne change aucune couleur)."""
    out = {}
    try:
        from nvapi import NvAPI
        import blackwell
        nv = NvAPI()
        nv.initialize()
        gpus = nv.enum_gpus()
        if gpus:
            ok, detail = blackwell.probe(nv, gpus[0], port=1)
            out["gpu"] = {"name": nv.get_name(gpus[0]), "ok": ok, "detail": detail}
        else:
            out["gpu"] = {"ok": False, "error": "aucun GPU"}
    except Exception as e:  # noqa: BLE001
        out["gpu"] = {"ok": False, "error": str(e)}
    try:
        import aura
        a = aura.Aura()
        try:
            out["aura"] = {"ok": True, **a.describe()}
        finally:
            a.close()
    except Exception as e:  # noqa: BLE001
        out["aura"] = {"ok": False, "error": str(e)}
    return out


# --- UI -----------------------------------------------------------------

class LedBlock(ttk.LabelFrame):
    """Un bloc couleur + luminosite + apercu + presets + boutons."""

    def __init__(self, parent, title, desc, default_hex="#00aaff", default_bri=100,
                 apply_label="Appliquer", on_apply=None):
        super().__init__(parent, text=title, padding=12)
        self.on_apply = on_apply
        self.hexvar = tk.StringVar(value=default_hex)
        self.brivar = tk.IntVar(value=default_bri)

        ttk.Label(self, text=desc, foreground="#6b7280", font=("Segoe UI", 8)).pack(anchor="w")

        top = ttk.Frame(self)
        top.pack(fill="x", pady=(6, 0))
        self.swatch = tk.Button(top, text="Choisir…", width=10, command=self.pick)
        self.swatch.pack(side="left")
        self.hexlbl = ttk.Label(top, text=default_hex, font=("Consolas", 10, "bold"))
        self.hexlbl.pack(side="left", padx=8)

        mid = ttk.Frame(self)
        mid.pack(fill="x", pady=(6, 0))
        ttk.Label(mid, text="Luminosite").pack(side="left")
        self.brilbl = ttk.Label(mid, text=f"{default_bri} %", font=("Segoe UI", 9, "bold"))
        self.brilbl.pack(side="right")
        self.scale = ttk.Scale(self, from_=0, to=100, orient="horizontal",
                               command=lambda _e: self.refresh())
        self.scale.pack(fill="x")

        self.prev = tk.Canvas(self, height=28, highlightthickness=1,
                              highlightbackground="#e5e7eb")
        self.prev.pack(fill="x", pady=(6, 0))

        presets = ttk.Frame(self)
        presets.pack(fill="x", pady=(6, 0))
        for c in PRESETS:
            b = tk.Label(presets, bg=c, width=3, relief="solid", borderwidth=1, cursor="hand2")
            b.pack(side="left", padx=2)
            b.bind("<Button-1>", lambda _e, col=c: self.set_hex(col))

        self.btn = ttk.Button(self, text=apply_label, command=self.submit)
        self.btn.pack(fill="x", pady=(8, 0))
        self.scale.set(default_bri)  # apres creation de l'apercu (le callback appelle refresh)
        self.refresh()

    def set_hex(self, h):
        self.hexvar.set(h)
        self.refresh()

    def set_values(self, h, bri):
        """Restaure teinte + pourcentage (ex : au lancement, ou synchro)."""
        self.hexvar.set(h)
        try:
            self.scale.set(max(0, min(100, int(bri))))
        except (ValueError, tk.TclError):
            pass
        self.refresh()

    def pick(self):
        _r, hx = colorchooser.askcolor(color=self.hexvar.get(), title="Choisir une couleur")
        if hx:
            self.set_hex(hx)

    def refresh(self):
        try:
            bri = int(float(self.scale.get()))
        except (ValueError, tk.TclError):
            bri = self.brivar.get()
        self.brivar.set(bri)
        hx = self.hexvar.get()
        self.hexlbl.config(text=hx)
        self.brilbl.config(text=f"{bri} %")
        try:
            r, g, b = hex_to_rgb(hx)
            f = bri / 100  # l'apercu montre le vrai niveau envoye aux LEDs
            shown = rgb_to_hex(round(r * f), round(g * f), round(b * f))
            self.prev.delete("all")
            self.prev.create_rectangle(0, 0, 2000, 30, fill=shown, outline="")
            self.prev.configure(bg=shown)
            self.swatch.configure(bg=hx, activebackground=hx)
        except ValueError:
            pass

    def values(self):
        r, g, b = hex_to_rgb(self.hexvar.get())
        return r, g, b, int(self.brivar.get())

    def submit(self):
        if self.on_apply:
            self.on_apply(self)


class App(tk.Tk):
    DEFAULT_GEO = "430x860"

    def __init__(self):
        super().__init__()
        self.title("Lumino — RGB local (sans fond)")
        self.resizable(True, True)
        self.minsize(360, 520)
        self._geo_after = None
        self._last_geo = None
        try:
            ico = os.path.join(BASE, "lumino.ico")
            if os.path.isfile(ico):
                self.iconbitmap(default=ico)
        except tk.TclError:
            pass

        cfg = load_config()
        last = cfg if "r" in cfg else {}
        cm = cfg.get("cm_last") or {}
        wc = cfg.get("wc_last") or {}

        def hx(d, fb):
            try:
                return rgb_to_hex(int(d["r"]), int(d["g"]), int(d["b"]))
            except (KeyError, TypeError, ValueError):
                return fb

        def hx_dim(d, fb):
            """cm_last/wc_last stockent du RGB deja attenue par le % :
            on inverse l'attenuation pour retrouver la teinte choisie
            (evite la double-attenuation a chaque re-application)."""
            try:
                bri = int(d.get("brightness", 100))
                if bri > 0:
                    return rgb_to_hex(*(
                        min(255, round(int(d[k]) * 100 / bri)) for k in ("r", "g", "b")))
                return hx(d, fb)
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                return fb

        style = ttk.Style(self)
        for theme in ("vista", "xpnative", "clam"):
            try:
                style.theme_use(theme)
                break
            except tk.TclError:
                pass
        self._style_bg = style.lookup("TFrame", "background")

        # --- restaure la taille/position memorisees (config.json : win_geo) ---
        geo = cfg.get("win_geo")
        self.geometry(geo if _valid_geo(geo) else self.DEFAULT_GEO)
        self.update_idletasks()
        self._last_geo = self.geometry()
        self.bind("<Configure>", self._on_configure)

        header = ttk.Frame(self, padding=(14, 10, 14, 0))
        header.pack(fill="x")
        ttk.Label(header, text="Lumino", font=("Segoe UI", 15, "bold")).pack(side="left")
        self.status = ttk.Label(header, text="Pret", foreground="#6b7280", font=("Segoe UI", 9))
        self.status.pack(side="right")
        ttk.Label(self, text="Applique puis ferme — rien ne tourne en fond. Re-applique au demarrage Windows.",
                  foreground="#6b7280", font=("Segoe UI", 8), padding=(14, 0, 14, 6)).pack(fill="x")

        # --- bas fixe (toujours visible, hors scroll) ---
        bottom = ttk.Frame(self)
        bottom.pack(side="bottom", fill="x")
        bar = ttk.Frame(bottom, padding=(14, 0, 14, 0))
        bar.pack(fill="x")
        ttk.Button(bar, text="Scanner (detection)", command=self.do_scan).pack(side="left", expand=True, fill="x", padx=(0, 4))
        ttk.Button(bar, text="Tout eteindre", command=self.do_off).pack(side="left", expand=True, fill="x", padx=(4, 0))
        ttk.Label(bottom, text="config.json + apply_boot.py au demarrage — aucun serveur.",
                  foreground="#9ca3af", font=("Segoe UI", 8),
                  padding=(0, 6, 0, 10)).pack()

        # --- zone centrale scrollable (molette + barre verticale) ---
        scroll_wrap = ttk.Frame(self)
        scroll_wrap.pack(side="top", fill="both", expand=True)
        self.canvas = tk.Canvas(scroll_wrap, highlightthickness=0,
                                bg=self._style_bg or None)
        vbar = ttk.Scrollbar(scroll_wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vbar.set)
        vbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        body = ttk.Frame(self.canvas, padding=(14, 0, 14, 14))
        self._body_win = self.canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>",
                  lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure(self._body_win, width=e.width))
        self._bind_mousewheel()

        self.blk_all = LedBlock(body, "Tout synchroniser", "GPU + carte mere + watercooling",
                                default_hex=hx(last, "#00aaff"),
                                default_bri=int(last.get("brightness", 100)),
                                apply_label="Tout appliquer", on_apply=self.do_all)
        self.blk_all.pack(fill="x", pady=4)

        self.blk_gpu = LedBlock(body, "Carte graphique (RTX 5080)", "Sauvegarde sur le GPU (persiste)",
                                default_hex=hx(last, "#00aaff"),
                                default_bri=int(last.get("brightness", 100)),
                                apply_label="Appliquer GPU", on_apply=self.do_gpu)
        self.blk_gpu.pack(fill="x", pady=4)

        self.blk_cm = LedBlock(body, "Carte mere (ASUS Aura)", "Static persiste au reboot",
                               default_hex=hx_dim(cm, "#00aaff"),
                               default_bri=int(cm.get("brightness", 100)),
                               apply_label="Appliquer CM", on_apply=self.do_cm)
        self.blk_cm.pack(fill="x", pady=4)

        self.blk_wc = LedBlock(body, "Watercooling (ARGB 1)", "Direct — re-applique au demarrage",
                               default_hex=hx_dim(wc, "#00aaff"),
                               default_bri=int(wc.get("brightness", 100)),
                               apply_label="Appliquer WC", on_apply=self.do_wc)
        self.blk_wc.pack(fill="x", pady=4)

    # -- taille/position memorisees + molette -------------------------------
    def _on_configure(self, event):
        if event.widget is not self:
            return
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

    def _bind_mousewheel(self):
        def _scroll(e):
            self.canvas.yview_scroll(-1 * (e.delta // 120), "units")

        def _on(_e):
            self.canvas.bind_all("<MouseWheel>", _scroll)

        def _off(_e):
            self.canvas.unbind_all("<MouseWheel>")

        self.canvas.bind("<Enter>", _on)
        self.canvas.bind("<Leave>", _off)

    # -- helpers threadés -------------------------------------------------

    # -- helpers threadés -------------------------------------------------
    def busy(self, msg):
        self.status.config(text=msg)
        self.update_idletasks()

    def done(self, msg):
        self.status.config(text=msg)

    def run_bg(self, fn):
        for w in (self.blk_all.btn, self.blk_gpu.btn, self.blk_cm.btn, self.blk_wc.btn):
            w.state(["disabled"])
        self.busy("Application…")

        def _t():
            try:
                msg = fn()
                self.after(0, lambda: (self.done(msg), messagebox.showinfo("Lumino", msg)))
            except Exception as e:  # noqa: BLE001
                self.after(0, lambda: (self.done("Erreur"), messagebox.showerror("Lumino", str(e))))
            finally:
                self.after(0, lambda: [w.state(["!disabled"]) for w in
                                       (self.blk_all.btn, self.blk_gpu.btn, self.blk_cm.btn, self.blk_wc.btn)])
        threading.Thread(target=_t, daemon=True).start()

    # -- actions ----------------------------------------------------------
    def do_all(self, blk):
        r, g, b, bri = blk.values()
        self.run_bg(lambda: self._apply_all(r, g, b, bri))

    def _apply_all(self, r, g, b, bri):
        det = gpu_apply(r, g, b, bri, do_save=True)
        _log(f"GPU r={r} g={g} b={b} bri={bri}")
        save_config({"r": r, "g": g, "b": b, "brightness": bri, "save": True, "detail": det})
        try:
            res = aura_apply(("cm", "wc"), r, g, b, bri)
            _log(f"AURA cm,wc r={res['r']} g={res['g']} b={res['b']} bri={bri}")
            save_config({"cm_last": res, "wc_last": dict(res)})
        except Exception as e:  # noqa: BLE001
            return f"GPU OK, Aura partiel : {e}"
        for blk in (self.blk_gpu, self.blk_cm, self.blk_wc):
            self.after(0, lambda b=blk: b.set_values(rgb_to_hex(r, g, b), bri))
        return f"Tout applique {r},{g},{b} a {bri} %"

    def do_gpu(self, blk):
        r, g, b, bri = blk.values()
        def _fn():
            det = gpu_apply(r, g, b, bri, do_save=True)
            _log(f"GPU r={r} g={g} b={b} bri={bri}")
            save_config({"r": r, "g": g, "b": b, "brightness": bri, "save": True, "detail": det})
            return f"GPU applique {r},{g},{b} a {bri} %"
        self.run_bg(_fn)

    def _aura_one(self, part, blk):
        r, g, b, bri = blk.values()
        def _fn():
            res = aura_apply((part,), r, g, b, bri)
            _log(f"AURA {part} r={res['r']} g={res['g']} b={res['b']} bri={bri}")
            save_config({part + "_last": res})
            return f"{part.upper()} applique a {bri} %"
        self.run_bg(_fn)

    def do_cm(self, blk):
        self._aura_one("cm", blk)

    def do_wc(self, blk):
        self._aura_one("wc", blk)

    def do_off(self):
        self.run_bg(self._all_off)

    def _all_off(self):
        try:
            gpu_off()
            _log("GPU r=0 g=0 b=0 bri=10")
        except Exception as e:  # noqa: BLE001
            _log(f"OFF gpu erreur: {e}")
        try:
            import aura
            a = aura.Aura()
            try:
                a.set_cm(0, 0, 0)
                a.set_wc(0, 0, 0)
            finally:
                a.close()
            off = {"r": 0, "g": 0, "b": 0, "brightness": 0}
            save_config({"cm_last": dict(off), "wc_last": dict(off)})
            _log("ALL_OFF gpu+cm+wc")
        except Exception as e:  # noqa: BLE001
            return f"GPU eteint, Aura : {e}"
        save_config({"r": 0, "g": 0, "b": 0, "brightness": 10, "save": True})
        return "Tout eteint"

    def do_scan(self):
        self.busy("Scan…")

        def _t():
            res = scan_all()
            g = res.get("gpu", {})
            au = res.get("aura", {})
            if g.get("ok"):
                gs = f"GPU OK ({g.get('name','')} 0x75 reply={g.get('detail',{}).get('reply')})"
            else:
                gs = f"GPU KO : {g.get('error', g.get('detail'))}"
            if au.get("ok"):
                aus = f"Aura OK fw={au.get('fw')} fixe={au.get('cfg',{}).get('fixed_leds')}"
            else:
                aus = f"Aura KO : {au.get('error')}"
            self.after(0, lambda: (self.done("Scan termine"),
                                   messagebox.showinfo("Lumino — scan", gs + "\n" + aus)))
        threading.Thread(target=_t, daemon=True).start()


def main():
    # Mode boot (exe --boot) : applique config.json puis quitte, sans fenetre.
    if "--boot" in sys.argv:
        sys.argv.remove("--boot")
        import apply_boot
        apply_boot.main()
        return
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
