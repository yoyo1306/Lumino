"""Lumino - re-applique la config sauvegardee au demarrage Windows.
One-shot statique pour le GPU et la carte mere, puis demarre le fond.
Si un effet est enregistre, ce fond le reprend sur les trois appareils
(le hub oublie sa couleur des qu'on arrete de lui parler).
Lance via le dossier Demarrage. Journalise dans lumino.log.

Usage :
  pythonw apply_boot.py [--delay 15] [--tries 10]
"""
import argparse
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False) \
    else os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
os.chdir(BASE)

CONFIG_PATH = os.path.join(BASE, "config.json")
LOG_PATH = os.path.join(BASE, "lumino.log")


def _log(msg):
    try:
        with open(LOG_PATH, "a") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " " + msg + "\n")
    except OSError:
        pass


def _aura_rgb(last):
    """RGB a envoyer. Nouveau format : teinte + pourcentage.
    Ancien format : RGB deja attenue."""
    r, g, b = int(last["r"]), int(last["g"]), int(last["b"])
    if not last.get("unscaled"):
        return r, g, b
    try:
        bri = int(last.get("brightness", 100))
    except (TypeError, ValueError):
        bri = 100
    bri = max(0, min(100, bri))
    return tuple(c * bri // 100 for c in (r, g, b))


def boot_gpu(cfg, tries=6):
    from nvapi import NvAPI
    import blackwell
    r, g, b = int(cfg["r"]), int(cfg["g"]), int(cfg["b"])
    level = blackwell.level_from_percent(cfg.get("brightness", 100))
    send_r, send_g, send_b = (0, 0, 0) if level <= 0 else (r, g, b)
    hw = 1 if level <= 0 else level
    import corsair_keep
    last_err = None
    for attempt in range(1, tries + 1):
        try:
            with corsair_keep.hw_hold():
                nv = NvAPI()
                nv.initialize()
                gpus = nv.enum_gpus()
                if not gpus:
                    raise RuntimeError("aucun GPU NVIDIA (driver pas pret ?)")
                h = gpus[0]
                ok, detail = blackwell.probe(nv, h, port=1)
                if not ok:
                    raise RuntimeError(f"sonde 0x75 KO ({detail})")
                # do_save=False : la couleur a deja ete gravee par l'UI.
                # On rejoue en volatile au cas ou le driver a efface les LEDs.
                blackwell.apply_static(nv, h, send_r, send_g, send_b, hw, False)
            _log(f"BOOT gpu r={send_r} g={send_g} b={send_b} niveau={hw} (essai {attempt})")
            return
        except Exception as e:  # noqa: BLE001
            last_err = e
            _log(f"BOOT gpu essai {attempt}/{tries} erreur: {e}")
            time.sleep(5)
    _log(f"BOOT gpu erreur definitive: {last_err}")


def boot_aura(key, fn, last, tries=6):
    import aura
    import corsair_keep
    last_err = None
    for attempt in range(1, tries + 1):
        a = None
        try:
            with corsair_keep.hw_hold():
                a = aura.Aura()
                try:
                    sr, sg, sb = _aura_rgb(last)
                    getattr(a, fn)(sr, sg, sb)
                finally:
                    a.close()
            _log(f"BOOT {key} r={sr} g={sg} b={sb} (essai {attempt})")
            return
        except Exception as e:  # noqa: BLE001
            last_err = e
            try:
                if a is not None:
                    a.close()
            except Exception:
                pass
            _log(f"BOOT {key} essai {attempt}/{tries} erreur: {e}")
            time.sleep(5)
    _log(f"BOOT {key} erreur definitive: {last_err}")


def main():
    ap = argparse.ArgumentParser(description="Lumino boot one-shot")
    ap.add_argument("--delay", type=int, default=10,
                    help="attente initiale en secondes (driver/USB pas prets)")
    ap.add_argument("--tries", type=int, default=6, help="essais par device")
    ap.add_argument("--check", action="store_true",
                    help="verifie la config sans rien appliquer (dry-run)")
    args = ap.parse_args()

    _log(f"BOOT debut (delay={args.delay}s)")
    if args.delay > 0 and not args.check:
        time.sleep(args.delay)

    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError) as e:
        _log(f"BOOT config illisible: {e}")
        return
    if not isinstance(cfg, dict):
        _log("BOOT config invalide")
        return

    if args.check:
        _log(f"BOOT check cfg gpu={('r' in cfg)} cm={('cm_last' in cfg)} corsair={('corsair_last' in cfg)}")
        txt = json.dumps({k: cfg.get(k) for k in ("r", "g", "b", "brightness", "cm_last", "corsair_last")}, indent=2)
        try:
            print(txt)  # absent en exe --windowed (stdout=None) : ignore
        except (AttributeError, OSError, ValueError):
            pass
        return

    if "r" in cfg:
        boot_gpu(cfg, tries=args.tries)
    for key, fn in (("cm_last", "set_cm"),):
        last = cfg.get(key)
        if isinstance(last, dict) and "r" in last:
            boot_aura(key, fn, last, tries=args.tries)
    glow = cfg.get("ambiglow_last")
    if isinstance(glow, dict) and "r" in glow:
        try:
            import ambiglow
            import corsair_keep
            with corsair_keep.hw_hold():
                ambiglow.apply_color(glow["r"], glow["g"], glow["b"], glow.get("brightness", 100))
            _log("BOOT ambiglow r=%s g=%s b=%s bri=%s" % (
                glow["r"], glow["g"], glow["b"], glow.get("brightness", 100)))
        except Exception as e:  # noqa: BLE001
            _log(f"BOOT ambiglow erreur: {e}")
    # Corsair LINK : one-shot inutile (Hub volatile) -> demarre le keepalive
    # de fond qui tient la couleur de "corsair_last". iCUE doit rester ferme.
    last = cfg.get("corsair_last")
    if isinstance(last, dict) and "r" in last:
        try:
            import corsair_keep
            ok = corsair_keep.ensure_running()
            _log(f"BOOT corsair keepalive={'ON' if ok else 'KO'}")
        except Exception as e:  # noqa: BLE001
            _log(f"BOOT corsair erreur: {e}")
    _log("BOOT fin")


if __name__ == "__main__":
    main()
