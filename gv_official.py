"""
Lumino - backend officiel Gigabyte (GvLedLib.dll de GCC).
DLLs copiées dans ./vendor avant désinstallation de GCC.

Signatures sûres (RGB-Fusion-Tool, Tyler Szabo, GPL-3.0, cdecl) :
  uint dllexp_GvLedInitial(out int count, int ids[256])
  uint dllexp_GvLedGetVersion(out int major, out int minor)
  uint dllexp_GvLedSet(int index, GVLED_CFG cfg)
  uint dllexp_GvLedSave(int index, GVLED_CFG cfg)

GVLED_CFG (11 x uint32) :
  nType (1=static), nSpeed, t1, t2, t3, minBri, maxBri (0..10),
  dwColor 0x00RRGGBB, nAngle=0, nOn=1, nSync=1

NOTE : sur une carte mère non-Gigabyte, GvLedInitial retourne count=0
(périphériques carte mère uniquement). Le GPU passe par le chemin VGA
(GvLedGetVgaInfo2 / GvVGAN30LedSet / ...) dont les signatures exactes
pour la RTX 5080 INFINITY doivent être capturées via trace_gcc.py.
Ce module expose donc : enum sûr + set périphérique + hooks VGA expérimentaux.
"""
import ctypes
import os

BASE = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(BASE, "vendor", "GvLedLib.dll")
GCC_DLL = r"C:\Program Files\GIGABYTE\Control Center\Lib\GBT_VGA\GvDll\GvLedLib.dll"


class GVLED_CFG(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint32) for n in (
        "nType", "nSpeed", "dwTime1", "dwTime2", "dwTime3",
        "nMinBrightness", "nMaxBrightness", "dwColor",
        "nAngle", "nOn", "nSync")]


def static_cfg(r, g, b, brightness10=10):
    brightness10 = max(0, min(10, int(brightness10)))
    return GVLED_CFG(
        nType=1, nSpeed=0, dwTime1=0, dwTime2=0, dwTime3=0,
        nMinBrightness=0, nMaxBrightness=brightness10,
        dwColor=(int(r) << 16) | (int(g) << 8) | int(b),
        nAngle=0, nOn=1, nSync=1)


def _load():
    for p in (VENDOR, GCC_DLL):
        if p and os.path.isfile(p):
            dll = ctypes.CDLL(p)
            dll.dllexp_GvLedInitial.restype = ctypes.c_uint32
            dll.dllexp_GvLedInitial.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
            dll.dllexp_GvLedGetVersion.restype = ctypes.c_uint32
            dll.dllexp_GvLedGetVersion.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
            dll.dllexp_GvLedSet.restype = ctypes.c_uint32
            dll.dllexp_GvLedSet.argtypes = [ctypes.c_int, GVLED_CFG]
            dll.dllexp_GvLedSave.restype = ctypes.c_uint32
            dll.dllexp_GvLedSave.argtypes = [ctypes.c_int, GVLED_CFG]
            return dll, p
    raise RuntimeError("GvLedLib.dll introuvable (ni ./vendor ni GCC)")


def enum_devices():
    dll, path = _load()
    maj, minor = ctypes.c_int(0), ctypes.c_int(0)
    v = dll.dllexp_GvLedGetVersion(ctypes.byref(maj), ctypes.byref(minor))
    arr = (ctypes.c_int * 256)()
    cnt = ctypes.c_int(256)
    r = dll.dllexp_GvLedInitial(ctypes.byref(cnt), arr)
    return {"dll": path, "version_ret": v, "version": (maj.value, minor.value),
            "init_ret": r, "count": cnt.value,
            "ids": list(arr)[:cnt.value] if 0 <= cnt.value <= 256 else []}


def apply_static_all(r, g, b, brightness10=10, save=False):
    """Applique via le chemin périphérique (index -1 = tous). Retourne le code DLL."""
    dll, path = _load()
    enum = enum_devices()
    cfg = static_cfg(r, g, b, brightness10)
    ret_set = dll.dllexp_GvLedSet(-1, cfg)
    ret_save = dll.dllexp_GvLedSave(-1, cfg) if save else None
    return {"dll": path, "devices": enum, "set_ret": ret_set, "save_ret": ret_save}
