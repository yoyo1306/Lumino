"""
Lumino - backend ASUS Aura USB (carte mère + headers ARGB, ex WC).
Référence : OpenRGB Controllers/AsusAuraUSBController (GPL-2.0-or-later,
Martin Hartl + contributeurs). Testé sur ROG Strix B850-A (0B05:19AF).

- HID 65 octets, premier octet 0xEC.
- Firmware : [EC 82 ...] -> réponse [?, 0x02, 16 octets ASCII...].
- Config   : [EC B0 ...] -> réponse [?, 0x30, ?, ?, 60 octets ...].
  config[0x02] = nb headers adressables, [0x1B] = nb LEDs fixes totales,
  [0x1D] = nb headers RGB.
- Canaux : device fixe -> direct_channel 0x04 ; adressables i -> direct i.
- Direct par LEDs : [EC 40 (0x80|dev si dernier), offset, count, RGB...] (20 max/paquet).
- Static persisté : SetGen1 [EC 52 53 00 01], par canal SendEffect [EC 35 ch 00 00 mode],
  SendColor [EC 36 mask_hi mask_lo 00 RGB...], puis SendCommit [EC 3F 55].
"""
import hid
import time

VID, PID = 0x0B05, 0x19AF
REPORT = 65
HEAD = 0xEC

REQ_FW = 0x82
REQ_CFG = 0xB0
CTL_DIRECT = 0x40
EFF = 0x35
EFF_COLOR = 0x36
COMMIT = 0x3F
MODE_STATIC = 1
MODE_DIRECT = 0xFF
ADDR_LED_CAP = 60  # LEDs poussées par header adressable (WC, surplus ignoré)
WC_LAG_COMP = 0.20  # compense le rendu firmware CM (commit) plus lent que le direct WC


