"""Philips Evnia Ambiglow.

Les modes du menu OSD passent par le DisplayPort (DDC).
Les LEDs une par une passent par le MCU ENE en USB (0CF2:A201),
câble amont branché, OSD en Static Mode.

Protocole repris d'open-ambiglow (ENE 6K7732, registre 0xC450).
Layout 27M2N8500 : 30 LEDs, transcrit du tableau Philips, pas encore
vérifié LED par LED sur ce panneau.
"""
import ctypes
import winreg
from ctypes import (
    POINTER, Structure, WinDLL, byref, c_ubyte, c_ushort, c_void_p, c_wchar_p,
    sizeof,
)
from ctypes.wintypes import BOOL, DWORD, HANDLE, ULONG, WORD

VID = 0x0CF2
PID = 0xA201
ADDR_CHIP = 0x4000
ADDR_SOURCE = 0xC450
MODEL = "27M2N8500"
# Ordre du buffer : right, rightup, leftup, left, center, bottom.
ZONES = (("right", 4), ("rightup", 5), ("leftup", 5),
         ("left", 4), ("center", 12), ("bottom", 0))
LED_COUNT = sum(n for _name, n in ZONES)

RT_WRITE, REQ_WRITE = 0x40, 0x80
RT_READ, REQ_READ = 0xC0, 0x81

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x80
DIGCF_PRESENT = 0x2
DIGCF_DEVICEINTERFACE = 0x10
INVALID_HANDLE = HANDLE(-1).value

kernel32 = WinDLL("kernel32", use_last_error=True)
setupapi = WinDLL("setupapi", use_last_error=True)
winusb = WinDLL("winusb", use_last_error=True)


class GUID(Structure):
    _fields_ = [
        ("Data1", DWORD),
        ("Data2", WORD),
        ("Data3", WORD),
        ("Data4", c_ubyte * 8),
    ]


class SP_DEVICE_INTERFACE_DATA(Structure):
    _fields_ = [
        ("cbSize", DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", DWORD),
        ("Reserved", c_void_p),
    ]


class WINUSB_SETUP_PACKET(Structure):
    _pack_ = 1
    _fields_ = [
        ("RequestType", c_ubyte),
        ("Request", c_ubyte),
        ("Value", c_ushort),
        ("Index", c_ushort),
        ("Length", c_ushort),
    ]


kernel32.CreateFileW.argtypes = [
    c_wchar_p, DWORD, DWORD, c_void_p, DWORD, DWORD, HANDLE]
kernel32.CreateFileW.restype = HANDLE
kernel32.CloseHandle.argtypes = [HANDLE]
kernel32.CloseHandle.restype = BOOL

setupapi.SetupDiGetClassDevsW.argtypes = [POINTER(GUID), c_void_p, HANDLE, DWORD]
setupapi.SetupDiGetClassDevsW.restype = HANDLE
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    HANDLE, c_void_p, POINTER(GUID), DWORD, POINTER(SP_DEVICE_INTERFACE_DATA)]
setupapi.SetupDiEnumDeviceInterfaces.restype = BOOL
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    HANDLE, POINTER(SP_DEVICE_INTERFACE_DATA), c_void_p, DWORD,
    POINTER(DWORD), c_void_p]
setupapi.SetupDiGetDeviceInterfaceDetailW.restype = BOOL
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [HANDLE]
setupapi.SetupDiDestroyDeviceInfoList.restype = BOOL

winusb.WinUsb_Initialize.argtypes = [HANDLE, POINTER(HANDLE)]
winusb.WinUsb_Initialize.restype = BOOL
winusb.WinUsb_Free.argtypes = [HANDLE]
winusb.WinUsb_Free.restype = BOOL
winusb.WinUsb_ControlTransfer.argtypes = [
    HANDLE, WINUSB_SETUP_PACKET, c_void_p, ULONG, POINTER(ULONG), c_void_p]
winusb.WinUsb_ControlTransfer.restype = BOOL


class DeviceNotFound(RuntimeError):
    pass


def _guid(text):
    import uuid
    u = uuid.UUID(text)
    g = GUID()
    g.Data1, g.Data2, g.Data3 = u.time_low, u.time_mid, u.time_hi_version
    for i, b in enumerate(u.bytes[8:]):
        g.Data4[i] = b
    return g


