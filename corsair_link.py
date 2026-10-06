"""
Lumino - backend Corsair iCUE LINK System Hub (boitier iCUE).
Reference : OpenRGB Controllers/CorsairICueLinkController (GPL-2.0-or-later,
Aiden Vigue / CalcProgrammer1 + contributeurs).

- USB HID VID 0x1B1C PID 0x0C3F, usage_page 0xFF42 usage 0x01.
- Report write 513 o (report_id 0x00 + 512), read 512 o, timeout 1000 ms.
- Delai ~30 ms entre commandes (timing critique, cf iCUE-LINK-Telemetry-Reader).
- Init : SOFTWARE_MODE, list endpoints via GET_DEVICES, DIRECT volatile
  (pas de save cote Hub ; le Hub retombe en hard/rainbow sans keepalive,
  d'ou iCUE Device Memory Mode ou image LCD hardware pour la persistance).
- Layout ecriture (comme OpenRGB RGBController, qui AJOUTE la zone LCD
  aux couleurs pour les AIO H/TITAN) :
  [pompe TITAN 20][anneau LCD 24][RX1 8][RX2 8] = 60 LEDs.
  L'anneau LCD (type 0x06) n'est PAS l'ecran (module 0C4E en USB-C separe,
  gere par HydroScreen / image hardware) : juste ses LEDs.
- Layout constate chez kevin : TITAN 240 (0x11/20) + 2x RX RGB (0x0F/8).

Usage lecture seule (sans rien changer) :
  python -c "import corsair_link; c=corsair_link.CorsairLink(); print(c.describe()); c.close()"
Pre-requis : fermer iCUE avant (acces exclusif HID), sinon device busy.
"""
import time

try:
    import hid
except ImportError as e:
    raise RuntimeError("hidapi manquant : pip install hidapi") from e

VID, PID = 0x1B1C, 0x0C3F
USAGE_PAGE = 0xFF42
USAGE = 0x01

BUF_WRITE = 513
BUF_READ = 512

CMD_OPEN_ENDPOINT = bytes([0x0D, 0x01])
CMD_OPEN_COLOR_ENDPOINT = bytes([0x0D, 0x00])
CMD_CLOSE_ENDPOINT = bytes([0x05, 0x01, 0x01])
CMD_GET_FIRMWARE = bytes([0x02, 0x13])
CMD_SOFTWARE_MODE = bytes([0x01, 0x03, 0x00, 0x02])
CMD_HARDWARE_MODE = bytes([0x01, 0x03, 0x00, 0x01])
CMD_WRITE = bytes([0x06, 0x01])
CMD_WRITE_COLOR = bytes([0x06, 0x00])
CMD_WRITE_COLOR_NEXT = bytes([0x07, 0x00])
CMD_READ = bytes([0x08, 0x01])

MODE_GET_DEVICES = bytes([0x36])
MODE_SET_COLOR = bytes([0x22])

DTYPE_GET_DEVICES = bytes([0x21, 0x00])
DTYPE_SET_COLOR = bytes([0x12, 0x00])

WRITE_HEADER = 4
MAX_PER_REQUEST = 508

KNOWN = {
    (0x11, 0x00): ("iCUE LINK TITAN 240", 20),
    (0x11, 0x04): ("iCUE LINK TITAN 240", 20),
    (0x11, 0x01): ("iCUE LINK TITAN 280", 20),
    (0x11, 0x02): ("iCUE LINK TITAN 360", 20),
    (0x11, 0x05): ("iCUE LINK TITAN 360", 20),
    (0x11, 0x03): ("iCUE LINK TITAN 420", 20),
    (0x07, 0x00): ("iCUE LINK H100i RGB", 20),
    (0x07, 0x04): ("iCUE LINK H100i RGB", 20),
    (0x07, 0x01): ("iCUE LINK H115i RGB", 20),
    (0x07, 0x02): ("iCUE LINK H150i RGB", 20),
    (0x07, 0x05): ("iCUE LINK H150i RGB", 20),
    (0x07, 0x03): ("iCUE LINK H170i RGB", 20),
    (0x0F, 0x00): ("iCUE LINK RX RGB", 8),
    (0x03, 0x00): ("iCUE LINK RX RGB MAX", 8),
    (0x02, 0x00): ("iCUE LINK LX RGB", 18),
    (0x01, 0x00): ("iCUE LINK QX RGB", 34),
    (0x06, 0x00): ("iCUE LINK COOLER PUMP LCD", 24),
    (0x05, 0x02): ("iCUE LINK 5000T RGB", 160),
}


