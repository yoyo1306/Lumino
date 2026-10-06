"""
Lumino - keepalive Hub iCUE LINK (waterblock et 2x RX, couleurs separees).
Le Hub retombe en couleurs hardware des qu'on arrete de lui parler :
ce worker renvoie la couleur toutes les N secondes, sans fenetre.
Le meme processus joue l'effet sur le GPU et la carte mere (meme horloge).

- Lit config.json a chaque changement du fichier.
  L'UI change la config, le fond suit au cycle suivant.
  Format actuel : teinte brute + brightness 0-100 ("unscaled": true).
  Ancien format : RGB deja attenue, brightness ignore (traite comme 100).
  L'effet partage est "effect" {mode, speed}, sinon celui de corsair_last.
- Verrou + heartbeat : BASE/corsair_keepalive.lock (pid + timestamp).
- iCUE doit rester ferme (sinon "device busy", on reessaie en boucle).
- LCD ecran : gere a part (image hardware figeante / HydroScreen).
- Refroidissement : non touche (endpoint couleurs 0x22 uniquement),
  la courbe ventilos/pompe reste celle du profil hardware du Hub.
  Regler une fois une courbe + couleur de base en iCUE Device Memory
  Mode AVANT de fermer iCUE (securite + fallback au boot).
"""
import json
import os
import sys
import threading
import time
from contextlib import contextmanager

import effects

BASE = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False) \
    else os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

CONFIG_PATH = os.path.join(BASE, "config.json")
LOG_PATH = os.path.join(BASE, "lumino.log")
LOCK_PATH = os.path.join(BASE, "corsair_keepalive.lock")
IDLE_PATH = os.path.join(BASE, "sync_idle.txt")
HW_LOCK = os.path.join(BASE, "hw_apply.lock")
STOP_PATH = os.path.join(BASE, "corsair_keepalive.stop")

INTERVAL = 4
STALE_AFTER = 15


def _log(msg):
    try:
        with open(LOG_PATH, "a") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " " + msg + "\n")
    except OSError:
        pass


def _pid_alive(pid):
    # os.kill(pid, 0) leve SystemError sur Windows quand le PID est mort
    # ("OSError returned a result with an exception set") : passe par
    # l'API Win32 directement.
    if os.name == "nt":
        try:
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if h:
                ctypes.windll.kernel32.CloseHandle(h)
                return True
            return False
        except OSError:
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def read_lock():
    try:
        with open(LOCK_PATH) as f:
            pid_s, ts_s = f.read().strip().split(":")
        return int(pid_s), float(ts_s)
    except (OSError, ValueError):
        return None, None


def is_running():
    pid, ts = read_lock()
    if pid is None:
        return False
    if time.time() - ts > STALE_AFTER:
        return False
    return _pid_alive(pid)


def _terminate(pid):
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(1, False, pid)  # PROCESS_TERMINATE
        if not h:
            return False
        ctypes.windll.kernel32.TerminateProcess(h, 0)
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    except OSError:
        return False


def stop_running():
    """Demande au fond de s'arreter proprement (GPU et CM reviennent au statique)."""
    pid, _ = read_lock()
    if pid is None or not _pid_alive(pid):
        return False
    try:
        with open(STOP_PATH, "w", encoding="utf-8") as f:
            f.write("1")
    except OSError:
        pass
    for _ in range(40):
        time.sleep(0.2)
        if not _pid_alive(pid):
            _log(f"KEEP corsair stop pid={pid}")
            try:
                os.remove(STOP_PATH)
            except OSError:
                pass
            return True
    if _terminate(pid):
        _log(f"KEEP corsair stop force pid={pid}")
        try:
            os.remove(LOCK_PATH)
        except OSError:
            pass
        try:
            os.remove(STOP_PATH)
        except OSError:
            pass
        return True
    return False


def heartbeat():
    try:
        with open(LOCK_PATH, "w") as f:
            f.write(f"{os.getpid()}:{time.time()}")
    except OSError:
        pass


