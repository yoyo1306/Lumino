"""
Lumino - Protocole Gigabyte RGB Fusion GPU (I2C)
Référence : OpenRGB Controllers/GigabyteRGBFusionGPUController
  GigabyteRGBFusionGPUController.h / .cpp (GPL-2.0-or-later, CalcProgrammer1)

Registres :
  COLOR = 0x40  -> write_byte(0x40), write R, write G, write B
  MODE  = 0x88  -> write_byte(0x88), write mode, write speed, write brightness
  SAVE  = 0xAA  -> write_byte(0xAA), write 0, write 0, write 0

Modes :
  STATIC=0x01 BREATHING=0x02 FLASHING=0x04 DUAL_FLASH=0x08 CYCLE=0x10 SPECTRUM=0x11
Speed : 0x00..0x09 | Brightness : 0x00..0x63 (0..99)

Détection (TestForGigabyteRGBFusionGPUController) :
  write 0xAB, 0x00, 0x00, 0x00 (+4x 0x00 si addr==0x62)
  read -> attend 0xAB, puis 0x10/0x11/0x12/0x14, puis 2 lectures ignorées
  addr 0x48 : supposé existant si writes OK (comportement OpenRGB)
"""

import time

REG_COLOR = 0x40
REG_MODE = 0x88
REG_SAVE = 0xAA

MODE_STATIC = 0x01
MODE_BREATHING = 0x02
MODE_FLASHING = 0x04
MODE_DUAL_FLASHING = 0x08
MODE_COLOR_CYCLE = 0x10
MODE_SPECTRUM_CYCLE = 0x11

BRIGHTNESS_MAX = 0x63

# Adresses vues dans OpenRGB pour les GPU Gigabyte (7-bit)
KNOWN_ADDRS = [0x47, 0x48, 0x55, 0x62, 0x63, 0x71, 0x32]


def _w(nv, h, addr, val, port=1):
    st = nv.write_byte(h, addr, val, port)
    time.sleep(0.005)
    return st


def _r(nv, h, addr, port=1):
    st, val = nv.read_byte(h, addr, port)
    time.sleep(0.005)
    return st, val


def test_controller(nv, handle, addr, port=1, verbose=False):
    """Retourne (ok, detail_dict). N'écrit que la séquence de probe 0xAB..."""
    log = []
    # write 0xAB 0x00 0x00 0x00
    for v in (0xAB, 0x00, 0x00, 0x00):
        st = _w(nv, handle, addr, v, port)
        log.append(f"W 0x{addr:02X} <- 0x{v:02X} st={st}")
        if st != 0:
            if verbose:
                print("\n".join(log))
            return False, {"log": log, "reason": f"write 0x{v:02X} st={st}"}
    if addr == 0x62:
        for _ in range(4):
            st = _w(nv, handle, addr, 0x00, port)
            if st != 0:
                return False, {"log": log, "reason": "pad 0x62 failed"}

    if addr == 0x48:
        # OpenRGB : on suppose présent si les writes passent
        return True, {"log": log, "note": "0x48 accepté sur writes seuls (règle OpenRGB)"}

    st, b0 = _r(nv, handle, addr, port)
    log.append(f"R 0x{addr:02X} -> {b0} st={st}")
    if st != 0 or b0 != 0xAB:
        return False, {"log": log, "reason": f"attendu 0xAB, reçu {b0} st={st}"}

    st, b1 = _r(nv, handle, addr, port)
    log.append(f"R 0x{addr:02X} -> {b1} st={st}")
    if st != 0 or b1 not in (0x10, 0x11, 0x12, 0x14):
        return False, {"log": log, "reason": f"attendu 0x10/11/12/14, reçu {b1}"}

    _r(nv, handle, addr, port)
    _r(nv, handle, addr, port)
    if verbose:
        print("\n".join(log))
    return True, {"log": log, "sig": [b0, b1]}


def scan_gpu(nv, handle, port=1, addrs=None):
    addrs = addrs or KNOWN_ADDRS
    found = []
    for a in addrs:
        ok, detail = test_controller(nv, handle, a, port)
        if ok:
            found.append({"addr": a, "port": port, "detail": detail})
    return found


def set_color(nv, handle, addr, r, g, b, port=1):
    for v in (REG_COLOR, r & 0xFF, g & 0xFF, b & 0xFF):
        st = _w(nv, handle, addr, v, port)
        if st != 0:
            raise RuntimeError(f"SetColor: write 0x{v:02X} st={st}")
    if addr == 0x62:  # padding RTX3060-style (conservé par prudence)
        for _ in range(4):
            _w(nv, handle, addr, 0x00, port)


def set_mode(nv, handle, addr, mode=MODE_STATIC, speed=0x05, brightness=BRIGHTNESS_MAX, port=1):
    for v in (REG_MODE, mode & 0xFF, speed & 0xFF, brightness & 0xFF):
        st = _w(nv, handle, addr, v, port)
        if st != 0:
            raise RuntimeError(f"SetMode: write 0x{v:02X} st={st}")
    if addr == 0x62:
        for _ in range(4):
            _w(nv, handle, addr, 0x00, port)


def save(nv, handle, addr, port=1):
    for v in (REG_SAVE, 0x00, 0x00, 0x00):
        st = _w(nv, handle, addr, v, port)
        if st != 0:
            raise RuntimeError(f"Save: write 0x{v:02X} st={st}")
    if addr == 0x62:
        for _ in range(4):
            _w(nv, handle, addr, 0x00, port)


def apply_static(nv, handle, addr, r, g, b, brightness_pct=100, do_save=False, port=1):
    brightness = max(0, min(100, int(brightness_pct)))
    bval = round(brightness * BRIGHTNESS_MAX / 100)
    set_color(nv, handle, addr, r, g, b, port)
    set_mode(nv, handle, addr, MODE_STATIC, 0x05, bval, port)
    if do_save:
        save(nv, handle, addr, port)
    return bval
