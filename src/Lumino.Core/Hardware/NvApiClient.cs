using System.Runtime.InteropServices;
using System.Text;

namespace Lumino.Core.Hardware;

/// <summary>
/// Direct nvapi64.dll wrapper via nvapi_QueryInterface (exact port of Lumino nvapi.py).
/// Provides internal GPU I2C Ex (Gigabyte RTX 5080 Blackwell), DDC I2C Ex (Philips Evnia Ambiglow),
/// GPU enumeration, thermal sensor readout, and connected display IDs.
/// </summary>
public sealed unsafe class NvApiClient
{
    public const int NvApiOk = 0;

    private const uint QInit = 0x0150E828;
    private const uint QEnumGpus = 0xE5AC921F;
    private const uint QGetPci = 0x2DDFB66E;
    private const uint QI2CWriteEx = 0x283AC65A;
    private const uint QI2CReadEx = 0x4D7B0709;
    private const uint QGetFullName = 0xCEEE8E9F;
    private const uint QGetThermal = 0xE3640A56;
    private const uint QGetConnectedDisplays = 0x0078DBA2;

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate IntPtr QueryInterfaceDelegate(uint id);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate int InitDelegate();

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate int EnumPhysicalGpusDelegate(IntPtr* handles, out int count);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate int GetPciIdentifiersDelegate(IntPtr gpu, out uint deviceId, out uint subSystemId, out uint revisionId, out uint extDeviceId);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate int GetFullNameDelegate(IntPtr gpu, byte* name);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate int GetThermalSettingsDelegate(IntPtr gpu, uint sensorIndex, ref NvGpuThermalSettings settings);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate int I2CExDelegate(IntPtr gpu, ref NvI2CInfoV3 info, ref uint unknown);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate int GetConnectedDisplayIdsDelegate(IntPtr gpu, NvDisplayIdV1* displayIds, ref uint count, uint flags);

