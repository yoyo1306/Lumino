# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Envoi JPEG vers l'ecran Corsair (HID separe du hub LEDs).
# Adapte de HydroScreen, UDPSendToFailed, MPL-2.0 :
# src-tauri/src/aio_communicator/device.rs

"""Ecran LCD Corsair (Elite / LINK Titan), pas l'anneau de LEDs.

Le hub iCUE LINK est 0x0C3F. L'ecran est un autre peripherique USB
(0x0C4E sur le Titan). iCUE et HydroScreen doivent etre fermes : un seul
programme peut tenir cet HID.
"""
import io
import os

import effects

try:
    import hid
except ImportError as e:
    raise RuntimeError("hidapi manquant : pip install hidapi") from e

VID = 0x1B1C
# Titan / iCUE LINK en premier : c'est l'ecran de ce PC.
PIDS = (0x0C4E, 0x0C39, 0x0C33, 0x0C42)
IMG_TX = 0x02
PACKET = 1024
HEADER = 8


def build_packets(jpeg):
    """Paquets HID de 1024 o. Le premier octet est l'id de report 0x02."""
    data = bytes(jpeg)
    if not data:
        raise ValueError("JPEG vide")
    chunk_cap = PACKET - HEADER
    packets = []
    part = 0
    for offset in range(0, len(data), chunk_cap):
        chunk = data[offset:offset + chunk_cap]
        is_end = 1 if offset + len(chunk) >= len(data) else 0
        pkt = bytearray(PACKET)
        pkt[0] = IMG_TX
        pkt[1] = 0x05
        pkt[2] = 0x40
        pkt[3] = is_end
        pkt[4:6] = part.to_bytes(2, "little")
        pkt[6:8] = len(chunk).to_bytes(2, "little")
        pkt[HEADER:HEADER + len(chunk)] = chunk
        packets.append(bytes(pkt))
        part += 1
    return packets


def _brightness_raw(percent):
    """0–100 % → registre 0x00–0x40.

    Les quatre crans 0x01 / 0x04 / 0x10 / 0x40 laissaient 84–100 %
    identiques, puis un saut vers le quart de la luminosité.
    """
    p = max(0, min(100, int(percent)))
    return (p * 0x40 + 50) // 100


