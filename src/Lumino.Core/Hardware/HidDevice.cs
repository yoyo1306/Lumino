using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace Lumino.Core.Hardware;

public sealed record HidDeviceInfo(
    string Path,
    ushort VendorId,
    ushort ProductId,
    ushort UsagePage,
    ushort Usage,
    ushort InputReportByteLength,
    ushort OutputReportByteLength,
    ushort FeatureReportByteLength);

/// <summary>
/// Native Win32 HID wrapper (hid.dll + setupapi.dll + kernel32.dll) with exact hidapi semantics:
/// - Overlapped I/O with read timeout
/// - Leading 0x00 report ID stripping on Read (matching hidapi Windows backend)
/// - Output report padding to OutputReportByteLength
/// - HidD_SetFeature support for feature reports
/// </summary>
public sealed class HidDevice : IDisposable
{
    private SafeFileHandle? _handle;
    private readonly ushort _inputLen;
    private readonly ushort _outputLen;
    private readonly ushort _featureLen;

    public string DevicePath { get; }
    public ushort VendorId { get; }
    public ushort ProductId { get; }

    private HidDevice(SafeFileHandle handle, HidDeviceInfo info)
    {
        _handle = handle;
        DevicePath = info.Path;
        VendorId = info.VendorId;
        ProductId = info.ProductId;
        _inputLen = info.InputReportByteLength;
        _outputLen = info.OutputReportByteLength;
        _featureLen = info.FeatureReportByteLength;
    }

    public static List<HidDeviceInfo> Enumerate(ushort vendorId = 0, ushort productId = 0)
    {
        var results = new List<HidDeviceInfo>();
        HidD_GetHidGuid(out Guid hidGuid);

        IntPtr devInfo = SetupDiGetClassDevsW(ref hidGuid, null, IntPtr.Zero, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE);
        if (devInfo == IntPtr.Zero || devInfo == INVALID_HANDLE_VALUE)
            return results;

        try
        {
            uint index = 0;
            while (true)
            {
                var ifaceData = new SP_DEVICE_INTERFACE_DATA
                {
                    cbSize = (uint)Marshal.SizeOf<SP_DEVICE_INTERFACE_DATA>()
                };

                if (!SetupDiEnumDeviceInterfaces(devInfo, IntPtr.Zero, ref hidGuid, index, ref ifaceData))
                    break;
                index++;

                uint requiredSize = 0;
                SetupDiGetDeviceInterfaceDetailW(devInfo, ref ifaceData, IntPtr.Zero, 0, out requiredSize, IntPtr.Zero);
                if (requiredSize < 8)
                    continue;

                IntPtr detailBuf = Marshal.AllocHGlobal((int)requiredSize);
                try
                {
                    // On x64, SP_DEVICE_INTERFACE_DETAIL_DATA_W.cbSize is 8
                    Marshal.WriteInt32(detailBuf, IntPtr.Size == 8 ? 8 : 6);
                    if (!SetupDiGetDeviceInterfaceDetailW(devInfo, ref ifaceData, detailBuf, requiredSize, out _, IntPtr.Zero))
                        continue;

                    string? path = Marshal.PtrToStringUni(IntPtr.Add(detailBuf, 4));
                    if (string.IsNullOrEmpty(path))
                        continue;

                    using var queryHandle = CreateFileW(
                        path,
                        0, // Metadata query only: succeeds even if exclusively opened
                        FILE_SHARE_READ | FILE_SHARE_WRITE,
                        IntPtr.Zero,
                        OPEN_EXISTING,
                        0,
                        IntPtr.Zero);

                    if (queryHandle.IsInvalid)
                        continue;

                    var attrs = new HIDD_ATTRIBUTES { Size = Marshal.SizeOf<HIDD_ATTRIBUTES>() };
                    if (!HidD_GetAttributes(queryHandle, ref attrs))
                        continue;

                    if (vendorId != 0 && attrs.VendorID != vendorId)
                        continue;
                    if (productId != 0 && attrs.ProductID != productId)
                        continue;

                    ushort usagePage = 0, usage = 0, inLen = 0, outLen = 0, featLen = 0;
                    if (HidD_GetPreparsedData(queryHandle, out IntPtr preparsed))
                    {
                        try
                        {
                            if (HidP_GetCaps(preparsed, out HIDP_CAPS caps) == HIDP_STATUS_SUCCESS)
                            {
                                usagePage = caps.UsagePage;
                                usage = caps.Usage;
                                inLen = caps.InputReportByteLength;
                                outLen = caps.OutputReportByteLength;
                                featLen = caps.FeatureReportByteLength;
                            }
                        }
                        finally
                        {
                            HidD_FreePreparsedData(preparsed);
                        }
                    }

                    results.Add(new HidDeviceInfo(
                        path,
                        attrs.VendorID,
                        attrs.ProductID,
                        usagePage,
                        usage,
                        inLen,
                        outLen,
                        featLen));
                }
                finally
                {
                    Marshal.FreeHGlobal(detailBuf);
                }
            }
        }
        finally
        {
            SetupDiDestroyDeviceInfoList(devInfo);
        }

        return results;
    }

