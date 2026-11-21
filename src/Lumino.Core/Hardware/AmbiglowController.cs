using System.Runtime.InteropServices;
using Lumino.Core.Services;
using Microsoft.Win32;
using Microsoft.Win32.SafeHandles;

namespace Lumino.Core.Hardware;

public sealed class AmbiglowDeviceNotFoundException : Exception
{
    public AmbiglowDeviceNotFoundException(string message) : base(message) { }
}

public sealed record AmbiglowOsdState(int? Mode, int? Color, int? Brightness, int? Speed, bool UsbAvailable);

/// <summary>
/// Philips Evnia 27M2N8500 Ambiglow controller:
/// - DisplayPort DDC via NvAPI I2C (OSD modes, 14 colors, 3 brightness levels, 3 speeds)
/// - Direct USB WinUSB MCU ENE 0CF2:A201 (30 LEDs individually addressable when USB upstream is connected)
/// Exact port of Lumino ambiglow.py.
/// </summary>
public static class AmbiglowController
{
    public const ushort UsbVid = 0x0CF2;
    public const ushort UsbPid = 0xA201;
    public const uint AddrChip = 0x4000;
    public const uint AddrSource = 0xC450;
    public const string Model = "27M2N8500";
    public const int LedCount = 30;

    public const int StaticMode = 7;

    public static readonly (int Value, string Label)[] LightModes =
    [
        (0, "Désactivé"),
        (1, "Suivre la vidéo"),
        (2, "Suivre l'audio"),
        (3, "Changement de couleur"),
        (4, "Onde de couleur"),
        (5, "Respiration des couleurs"),
        (6, "Nuit étoilée"),
        (7, "Mode statique"),
    ];

    public static readonly (int Value, string Label)[] Colors =
    [
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
    ];

    public static readonly Dictionary<int, string> ColorHex = new()
    {
        [1] = "#ffffff",
        [2] = "#ff0000",
        [3] = "#ff1567",
        [4] = "#ff15d0",
        [5] = "#6726ee",
        [6] = "#0000ff",
        [7] = "#4aa3ff",
        [8] = "#00d4ff",
        [9] = "#2ad4b8",
        [10] = "#00ff00",
        [11] = "#b6d400",
        [12] = "#ffe14a",
        [13] = "#ff8a00",
    };

    /// <summary>
    /// The 13 preset swatches used throughout Lumino (exact Philips Ambiglow palette).
    /// </summary>
    public static readonly string[] PresetSwatches =
        Colors.Where(c => ColorHex.ContainsKey(c.Value)).Select(c => ColorHex[c.Value]).ToArray();

    public static readonly (int Value, string Label)[] BrightnessLevels =
    [
        (0, "Brillant"),
        (1, "Plus brillant"),
        (2, "Très brillant"),
    ];

    public static readonly (int Value, string Label)[] SpeedLevels =
    [
        (0, "Bas"),
        (1, "Normal"),
        (2, "Haut"),
    ];

    public static readonly HashSet<int> SpeedModes = [3, 4, 5, 6];
    public static readonly HashSet<int> ColorModes = [3, 4, 5, 6, 7];

    private const byte SubMode = 25;
    private const byte SubColor = 26;
    private const byte SubBrightness = 28;
    private const byte SubSpeed = 29;

    private static readonly Dictionary<byte, byte> SubMax = new()
    {
        [SubMode] = 7,
        [SubColor] = 13,
        [SubBrightness] = 2,
        [SubSpeed] = 2,
    };

    private static readonly object DdcLock = new();
    private static NvApiClient? _nv;
    private static IntPtr _gpu;
    private static uint _philipsMask;

    public static int? IndexForHex(string? hx)
    {
        if (string.IsNullOrWhiteSpace(hx))
            return null;
        string want = hx.Trim().ToLowerInvariant();
        foreach (var (val, hex) in ColorHex)
        {
            if (hex == want)
                return val;
        }
        return null;
    }

    // -------------------------------------------------------------------------
    // DisplayPort DDC (NvAPI I2C VCP 0xE2)
    // -------------------------------------------------------------------------

    private static byte ComputeChecksum(byte[] payload)
    {
        byte c = 0x3F;
        foreach (byte b in payload)
            c ^= b;
        return c;
    }

    private static (NvApiClient Nv, IntPtr Gpu) GetSession()
    {
        if (_nv is null)
        {
            var nv = new NvApiClient();
            nv.Initialize();
            var gpus = nv.EnumGpus();
            if (gpus.Length == 0)
                throw new InvalidOperationException("GPU NVIDIA introuvable");
            _nv = nv;
            _gpu = gpus[0];
        }
        return (_nv, _gpu);
    }

