"""
Lumino - Protocole Gigabyte RGB Fusion 2 Blackwell GPU (RTX 50).
Référence : OpenRGB Controllers/GigabyteRGBFusion2BlackwellGPUController
(GPL-2.0-or-later, CalcProgrammer1 + contributeurs).

- Adresse I2C : 0x75, port GPU 1, blocs de 64 octets SANS registre.
- Détection : write [0x10,0x01,0...] (64 o) puis read 4 o.
  Attendu : [0x01, 0x01|0x02, 0x01, *] (ex MASTER 5080 : 01 01 01 10).
- Static par zone : [0x12,0x01,mode=0x01,speed,brightness,R,G,B,0x00,zone,0x00,...]
  brightness 0x01..0x0A (1..10), speed 0x01..0x06.
- Save : [0x13,0x01,0...] (64 o).
- Layouts : on écrit les zones hw 0..5 avec la même couleur (couvre
  SINGLE/GAMING/WATERFORCE/MASTER), comme le fait OpenRGB/GCC.
"""
import time

ADDR = 0x75
REG_MODE = 0x12  # effets matériels, plafonné à ~9 màj/s
REG_COLOR = 0x16  # direct : couleurs sans relancer l'effet (pas de clignotement)
MODE_DIRECT = 0x00
MODE_STATIC = 0x01
MODE_BREATHING = 0x02
MODE_COLOR_CYCLE = 0x05  # arc-en-ciel calculé par la carte
MODE_WAVE = 0x06  # vague arc-en-ciel GCC : spectre autour des ventilateurs
SPEED_NORMAL = 0x03
BRIGHT_MAX = 0x0A
HW_ZONES = range(6)


def level_from_percent(brightness100):
    """Pourcentage UI 0–100 → cran matériel GPU.
    0 = éteint (l'appelant envoie du noir). 1–100 → cran 1–10,
    paliers réguliers. Évite round() de Python (arrondi bancaire),
    qui tassait certains crans et en sautait d'autres."""
    try:
        b = int(brightness100)
    except (TypeError, ValueError):
        b = 100
    b = max(0, min(100, b))
    if b <= 0:
        return 0
    # 1–10 % → 1, 11–20 % → 2, …, 91–100 % → 10.
    return min(10, (b + 9) // 10)


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


def speed_from_ui(speed):
    """Cran Lumino 1–10 → vitesse matérielle 1–6.

    1–4 restent au cran le plus lent. L'ancien calcul mettait 4 sur le cran 2,
    trop rapide pour la vague du 5080.
    """
    try:
        s = int(speed)
    except (TypeError, ValueError):
        s = 4
    s = max(1, min(10, s))
    if s <= 4:
        return 1
    return min(6, s - 3)


def _zone_packet(r, g, b, zone, brightness=BRIGHT_MAX, speed=SPEED_NORMAL, mode=MODE_STATIC, reg=REG_MODE):
    pkt = bytearray(64)
    pkt[0] = reg & 0xFF
    pkt[1] = 0x01
    pkt[2] = mode & 0xFF
    pkt[3] = speed & 0xFF
    pkt[4] = max(1, min(0x0A, int(brightness))) & 0xFF
    pkt[5], pkt[6], pkt[7] = r & 0xFF, g & 0xFF, b & 0xFF
    pkt[8] = 0x00
    pkt[9] = zone & 0xFF
    pkt[10] = 0x00  # numberOfColors = 0 : couleur unique bytes 5-7
    return bytes(pkt)


def _save_packet():
    return bytes([0x13, 0x01] + [0] * 62)


def apply_zones(nv, handle, colors, brightness10=10, do_save=False, port=1):
    """Ecrit une couleur par zone hw (0..5). Retourne le détail.

    Le contrôleur limite à ~9 màj/s : 120 ms entre chaque zone.
    `colors` est déjà l'RGB à envoyer (l'appelant a appliqué la luminosité).
    """
    bval = max(1, min(10, int(brightness10)))
    cols = list(colors)
    if not cols:
        cols = [(0, 0, 0)]
    if len(cols) < 6:
        cols = cols + [cols[-1]] * (6 - len(cols))
    results = []
    for z, (r, g, b) in enumerate(cols[:6]):
        st = nv.write_block(handle, ADDR, _zone_packet(r, g, b, z, bval), port)
        results.append({"zone": z, "status": st})
        if st != 0:
            raise RuntimeError(f"Zone {z} : write status={st}")
        time.sleep(0.12)
    saved = None
    if do_save:
        time.sleep(0.12)
        st = nv.write_block(handle, ADDR, _save_packet(), port)
        saved = st
        if st != 0:
            raise RuntimeError(f"Save : write status={st}")
    return {"brightness_raw": bval, "zones": results, "save_status": saved}


def apply_effect(nv, handle, mode, brightness10=10, speed_ui=4, color=(0, 0, 0), port=1):
    """Effet calculé par la carte (une passe). Pas d'images ensuite : pas de clignotement."""
    bval = max(1, min(10, int(brightness10)))
    spd = speed_from_ui(speed_ui)
    r, g, b = color
    results = []
    for z in HW_ZONES:
        st = nv.write_block(
            handle, ADDR, _zone_packet(r, g, b, z, bval, spd, mode), port)
        results.append({"zone": z, "status": st})
        if st != 0:
            raise RuntimeError(f"Zone {z} : write status={st}")
        time.sleep(0.12)
    return {"mode": mode, "speed_raw": spd, "brightness_raw": bval, "zones": results}


def apply_wave(nv, handle, brightness10=10, speed_ui=4, port=1):
    """Arc-en-ciel matériel du 5080 (mode Wave GCC), une fois par zone.

    La carte anime le spectre autour des ventilateurs. On n'enregistre pas :
    quitter l'effet revient au statique.
    """
    return apply_effect(nv, handle, MODE_WAVE, brightness10, speed_ui, port=port)


def apply_direct(nv, handle, colors, brightness10=10, port=1):
    """Couleurs immédiates (registre 0x16).

    Le registre 0x12 réécrit le mode à chaque zone : la carte clignote et
    ne suit pas plus de 9 màj/s. Le direct ne relance pas l'effet.
    """
    bval = max(1, min(10, int(brightness10)))
    cols = list(colors)
    if not cols:
        cols = [(0, 0, 0)]
    if len(cols) < 6:
        cols = cols + [cols[-1]] * (6 - len(cols))
    results = []
    for z, (r, g, b) in enumerate(cols[:6]):
        st = nv.write_block(
            handle, ADDR,
            _zone_packet(r, g, b, z, bval, SPEED_NORMAL, MODE_DIRECT, REG_COLOR),
            port)
        results.append({"zone": z, "status": st})
        if st != 0:
            raise RuntimeError(f"Zone {z} : write status={st}")
        time.sleep(0.01)
    return {"mode": "direct", "brightness_raw": bval, "zones": results}


def apply_static(nv, handle, r, g, b, brightness10=10, do_save=False, port=1):
    """Applique une couleur statique à toutes les zones hw. Retourne le détail."""
    return apply_zones(nv, handle, [(r, g, b)] * 6, brightness10, do_save, port)