    public static HidDevice OpenPath(string path)
    {
        var handle = CreateFileW(
            path,
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            IntPtr.Zero,
            OPEN_EXISTING,
            FILE_FLAG_OVERLAPPED,
            IntPtr.Zero);

        if (handle.IsInvalid)
        {
            int err = Marshal.GetLastWin32Error();
            throw new IOException($"Impossible d'ouvrir le périphérique HID ({err}) : {new Win32Exception(err).Message}");
        }

        try
        {
            var attrs = new HIDD_ATTRIBUTES { Size = Marshal.SizeOf<HIDD_ATTRIBUTES>() };
            HidD_GetAttributes(handle, ref attrs);

            ushort usagePage = 0, usage = 0, inLen = 0, outLen = 0, featLen = 0;
            if (HidD_GetPreparsedData(handle, out IntPtr preparsed))
            {
                try
                {
                    if (HidP_GetCaps(preparsed, out HIDP_CAPS caps) == HIDP_STATUS_SUCCESS)
                    {
                        usagePage = caps.UsagePage;
                        usage = caps.Usage;
                        inLen = caps.InputReportByteLength;
                        outLen = caps.OutputReportByteLength;
                        featLen = caps.FeatureReportByteLength;
                    }
                }
                finally
                {
                    HidD_FreePreparsedData(preparsed);
                }
            }

            var info = new HidDeviceInfo(path, attrs.VendorID, attrs.ProductID, usagePage, usage, inLen, outLen, featLen);
            return new HidDevice(handle, info);
        }
        catch
        {
            handle.Dispose();
            throw;
        }
    }

    public static HidDevice OpenFirst(ushort vendorId, ushort productId)
    {
        var devs = Enumerate(vendorId, productId);
        if (devs.Count == 0)
            throw new IOException($"Périphérique HID {vendorId:X4}:{productId:X4} introuvable");

        Exception? last = null;
        foreach (var d in devs)
        {
            try
            {
                return OpenPath(d.Path);
            }
            catch (Exception ex)
            {
                last = ex;
            }
        }
        throw last ?? new IOException($"Impossible d'ouvrir le périphérique HID {vendorId:X4}:{productId:X4}");
    }

