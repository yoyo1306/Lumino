"""
Lumino - presets style iCUE (reproduction) pour le Hub LINK 60 LEDs.
Layout lineaire (ordre d'ecriture HID) : [pompe 20][anneau LCD 24][RX1 8][RX2 8].

Presets iCUE exacts reproduits :
  static          Couleur statique (unie).
  rainbow         Arc-en-ciel : tout le Hub sur la meme teinte qui tourne.
  rainbow_wave    Vague arc-en-ciel : spectre etale qui defile.
  color_wave      Vague couleur : vague de la couleur choisie qui traverse.
  color_pulse     Pulsation : tout pulse doucement (couleur choisie).
  ripple          Ondulation : anneaux emis depuis le centre, couleur choisie.
  temperature     Temperature : couleur selon temp GPU (bleu->rouge).

Non reproduits (selecteur 2e couleur requis) : Degrade, Changement couleur.
Niche : Stroboscope, Battement, Sequentiel, Defilement (sur demande).

speed 1..10 -> cycles/s = speed * 0.03.
Le hub limite le debit (~30 ms entre commandes) : une animation
tourne vers 5 images/s. Rain est stateful : passer un EffectState()
partage entre frames.
brightness100 est applique une seule fois. Passer la teinte brute
(pas deja attenuee) et le pourcentage reel.
"""
import colorsys
import math

MODES = ("static", "rainbow", "rainbow_wave",
         "color_wave", "color_pulse", "ripple", "temperature")
MODE_LABELS = {"static": "Statique",
               "rainbow": "Arc-en-ciel",
               "rainbow_wave": "Vague arc-en-ciel",
               "color_wave": "Vague couleur",
               "color_pulse": "Pulsation",
               "ripple": "Ondulation",
               "temperature": "Température"}
LABEL_TO_MODE = {v: k for k, v in MODE_LABELS.items()}

# Echelle Temperature iCUE (defaut) : seuils °C -> RGB.
TEMP_STOPS = [(30, (0, 150, 255)),
              (45, (0, 255, 150)),
              (60, (255, 220, 0)),
              (72, (255, 120, 0)),
              (82, (255, 0, 0))]


class EffectState:
    """Etat partage entre frames (Pluie). Un par animateur."""
    def __init__(self):
        self.drops = []  # [pos, naissance_t]
        self.last_t = None


def _scale(color, brightness100):
    f = max(0, min(100, int(brightness100))) / 100
    r, g, b = color
    return (round(r * f), round(g * f), round(b * f))


def _hue_rgb(h):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, 1.0, 1.0)
    return (round(r * 255), round(g * 255), round(b * 255))


def _lerp(c1, c2, k):
    return (round(c1[0] + (c2[0] - c1[0]) * k),
            round(c1[1] + (c2[1] - c1[1]) * k),
            round(c1[2] + (c2[2] - c1[2]) * k))


def render_aura_hue_wave(n, period, phase, brightness100, shift=0.0):
    """Dent de scie Aura Sync (RainbowSingle). shift fait defiler la teinte."""
    period = max(1, float(period))
    return [
        _scale(_hue_rgb(((i + phase) / period + shift) % 1.0), brightness100)
        for i in range(n)
    ]


def temp_color(temp_c):
    """Couleur echelle Temperature (fallback 40 degres si sonde KO)."""
    t = 40.0 if temp_c is None else float(temp_c)
    if t <= TEMP_STOPS[0][0]:
        return TEMP_STOPS[0][1]
    for (t0, c0), (t1, c1) in zip(TEMP_STOPS, TEMP_STOPS[1:]):
        if t <= t1:
            return _lerp(c0, c1, (t - t0) / (t1 - t0))
    return TEMP_STOPS[-1][1]


def render(n, mode, t, color=(255, 255, 255), brightness100=100, speed=4,
           state=None, temp_c=None, rate=None):
    """Retourne [(r,g,b)] * n pour l'instant t (secondes)."""
    if mode not in MODES:
        mode = "static"
    if rate is None:
        cps = max(1, min(10, int(speed))) * 0.03
    else:
        cps = max(0.005, float(rate))
    # Arc-en-ciel : le cran 4 prend l'ancienne vitesse du cran 2.
    if mode == "rainbow" and rate is None:
        cps *= 0.5
    base = _scale(color, brightness100)

    if mode == "static":
        return [base] * n

    if mode == "rainbow":
        return [_scale(_hue_rgb((t * cps) % 1.0), brightness100)] * n

    if mode == "rainbow_wave":
        return [_scale(_hue_rgb(i / max(1, n) + t * cps), brightness100)
                for i in range(n)]

    if mode == "color_wave":
        out = []
        for i in range(n):
            w = 0.5 + 0.5 * math.sin(2 * math.pi * (i / max(1, n) - t * cps))
            k = 0.08 + 0.92 * w ** 1.5
            out.append((round(base[0] * k), round(base[1] * k), round(base[2] * k)))
        return out

    if mode == "color_pulse":
        w = 0.5 + 0.5 * math.sin(2 * math.pi * t * cps * 0.5)
        k = 0.10 + 0.90 * w ** 1.5
        return [(round(base[0] * k), round(base[1] * k), round(base[2] * k))] * n

    if mode == "ripple":
        # Anneaux emis depuis le centre, un par cycle.
        p = (t * cps) % 1.0
        out = []
        for i in range(n):
            d = abs(i / max(1, n - 1) - 0.5)
            g = math.exp(-(((d - p * 0.5) / 0.045) ** 2))
            k = min(1.0, g)
            out.append((round(base[0] * k), round(base[1] * k), round(base[2] * k)))
        return out

    if mode == "temperature":
        col = _scale(temp_color(temp_c), brightness100)
        return [col] * n

    return [base] * n