    private static byte? ParseReplyValue(byte[] data, byte sub)
    {
        if (data.Length < 11 || data[3] != 0 || data[4] != 0xE2)
            return null;
        if (!SubMax.TryGetValue(sub, out byte expectedMax) || data[7] != expectedMax)
            return null;

        byte total = 0;
        foreach (byte b in data)
            total ^= b;
        if (total != 0x50)
            return null;

        return data[9];
    }

    private static int? ReadSub(NvApiClient nv, IntPtr gpu, uint mask, byte sub)
    {
        byte[] body = [0x84, 0x01, 0xE2, 0xA0, sub];
        byte[] payload = [.. body, ComputeChecksum(body)];

        for (int attempt = 0; attempt < 6; attempt++)
        {
            nv.DdcTransfer(gpu, mask, payload, readCount: 0);
            Thread.Sleep(80);
            var (_, data) = nv.DdcTransfer(gpu, mask, new byte[12], readCount: 12);
            byte? val = ParseReplyValue(data, sub);
            if (val.HasValue)
                return val.Value;

            bool foreign = data.Length >= 5 && data[4] == 0x10;
            bool anyNonZero = data.Any(b => b != 0);
            Thread.Sleep((foreign || !anyNonZero) ? 120 : 50);
        }

        return null;
    }

    private static int WriteSub(NvApiClient nv, IntPtr gpu, uint mask, byte sub, int value)
    {
        byte[] body = [0x86, 0x03, 0xE2, 0xA0, sub, 0x00, (byte)(value & 0xFF)];
        byte[] payload = [.. body, ComputeChecksum(body)];
        var (status, _) = nv.DdcTransfer(gpu, mask, payload, readCount: 0);
        return status;
    }

    private static uint GetPhilipsMask(NvApiClient nv, IntPtr gpu)
    {
        if (_philipsMask != 0)
            return _philipsMask;

        foreach (uint mask in nv.GetConnectedDisplayIds(gpu))
        {
            if (ReadSub(nv, gpu, mask, SubMode).HasValue)
            {
                _philipsMask = mask;
                return mask;
            }
        }
        throw new InvalidOperationException("Philips introuvable sur le DisplayPort");
    }

    private static T Held<T>(Func<T> fn)
    {
        using var _ = HardwareHold.Acquire();
        lock (DdcLock)
        {
            return fn();
        }
    }

    public static int GetLightMode() => Held(() =>
    {
        var (nv, gpu) = GetSession();
        uint mask = GetPhilipsMask(nv, gpu);
        return ReadSub(nv, gpu, mask, SubMode)
            ?? throw new InvalidOperationException("Lecture du mode Ambiglow impossible");
    });

    public static int SetLightMode(int value)
    {
        if (!LightModes.Any(m => m.Value == value))
            throw new ArgumentOutOfRangeException(nameof(value), "Mode Ambiglow inconnu");

        return Held(() =>
        {
            var (nv, gpu) = GetSession();
            uint mask = GetPhilipsMask(nv, gpu);
            int st = WriteSub(nv, gpu, mask, SubMode, value);
            if (st != NvApiClient.NvApiOk)
                throw new InvalidOperationException($"Écriture du mode refusée ({st})");
            Thread.Sleep(120);
            int? got = ReadSub(nv, gpu, mask, SubMode);
            if (got != value)
                throw new InvalidOperationException($"L'écran a gardé {got}");
            return got.Value;
        });
    }

    public static int GetColor() => Held(() =>
    {
        var (nv, gpu) = GetSession();
        uint mask = GetPhilipsMask(nv, gpu);
        return ReadSub(nv, gpu, mask, SubColor)
            ?? throw new InvalidOperationException("Lecture de la couleur Ambiglow impossible");
    });

    public static int SetColor(int value)
    {
        if (!Colors.Any(c => c.Value == value))
            throw new ArgumentOutOfRangeException(nameof(value), "Couleur Ambiglow inconnue");

        return Held(() =>
        {
            var (nv, gpu) = GetSession();
            uint mask = GetPhilipsMask(nv, gpu);
            int st = WriteSub(nv, gpu, mask, SubColor, value);
            if (st != NvApiClient.NvApiOk)
                throw new InvalidOperationException($"Écriture de la couleur refusée ({st})");
            return value;
        });
    }