def _block_color(block, legacy_scaled=True):
    """{"color": (r,g,b), "bri": 0-100} ou None si le bloc n'a pas de teinte."""
    if not isinstance(block, dict):
        return None
    try:
        rgb = tuple(max(0, min(255, int(block[k]))) for k in ("r", "g", "b"))
    except (KeyError, TypeError, ValueError):
        return None
    try:
        bri = max(0, min(100, int(block.get("brightness", 100))))
    except (TypeError, ValueError):
        bri = 100
    if legacy_scaled and not block.get("unscaled"):
        # Ancienne config : le RGB est deja multiplie par le pourcentage.
        bri = 100
    return {"color": rgb, "bri": bri}


def setup_from_cfg(cfg):
    """Dict mode/speed/corsair/gpu/cm, ou None s'il n'y a aucune couleur.

    None = garder le dernier etat connu. Jamais de couleur de repli.
    """
    if not isinstance(cfg, dict):
        return None
    last = cfg.get("corsair_last") if isinstance(cfg.get("corsair_last"), dict) else {}
    fx = cfg.get("effect") if isinstance(cfg.get("effect"), dict) else {}
    mode = str(fx.get("mode", last.get("mode", "static")))
    if mode not in effects.MODES:
        mode = "static"
    try:
        speed = max(1, min(10, int(fx.get("speed", last.get("speed", 4)))))
    except (TypeError, ValueError):
        speed = 4
    corsair = _block_color(last, True)
    fans = _block_color(cfg.get("corsair_fans_last"), True)
    if fans is None:
        fans = corsair
    cm = _block_color(cfg.get("cm_last"), True)
    gpu = None
    if all(k in cfg for k in ("r", "g", "b")):
        gpu = _block_color({
            "r": cfg.get("r"), "g": cfg.get("g"), "b": cfg.get("b"),
            "brightness": cfg.get("brightness", 100),
            "unscaled": True,
        }, True)
    if corsair is None and fans is None and gpu is None and cm is None:
        return None
    lcd_image = None
    if cfg.get("lcd_image_on") and not cfg.get("lcd_temp"):
        path = cfg.get("lcd_image_path")
        if isinstance(path, str) and path:
            try:
                scale = int(cfg.get("lcd_image_scale", 100))
            except (TypeError, ValueError):
                scale = 100
            lcd_image = {"path": path, "scale": max(25, min(200, scale))}
    try:
        lcd_bri = max(0, min(100, int(cfg.get("lcd_brightness", 100))))
    except (TypeError, ValueError):
        lcd_bri = 100
    return {"mode": mode, "speed": speed, "corsair": corsair, "fans": fans,
            "gpu": gpu, "cm": cm,
            "lcd": bool(cfg.get("lcd_temp")), "lcd_image": lcd_image,
            "lcd_brightness": lcd_bri}


def render_link(spans, mode, t, block, fans, speed, state=None, temp_c=None):
    """Buffer HID : waterblock puis ventilos, chacun avec sa teinte.

    Des spans consecutifs du meme role forment un seul groupe, pour que
    la vague de la pompe continue sur l'anneau, et celle d'un RX sur l'autre.
    """
    if block is None:
        block = fans
    if fans is None:
        fans = block
    if block is None:
        return []
    groups = []
    for role, n in spans:
        n = int(n)
        if n <= 0:
            continue
        if groups and groups[-1][0] == role:
            groups[-1] = (role, groups[-1][1] + n)
        else:
            groups.append((role, n))
    out = []
    for role, n in groups:
        src = fans if role == "fans" else block
        draw = "static" if src["bri"] <= 0 else mode
        out.extend(effects.render(
            n, draw, t, src["color"], src["bri"], speed, state=state, temp_c=temp_c))
    return out


