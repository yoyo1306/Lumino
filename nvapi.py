"""
Lumino - NvAPI wrapper (I2C Ex, port GPU interne)
Basé sur OpenRGB dependencies/NVFC + i2c_smbus_nvapi.cpp (CalcProgrammer1, GPL-2.0).

Principe OpenRGB :
- NvAPI_Initialize via nvapi_QueryInterface(0x0150E828)
- NvAPI_EnumPhysicalGPUs (0xE5AC921F)
- NvAPI_GPU_GetPCIIdentifiers (0x2DDFB66E)
- NvAPI_I2CWriteEx (0x283AC65A) / NvAPI_I2CReadEx (0x4D7B0709)
- Bus interne : is_ddc_port=0, port_id=1, is_port_id_set=1, speed default
- Adresse device : (addr_7bit << 1)
"""

import ctypes

NVAPI_OK = 0

_Q_INIT = 0x0150E828
_Q_ENUM_GPUS = 0xE5AC921F
_Q_GET_PCI = 0x2DDFB66E
_Q_I2C_WRITE_EX = 0x283AC65A
_Q_I2C_READ_EX = 0x4D7B0709
_Q_GET_FULL_NAME = 0x0CEEE8E9F
_Q_GET_THERMAL = 0xE3640A56


class NV_GPU_THERMAL_SENSOR(ctypes.Structure):
    _fields_ = [
        ("controller", ctypes.c_int32),
        ("defaultMinTemp", ctypes.c_int32),
        ("defaultMaxTemp", ctypes.c_int32),
        ("currentTemp", ctypes.c_int32),
        ("target", ctypes.c_int32),
    ]


class NV_GPU_THERMAL_SETTINGS(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_uint32),
        ("count", ctypes.c_uint32),
        ("sensor", NV_GPU_THERMAL_SENSOR * 3),
    ]


def _thermal_version():
    return (2 << 16) | ctypes.sizeof(NV_GPU_THERMAL_SETTINGS)


class NV_I2C_INFO_V3(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_uint32),
        ("display_mask", ctypes.c_uint32),
        ("is_ddc_port", ctypes.c_uint8),
        ("i2c_dev_address", ctypes.c_uint8),
        ("i2c_reg_address", ctypes.POINTER(ctypes.c_uint8)),
        ("reg_addr_size", ctypes.c_uint32),
        ("data", ctypes.POINTER(ctypes.c_uint8)),
        ("size", ctypes.c_uint32),
        ("i2c_speed", ctypes.c_uint32),
        ("i2c_speed_khz", ctypes.c_uint32),  # NV_I2C_SPEED enum, 0 = DEFAULT
        ("port_id", ctypes.c_uint8),
        ("is_port_id_set", ctypes.c_uint32),
    ]


def _struct_version():
    return (3 << 16) | ctypes.sizeof(NV_I2C_INFO_V3)