    public int Write(byte[] data, int timeoutMs = 1000)
    {
        ObjectDisposedException.ThrowIf(_handle is null || _handle.IsClosed, this);

        int sendLen = _outputLen > 0 ? Math.Max(_outputLen, data.Length) : data.Length;
        byte[] buf = sendLen == data.Length ? data : new byte[sendLen];
        if (buf != data)
            Buffer.BlockCopy(data, 0, buf, 0, data.Length);

        using var evt = new ManualResetEvent(false);
        var ov = new NativeOverlapped { EventHandle = evt.SafeWaitHandle.DangerousGetHandle() };

        bool ok = WriteFile(_handle, buf, (uint)buf.Length, out uint written, ref ov);
        if (!ok)
        {
            int err = Marshal.GetLastWin32Error();
            if (err != ERROR_IO_PENDING)
                throw new IOException($"WriteFile HID échoué ({err})");

            if (!evt.WaitOne(timeoutMs))
            {
                CancelIo(_handle);
                GetOverlappedResult(_handle, ref ov, out _, true);
                throw new IOException("Timeout WriteFile HID");
            }

            if (!GetOverlappedResult(_handle, ref ov, out written, false))
                throw new IOException($"GetOverlappedResult WriteFile HID échoué ({Marshal.GetLastWin32Error()})");
        }

        // Match hidapi: if padded to OutputReportByteLength, report original requested length
        return written == buf.Length ? data.Length : (int)written;
    }

    public byte[] Read(int maxLength, int timeoutMs = 1000)
    {
        ObjectDisposedException.ThrowIf(_handle is null || _handle.IsClosed, this);

        int readBufLen = _inputLen > 0 ? _inputLen : maxLength;
        byte[] raw = new byte[readBufLen];

        using var evt = new ManualResetEvent(false);
        var ov = new NativeOverlapped { EventHandle = evt.SafeWaitHandle.DangerousGetHandle() };

        bool ok = ReadFile(_handle, raw, (uint)raw.Length, out uint bytesRead, ref ov);
        if (!ok)
        {
            int err = Marshal.GetLastWin32Error();
            if (err != ERROR_IO_PENDING)
                throw new IOException($"ReadFile HID échoué ({err})");

            if (!evt.WaitOne(timeoutMs))
            {
                CancelIo(_handle);
                GetOverlappedResult(_handle, ref ov, out _, true);
                return [];
            }

            if (!GetOverlappedResult(_handle, ref ov, out bytesRead, false))
            {
                int ovErr = Marshal.GetLastWin32Error();
                if (ovErr == ERROR_OPERATION_ABORTED)
                    return [];
                throw new IOException($"GetOverlappedResult ReadFile HID échoué ({ovErr})");
            }
        }

        if (bytesRead == 0)
            return [];

        // Exact hidapi Windows behavior: strip leading 0x00 report ID when unnumbered reports are used
        int start = raw[0] == 0x00 ? 1 : 0;
        int avail = Math.Max(0, (int)bytesRead - start);
        int copyLen = Math.Min(maxLength, avail);
        var result = new byte[copyLen];
        Buffer.BlockCopy(raw, start, result, 0, copyLen);
        return result;
    }

    public bool SendFeatureReport(byte[] data)
    {
        ObjectDisposedException.ThrowIf(_handle is null || _handle.IsClosed, this);

        int featLen = _featureLen > 0 ? Math.Max(_featureLen, data.Length) : data.Length;
        byte[] buf = featLen == data.Length ? data : new byte[featLen];
        if (buf != data)
            Buffer.BlockCopy(data, 0, buf, 0, data.Length);

        return HidD_SetFeature(_handle, buf, (uint)buf.Length);
    }

    public void Dispose()
    {
        _handle?.Dispose();
        _handle = null;
    }

    private const uint DIGCF_PRESENT = 0x02;
    private const uint DIGCF_DEVICEINTERFACE = 0x10;
    private const uint GENERIC_READ = 0x80000000;
    private const uint GENERIC_WRITE = 0x40000000;
    private const uint FILE_SHARE_READ = 0x01;
    private const uint FILE_SHARE_WRITE = 0x02;
    private const uint OPEN_EXISTING = 3;
    private const uint FILE_FLAG_OVERLAPPED = 0x40000000;
    private const int ERROR_IO_PENDING = 997;
    private const int ERROR_OPERATION_ABORTED = 995;
    private const int HIDP_STATUS_SUCCESS = 0x00110000;
    private static readonly IntPtr INVALID_HANDLE_VALUE = new(-1);