    [StructLayout(LayoutKind.Sequential)]
    public struct NvGpuThermalSensor
    {
        public int Controller;
        public int DefaultMinTemp;
        public int DefaultMaxTemp;
        public int CurrentTemp;
        public int Target;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct NvGpuThermalSettings
    {
        public uint Version;
        public uint Count;
        public NvGpuThermalSensor Sensor0;
        public NvGpuThermalSensor Sensor1;
        public NvGpuThermalSensor Sensor2;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct NvI2CInfoV3
    {
        public uint Version;
        public uint DisplayMask;
        public byte IsDdcPort;
        public byte I2CDevAddress;
        public IntPtr I2CRegAddress;
        public uint RegAddrSize;
        public IntPtr Data;
        public uint Size;
        public uint I2CSpeed;
        public uint I2CSpeedKhz;
        public byte PortId;
        public uint IsPortIdSet;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct NvDisplayIdV1
    {
        public uint Version;
        public uint ConnectorType;
        public uint DisplayId;
        public uint Flags;
    }

    private readonly IntPtr _dll;
    private readonly QueryInterfaceDelegate _qif;
    private readonly InitDelegate _initFn;
    private readonly EnumPhysicalGpusDelegate _enumGpusFn;
    private readonly GetPciIdentifiersDelegate _pciFn;
    private readonly I2CExDelegate _i2cWriteFn;
    private readonly I2CExDelegate _i2cReadFn;
    private readonly GetFullNameDelegate? _nameFn;
    private readonly GetThermalSettingsDelegate? _thermalFn;
    private readonly GetConnectedDisplayIdsDelegate? _displaysFn;

    public NvApiClient(string dllName = "nvapi64.dll")
    {
        if (!NativeLibrary.TryLoad(dllName, out _dll))
            throw new InvalidOperationException($"Impossible de charger {dllName}");

        IntPtr qifAddr = NativeLibrary.GetExport(_dll, "nvapi_QueryInterface");
        _qif = Marshal.GetDelegateForFunctionPointer<QueryInterfaceDelegate>(qifAddr);

        _initFn = QueryRequired<InitDelegate>(QInit);
        _enumGpusFn = QueryRequired<EnumPhysicalGpusDelegate>(QEnumGpus);
        _pciFn = QueryRequired<GetPciIdentifiersDelegate>(QGetPci);
        _i2cWriteFn = QueryRequired<I2CExDelegate>(QI2CWriteEx);
        _i2cReadFn = QueryRequired<I2CExDelegate>(QI2CReadEx);
        _nameFn = QueryOptional<GetFullNameDelegate>(QGetFullName);
        _thermalFn = QueryOptional<GetThermalSettingsDelegate>(QGetThermal);
        _displaysFn = QueryOptional<GetConnectedDisplayIdsDelegate>(QGetConnectedDisplays);
    }

    private T QueryRequired<T>(uint qid) where T : Delegate
    {
        IntPtr addr = _qif(qid);
        if (addr == IntPtr.Zero)
            throw new InvalidOperationException($"nvapi_QueryInterface(0x{qid:X8}) a retourné NULL");
        return Marshal.GetDelegateForFunctionPointer<T>(addr);
    }

    private T? QueryOptional<T>(uint qid) where T : Delegate
    {
        IntPtr addr = _qif(qid);
        return addr == IntPtr.Zero ? null : Marshal.GetDelegateForFunctionPointer<T>(addr);
    }

    public static uint I2CInfoVersion => (3u << 16) | (uint)sizeof(NvI2CInfoV3);
    private static uint ThermalSettingsVersion => (2u << 16) | (uint)sizeof(NvGpuThermalSettings);
    private static uint DisplayIdVersion => (1u << 16) | (uint)sizeof(NvDisplayIdV1);

    public int Initialize()
    {
        int st = _initFn();
        if (st != NvApiOk)
            throw new InvalidOperationException($"NvAPI_Initialize a échoué (status={st})");
        return st;
    }

    public IntPtr[] EnumGpus()
    {
        IntPtr* handles = stackalloc IntPtr[64];
        int st = _enumGpusFn(handles, out int count);
        if (st != NvApiOk)
            throw new InvalidOperationException($"EnumPhysicalGPUs a échoué (status={st})");

        var result = new IntPtr[Math.Clamp(count, 0, 64)];
        for (int i = 0; i < result.Length; i++)
            result[i] = handles[i];
        return result;
    }

    public string GetName(IntPtr handle)
    {
        if (_nameFn is null)
            return "NVIDIA GPU";

        byte* buf = stackalloc byte[64];
        int st = _nameFn(handle, buf);
        if (st != NvApiOk)
            return "NVIDIA GPU";

        int len = 0;
        while (len < 64 && buf[len] != 0)
            len++;
        return Encoding.ASCII.GetString(buf, len);
    }

    public int? GetGpuTemp(IntPtr handle)
    {
        if (_thermalFn is null)
            return null;

        uint[] targets = [1, 0];
        foreach (uint target in targets)
        {
            try
            {
                var s = new NvGpuThermalSettings { Version = ThermalSettingsVersion };
                int st = _thermalFn(handle, target, ref s);
                if (st == NvApiOk && s.Count > 0)
                {
                    int t = s.Sensor0.CurrentTemp;
                    if (t > -40 && t < 125)
                        return t;
                }
            }
            catch
            {
                return null;
            }
        }
        return null;
    }

    public int WriteBlock(IntPtr handle, byte addr7, byte[] data, byte port = 1)
    {
        fixed (byte* pData = data)
        {
            var info = new NvI2CInfoV3
            {
                Version = I2CInfoVersion,
                DisplayMask = 0,
                IsDdcPort = 0,
                I2CDevAddress = (byte)((addr7 << 1) & 0xFF),
                I2CRegAddress = IntPtr.Zero,
                RegAddrSize = 0,
                Data = (IntPtr)pData,
                Size = (uint)data.Length,
                I2CSpeed = 0xFFFF,
                I2CSpeedKhz = 0,
                PortId = port,
                IsPortIdSet = 1
            };
            uint unknown = 0;
            return _i2cWriteFn(handle, ref info, ref unknown);
        }
    }

    public (int Status, byte[]? Data) ReadBlock(IntPtr handle, byte addr7, int count, byte port = 1)
    {
        var buf = new byte[count];
        fixed (byte* pData = buf)
        {
            var info = new NvI2CInfoV3
            {
                Version = I2CInfoVersion,
                DisplayMask = 0,
                IsDdcPort = 0,
                I2CDevAddress = (byte)((addr7 << 1) & 0xFF),
                I2CRegAddress = IntPtr.Zero,
                RegAddrSize = 0,
                Data = (IntPtr)pData,
                Size = (uint)count,
                I2CSpeed = 0xFFFF,
                I2CSpeedKhz = 0,
                PortId = port,
                IsPortIdSet = 1
            };
            uint unknown = 0;
            int st = _i2cReadFn(handle, ref info, ref unknown);
            return st == NvApiOk ? (st, buf) : (st, null);
        }
    }

    public uint[] GetConnectedDisplayIds(IntPtr gpu)
    {
        if (_displaysFn is null)
            throw new InvalidOperationException("Liste des écrans NVIDIA indisponible");

        uint count = 0;
        _displaysFn(gpu, null, ref count, 0);
        uint n = count > 0 ? count : 8u;
        var arr = new NvDisplayIdV1[n];
        uint ver = DisplayIdVersion;
        for (int i = 0; i < arr.Length; i++)
            arr[i].Version = ver;

        count = n;
        int st;
        fixed (NvDisplayIdV1* pArr = arr)
        {
            st = _displaysFn(gpu, pArr, ref count, 0);
        }

        if (st != NvApiOk)
            throw new InvalidOperationException($"Liste des écrans NVIDIA refusée ({st})");

        int valid = (int)Math.Min(count, n);
        var ids = new List<uint>(valid);
        for (int i = 0; i < valid; i++)
        {
            if (arr[i].DisplayId != 0)
                ids.Add(arr[i].DisplayId);
        }
        return ids.ToArray();
    }

    public (int Status, byte[] Data) DdcTransfer(IntPtr gpu, uint displayMask, byte[] payload, int readCount = 0)
    {
        int bufSize = Math.Max(payload.Length, readCount > 0 ? readCount : 1);
        var buf = new byte[bufSize];
        Buffer.BlockCopy(payload, 0, buf, 0, payload.Length);
        byte reg = 0x51;

        fixed (byte* pData = buf)
        {
            var info = new NvI2CInfoV3
            {
                Version = I2CInfoVersion,
                DisplayMask = displayMask,
                IsDdcPort = 1,
                I2CDevAddress = 0x6E,
                I2CRegAddress = (IntPtr)(&reg),
                RegAddrSize = 1,
                Data = (IntPtr)pData,
                Size = (uint)(readCount > 0 ? readCount : payload.Length),
                I2CSpeed = 27,
                I2CSpeedKhz = 0,
                PortId = 0,
                IsPortIdSet = 0
            };

            uint unknown = 0;
            int st = readCount > 0
                ? _i2cReadFn(gpu, ref info, ref unknown)
                : _i2cWriteFn(gpu, ref info, ref unknown);

            var outData = new byte[(int)info.Size];
            Buffer.BlockCopy(buf, 0, outData, 0, outData.Length);
            return (st, outData);
        }
    }
}