class NvAPI:
    def __init__(self, dll_name="nvapi64.dll"):
        self.dll = ctypes.WinDLL(dll_name)
        qif = self.dll.nvapi_QueryInterface
        qif.restype = ctypes.c_void_p
        qif.argtypes = [ctypes.c_uint32]
        self._qif = qif

        def _get(qid, proto):
            addr = qif(qid)
            if not addr:
                raise RuntimeError(f"nvapi_QueryInterface(0x{qid:08X}) a retourné NULL")
            return proto(addr)

        self._init_fn = _get(_Q_INIT, ctypes.CFUNCTYPE(ctypes.c_int32))
        self._enum_gpus_fn = _get(
            _Q_ENUM_GPUS,
            ctypes.CFUNCTYPE(
                ctypes.c_int32,
                ctypes.POINTER(ctypes.c_void_p),
                ctypes.POINTER(ctypes.c_int32),
            ),
        )
        self._pci_fn = _get(
            _Q_GET_PCI,
            ctypes.CFUNCTYPE(
                ctypes.c_int32,
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_uint32),
                ctypes.POINTER(ctypes.c_uint32),
                ctypes.POINTER(ctypes.c_uint32),
                ctypes.POINTER(ctypes.c_uint32),
            ),
        )
        self._i2c_write_fn = _get(
            _Q_I2C_WRITE_EX,
            ctypes.CFUNCTYPE(
                ctypes.c_int32,
                ctypes.c_void_p,
                ctypes.POINTER(NV_I2C_INFO_V3),
                ctypes.POINTER(ctypes.c_uint32),
            ),
        )
        self._i2c_read_fn = _get(
            _Q_I2C_READ_EX,
            ctypes.CFUNCTYPE(
                ctypes.c_int32,
                ctypes.c_void_p,
                ctypes.POINTER(NV_I2C_INFO_V3),
                ctypes.POINTER(ctypes.c_uint32),
            ),
        )
        try:
            self._name_fn = _get(
                _Q_GET_FULL_NAME,
                ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_char_p),
            )
        except RuntimeError:
            self._name_fn = None
        try:
            self._thermal_fn = _get(
                _Q_GET_THERMAL,
                ctypes.CFUNCTYPE(
                    ctypes.c_int32,
                    ctypes.c_void_p,
                    ctypes.c_uint32,
                    ctypes.POINTER(NV_GPU_THERMAL_SETTINGS),
                ),
            )
        except RuntimeError:
            self._thermal_fn = None

    # --- init / enum -------------------------------------------------
    def initialize(self):
        st = self._init_fn()
        if st != NVAPI_OK:
            raise RuntimeError(f"NvAPI_Initialize a échoué (status={st})")
        return st

    def enum_gpus(self):
        handles = (ctypes.c_void_p * 64)()
        count = ctypes.c_int32(0)
        st = self._enum_gpus_fn(handles, ctypes.byref(count))
        if st != NVAPI_OK:
            raise RuntimeError(f"EnumPhysicalGPUs a échoué (status={st})")
        return [handles[i] for i in range(count.value)]

    def get_pci(self, handle):
        dev = ctypes.c_uint32(0)
        sub = ctypes.c_uint32(0)
        rev = ctypes.c_uint32(0)
        ext = ctypes.c_uint32(0)
        st = self._pci_fn(
            handle, ctypes.byref(dev), ctypes.byref(sub), ctypes.byref(rev), ctypes.byref(ext)
        )
        if st != NVAPI_OK:
            raise RuntimeError(f"GetPCIIdentifiers a échoué (status={st})")
        return {
            "device_id": dev.value >> 16,
            "vendor_id": dev.value & 0xFFFF,
            "sub_device": sub.value >> 16,
            "sub_vendor": sub.value & 0xFFFF,
            "revision": rev.value,
            "ext": ext.value,
        }

    def get_name(self, handle):
        if self._name_fn is None:
            return "NVIDIA GPU"
        buf = ctypes.create_string_buffer(64)
        st = self._name_fn(handle, buf)
        if st != NVAPI_OK:
            return "NVIDIA GPU"
        return buf.value.decode(errors="ignore")

    def get_gpu_temp(self, handle):
        """Temperature GPU °C (NvAPI_GPU_GetThermalSettings). None si KO."""
        if self._thermal_fn is None:
            return None
        for target in (1, 0):  # GPU puis defaut
            try:
                s = NV_GPU_THERMAL_SETTINGS()
                s.version = _thermal_version()
                st = self._thermal_fn(handle, target, ctypes.byref(s))
                if st == NVAPI_OK and s.count > 0:
                    t = int(s.sensor[0].currentTemp)
                    if -40 < t < 125:
                        return t
            except OSError:
                return None
        return None

    # --- I2C bas niveau (style OpenRGB i2c_smbus_nvapi) ---------------
    def _make_info(self, addr7, data_buf, size, port=1):
        info = NV_I2C_INFO_V3()
        info.version = _struct_version()
        info.display_mask = 0
        info.is_ddc_port = 0
        info.i2c_dev_address = (addr7 << 1) & 0xFF
        info.i2c_reg_address = None
        info.reg_addr_size = 0
        info.data = ctypes.cast(data_buf, ctypes.POINTER(ctypes.c_uint8))
        info.size = size
        info.i2c_speed = 0xFFFF
        info.i2c_speed_khz = 0  # DEFAULT
        info.port_id = port
        info.is_port_id_set = 1
        return info

    def write_byte(self, handle, addr7, value, port=1):
        """i2c_smbus_write_byte : 1 transaction d'1 octet, sans registre."""
        buf = (ctypes.c_uint8 * 1)(value & 0xFF)
        info = self._make_info(addr7, buf, 1, port)
        unknown = ctypes.c_uint32(0)
        st = self._i2c_write_fn(handle, ctypes.byref(info), ctypes.byref(unknown))
        return st

    def read_byte(self, handle, addr7, port=1):
        """i2c_smbus_read_byte : 1 transaction de lecture d'1 octet."""
        buf = (ctypes.c_uint8 * 1)(0)
        info = self._make_info(addr7, buf, 1, port)
        unknown = ctypes.c_uint32(0)
        st = self._i2c_read_fn(handle, ctypes.byref(info), ctypes.byref(unknown))
        if st != NVAPI_OK:
            return st, None
        return st, int(buf[0])

    def write_block(self, handle, addr7, data: bytes, port=1):
        """i2c_write_block : 1 transaction d'écriture de N octets, sans registre."""
        n = len(data)
        buf = (ctypes.c_uint8 * n)(*data)
        info = self._make_info(addr7, buf, n, port)
        unknown = ctypes.c_uint32(0)
        return self._i2c_write_fn(handle, ctypes.byref(info), ctypes.byref(unknown))

    def read_block(self, handle, addr7, n: int, port=1):
        """i2c_read_block : 1 transaction de lecture de N octets."""
        buf = (ctypes.c_uint8 * n)(*([0] * n))
        info = self._make_info(addr7, buf, n, port)
        unknown = ctypes.c_uint32(0)
        st = self._i2c_read_fn(handle, ctypes.byref(info), ctypes.byref(unknown))
        if st != NVAPI_OK:
            return st, None
        return st, bytes(buf)
