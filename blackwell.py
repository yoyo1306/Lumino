"""
Lumino - Protocole Gigabyte RGB Fusion 2 Blackwell GPU (RTX 50).
Référence : OpenRGB Controllers/GigabyteRGBFusion2BlackwellGPUController
(GPL-2.0-or-later, CalcProgrammer1 + contributeurs).

- Adresse I2C : 0x75, port GPU 1, blocs de 64 octets SANS registre.
- Détection : write [0x10,0x01,0...] (64 o) puis read 4 o.
  Attendu : [0x01, 0x01|0x02, 0x01, *] (ex MASTER 5080 : 01 01 01 10).
- Static par zone : [0x12,0x01,mode=0x01,speed,brightness,R,G,B,0x00,zone,0x00,...]
  brightness 0x01..0x10 (1..10), speed 0x01..0x06.
- Save : [0x13,0x01,0...] (64 o).
- Layouts : on écrit les zones hw 0..5 avec la même couleur (couvre
  SINGLE/GAMING/WATERFORCE/MASTER), comme le fait OpenRGB/GCC.
"""
import time

ADDR = 0x75
REG_MODE = 0x12
MODE_STATIC = 0x01
SPEED_NORMAL = 0x03
BRIGHT_MAX = 0x0A
HW_ZONES = range(6)


def _probe_packet():
    return bytes([0x10, 0x01] + [0] * 62)


def probe(nv, handle, port=1):
    """Sonde non destructive (packet requête OpenRGB). Retourne (ok, réponse)."""
    st = nv.write_block(handle, ADDR, _probe_packet(), port)
    if st != 0:
        return False, {"write_status": st}
    time.sleep(0.02)
    st, data = nv.read_block(handle, ADDR, 4, port)
    if st != 0 or data is None:
        return False, {"read_status": st}
    ok = data[0] == 0x01 and data[1] in (0x01, 0x02) and data[2] == 0x01
    return ok, {"reply": list(data)}


def _zone_packet(r, g, b, zone, brightness=BRIGHT_MAX, speed=SPEED_NORMAL):
    pkt = bytearray(64)
    pkt[0] = REG_MODE
    pkt[1] = 0x01
    pkt[2] = MODE_STATIC
    pkt[3] = speed & 0xFF
    pkt[4] = max(1, min(0x0A, int(brightness))) & 0xFF
    pkt[5], pkt[6], pkt[7] = r & 0xFF, g & 0xFF, b & 0xFF
    pkt[8] = 0x00
    pkt[9] = zone & 0xFF
    pkt[10] = 0x00  # numberOfColors = 0 : couleur unique bytes 5-7
    return bytes(pkt)


def _save_packet():
    return bytes([0x13, 0x01] + [0] * 62)


def apply_static(nv, handle, r, g, b, brightness10=10, do_save=False, port=1):
    """Applique une couleur statique à toutes les zones hw. Retourne le détail."""
    bval = max(1, min(10, int(brightness10)))
    results = []
    for z in HW_ZONES:
        st = nv.write_block(handle, ADDR, _zone_packet(r, g, b, z, bval), port)
        results.append({"zone": z, "status": st})
        if st != 0:
            raise RuntimeError(f"Zone {z} : write status={st}")
        time.sleep(0.12)  # le contrôleur limite à ~9 màj/s (registre 0x12)
    saved = None
    if do_save:
        time.sleep(0.12)
        st = nv.write_block(handle, ADDR, _save_packet(), port)
        saved = st
        if st != 0:
            raise RuntimeError(f"Save : write status={st}")
    return {"brightness_raw": bval, "zones": results, "save_status": saved}