    public static int SetStaticColor(int value)
    {
        if (!Colors.Any(c => c.Value == value))
            throw new ArgumentOutOfRangeException(nameof(value), "Couleur Ambiglow inconnue");

        return Held(() =>
        {
            var (nv, gpu) = GetSession();
            uint mask = GetPhilipsMask(nv, gpu);
            int? mode = ReadSub(nv, gpu, mask, SubMode);
            if (mode != StaticMode)
            {
                int st = WriteSub(nv, gpu, mask, SubMode, StaticMode);
                if (st != NvApiClient.NvApiOk)
                    throw new InvalidOperationException($"Écriture du mode refusée ({st})");
                Thread.Sleep(200);
            }
            int cst = WriteSub(nv, gpu, mask, SubColor, value);
            if (cst != NvApiClient.NvApiOk)
                throw new InvalidOperationException($"Écriture de la couleur refusée ({cst})");
            return value;
        });
    }

    public static int GetBrightness() => Held(() =>
    {
        var (nv, gpu) = GetSession();
        uint mask = GetPhilipsMask(nv, gpu);
        return ReadSub(nv, gpu, mask, SubBrightness)
            ?? throw new InvalidOperationException("Lecture de la luminosité Ambiglow impossible");
    });

    public static int SetBrightness(int value)
    {
        if (!BrightnessLevels.Any(b => b.Value == value))
            throw new ArgumentOutOfRangeException(nameof(value), "Luminosité Ambiglow inconnue");

        return Held(() =>
        {
            var (nv, gpu) = GetSession();
            uint mask = GetPhilipsMask(nv, gpu);
            int st = WriteSub(nv, gpu, mask, SubBrightness, value);
            if (st != NvApiClient.NvApiOk)
                throw new InvalidOperationException($"Écriture de la luminosité refusée ({st})");
            return value;
        });
    }

    public static int GetSpeed() => Held(() =>
    {
        var (nv, gpu) = GetSession();
        uint mask = GetPhilipsMask(nv, gpu);
        return ReadSub(nv, gpu, mask, SubSpeed)
            ?? throw new InvalidOperationException("Lecture de la vitesse Ambiglow impossible");
    });

    public static int SetSpeed(int value)
    {
        if (!SpeedLevels.Any(s => s.Value == value))
            throw new ArgumentOutOfRangeException(nameof(value), "Vitesse Ambiglow inconnue");

        return Held(() =>
        {
            var (nv, gpu) = GetSession();
            uint mask = GetPhilipsMask(nv, gpu);
            int st = WriteSub(nv, gpu, mask, SubSpeed, value);
            if (st != NvApiClient.NvApiOk)
                throw new InvalidOperationException($"Écriture de la vitesse refusée ({st})");
            return value;
        });
    }

    public static AmbiglowOsdState ReadAllOsd()
    {
        int? mode = null, color = null, bri = null, speed = null;
        try { mode = GetLightMode(); } catch { }
        try { color = GetColor(); } catch { }
        try { bri = GetBrightness(); } catch { }
        try { speed = GetSpeed(); } catch { }
        bool usb = IsUsbAvailable();
        return new AmbiglowOsdState(mode, color, bri, speed, usb);
    }

    // -------------------------------------------------------------------------
    // Direct USB WinUSB (ENE 0CF2:A201)
    // -------------------------------------------------------------------------

    public static bool IsUsbAvailable()
    {
        try
        {
            using var dev = new EvniaUsbSession();
            return true;
        }
        catch
        {
            return false;
        }
    }

    public static void ApplyUsbColor(byte r, byte g, byte b, int brightness100)
    {
        var scaled = new RgbColor(r, g, b).ScaledFloor(brightness100);
        var cols = new RgbColor[LedCount];
        Array.Fill(cols, scaled);

        using var dev = new EvniaUsbSession();
        dev.SetColors(cols);
    }

    public sealed class EvniaUsbSession : IDisposable
    {
        private SafeFileHandle? _file;
        private IntPtr _usb;
        public ushort ProductId { get; private set; }

        public EvniaUsbSession()
        {
            Open();
        }

