namespace Lumino.Core.Hardware;

/// <summary>
/// Gigabyte RGB Fusion 2 Blackwell GPU (RTX 50 series) I2C protocol over NvAPI (exact port of blackwell.py).
/// Address 0x75, internal port 1, 64-byte registerless blocks, 6 hardware zones (0..5).
/// </summary>
public static class BlackwellGpu
{
    public const byte Addr = 0x75;
    public const byte RegMode = 0x12;
    public const byte RegColor = 0x16;
    public const byte ModeDirect = 0x00;
    public const byte ModeStatic = 0x01;
    public const byte ModeBreathing = 0x02;
    public const byte ModeColorCycle = 0x05;
    public const byte ModeWave = 0x06;
    public const byte SpeedNormal = 0x03;
    public const int BrightMax = 10;
    public const int ZoneCount = 6;

    public static int LevelFromPercent(int brightness100)
    {
        int b = Math.Clamp(brightness100, 0, 100);
        if (b <= 0)
            return 0;
        return Math.Min(10, (b + 9) / 10);
    }

    public static byte SpeedFromUi(int speedUi)
    {
        int s = Math.Clamp(speedUi, 1, 10);
        if (s <= 4)
            return 1;
        return (byte)Math.Min(6, s - 3);
    }

    private static byte[] ProbePacket()
    {
        var pkt = new byte[64];
        pkt[0] = 0x10;
        pkt[1] = 0x01;
        return pkt;
    }

    private static byte[] ZonePacket(byte r, byte g, byte b, int zone, int brightness, byte speed = SpeedNormal, byte mode = ModeStatic, byte reg = RegMode)
    {
        var pkt = new byte[64];
        pkt[0] = reg;
        pkt[1] = 0x01;
        pkt[2] = mode;
        pkt[3] = speed;
        pkt[4] = (byte)Math.Clamp(brightness, 1, 10);
        pkt[5] = r;
        pkt[6] = g;
        pkt[7] = b;
        pkt[8] = 0x00;
        pkt[9] = (byte)(zone & 0xFF);
        pkt[10] = 0x00;
        return pkt;
    }

    private static byte[] SavePacket()
    {
        var pkt = new byte[64];
        pkt[0] = 0x13;
        pkt[1] = 0x01;
        return pkt;
    }

    public static (bool Ok, string Detail) Probe(NvApiClient nv, IntPtr handle, byte port = 1)
    {
        int st = nv.WriteBlock(handle, Addr, ProbePacket(), port);
        if (st != NvApiClient.NvApiOk)
            return (false, $"write_status={st}");

        Thread.Sleep(20);
        var (rst, data) = nv.ReadBlock(handle, Addr, 4, port);
        if (rst != NvApiClient.NvApiOk || data is null || data.Length < 4)
            return (false, $"read_status={rst}");

        bool ok = data[0] == 0x01 && (data[1] == 0x01 || data[1] == 0x02) && data[2] == 0x01;
        return (ok, $"reply=[{string.Join(", ", data)}]");
    }

    public static (int BrightnessRaw, int[] ZoneStatuses, int? SaveStatus) ApplyZones(
        NvApiClient nv,
        IntPtr handle,
        IReadOnlyList<RgbColor> colors,
        int brightness10 = BrightMax,
        bool doSave = false,
        byte port = 1)
    {
        int bval = Math.Clamp(brightness10, 1, 10);
        var cols = PadToZones(colors);
        var statuses = new int[ZoneCount];

        for (int z = 0; z < ZoneCount; z++)
        {
            var c = cols[z];
            int st = nv.WriteBlock(handle, Addr, ZonePacket(c.R, c.G, c.B, z, bval), port);
            statuses[z] = st;
            if (st != NvApiClient.NvApiOk)
                throw new InvalidOperationException($"Zone {z} : write status={st}");
            Thread.Sleep(120);
        }

        int? saved = null;
        if (doSave)
        {
            Thread.Sleep(120);
            int st = nv.WriteBlock(handle, Addr, SavePacket(), port);
            saved = st;
            if (st != NvApiClient.NvApiOk)
                throw new InvalidOperationException($"Save : write status={st}");
        }

        return (bval, statuses, saved);
    }

    public static void ApplyEffect(
        NvApiClient nv,
        IntPtr handle,
        byte mode,
        int brightness10 = BrightMax,
        int speedUi = 4,
        RgbColor color = default,
        byte port = 1)
    {
        int bval = Math.Clamp(brightness10, 1, 10);
        byte spd = SpeedFromUi(speedUi);
        for (int z = 0; z < ZoneCount; z++)
        {
            int st = nv.WriteBlock(handle, Addr, ZonePacket(color.R, color.G, color.B, z, bval, spd, mode, RegMode), port);
            if (st != NvApiClient.NvApiOk)
                throw new InvalidOperationException($"Zone {z} : write status={st}");
            Thread.Sleep(120);
        }
    }

    public static void ApplyWave(NvApiClient nv, IntPtr handle, int brightness10 = BrightMax, int speedUi = 4, byte port = 1)
    {
        ApplyEffect(nv, handle, ModeWave, brightness10, speedUi, RgbColor.Black, port);
    }

    public static void ApplyDirect(
        NvApiClient nv,
        IntPtr handle,
        IReadOnlyList<RgbColor> colors,
        int brightness10 = BrightMax,
        byte port = 1)
    {
        int bval = Math.Clamp(brightness10, 1, 10);
        var cols = PadToZones(colors);
        for (int z = 0; z < ZoneCount; z++)
        {
            var c = cols[z];
            int st = nv.WriteBlock(handle, Addr, ZonePacket(c.R, c.G, c.B, z, bval, SpeedNormal, ModeDirect, RegColor), port);
            if (st != NvApiClient.NvApiOk)
                throw new InvalidOperationException($"Zone {z} : write status={st}");
            Thread.Sleep(10);
        }
    }

    public static (int BrightnessRaw, int[] ZoneStatuses, int? SaveStatus) ApplyStatic(
        NvApiClient nv,
        IntPtr handle,
        byte r,
        byte g,
        byte b,
        int brightness10 = BrightMax,
        bool doSave = false,
        byte port = 1)
    {
        var cols = new RgbColor[ZoneCount];
        Array.Fill(cols, new RgbColor(r, g, b));
        return ApplyZones(nv, handle, cols, brightness10, doSave, port);
    }

    private static RgbColor[] PadToZones(IReadOnlyList<RgbColor> colors)
    {
        var result = new RgbColor[ZoneCount];
        if (colors.Count == 0)
        {
            Array.Fill(result, RgbColor.Black);
            return result;
        }
        for (int i = 0; i < ZoneCount; i++)
            result[i] = i < colors.Count ? colors[i] : colors[^1];
        return result;
    }
}