class CorsairLink:
    def __init__(self, path=None):
        devs = hid.enumerate(VID, PID)
        if not devs:
            raise RuntimeError("System Hub iCUE LINK (1B1C:0C3F) introuvable")
        # Prefere l'interface controle usage_page FF42 / usage 01 (detecteur OpenRGB).
        def _score(d):
            try:
                up = int(d.get("usage_page") or 0)
                u = int(d.get("usage") or 0)
            except (TypeError, ValueError):
                up, u = 0, 0
            return 0 if (up == USAGE_PAGE and u == USAGE) else 1
        devs = sorted(devs, key=_score)
        if path is None:
            path = devs[0]["path"]
        self.dev = hid.device()
        self.dev.open_path(path)
        try:
            self.dev.set_nonblocking(False)
        except Exception:
            pass
        self.path = path
        self.firmware = self._firmware()
        self._software_mode()
        self.endpoints = self._devices()
        self.total_leds = sum(e["leds"] for e in self.endpoints)

    def close(self, hardware_mode=False):
        try:
            if hardware_mode:
                try:
                    self._send(CMD_HARDWARE_MODE, b"")
                except Exception:
                    pass
            self.dev.close()
        except Exception:
            pass

    # --- bas niveau -------------------------------------------------
    def _send(self, cmd: bytes, data: bytes, wait_for: bytes | None = None):
        buf = bytearray(BUF_WRITE)
        buf[0] = 0x00  # report id
        buf[2] = 0x01
        buf[3:3 + len(cmd)] = cmd
        buf[3 + len(cmd):3 + len(cmd) + len(data)] = data
        if self.dev.write(bytes(buf)) != BUF_WRITE:
            raise RuntimeError("hid_write incomplet (Hub LINK)")
        time.sleep(0.03)  # delai critique : le Hub ignore les rafales
        res = bytes(self.dev.read(BUF_READ, timeout_ms=1000))
        if wait_for and len(wait_for) == 2:
            for _ in range(5):
                if len(res) >= 6 and res[4] == wait_for[0]:
                    break
                res = bytes(self.dev.read(BUF_READ, timeout_ms=1000))
        return res

    def _read_endpoint(self, mode: bytes, dtype: bytes):
        self._send(CMD_CLOSE_ENDPOINT, mode)
        self._send(CMD_OPEN_ENDPOINT, mode)
        res = self._send(CMD_READ, b"", wait_for=dtype)
        self._send(CMD_CLOSE_ENDPOINT, mode)
        return res

    def _firmware(self):
        r = self._send(CMD_GET_FIRMWARE, b"")
        if len(r) < 8:
            return "?"
        return f"v{r[4]}.{r[5]}.{r[6] | (r[7] << 8)}"

    def _software_mode(self):
        self._send(CMD_SOFTWARE_MODE, b"")

    def enter_software_mode(self):
        """Reprend la main. iCUE, en se fermant, remet le hub en mode memoire."""
        self._software_mode()

    def _devices(self):
        r = self._read_endpoint(MODE_GET_DEVICES, DTYPE_GET_DEVICES)
        if len(r) < 8:
            raise RuntimeError(f"GET_DEVICES reponse courte ({len(r)} o)")
        n_channels = r[6]
        self.raw_channels = n_channels
        idx = bytes(r[7:])
        eps = []
        self.raw_metas = []
        pos = 0
        for _ in range(1, n_channels + 1):
            if pos + 8 > len(idx):
                break
            meta = idx[pos:pos + 8]
            id_len = meta[7]
            self.raw_metas.append({"type": meta[2], "model": meta[3],
                                   "id_len": id_len,
                                   "meta": bytes(meta).hex()})
            if id_len == 0:
                pos += 8
                continue
            if pos + 8 + id_len > len(idx):
                break
            ep_id = idx[pos + 8:pos + 8 + id_len]
            typ, model = meta[2], meta[3]
            name, leds = KNOWN.get((typ, model), (f"LINK {typ:02X}:{model:02X}", 0))
            if leds <= 0:
                pos += 8 + id_len
                continue
            eps.append({"type": typ, "model": model, "name": name,
                        "leds": leds,
                        "id": bytes(ep_id).decode(errors="ignore"),
                        "lcd": typ == 0x06})
            pos += 8 + id_len
        # Garde TOUT (dont anneau LCD 0x06) : OpenRGB l'inclut dans le
        # buffer couleurs pour les AIO H/TITAN. Si l'anneau LCD n'est pas
        # enumere mais que le Hub l'attend, il est ajoute apres la pompe.
        out = list(eps)
        if any(e["type"] in (0x07, 0x11) for e in out) \
                and not any(e.get("lcd") for e in out):
            out.insert(1, {"type": 0x06, "model": 0x00,
                           "name": "iCUE LINK COOLER PUMP LCD (anneau, implicite)",
                           "leds": 24, "id": "", "lcd": True,
                           "implicit": True})
        return out

    # --- haut niveau --------------------------------------------------
    def describe(self):
        return {"firmware": self.firmware,
                "raw_channels": getattr(self, "raw_channels", "?"),
                "raw_metas": getattr(self, "raw_metas", []),
                "total_leds": self.total_leds,
                "endpoints": self.endpoints}

    def set_segments(self, segments, settle=True):
        """Ecrit un buffer segmente [(r,g,b)] de len == total_leds.

        settle attend 50 ms après l'écriture. Inutile pendant une animation :
        les 30 ms entre commandes suffisent, et l'attente en plus saccade.
        """
        n = self.total_leds
        if len(segments) != n:
            raise RuntimeError(f"segments={len(segments)}, attendu={n}")
        colors = b"".join(bytes((r & 0xFF, g & 0xFF, b & 0xFF))
                          for (r, g, b) in segments)
        buf = bytearray(WRITE_HEADER + len(DTYPE_SET_COLOR) + len(colors))
        data_len = len(colors) + 2
        buf[0] = data_len & 0xFF
        buf[1] = (data_len >> 8) & 0xFF
        # buf[2..3] = header reserve (0)
        buf[WRITE_HEADER:WRITE_HEADER + len(DTYPE_SET_COLOR)] = DTYPE_SET_COLOR
        buf[WRITE_HEADER + len(DTYPE_SET_COLOR):] = colors
        payload = bytes(buf)
        self._send(CMD_CLOSE_ENDPOINT, MODE_SET_COLOR)
        self._send(CMD_OPEN_COLOR_ENDPOINT, MODE_SET_COLOR)
        for i in range(0, len(payload), MAX_PER_REQUEST):
            chunk = payload[i:i + MAX_PER_REQUEST]
            if i == 0:
                self._send(CMD_WRITE_COLOR, chunk)
            else:
                self._send(CMD_WRITE_COLOR_NEXT, chunk)
        self._send(CMD_CLOSE_ENDPOINT, MODE_SET_COLOR)
        if settle:
            time.sleep(0.05)
        return {"leds": n, "mode": "direct-volatile"}

    def led_spans(self):
        """(role, nombre de LEDs) dans l'ordre du buffer.

        block = pompe + anneau LCD. fans = le reste (les deux RX).
        """
        spans = []
        for e in self.endpoints:
            role = "block" if e.get("lcd") or e["type"] in (0x06, 0x07, 0x11) else "fans"
            spans.append((role, int(e["leds"])))
        return spans

    def set_static(self, r, g, b):
        """Meme couleur partout (pompe + anneau LCD + ventilos)."""
        n = self.total_leds
        if n <= 0:
            raise RuntimeError("Aucune LED LINK RGB detectee")
        return self.set_segments([(r, g, b)] * n)