def _font(size, bold=False):
    from PIL import ImageFont
    name = "segoeuib.ttf" if bold else "segoeui.ttf"
    windir = os.environ.get("WINDIR", r"C:\Windows")
    path = os.path.join(windir, "Fonts", name)
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def compose_image(src, scale, size=480):
    """Centre l'image. 100 couvre le carre, au-dessus zoome, en dessous retrecit."""
    from PIL import Image
    scale = max(25, min(200, int(scale)))
    img = src.convert("RGB")
    cover = max(size / img.width, size / img.height)
    factor = cover * (scale / 100.0)
    nw = max(1, int(round(img.width * factor)))
    nh = max(1, int(round(img.height * factor)))
    resample = getattr(Image, "Resampling", Image).LANCZOS
    img = img.resize((nw, nh), resample)
    canvas = Image.new("RGB", (size, size), (0, 0, 0))
    canvas.paste(img, ((size - nw) // 2, (size - nh) // 2))
    return canvas


def _jpeg(img, quality=85):
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _source_frames(im):
    """Images RGB entières. Un GIF animé garde le délai de chaque image, en ms."""
    from PIL import Image
    n = getattr(im, "n_frames", 1) or 1
    animated = n > 1 and (im.format or "").upper() == "GIF"
    im.seek(0)
    if not animated:
        yield im.convert("RGB"), None
        return
    width, height = im.size
    for i in range(n):
        im.seek(i)
        frame = im.convert("RGBA")
        if frame.size != (width, height):
            canvas = Image.new("RGBA", (width, height))
            canvas.paste(frame, (0, 0))
            frame = canvas
        try:
            dur = int(im.info.get("duration") or 100)
        except (TypeError, ValueError):
            dur = 100
        if dur <= 0:
            dur = 100
        yield frame.convert("RGB"), max(20, dur)


def read_preview_frames(path):
    """Images source pour l'aperçu. Le second retour est le délai en ms, ou None."""
    from PIL import Image
    with Image.open(path) as im:
        frames = []
        durs = []
        for frame, dur in _source_frames(im):
            frames.append(frame)
            durs.append(dur)
    return frames, durs


def load_lcd_frames(path, scale, size=480, tick=None):
    """JPEG prêts pour l'écran. Délai en secondes, ou None si l'image est fixe."""
    from PIL import Image
    with Image.open(path) as im:
        out = []
        for i, (frame, dur) in enumerate(_source_frames(im)):
            if tick and i % 4 == 0:
                tick()
            jpeg = _jpeg(compose_image(frame, scale, size))
            out.append((jpeg, None if dur is None else dur / 1000.0))
    if not out:
        raise ValueError("image vide")
    return out


def render_image_jpeg(path, scale, size=480):
    """JPEG 480x480. Une image fixe, ou la première image d'un GIF."""
    return load_lcd_frames(path, scale, size)[0][0]


def render_temp_jpeg(temp_c, size=480):
    """JPEG 480x480 : temperature GPU, couleur de l'echelle Lumino."""
    from PIL import Image, ImageDraw
    col = effects.temp_color(temp_c)
    img = Image.new("RGB", (size, size), (8, 10, 16))
    draw = ImageDraw.Draw(img)
    margin = 36
    draw.ellipse((margin, margin, size - margin, size - margin), outline=col, width=18)
    if temp_c is None:
        label = "--"
    else:
        label = str(int(round(float(temp_c))))
    font = _font(168, bold=True)
    small = _font(36, bold=False)
    draw.text((size / 2, size / 2 - 16), label, fill=col, font=font, anchor="mm")
    draw.text((size / 2, size / 2 + 118), "GPU  °C", fill=(210, 214, 220), font=small, anchor="mm")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


class Lcd:
    def __init__(self):
        self.dev = hid.device()
        last = None
        for pid in PIDS:
            try:
                self.dev.open(VID, pid)
                self.dev.set_nonblocking(False)
                self.pid = pid
                return
            except OSError as e:
                last = e
        raise RuntimeError(f"Ecran LCD Corsair introuvable ({last})")

    def close(self):
        try:
            self.dev.close()
        except OSError:
            pass

    def _feature(self, payload):
        pkt = bytearray(32)
        pkt[0] = 0x03
        pkt[1:1 + len(payload)] = payload
        if self.dev.send_feature_report(bytes(pkt)) < 0:
            raise RuntimeError("feature LCD refuse")

    def set_brightness(self, percent, persist=False):
        raw = _brightness_raw(percent)
        self._feature(bytes((0x0B, raw)))
        if persist:
            self._feature(bytes((0x19, raw)))

    def restore_hardware(self):
        """Repasse sur l'image stockee dans l'ecran, sans attendre le firmware.

        0x1e puis 0x1d : sortie du mode image logicielle. Sans ces reports,
        l'ecran ne reprend le fond qu'apres un long delai une fois l'HID lache.
        """
        last = None
        for payload in (bytes((0x1E, 0x01, 0x01)), bytes((0x1D, 0x00, 0x01))):
            try:
                self._feature(payload)
            except Exception as e:  # noqa: BLE001
                last = e
        if last is not None:
            raise last

    def send_jpeg(self, jpeg):
        for pkt in build_packets(jpeg):
            written = self.dev.write(pkt)
            if written != len(pkt):
                raise RuntimeError(f"ecriture LCD incomplete ({written}/{len(pkt)})")


def release_to_hardware():
    """Ouvre l'ecran, rend le fond enregistre, referme."""
    lcd = Lcd()
    try:
        lcd.restore_hardware()
    finally:
        lcd.close()