class Aura:
    def __init__(self, path=None):
        if path is None:
            devs = hid.enumerate(VID, PID)
            if not devs:
                raise RuntimeError("AURA LED Controller (0B05:19AF) introuvable")
            # préfère l'interface de contrôle (MI_02 sur la B850-A)
            devs.sort(key=lambda d: 0 if "MI_02" in (d.get("path") or b"").decode(errors="ignore") else 1)
            path = devs[0]["path"]
        self.dev = hid.device()
        self.dev.open_path(path)
        self.dev.set_nonblocking(False)
        self.path = path
        self.version = self.firmware()
        self.cfg = self.config()
        n_fixed = self.cfg[0x1B]
        n_rgb = self.cfg[0x1D]
        n_addr = self.cfg[0x02]
        if n_fixed < n_rgb:
            n_rgb = 0
        self.channels = []
        if n_fixed > 0:
            self.channels.append({"kind": "fixed", "effect": 0, "direct": 0x04, "leds": n_fixed})
        for i in range(min(n_addr, 8)):
            self.channels.append({"kind": "addr", "effect": len(self.channels), "direct": i, "leds": ADDR_LED_CAP})

    def close(self):
        try:
            self.dev.close()
        except Exception:
            pass

    # --- bas niveau -------------------------------------------------
    def _xfer(self, payload: bytes, read=True):
        buf = bytearray(REPORT)
        buf[0] = HEAD
        buf[1:1 + len(payload)] = payload
        if self.dev.write(bytes(buf)) != REPORT:
            raise RuntimeError("hid_write incomplet")
        if not read:
            return None
        # hidapi lit report_id + 64 ; on normalise à 65 octets
        r = bytes(self.dev.read(REPORT, timeout_ms=500))
        if len(r) == REPORT - 1:
            r = b"\x00" + r
        return r

    def firmware(self):
        r = self._xfer(bytes([REQ_FW]))
        if len(r) < 18 or r[1] != 0x02:
            raise RuntimeError(f"Firmware inattendu: {r[:8].hex()}")
        return bytes(r[2:18]).decode(errors="ignore").strip("\x00")

    def config(self):
        r = self._xfer(bytes([REQ_CFG]))
        if len(r) < 64 or r[1] != 0x30:
            raise RuntimeError(f"Config inattendue: {r[:8].hex()}")
        return bytes(r[4:64])

    # --- effets ------------------------------------------------------
    def gen1(self):
        self._xfer(bytes([0x52, 0x53, 0x00, 0x01]), read=False)

    def send_effect(self, channel, mode):
        self._xfer(bytes([EFF, channel & 0xFF, 0x00, 0x00, mode & 0xFF]), read=False)

    def send_direct(self, device, colors):
        """colors : liste de (r,g,b). 20 max par paquet, dernier flagué 0x80."""
        offset = 0
        n = len(colors)
        while offset < n:
            chunk = colors[offset:offset + 20]
            last = offset + len(chunk) >= n
            pkt = bytearray([CTL_DIRECT, ((0x80 if last else 0) | (device & 0x7F)), offset, len(chunk)])
            for (r, g, b) in chunk:
                pkt += bytes((r & 0xFF, g & 0xFF, b & 0xFF))
            self._xfer(bytes(pkt), read=False)
            offset += len(chunk)

    def send_commit(self):
        self._xfer(bytes([COMMIT, 0x55]), read=False)

    # --- haut niveau --------------------------------------------------
    def describe(self):
        return {"fw": self.version,
                "cfg": {"fixed_leds": self.cfg[0x1B], "rgb_headers": self.cfg[0x1D],
                        "addr_headers": self.cfg[0x02]},
                "channels": self.channels}

    def set_both(self, r, g, b):
        """CM (static) puis WC (direct) : la CM se fige au commit juste avant le WC."""
        wc = next(c for c in self.channels if c["kind"] == "addr")
        n = next(c["leds"] for c in self.channels if c["kind"] == "fixed")
        self.gen1()
        self.send_effect(0, MODE_STATIC)
        mask = (1 << n) - 1
        pkt = bytes([EFF_COLOR, (mask >> 8) & 0xFF, mask & 0xFF, 0x00])
        pkt += bytes((r & 0xFF, g & 0xFF, b & 0xFF)) * n
        self._xfer(pkt, read=False)
        self.send_commit()
        import time as _t
        _t.sleep(WC_LAG_COMP)
        self.send_effect(wc["effect"], MODE_DIRECT)
        self.send_direct(wc["direct"], [(r, g, b)] * wc["leds"])

    def set_cm(self, r, g, b):
        """Carte mère (LEDs fixes) en static persisté."""
        import time as _t
        self.gen1()
        _t.sleep(0.02)
        n = next(c["leds"] for c in self.channels if c["kind"] == "fixed")
        self.send_effect(0, MODE_STATIC)
        _t.sleep(0.02)
        mask = (1 << n) - 1
        pkt = bytes([EFF_COLOR, (mask >> 8) & 0xFF, mask & 0xFF, 0x00])
        pkt += bytes((r & 0xFF, g & 0xFF, b & 0xFF)) * n
        self._xfer(pkt, read=False)
        _t.sleep(0.02)
        self.send_commit()

    def set_wc(self, r, g, b):
        """WC (header ARGB 1) en direct (volatile : ré-appliquer après reboot)."""
        import time as _t
        wc = next(c for c in self.channels if c["kind"] == "addr")
        self.send_effect(wc["effect"], MODE_DIRECT)
        _t.sleep(0.02)
        self.send_direct(wc["direct"], [(r, g, b)] * wc["leds"])

    def direct_all(self, r, g, b):
        """Couleur immédiate partout (volatile)."""
        for ch in self.channels:
            self.send_effect(ch["effect"], MODE_DIRECT)
            time.sleep(0.02)
            self.send_direct(ch["direct"], [(r, g, b)] * ch["leds"])
            time.sleep(0.02)

    def static_all(self, r, g, b):
        """Static persisté (effect + commit). Adressables via direct ( nb LEDs fixe)."""
        self.gen1()
        time.sleep(0.02)
        for ch in self.channels:
            self.send_effect(ch["effect"], MODE_STATIC)
            time.sleep(0.02)
            if ch["kind"] == "fixed":
                # SendColor : masque sur les LEDs fixes (comme OpenRGB)
                start = 0
                mask = ((1 << ch["leds"]) - 1) << start
                pkt = bytearray([EFF_COLOR, (mask >> 8) & 0xFF, mask & 0xFF, 0x00])
                pkt += bytes((r & 0xFF, g & 0xFF, b & 0xFF)) * ch["leds"]
                self._xfer(bytes(pkt), read=False)
            else:
                self.send_effect(ch["effect"], MODE_DIRECT)
                time.sleep(0.02)
                self.send_direct(ch["direct"], [(r, g, b)] * ch["leds"])
            time.sleep(0.02)
        self.send_commit()