    [StructLayout(LayoutKind.Sequential)]
    private struct SP_DEVICE_INTERFACE_DATA
    {
        public uint cbSize;
        public Guid InterfaceClassGuid;
        public uint Flags;
        public IntPtr Reserved;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct HIDD_ATTRIBUTES
    {
        public int Size;
        public ushort VendorID;
        public ushort ProductID;
        public ushort VersionNumber;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct HIDP_CAPS
    {
        public ushort Usage;
        public ushort UsagePage;
        public ushort InputReportByteLength;
        public ushort OutputReportByteLength;
        public ushort FeatureReportByteLength;
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 17)]
        public ushort[] Reserved;
        public ushort NumberLinkCollectionNodes;
        public ushort NumberInputButtonCaps;
        public ushort NumberInputValueCaps;
        public ushort NumberInputDataIndices;
        public ushort NumberOutputButtonCaps;
        public ushort NumberOutputValueCaps;
        public ushort NumberOutputDataIndices;
        public ushort NumberFeatureButtonCaps;
        public ushort NumberFeatureValueCaps;
        public ushort NumberFeatureDataIndices;
    }

    [DllImport("hid.dll", SetLastError = true)]
    private static extern void HidD_GetHidGuid(out Guid hidGuid);

    [DllImport("hid.dll", SetLastError = true)]
    private static extern bool HidD_GetAttributes(SafeFileHandle hidDeviceObject, ref HIDD_ATTRIBUTES attributes);

    [DllImport("hid.dll", SetLastError = true)]
    private static extern bool HidD_GetPreparsedData(SafeFileHandle hidDeviceObject, out IntPtr preparsedData);

    [DllImport("hid.dll", SetLastError = true)]
    private static extern bool HidD_FreePreparsedData(IntPtr preparsedData);

    [DllImport("hid.dll", SetLastError = true)]
    private static extern int HidP_GetCaps(IntPtr preparsedData, out HIDP_CAPS capabilities);

    [DllImport("hid.dll", SetLastError = true)]
    private static extern bool HidD_SetFeature(SafeFileHandle hidDeviceObject, byte[] reportBuffer, uint reportBufferLength);

    [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr SetupDiGetClassDevsW(
        ref Guid classGuid,
        string? enumerator,
        IntPtr hwndParent,
        uint flags);

    [DllImport("setupapi.dll", SetLastError = true)]
    private static extern bool SetupDiEnumDeviceInterfaces(
        IntPtr deviceInfoSet,
        IntPtr deviceInfoData,
        ref Guid interfaceClassGuid,
        uint memberIndex,
        ref SP_DEVICE_INTERFACE_DATA deviceInterfaceData);

    [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool SetupDiGetDeviceInterfaceDetailW(
        IntPtr deviceInfoSet,
        ref SP_DEVICE_INTERFACE_DATA deviceInterfaceData,
        IntPtr deviceInterfaceDetailData,
        uint deviceInterfaceDetailDataSize,
        out uint requiredSize,
        IntPtr deviceInfoData);

    [DllImport("setupapi.dll", SetLastError = true)]
    private static extern bool SetupDiDestroyDeviceInfoList(IntPtr deviceInfoSet);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFileW(
        string lpFileName,
        uint dwDesiredAccess,
        uint dwShareMode,
        IntPtr lpSecurityAttributes,
        uint dwCreationDisposition,
        uint dwFlagsAndAttributes,
        IntPtr hTemplateFile);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool WriteFile(
        SafeFileHandle hFile,
        byte[] lpBuffer,
        uint nNumberOfBytesToWrite,
        out uint lpNumberOfBytesWritten,
        ref NativeOverlapped lpOverlapped);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool ReadFile(
        SafeFileHandle hFile,
        byte[] lpBuffer,
        uint nNumberOfBytesToRead,
        out uint lpNumberOfBytesRead,
        ref NativeOverlapped lpOverlapped);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetOverlappedResult(
        SafeFileHandle hFile,
        ref NativeOverlapped lpOverlapped,
        out uint lpNumberOfBytesTransferred,
        bool bWait);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CancelIo(SafeFileHandle hFile);
}
