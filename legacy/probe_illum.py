"""Probe NvAPI ClientIllum (API officielle RTX 40/50) + test elevation."""
import ctypes

_Q_INIT = 0x0150E828
_Q_ENUM = 0xE5AC921F
_Q_GET = 0x73C01D58
_Q_SET = 0x57024C62

# --- structs minimales fidèles au header NVFC ---
class ZONE_CTRL_V1(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("ctrlMode", ctypes.c_uint32),
        ("data", ctypes.c_uint8 * 128),
        ("rsvd", ctypes.c_uint8 * 64),
    ]

class ZONE_CTRL_PARAMS_V1(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_uint32),
        ("bDefault_rsvd", ctypes.c_uint32),  # bitfield bDefault:1 + rsvd:31
        ("numZones", ctypes.c_uint32),
        ("rsvd", ctypes.c_uint8 * 64),
        ("zones", ZONE_CTRL_V1 * 32),
    ]

print("sizeof ZONE_CTRL_V1 =", ctypes.sizeof(ZONE_CTRL_V1))
print("sizeof PARAMS =", ctypes.sizeof(ZONE_CTRL_PARAMS_V1))
ver = (1 << 16) | ctypes.sizeof(ZONE_CTRL_PARAMS_V1)
print("version =", hex(ver))

dll = ctypes.WinDLL("nvapi64.dll")
q = dll.nvapi_QueryInterface
q.restype = ctypes.c_void_p
q.argtypes = [ctypes.c_uint32]

def get(qid, proto):
    a = q(qid)
    print(f"Q 0x{qid:08X} ->", hex(a) if a else None)
    return proto(a) if a else None

Init = get(_Q_INIT, ctypes.CFUNCTYPE(ctypes.c_int32))
Enum = get(_Q_ENUM, ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_int32)))
GetCtl = get(_Q_GET, ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.POINTER(ZONE_CTRL_PARAMS_V1)))

print("init:", Init())
hs = (ctypes.c_void_p * 64)()
n = ctypes.c_int32(0)
print("enum:", Enum(hs, ctypes.byref(n)), "count=", n.value)
for i in range(n.value):
    p = ZONE_CTRL_PARAMS_V1()
    ctypes.memset(ctypes.byref(p), 0, ctypes.sizeof(p))
    p.version = ver
    st = GetCtl(hs[i], ctypes.byref(p))
    print(f"GPU{i} ClientIllum GetControl st={st} numZones={p.numZones}")
    for z in range(min(p.numZones, 8)):
        print(f"  zone{z} type={p.zones[z].type} mode={p.zones[z].ctrlMode} data0={list(p.zones[z].data[:8])}")

# elevation ?
import ctypes as _c
print("admin:", bool(_c.windll.shell32.IsUserAnAdmin()))