def read_setup():
    """setup_from_cfg du fichier, ou None si le JSON est illisible."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return None
    return setup_from_cfg(cfg)


def hw_locked():
    """True pendant qu'une fenetre ecrit le materiel elle-meme."""
    try:
        return time.time() - os.path.getmtime(HW_LOCK) < 8
    except OSError:
        return False


@contextmanager
def hw_hold():
    """Le fond s'ecarte le temps d'une ecriture lancee par la fenetre."""
    try:
        with open(HW_LOCK, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except OSError:
        yield
        return
    try:
        yield
    finally:
        try:
            os.remove(HW_LOCK)
        except OSError:
            pass


def _publish_idle(mtime):
    tmp = IDLE_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(repr(float(mtime)))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, IDLE_PATH)
    except OSError:
        pass


def _clear_idle():
    try:
        os.remove(IDLE_PATH)
    except OSError:
        pass


def _idle_mtime():
    try:
        with open(IDLE_PATH, encoding="utf-8") as f:
            return float(f.read().strip())
    except (OSError, ValueError):
        return None


def wait_static(timeout=4.0):
    """True si le fond a fini de reposer le statique de la config actuelle."""
    if not is_running():
        return False
    try:
        mt = os.path.getmtime(CONFIG_PATH)
    except OSError:
        return False
    deadline = time.time() + timeout
    while time.time() < deadline:
        seen = _idle_mtime()
        if seen is not None and seen + 0.05 >= mt:
            return True
        time.sleep(0.1)
    return False