def _ene_interface_guids():
    """GUIDs WinUSB déclarés pour les périphériques ENE 0CF2 branchés."""
    found = []
    root = r"SYSTEM\CurrentControlSet\Enum\USB"
    try:
        usb = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root)
    except OSError:
        return found
    try:
        i = 0
        while True:
            try:
                name = winreg.EnumKey(usb, i)
            except OSError:
                break
            i += 1
            upper = name.upper()
            if not upper.startswith("VID_%04X&PID_" % VID):
                continue
            try:
                pid = int(upper.split("PID_")[1][:4], 16)
            except ValueError:
                continue
            dev = winreg.OpenKey(usb, name)
            try:
                j = 0
                while True:
                    try:
                        inst = winreg.EnumKey(dev, j)
                    except OSError:
                        break
                    j += 1
                    try:
                        params = winreg.OpenKey(dev, inst + r"\Device Parameters")
                    except OSError:
                        continue
                    try:
                        raw, _typ = winreg.QueryValueEx(params, "DeviceInterfaceGUIDs")
                    except OSError:
                        raw = []
                    finally:
                        winreg.CloseKey(params)
                    guids = raw if isinstance(raw, list) else [raw]
                    for g in guids:
                        if isinstance(g, str) and g.strip():
                            found.append((pid, g.strip()))
            finally:
                winreg.CloseKey(dev)
    finally:
        winreg.CloseKey(usb)
    found.sort(key=lambda item: 0 if item[0] == PID else 1)
    return found