        private void Open()
        {
            var guids = FindEneInterfaceGuids();
            if (guids.Count == 0)
                throw new AmbiglowDeviceNotFoundException("Ambiglow introuvable. Branche le câble USB de l'écran (le DisplayPort ne suffit pas) et ferme Evnia Precision Center.");

            Exception? last = null;
            foreach (var (pid, guidText) in guids)
            {
                if (!Guid.TryParse(guidText, out Guid guid))
                    continue;

                string marker = $"VID_{UsbVid:X4}&PID_{pid:X4}";
                foreach (var path in EnumInterfacePaths(guid))
                {
                    if (!path.ToUpperInvariant().Contains(marker, StringComparison.Ordinal))
                        continue;

                    var file = CreateFileW(
                        path,
                        GENERIC_READ | GENERIC_WRITE,
                        FILE_SHARE_READ | FILE_SHARE_WRITE,
                        IntPtr.Zero,
                        OPEN_EXISTING,
                        FILE_ATTRIBUTE_NORMAL,
                        IntPtr.Zero);

                    if (file.IsInvalid)
                    {
                        last = new IOException("Accès WinUSB refusé");
                        continue;
                    }

                    if (!WinUsb_Initialize(file, out IntPtr usb))
                    {
                        file.Dispose();
                        last = new IOException("WinUSB n'a pas pris l'interface");
                        continue;
                    }

                    _file = file;
                    _usb = usb;
                    ProductId = pid;

                    try
                    {
                        byte[] chip = Read(AddrChip, 2);
                        if (chip.Length == 2 && chip[0] == (byte)'w' && chip[1] == (byte)'0')
                            return;

                        Dispose();
                        last = new IOException($"Puce ENE inattendue : {Convert.ToHexString(chip)}");
                    }
                    catch (Exception ex)
                    {
                        Dispose();
                        last = ex;
                    }
                }
            }

            throw new AmbiglowDeviceNotFoundException(last?.Message
                ?? "Ambiglow vu par Windows mais inaccessible. Ferme Evnia Precision Center, puis réessaie.");
        }

        public byte[] Read(uint addr, int length) =>
            ControlTransfer(0xC0, 0x81, addr, new byte[length]);

        public int Write(uint addr, byte[] data)
        {
            ControlTransfer(0x40, 0x80, addr, data);
            return data.Length;
        }

        public void SetColors(IReadOnlyList<RgbColor> colors)
        {
            if (colors.Count != LedCount)
                throw new ArgumentException($"Il faut {LedCount} LEDs, pas {colors.Count}");

            var raw = new byte[LedCount * 3];
            for (int i = 0; i < LedCount; i++)
            {
                raw[i * 3] = colors[i].R;
                raw[i * 3 + 1] = colors[i].G;
                raw[i * 3 + 2] = colors[i].B;
            }
            Write(AddrSource, raw);
        }

        private byte[] ControlTransfer(byte requestType, byte request, uint addr, byte[] payload)
        {
            var packet = new WINUSB_SETUP_PACKET
            {
                RequestType = requestType,
                Request = request,
                Value = (ushort)((addr >> 16) & 0xFFFF),
                Index = (ushort)(addr & 0xFFFF),
                Length = (ushort)payload.Length
            };

            var buf = new byte[payload.Length];
            Buffer.BlockCopy(payload, 0, buf, 0, payload.Length);

            if (!WinUsb_ControlTransfer(_usb, packet, buf, (uint)buf.Length, out uint moved, IntPtr.Zero))
                throw new IOException("Transfert USB Ambiglow refusé");

            var outBuf = new byte[moved];
            Buffer.BlockCopy(buf, 0, outBuf, 0, (int)moved);
            return outBuf;
        }

        public void Dispose()
        {
            if (_usb != IntPtr.Zero)
            {
                WinUsb_Free(_usb);
                _usb = IntPtr.Zero;
            }
            _file?.Dispose();
            _file = null;
        }
    }

    private static List<(ushort Pid, string GuidText)> FindEneInterfaceGuids()
    {
        var found = new List<(ushort Pid, string GuidText)>();
        try
        {
            using var usbKey = Registry.LocalMachine.OpenSubKey(@"SYSTEM\CurrentControlSet\Enum\USB");
            if (usbKey is null)
                return found;

            string prefix = $"VID_{UsbVid:X4}&PID_";
            foreach (var name in usbKey.GetSubKeyNames())
            {
                string upper = name.ToUpperInvariant();
                if (!upper.StartsWith(prefix, StringComparison.Ordinal))
                    continue;

                int pidIdx = upper.IndexOf("PID_", StringComparison.Ordinal);
                if (pidIdx < 0 || pidIdx + 8 > upper.Length)
                    continue;
                if (!ushort.TryParse(upper.Substring(pidIdx + 4, 4), System.Globalization.NumberStyles.HexNumber, null, out ushort pid))
                    continue;

                using var devKey = usbKey.OpenSubKey(name);
                if (devKey is null)
                    continue;

                foreach (var inst in devKey.GetSubKeyNames())
                {
                    using var paramsKey = devKey.OpenSubKey(inst + @"\Device Parameters");
                    var raw = paramsKey?.GetValue("DeviceInterfaceGUIDs");
                    if (raw is string[] arr)
                    {
                        foreach (var g in arr)
                            if (!string.IsNullOrWhiteSpace(g))
                                found.Add((pid, g.Trim()));
                    }
                    else if (raw is string s && !string.IsNullOrWhiteSpace(s))
                    {
                        found.Add((pid, s.Trim()));
                    }
                }
            }
        }
        catch
        {
            // Ignore registry enumeration errors
        }

        found.Sort((a, b) => (a.Pid == UsbPid ? 0 : 1).CompareTo(b.Pid == UsbPid ? 0 : 1));
        return found;
    }

