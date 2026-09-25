"""Lumino - re-applique la config sauvegardee au demarrage Windows.
One-shot : applique GPU + CM + WC puis SE FERME (aucun processus resident,
aucun serveur). Lance via le dossier Demarrage ou la tache LuminoRGB.
Journalise dans lumino.log. Utiliser pythonw.exe (pas de console).

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


def boot_gpu(cfg, tries=6):
    from nvapi import NvAPI
    import blackwell
    r, g, b = int(cfg["r"]), int(cfg["g"]), int(cfg["b"])
    bri = max(1, min(10, round(int(cfg.get("brightness", 100)) / 10)))
    last_err = None
    for attempt in range(1, tries + 1):
        try:
            nv = NvAPI()
            nv.initialize()
            gpus = nv.enum_gpus()
            if not gpus:
                raise RuntimeError("aucun GPU NVIDIA (driver pas pret ?)")
            h = gpus[0]
            ok, detail = blackwell.probe(nv, h, port=1)
            if not ok:
                raise RuntimeError(f"sonde 0x75 KO ({detail})")
            # do_save=False : la CM a deja le save persistant, on use pas l'EEPROM au boot
            blackwell.apply_static(nv, h, r, g, b, bri, False)
            _log(f"BOOT gpu r={r} g={g} b={b} (essai {attempt})")
            return
        except Exception as e:  # noqa: BLE001
            last_err = e
            _log(f"BOOT gpu essai {attempt}/{tries} erreur: {e}")
            time.sleep(5)
    _log(f"BOOT gpu erreur definitive: {last_err}")


def boot_aura(key, fn, last, tries=6):
    import aura
    last_err = None
    for attempt in range(1, tries + 1):
        a = None
        try:
            a = aura.Aura()
            try:
                getattr(a, fn)(int(last["r"]), int(last["g"]), int(last["b"]))
            finally:
                a.close()
            _log(f"BOOT {key} r={last['r']} g={last['g']} b={last['b']} (essai {attempt})")
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
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
    except (OSError, ValueError) as e:
        _log(f"BOOT config illisible: {e}")
        return
    if not isinstance(cfg, dict):
        _log("BOOT config invalide")
        return

    if args.check:
        _log(f"BOOT check cfg gpu={('r' in cfg)} cm={('cm_last' in cfg)} wc={('wc_last' in cfg)}")
        txt = json.dumps({k: cfg.get(k) for k in ("r", "g", "b", "brightness", "cm_last", "wc_last")}, indent=2)
        try:
            print(txt)  # absent en exe --windowed (stdout=None) : ignore
        except (AttributeError, OSError, ValueError):
            pass
        return

    if "r" in cfg:
        boot_gpu(cfg, tries=args.tries)
    for key, fn in (("cm_last", "set_cm"), ("wc_last", "set_wc")):
        last = cfg.get(key)
        if isinstance(last, dict) and "r" in last:
            boot_aura(key, fn, last, tries=args.tries)
    _log("BOOT fin")


if __name__ == "__main__":
    main()