def _interface_paths(guid):
    handle = setupapi.SetupDiGetClassDevsW(
        byref(guid), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if handle == INVALID_HANDLE or not handle:
        return
    try:
        index = 0
        while True:
            data = SP_DEVICE_INTERFACE_DATA()
            data.cbSize = sizeof(SP_DEVICE_INTERFACE_DATA)
            if not setupapi.SetupDiEnumDeviceInterfaces(
                    handle, None, byref(guid), index, byref(data)):
                break
            index += 1
            need = DWORD(0)
            setupapi.SetupDiGetDeviceInterfaceDetailW(
                handle, byref(data), None, 0, byref(need), None)
            if need.value < 8:
                continue
            buf = (c_ubyte * need.value)()
            # cbSize = 8 sur 64 bits, 6 sur 32 bits.
            cb = 8 if sizeof(c_void_p) == 8 else 6
            buf[0] = cb & 0xFF
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(
                    handle, byref(data), buf, need, None, None):
                continue
            path = bytes(buf[4:]).split(b"\x00\x00", 1)[0]
            try:
                yield path.decode("utf-16-le")
            except UnicodeDecodeError:
                continue
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(handle)


def _xfer(iface, request_type, request, addr, payload):
    packet = WINUSB_SETUP_PACKET()
    packet.RequestType = request_type
    packet.Request = request
    packet.Value = (addr >> 16) & 0xFFFF
    packet.Index = addr & 0xFFFF
    packet.Length = len(payload)
    moved = ULONG(0)
    buf = (c_ubyte * len(payload)).from_buffer_copy(payload)
    ok = winusb.WinUsb_ControlTransfer(
        iface, packet, buf, len(payload), byref(moved), None)
    if not ok:
        raise OSError("transfert USB Ambiglow refusé")
    return bytes(buf[:moved.value])


class Evnia:
    """Une session WinUSB. À fermer : un seul programme tient l'interface."""

    def __init__(self):
        self._file = None
        self._usb = None
        self.pid = None
        self._open()

    def _open(self):
        guids = _ene_interface_guids()
        if not guids:
            raise DeviceNotFound(
                "Ambiglow introuvable. Branche le câble USB de l'écran "
                "(le DisplayPort ne suffit pas) et ferme Evnia Precision Center."
            )
        last = None
        for pid, text in guids:
            try:
                guid = _guid(text)
            except ValueError:
                continue
            for path in _interface_paths(guid):
                marker = "VID_%04X&PID_%04X" % (VID, pid)
                if marker not in path.upper():
                    continue
                file_handle = kernel32.CreateFileW(
                    path, GENERIC_READ | GENERIC_WRITE,
                    FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING,
                    FILE_ATTRIBUTE_NORMAL, None)
                if file_handle == INVALID_HANDLE or not file_handle:
                    last = OSError("accès WinUSB refusé")
                    continue
                usb = HANDLE()
                if not winusb.WinUsb_Initialize(file_handle, byref(usb)):
                    kernel32.CloseHandle(file_handle)
                    last = OSError("WinUSB n'a pas pris l'interface")
                    continue
                self._file = file_handle
                self._usb = usb
                self.pid = pid
                try:
                    chip = self.read(ADDR_CHIP, 2)
                except OSError as err:
                    self.close()
                    last = err
                    continue
                if chip != b"\x77\x30":
                    self.close()
                    last = OSError("puce ENE inattendue : %s" % chip.hex())
                    continue
                return
        raise DeviceNotFound(
            "Ambiglow vu par Windows mais inaccessible. "
            "Ferme Evnia Precision Center, puis réessaie."
            if last is None else str(last)
        )

    def read(self, addr, length):
        return _xfer(self._usb, RT_READ, REQ_READ, addr, bytes(length))

    def write(self, addr, data):
        data = bytes(data)
        _xfer(self._usb, RT_WRITE, REQ_WRITE, addr, data)
        return len(data)

    def set_colors(self, colors):
        """colors : LED_COUNT triplets (r, g, b)."""
        if len(colors) != LED_COUNT:
            raise ValueError("il faut %d LEDs, pas %d" % (LED_COUNT, len(colors)))
        raw = bytearray()
        for rgb in colors:
            raw.extend(max(0, min(255, int(c))) for c in rgb)
        self.write(ADDR_SOURCE, raw)

    def close(self):
        if self._usb:
            winusb.WinUsb_Free(self._usb)
            self._usb = None
        if self._file:
            kernel32.CloseHandle(self._file)
            self._file = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def apply_color(r, g, b, brightness100):
    """Couleur unie, luminosité 0–100. 0 % éteint. L'OSD doit être en Static Mode."""
    bri = max(0, min(100, int(brightness100)))
    r, g, b = (max(0, min(255, int(c))) for c in (r, g, b))
    sr, sg, sb = (c * bri // 100 for c in (r, g, b))
    dev = Evnia()
    try:
        dev.set_colors([(sr, sg, sb)] * LED_COUNT)
        pid = dev.pid
    finally:
        dev.close()
    return {"model": MODEL, "leds": LED_COUNT, "pid": pid,
            "r": r, "g": g, "b": b, "brightness": bri}


def probe():
    """Lecture seule : puce + premiers octets du buffer. Lève DeviceNotFound."""
    dev = Evnia()
    try:
        chip = dev.read(ADDR_CHIP, 2)
        head = dev.read(ADDR_SOURCE, 12)
        return {"model": MODEL, "leds": LED_COUNT, "pid": dev.pid,
                "chip": chip.hex(), "source": head.hex(" ")}
    finally:
        dev.close()


# Modes du menu OSD (Light Mode). E2A019 accepte 0–7.
# 0 = désactivé, puis l'ordre du menu 27M2N8500. À vérifier à l'écran.
LIGHT_MODES = (
    (0, "Désactivé"),
    (1, "Suivre la vidéo"),
    (2, "Suivre l'audio"),
    (3, "Changement de couleur"),
    (4, "Onde de couleur"),
    (5, "Respiration des couleurs"),
    (6, "Nuit étoilée"),
    (7, "Mode statique"),
)
STATIC_MODE = 7
# Couleurs du menu Ambiglow. E2A01A accepte 0–13, ordre du manuel.
COLORS = (
    (0, "Arc-en-ciel"),
    (1, "Blanc"),
    (2, "Rouge"),
    (3, "Rose"),
    (4, "Magenta"),
    (5, "Violet"),
    (6, "Bleu"),
    (7, "Azur"),
    (8, "Cyan"),
    (9, "Aqua"),
    (10, "Vert"),
    (11, "Poire"),
    (12, "Jaune"),
    (13, "Orange"),
)
# Teintes des pastilles, même ordre que le menu, sans l'arc-en-ciel.
COLOR_HEX = {
    1: "#ffffff",
    2: "#ff0000",
    3: "#ff1567",
    4: "#ff15d0",
    5: "#6726ee",
    6: "#0000ff",
    7: "#4aa3ff",
    8: "#00d4ff",
    9: "#2ad4b8",
    10: "#00ff00",
    11: "#b6d400",
    12: "#ffe14a",
    13: "#ff8a00",
}
# Luminosité du halo, juste après la position dans le menu. E2A01C : 0–2.
BRIGHTNESS_LEVELS = (
    (0, "Brillant"),
    (1, "Plus brillant"),
    (2, "Très brillant"),
)
# Vitesse, après la luminosité dans le menu. E2A01D : 0–2.
# Seulement pour changement, onde, respiration et nuit étoilée.
SPEED_MODES = (3, 4, 5, 6)
# Couleur au choix. Suivre la vidéo, suivre l'audio et désactivé n'en ont pas.
COLOR_MODES = (3, 4, 5, 6, 7)
SPEED_LEVELS = (
    (0, "Bas"),
    (1, "Normal"),
    (2, "Haut"),
)
SUB_MODE = 0x19
SUB_COLOR = 0x1A
SUB_BRIGHTNESS = 0x1C
SUB_SPEED = 0x1D
_GET_DISPLAYS = 0x0078DBA2

_nv = None
_gpu = None
_mask = None


class _DisplayId(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_uint32),
        ("connectorType", ctypes.c_uint32),
        ("displayId", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
    ]


def _chk(payload):
    c = 0x6E ^ 0x51
    for b in payload:
        c ^= b
    return c & 0xFF


def _session():
    global _nv, _gpu
    if _nv is None:
        from nvapi import NvAPI
        nv = NvAPI()
        nv.initialize()
        gpus = nv.enum_gpus()
        if not gpus:
            raise RuntimeError("GPU NVIDIA introuvable")
        _nv = nv
        _gpu = gpus[0]
    return _nv, _gpu


def _xfer(nv, gpu, mask, payload, read_n=0):
    import ctypes
    from nvapi import NV_I2C_INFO_V3, _struct_version
    body = bytes(payload)
    buf = (ctypes.c_uint8 * max(len(body), read_n or 1))(*body)
    regb = (ctypes.c_uint8 * 1)(0x51)
    info = NV_I2C_INFO_V3()
    info.version = _struct_version()
    info.display_mask = mask
    info.is_ddc_port = 1
    info.i2c_dev_address = 0x6E
    info.i2c_reg_address = ctypes.cast(regb, ctypes.POINTER(ctypes.c_uint8))
    info.reg_addr_size = 1
    info.data = ctypes.cast(buf, ctypes.POINTER(ctypes.c_uint8))
    info.size = read_n or len(body)
    info.i2c_speed = 27
    info.i2c_speed_khz = 0
    info.port_id = 0
    info.is_port_id_set = 0
    unknown = ctypes.c_uint32(0)
    fn = nv._i2c_read_fn if read_n else nv._i2c_write_fn
    status = fn(gpu, ctypes.byref(info), ctypes.byref(unknown))
    return status, bytes(buf[:info.size])


def _read_sub(nv, gpu, mask, sub):
    import time
    payload = [0x84, 0x01, 0xE2, 0xA0, sub]
    payload.append(_chk(payload))
    _xfer(nv, gpu, mask, payload)
    time.sleep(0.08)
    _status, data = _xfer(nv, gpu, mask, [0] * 12, read_n=12)
    # data[3] == 0 : code accepté. L'autre écran renvoie 1 et une valeur fantôme.
    if len(data) < 10 or data[3] != 0x00 or data[4] != 0xE2:
        return None
    return data[9]


def _write_sub(nv, gpu, mask, sub, value):
    payload = [0x86, 0x03, 0xE2, 0xA0, sub, 0x00, value & 0xFF]
    payload.append(_chk(payload))
    return _xfer(nv, gpu, mask, payload)[0]


def _display_ids(nv, gpu):
    import ctypes
    addr = nv._qif(_GET_DISPLAYS)
    if not addr:
        raise RuntimeError("liste des écrans NVIDIA indisponible")
    fn = ctypes.CFUNCTYPE(
        ctypes.c_int32, ctypes.c_void_p,
        ctypes.POINTER(_DisplayId), ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32,
    )(addr)
    count = ctypes.c_uint32(0)
    fn(gpu, None, ctypes.byref(count), 0)
    n = count.value or 8
    arr = (_DisplayId * n)()
    version = (1 << 16) | ctypes.sizeof(_DisplayId)
    for item in arr:
        item.version = version
    count = ctypes.c_uint32(n)
    status = fn(gpu, arr, ctypes.byref(count), 0)
    if status != 0:
        raise RuntimeError(f"liste des écrans NVIDIA refusée ({status})")
    return [int(arr[i].displayId) for i in range(min(count.value, n)) if arr[i].displayId]


def _philips_mask(nv, gpu):
    global _mask
    if _mask:
        return _mask
    for mask in _display_ids(nv, gpu):
        if _read_sub(nv, gpu, mask, SUB_MODE) is not None:
            _mask = mask
            return mask
    raise RuntimeError("Philips introuvable sur le DisplayPort")


def _held(fn):
    import corsair_keep
    with corsair_keep.hw_hold():
        return fn()


def get_light_mode():
    def _fn():
        nv, gpu = _session()
        mask = _philips_mask(nv, gpu)
        value = _read_sub(nv, gpu, mask, SUB_MODE)
        if value is None:
            raise RuntimeError("lecture du mode Ambiglow impossible")
        return value
    return _held(_fn)


def set_light_mode(value):
    value = int(value)
    if value not in {v for v, _name in LIGHT_MODES}:
        raise ValueError("mode Ambiglow inconnu")

    def _fn():
        nv, gpu = _session()
        mask = _philips_mask(nv, gpu)
        status = _write_sub(nv, gpu, mask, SUB_MODE, value)
        if status != 0:
            raise RuntimeError(f"écriture du mode refusée ({status})")
        import time
        time.sleep(0.12)
        got = _read_sub(nv, gpu, mask, SUB_MODE)
        if got != value:
            raise RuntimeError(f"l'écran a gardé {got}")
        return got
    return _held(_fn)


def get_color():
    def _fn():
        nv, gpu = _session()
        mask = _philips_mask(nv, gpu)
        value = _read_sub(nv, gpu, mask, SUB_COLOR)
        if value is None:
            raise RuntimeError("lecture de la couleur Ambiglow impossible")
        return value
    return _held(_fn)


def _write_color(nv, gpu, mask, value):
    status = _write_sub(nv, gpu, mask, SUB_COLOR, value)
    if status != 0:
        raise RuntimeError(f"écriture de la couleur refusée ({status})")


def get_brightness():
    def _fn():
        nv, gpu = _session()
        mask = _philips_mask(nv, gpu)
        value = _read_sub(nv, gpu, mask, SUB_BRIGHTNESS)
        if value is None:
            raise RuntimeError("lecture de la luminosité Ambiglow impossible")
        return value
    return _held(_fn)


def set_brightness(value):
    """Une seule écriture, sans relecture."""
    value = int(value)
    if value not in {v for v, _name in BRIGHTNESS_LEVELS}:
        raise ValueError("luminosité Ambiglow inconnue")

    def _fn():
        nv, gpu = _session()
        mask = _philips_mask(nv, gpu)
        status = _write_sub(nv, gpu, mask, SUB_BRIGHTNESS, value)
        if status != 0:
            raise RuntimeError(f"écriture de la luminosité refusée ({status})")
        return value
    return _held(_fn)


def get_speed():
    def _fn():
        nv, gpu = _session()
        mask = _philips_mask(nv, gpu)
        value = _read_sub(nv, gpu, mask, SUB_SPEED)
        if value is None:
            raise RuntimeError("lecture de la vitesse Ambiglow impossible")
        return value
    return _held(_fn)


def set_speed(value):
    """Une seule écriture, sans relecture."""
    value = int(value)
    if value not in {v for v, _name in SPEED_LEVELS}:
        raise ValueError("vitesse Ambiglow inconnue")

    def _fn():
        nv, gpu = _session()
        mask = _philips_mask(nv, gpu)
        status = _write_sub(nv, gpu, mask, SUB_SPEED, value)
        if status != 0:
            raise RuntimeError(f"écriture de la vitesse refusée ({status})")
        return value
    return _held(_fn)


def index_for_hex(hx):
    """Indice Ambiglow d'une pastille, ou None si ce n'est pas une couleur du menu."""
    if not isinstance(hx, str):
        return None
    want = hx.strip().lower()
    for value, hex_color in COLOR_HEX.items():
        if hex_color == want:
            return value
    return None


def set_color(value):
    """Une seule écriture. Pas de relecture : elle fait clignoter les LED."""
    value = int(value)
    if value not in {v for v, _name in COLORS}:
        raise ValueError("couleur Ambiglow inconnue")

    def _fn():
        nv, gpu = _session()
        mask = _philips_mask(nv, gpu)
        _write_color(nv, gpu, mask, value)
        return value
    return _held(_fn)


def set_static_color(value):
    """Passe en mode statique seulement s'il n'est pas déjà actif, puis la couleur."""
    value = int(value)
    if value not in {v for v, _name in COLORS}:
        raise ValueError("couleur Ambiglow inconnue")

    def _fn():
        import time
        nv, gpu = _session()
        mask = _philips_mask(nv, gpu)
        mode = _read_sub(nv, gpu, mask, SUB_MODE)
        if mode != STATIC_MODE:
            status = _write_sub(nv, gpu, mask, SUB_MODE, STATIC_MODE)
            if status != 0:
                raise RuntimeError(f"écriture du mode refusée ({status})")
            # Le mode statique remet le blanc. La couleur n'est acceptée qu'après.
            time.sleep(0.2)
        _write_color(nv, gpu, mask, value)
        return value
    return _held(_fn)