class DeviceSync:
    """Joue l'effet du fichier sur le GPU et la carte mere, meme horloge.

    Arc-en-ciel et pulsation suivent le temps du hub, une couleur
    sur les 6 zones. Vague arc-en-ciel GPU : effet matériel, une fois.
    Vague couleur et ondulation GPU : la couleur du milieu, pas les
    6 zones (ça clignote). Le GPU est écrit au plus cinq fois par seconde.
    La carte mere est poussée à chaque image Corsair, en direct.
    En statique, ce thread ne touche au materiel qu'en quittant un effet,
    pour graver la couleur enregistree et lacher l'USB.
    """

    def __init__(self, t0):
        self.t0 = t0
        self._stop = threading.Event()
        self._temp_lock = threading.Lock()
        self._temp = None
        self._thread = threading.Thread(target=self._run, name="lumino-sync", daemon=True)
        self.gpu_was_live = False
        self.cm_was_live = False
        self.gpu_lit = False
        self.cm_lit = False
        self.announced = False
        self.nv = None
        self.gpu_handle = None
        self.aura = None
        self.cm_leds = 0
        self.fx_gpu = effects.EffectState()
        self.fx_cm = effects.EffectState()
        self.gpu_hw_key = None
        self.gpu_frame = None
        self.cm_wave_key = None
        self.cm_strips = False
        self.fail = {}
        self._last_temp_t = 0.0

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=3)
        if self._thread.is_alive():
            _log("KEEP sync thread encore occupe")
            return
        snap = read_setup()
        if snap and (self.gpu_was_live or self.cm_was_live):
            try:
                if self.gpu_was_live:
                    self._restore_gpu(snap)
                    self.gpu_was_live = False
                if self.cm_was_live:
                    self._restore_cm(snap)
                    self.cm_was_live = False
            except Exception as e:  # noqa: BLE001
                _log(f"KEEP sync retour a l'arret: {e}")
        self._close_aura()

    def get_temp(self):
        with self._temp_lock:
            return self._temp

    def _set_temp(self, value):
        with self._temp_lock:
            self._temp = value

    def _run(self):
        while not self._stop.is_set():
            try:
                self._cycle()
            except Exception as e:  # noqa: BLE001
                _log(f"KEEP sync boucle: {e}")
                self._stop.wait(0.5)

    def _cycle(self):
        if hw_locked():
            self._stop.wait(0.2)
            return
        try:
            mt = os.path.getmtime(CONFIG_PATH)
        except OSError:
            self._stop.wait(0.3)
            return
        snap = read_setup()
        if snap is None:
            self._stop.wait(0.3)
            return
        if snap.get("lcd") or snap["mode"] == "temperature":
            self._refresh_temp(time.monotonic() - self.t0)
        if snap["mode"] == "static":
            if self.gpu_was_live:
                try:
                    self._restore_gpu(snap)
                except Exception as e:  # noqa: BLE001
                    _log(f"KEEP sync retour statique: {e}")
                    self.nv = None
                    self.gpu_handle = None
                    self._stop.wait(0.5)
                    return
                self.gpu_was_live = False
                self.gpu_lit = False
                self.announced = False
            # La carte mere est reposee par le thread principal. On publie
            # seulement quand les deux ont lache le materiel.
            if not self.cm_was_live:
                _publish_idle(mt)
            self._stop.wait(0.25)
            return
        _clear_idle()
        self.gpu_was_live = True
        if not self.announced:
            _log(f"KEEP sync effet {snap['mode']}")
            self.announced = True
        t = time.monotonic() - self.t0
        temp = self.get_temp()
        self._guard("gpu", lambda: self._push_gpu(snap, t, temp))
        self._stop.wait(0.2)

    def service_cm(self, snap, t):
        """Carte mere, appelee depuis la boucle Corsair (pas le thread GPU)."""
        if hw_locked() or snap is None:
            return
        if snap["mode"] == "static":
            if not self.cm_was_live:
                return
            try:
                self._restore_cm(snap)
            except Exception as e:  # noqa: BLE001
                _log(f"KEEP sync cm retour: {e}")
                self._close_aura()
                return
            self.cm_was_live = False
            self.cm_lit = False
            return
        self.cm_was_live = True
        self._guard("cm", lambda: self._push_cm(snap, t, self.get_temp()))

    def _guard(self, label, fn):
        try:
            wrote = bool(fn())
            self.fail[label] = 0
            return wrote
        except Exception as e:  # noqa: BLE001
            self.fail[label] = self.fail.get(label, 0) + 1
            if self.fail[label] == 1 or self.fail[label] % 15 == 0:
                _log(f"KEEP sync {label}: {e}")
            if label == "gpu":
                self.nv = None
                self.gpu_handle = None
            else:
                self._close_aura()
            return False

    def _refresh_temp(self, t):
        if t - self._last_temp_t < 2:
            return
        self._last_temp_t = t
        try:
            self._ensure_nv()
            self._set_temp(self.nv.get_gpu_temp(self.gpu_handle))
        except Exception as e:  # noqa: BLE001
            self._set_temp(None)
            self.nv = None
            self.gpu_handle = None
            n = self.fail.get("temp", 0) + 1
            self.fail["temp"] = n
            if n == 1 or n % 15 == 0:
                _log(f"KEEP sync sonde: {e}")

    def _ensure_nv(self):
        if self.nv is not None and self.gpu_handle is not None:
            return
        from nvapi import NvAPI
        import blackwell
        nv = NvAPI()
        nv.initialize()
        gpus = nv.enum_gpus()
        if not gpus:
            raise RuntimeError("aucun GPU NVIDIA")
        h = gpus[0]
        ok, detail = blackwell.probe(nv, h, port=1)
        if not ok:
            raise RuntimeError(f"sonde 0x75 KO ({detail})")
        self.nv = nv
        self.gpu_handle = h

    def _gpu_write(self, colors, brightness10, do_save, direct=False):
        import blackwell
        self._ensure_nv()
        if direct and not do_save:
            blackwell.apply_direct(self.nv, self.gpu_handle, colors, brightness10)
        else:
            blackwell.apply_zones(self.nv, self.gpu_handle, colors, brightness10, do_save)

    def _push_gpu(self, snap, t, temp):
        gpu = snap.get("gpu")
        if not gpu or hw_locked():
            return False
        if gpu["bri"] <= 0:
            self.gpu_hw_key = None
            self.gpu_frame = None
            if self.gpu_lit:
                self._gpu_write([(0, 0, 0)] * 6, 1, True)
                self.gpu_lit = False
                return True
            return False
        if snap["mode"] == "rainbow_wave":
            # Six couleurs différentes en direct font clignoter les ventilateurs.
            # La carte joue la vague une fois, sur son horloge.
            import blackwell
            level = blackwell.level_from_percent(gpu["bri"])
            key = ("wave", level, int(snap["speed"]))
            if key == self.gpu_hw_key:
                return False
            self._ensure_nv()
            blackwell.apply_effect(
                self.nv, self.gpu_handle, blackwell.MODE_WAVE,
                level, snap["speed"], (0, 0, 0))
            self.gpu_hw_key = key
            self.gpu_frame = None
            self.gpu_lit = True
            _log(f"KEEP sync gpu vague arc-en-ciel v={snap['speed']}")
            return True
        self.gpu_hw_key = None
        cols = effects.render(
            6, snap["mode"], t, gpu["color"], gpu["bri"], snap["speed"],
            state=self.fx_gpu, temp_c=temp)
        if snap["mode"] in ("color_wave", "ripple") and cols:
            # Même couleur sur les 6 zones : la vague spatiale clignote.
            cols = [cols[len(cols) // 2]] * len(cols)
        if cols == self.gpu_frame:
            return False
        self._gpu_write(cols, 10, False, direct=True)
        self.gpu_frame = list(cols)
        self.gpu_lit = True
        return True

    def _ensure_aura(self):
        if self.aura is not None:
            return
        import aura
        dev = aura.Aura()
        fixed = next((c for c in dev.channels if c["kind"] == "fixed"), None)
        if not fixed or fixed["leds"] <= 0:
            dev.close()
            raise RuntimeError("pas de LEDs fixes Aura")
        self.aura = dev
        self.cm_leds = int(fixed["leds"])

    def _close_aura(self):
        if self.aura is not None:
            try:
                self.aura.close()
            except Exception:
                pass
        self.aura = None

    def _cm_static(self, r, g, b):
        self._ensure_aura()
        self.aura.set_cm(r, g, b)
        self.aura._direct_fixed = False

    def _strips_solid(self, color):
        """Remet les headers ARGB sur une couleur unie (120 LEDs, comme Aura)."""
        self._ensure_aura()
        for ch in self.aura.channels:
            if ch["kind"] != "addr":
                continue
            setattr(self.aura, "_direct_addr_%s" % ch["direct"], False)
            self.aura.set_channel_colors(ch, [color] * 120)

    def _push_cm(self, snap, t, temp):
        cm = snap.get("cm")
        if not cm or hw_locked():
            return False
        if cm["bri"] <= 0:
            self.cm_wave_key = None
            if self.cm_lit:
                self._cm_static(0, 0, 0)
                if self.cm_strips:
                    self._strips_solid((0, 0, 0))
                    self.cm_strips = False
                self._close_aura()
                self.cm_lit = False
                return True
            return False
        if snap["mode"] == "rainbow_wave":
            self._ensure_aura()
            strips = [c for c in self.aura.channels if c["kind"] == "addr"]
            cps = max(1, min(10, int(snap["speed"]))) * 0.03
            shift = t * cps
            fixed = next(c for c in self.aura.channels if c["kind"] == "fixed")
            for ch in strips:
                self.aura.set_channel_colors(
                    ch, effects.render_aura_hue_wave(120, 22, 16, cm["bri"], shift))
            cols = effects.render_aura_hue_wave(
                fixed["leds"], 16, 16, cm["bri"], shift)
            self.aura.set_channel_colors(fixed, cols)
            if self.cm_wave_key is None:
                _log(f"KEEP sync cm vague arc-en-ciel n={self.cm_leds} strips={len(strips)}")
            self.cm_wave_key = (int(cm["bri"]), self.cm_leds, len(strips))
            self.cm_strips = bool(strips)
            self.cm_lit = True
            return True
        if self.cm_strips:
            r, g, b = cm["color"]
            bri = int(cm["bri"])
            self._strips_solid((r * bri // 100, g * bri // 100, b * bri // 100))
            self.cm_strips = False
        self.cm_wave_key = None
        self._ensure_aura()
        cols = effects.render(
            self.cm_leds, snap["mode"], t, cm["color"], cm["bri"], snap["speed"],
            state=self.fx_cm, temp_c=temp)
        self.aura.set_cm_colors(cols)
        self.cm_lit = True
        return True

    def _restore_gpu(self, snap):
        if hw_locked():
            raise RuntimeError("materiel occupe")
        gpu = snap.get("gpu")
        if not gpu:
            return
        self.gpu_hw_key = None
        self.gpu_frame = None
        import blackwell
        level = blackwell.level_from_percent(gpu["bri"])
        if level <= 0:
            self._gpu_write([(0, 0, 0)] * 6, 1, True)
        else:
            self._gpu_write([gpu["color"]] * 6, level, True)

    def _restore_cm(self, snap):
        if hw_locked():
            raise RuntimeError("materiel occupe")
        self.cm_wave_key = None
        cm = snap.get("cm")
        if not cm:
            self._close_aura()
            return
        if cm["bri"] <= 0:
            self._cm_static(0, 0, 0)
        else:
            r, g, b = cm["color"]
            bri = cm["bri"]
            self._cm_static(r * bri // 100, g * bri // 100, b * bri // 100)
        if self.cm_strips:
            if cm["bri"] <= 0:
                self._strips_solid((0, 0, 0))
            else:
                r, g, b = cm["color"]
                bri = int(cm["bri"])
                self._strips_solid((r * bri // 100, g * bri // 100, b * bri // 100))
            self.cm_strips = False
        self._close_aura()


class _LcdFeed:
    """Pousse la temperature GPU sur l'ecran, seulement quand elle change."""

    def __init__(self):
        self.dev = None
        self.last_key = None
        self.last_t = 0.0
        self.fails = 0
        self.logged = False
        self.active = False
        self.clip = None
        self.clip_key = None
        self.frame_i = 0
        self.next_frame = 0.0
        self.wait = None
        self.brightness = None

    def close(self):
        if self.dev is not None:
            self.dev.close()
            self.dev = None
        self.brightness = None

    def release(self):
        """Rend le fond enregistre, puis lache l'HID."""
        if not self.active and self.dev is None:
            return
        try:
            import corsair_lcd
            if self.dev is None:
                self.dev = corsair_lcd.Lcd()
            self.dev.restore_hardware()
            _log("KEEP lcd fond hardware")
        except Exception as e:  # noqa: BLE001
            _log(f"KEEP lcd fond: {e}")
        finally:
            self.close()
            self.active = False
            self.last_key = None
            self.logged = False
            self.clip = None
            self.clip_key = None
            self.brightness = None

    def _apply_brightness(self, percent):
        """True si le pourcentage a changé. L'appelant renvoie le JPEG."""
        try:
            percent = max(0, min(100, int(percent)))
        except (TypeError, ValueError):
            percent = 100
        if self.dev is None or percent == self.brightness:
            return False
        self.dev.set_brightness(percent, persist=False)
        self.brightness = percent
        return True

    def service(self, enabled, image, temp_c, brightness=100):
        self.wait = None
        if not enabled and not image:
            self.release()
            return
        if not enabled:
            self._service_image(image, brightness)
            return
        key = None if temp_c is None else int(round(float(temp_c)))
        now = time.monotonic()
        bright_changed = False
        if self.dev is not None:
            try:
                bright_changed = self._apply_brightness(brightness)
            except Exception as e:  # noqa: BLE001
                self.close()
                _log(f"KEEP lcd: {e}")
                return
        if key == self.last_key and now - self.last_t < 20 and not bright_changed:
            return
        try:
            import corsair_lcd
            if self.dev is None:
                self.dev = corsair_lcd.Lcd()
            self._apply_brightness(brightness)
            self.active = True
            self.dev.send_jpeg(corsair_lcd.render_temp_jpeg(temp_c))
            self.last_key = key
            self.last_t = now
            self.fails = 0
            if not self.logged:
                _log(f"KEEP lcd temperature pid={self.dev.pid:04x}")
                self.logged = True
        except Exception as e:  # noqa: BLE001
            self.close()
            self.fails += 1
            if self.fails == 1 or self.fails % 15 == 0:
                _log(f"KEEP lcd: {e}")

    def _service_image(self, image, brightness):
        path = image["path"]
        scale = image["scale"]
        try:
            mt = os.path.getmtime(path)
        except OSError:
            self.fails += 1
            if self.fails == 1 or self.fails % 15 == 0:
                _log(f"KEEP lcd image absente: {path}")
            return
        key = ("image", path, scale, mt)
        now = time.monotonic()
        try:
            import corsair_lcd
            if key != self.clip_key or not self.clip:
                self.clip = corsair_lcd.load_lcd_frames(path, scale, tick=heartbeat)
                self.clip_key = key
                self.frame_i = 0
                self.next_frame = 0.0
                if len(self.clip) > 1:
                    _log(f"KEEP lcd gif n={len(self.clip)}")
            animated = len(self.clip) > 1
            if self.dev is None:
                self.dev = corsair_lcd.Lcd()
            bright_changed = self._apply_brightness(brightness)
            due = True
            if not animated and key == self.last_key and now - self.last_t < 20:
                due = False
            if animated and now < self.next_frame:
                self.wait = self.next_frame - now
                due = False
            if not due and not bright_changed:
                return
            self.active = True
            jpeg, dur = self.clip[self.frame_i % len(self.clip)]
            self.dev.send_jpeg(jpeg)
            self.last_key = key
            self.last_t = time.monotonic()
            self.fails = 0
            if animated and due:
                self.frame_i = (self.frame_i + 1) % len(self.clip)
                step = dur if dur else 0.1
                self.next_frame = self.last_t + step
                self.wait = step
            if not self.logged:
                _log(f"KEEP lcd image pid={self.dev.pid:04x}")
                self.logged = True
        except Exception as e:  # noqa: BLE001
            self.close()
            self.fails += 1
            self.wait = 0.5
            if self.fails == 1 or self.fails % 15 == 0:
                _log(f"KEEP lcd image: {e}")


def keepalive_loop(interval=INTERVAL):
    import time as _t
    import corsair_link
    if is_running():
        return  # deja actif
    try:
        os.remove(STOP_PATH)
    except OSError:
        pass
    heartbeat()
    _log("KEEP corsair start")
    link = None
    fx = effects.EffectState()
    failures = 0
    last_cfg_mtime = 0
    setup = None
    last_sent_key = None
    last_sent_t = 0.0
    last_sw = 0.0
    t0 = _t.monotonic()
    sync = DeviceSync(t0)
    sync.start()
    lcd = _LcdFeed()
    try:
        while True:
            if os.path.isfile(STOP_PATH):
                _log("KEEP corsair arret demande")
                break
            heartbeat()
            cfg_changed = False
            try:
                mt = os.path.getmtime(CONFIG_PATH)
                if setup is None or mt != last_cfg_mtime:
                    last_cfg_mtime = mt
                    cfg_changed = True
                    fresh = read_setup()
                    if fresh is not None:
                        setup = fresh
            except OSError:
                pass
            if setup is None:
                _t.sleep(0.25)
                continue
            t = _t.monotonic() - t0
            # Meme instant t que le Corsair : la carte mere suit image par image.
            sync.service_cm(setup, t)
            lcd.service(bool(setup.get("lcd")), setup.get("lcd_image"), sync.get_temp(),
                        setup.get("lcd_brightness", 100))
            block = setup.get("corsair")
            fans = setup.get("fans")
            if block is None and fans is None:
                _t.sleep(0.25 if lcd.wait is None else min(0.25, max(0.01, lcd.wait)))
                continue
            mode = setup["mode"]
            speed = setup["speed"]
            # 0 % : noir statique sur ce groupe seulement. On garde le mode
            # en config pour le rallumage, sans animer du noir a 5 img/s.
            still = all(
                (src is None) or src["bri"] <= 0 or mode in ("static", "temperature")
                for src in (block, fans))
            temp_c = sync.get_temp()
            try:
                if link is None:
                    link = corsair_link.CorsairLink()
                    last_sent_key = None
                    last_sw = _t.monotonic()
                elif cfg_changed or _t.monotonic() - last_sw > 3:
                    # iCUE ferme le hub en mode memoire sans casser notre HID.
                    link.enter_software_mode()
                    last_sw = _t.monotonic()
                    last_sent_key = None
                spans = link.led_spans()
                if still:
                    key = (mode,
                           None if block is None else (block["color"], block["bri"]),
                           None if fans is None else (fans["color"], fans["bri"]),
                           round(temp_c or -999) if mode == "temperature" else 0)
                    if key != last_sent_key or _t.monotonic() - last_sent_t > 4:
                        link.set_segments(render_link(
                            spans, mode, t, block, fans, speed, state=fx, temp_c=temp_c))
                        last_sent_key = key
                        last_sent_t = _t.monotonic()
                    _t.sleep(0.25 if lcd.wait is None else min(0.25, max(0.01, lcd.wait)))
                else:
                    link.set_segments(render_link(
                        spans, mode, t, block, fans, speed, state=fx, temp_c=temp_c),
                        settle=False)
                    last_sent_key = None
                    # ~4 echanges HID a 30 ms : le debit reel est vers 5 img/s.
                    _t.sleep(0.01)
                if failures:
                    _log("KEEP corsair repris")
                failures = 0
            except Exception as e:  # noqa: BLE001
                failures += 1
                try:
                    if link is not None:
                        link.close()
                except Exception:
                    pass
                link = None
                if failures == 1 or failures % 15 == 0:
                    _log(f"KEEP corsair erreur x{failures}: {e}")
                _t.sleep(min(interval, 10))
                continue
    finally:
        lcd.release()
        sync.stop()
        try:
            if link is not None:
                link.close(hardware_mode=True)
        except Exception:
            pass
        try:
            os.remove(LOCK_PATH)
        except OSError:
            pass
        _log("KEEP corsair fin")


def ensure_running():
    """Lance le worker detache s'il ne tourne pas. Retourne True si actif."""
    if is_running():
        return True
    try:
        import subprocess
        exe = sys.executable if getattr(sys, "frozen", False) else sys.executable
        script = os.path.join(BASE, "corsair_keep.py")
        if getattr(sys, "frozen", False):
            cmd = [exe, "--corsair-keepalive"]
        else:
            cmd = [exe, script]
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "DETACHED_PROCESS", 0x8) \
                | getattr(subprocess, "CREATE_NO_WINDOW", 0x8000000)
        env = os.environ.copy()
        if getattr(sys, "frozen", False):
            # Sans ca, le fond reprend le dossier _MEI de la fenetre.
            # A la fermeture le bootloader ne peut pas l'effacer et affiche
            # "Failed to remove temporary directory".
            env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        subprocess.Popen(cmd, cwd=BASE, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                         creationflags=flags, close_fds=True, env=env)
    except OSError as e:
        _log(f"KEEP corsair lancement impossible: {e}")
        return False
    for _ in range(12):
        time.sleep(0.25)
        if is_running():
            return True
    return False


if __name__ == "__main__":
    if "--stop" in sys.argv:
        print("stop:", stop_running())
    else:
        keepalive_loop()