    private static IEnumerable<string> EnumInterfacePaths(Guid guid)
    {
        IntPtr handle = SetupDiGetClassDevsW(ref guid, null, IntPtr.Zero, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE);
        if (handle == IntPtr.Zero || handle == new IntPtr(-1))
            yield break;

        try
        {
            uint index = 0;
            while (true)
            {
                var data = new SP_DEVICE_INTERFACE_DATA
                {
                    cbSize = (uint)Marshal.SizeOf<SP_DEVICE_INTERFACE_DATA>()
                };
                if (!SetupDiEnumDeviceInterfaces(handle, IntPtr.Zero, ref guid, index, ref data))
                    break;
                index++;

                SetupDiGetDeviceInterfaceDetailW(handle, ref data, IntPtr.Zero, 0, out uint need, IntPtr.Zero);
                if (need < 8)
                    continue;

                IntPtr buf = Marshal.AllocHGlobal((int)need);
                try
                {
                    Marshal.WriteInt32(buf, IntPtr.Size == 8 ? 8 : 6);
                    if (!SetupDiGetDeviceInterfaceDetailW(handle, ref data, buf, need, out _, IntPtr.Zero))
                        continue;

                    string? path = Marshal.PtrToStringUni(IntPtr.Add(buf, 4));
                    if (!string.IsNullOrEmpty(path))
                        yield return path;
                }
                finally
                {
                    Marshal.FreeHGlobal(buf);
                }
            }
        }
        finally
        {
            SetupDiDestroyDeviceInfoList(handle);
        }
    }

    private const uint GENERIC_READ = 0x80000000;
    private const uint GENERIC_WRITE = 0x40000000;
    private const uint FILE_SHARE_READ = 0x01;
    private const uint FILE_SHARE_WRITE = 0x02;
    private const uint OPEN_EXISTING = 3;
    private const uint FILE_ATTRIBUTE_NORMAL = 0x80;
    private const uint DIGCF_PRESENT = 0x02;
    private const uint DIGCF_DEVICEINTERFACE = 0x10;

    [StructLayout(LayoutKind.Sequential)]
    private struct SP_DEVICE_INTERFACE_DATA
    {
        public uint cbSize;
        public Guid InterfaceClassGuid;
        public uint Flags;
        public IntPtr Reserved;
    }

    [StructLayout(LayoutKind.Sequential, Pack = 1)]
    private struct WINUSB_SETUP_PACKET
    {
        public byte RequestType;
        public byte Request;
        public ushort Value;
        public ushort Index;
        public ushort Length;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFileW(
        string lpFileName,
        uint dwDesiredAccess,
        uint dwShareMode,
        IntPtr lpSecurityAttributes,
        uint dwCreationDisposition,
        uint dwFlagsAndAttributes,
        IntPtr hTemplateFile);

    [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr SetupDiGetClassDevsW(ref Guid classGuid, string? enumerator, IntPtr hwndParent, uint flags);

    [DllImport("setupapi.dll", SetLastError = true)]
    private static extern bool SetupDiEnumDeviceInterfaces(IntPtr devInfo, IntPtr devInfoData, ref Guid guid, uint index, ref SP_DEVICE_INTERFACE_DATA data);

    [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool SetupDiGetDeviceInterfaceDetailW(IntPtr devInfo, ref SP_DEVICE_INTERFACE_DATA data, IntPtr detail, uint size, out uint required, IntPtr devInfoData);

    [DllImport("setupapi.dll", SetLastError = true)]
    private static extern bool SetupDiDestroyDeviceInfoList(IntPtr devInfo);

    [DllImport("winusb.dll", SetLastError = true)]
    private static extern bool WinUsb_Initialize(SafeFileHandle deviceHandle, out IntPtr interfaceHandle);

    [DllImport("winusb.dll", SetLastError = true)]
    private static extern bool WinUsb_Free(IntPtr interfaceHandle);

    [DllImport("winusb.dll", SetLastError = true)]
    private static extern bool WinUsb_ControlTransfer(
        IntPtr interfaceHandle,
        WINUSB_SETUP_PACKET setupPacket,
        byte[] buffer,
        uint bufferLength,
        out uint lengthTransferred,
        IntPtr overlapped);
}
