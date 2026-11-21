using System.Text;

namespace Lumino.Core.Hardware;

public sealed record AuraChannel(string Kind, int EffectIndex, int DirectIndex, int LedCount);

/// <summary>
/// ASUS Aura USB HID backend (0B05:19AF, ROG Strix B850-A and compatible Aura USB controllers).
/// Exact port of Lumino aura.py.
/// </summary>
public sealed class AsusAuraUsb : IDisposable
{
    public const ushort Vid = 0x0B05;
    public const ushort Pid = 0x19AF;
    private const int ReportSize = 65;
    private const byte Head = 0xEC;
    private const byte ReqFw = 0x82;
    private const byte ReqCfg = 0xB0;
    private const byte CtlDirect = 0x40;
    private const byte CmdEffect = 0x35;
    private const byte CmdEffectColor = 0x36;
    private const byte CmdCommit = 0x3F;
    private const byte ModeStatic = 0x01;
    private const byte ModeDirect = 0xFF;
    private const int AddrLedCap = 60;

    private readonly HidDevice _dev;
    private bool _directFixed;
    private readonly Dictionary<int, bool> _directAddr = new();

    public string Firmware { get; }
    public byte[] ConfigBytes { get; }
    public List<AuraChannel> Channels { get; } = [];

    public AsusAuraUsb(string? path = null)
    {
        if (path is null)
        {
            var devs = HidDevice.Enumerate(Vid, Pid);
            if (devs.Count == 0)
                throw new IOException("AURA LED Controller (0B05:19AF) introuvable");

            devs.Sort((a, b) =>
            {
                int sa = a.Path.Contains("MI_02", StringComparison.OrdinalIgnoreCase) ? 0 : 1;
                int sb = b.Path.Contains("MI_02", StringComparison.OrdinalIgnoreCase) ? 0 : 1;
                return sa.CompareTo(sb);
            });
            path = devs[0].Path;
        }

        _dev = HidDevice.OpenPath(path);
        try
        {
            Firmware = ReadFirmware();
            ConfigBytes = ReadConfig();

            int nFixed = ConfigBytes[0x1B];
            int nRgb = ConfigBytes[0x1D];
            int nAddr = ConfigBytes[0x02];
            if (nFixed < nRgb)
                nRgb = 0;

            if (nFixed > 0)
                Channels.Add(new AuraChannel("fixed", 0, 4, nFixed));

            int addrCount = Math.Min(nAddr, 8);
            for (int i = 0; i < addrCount; i++)
                Channels.Add(new AuraChannel("addr", Channels.Count, i, AddrLedCap));
        }
        catch
        {
            _dev.Dispose();
            throw;
        }
    }

    private byte[]? Xfer(byte[] payload, bool read = true)
    {
        var buf = new byte[ReportSize];
        buf[0] = Head;
        Buffer.BlockCopy(payload, 0, buf, 1, Math.Min(payload.Length, ReportSize - 1));

        int written = _dev.Write(buf);
        if (written != ReportSize)
            throw new IOException("hid_write incomplet (ASUS Aura)");

        if (!read)
            return null;

        byte[] r = _dev.Read(ReportSize, 500);
        if (r.Length == ReportSize - 1)
        {
            var padded = new byte[ReportSize];
            Buffer.BlockCopy(r, 0, padded, 1, r.Length);
            return padded;
        }
        return r;
    }

    private string ReadFirmware()
    {
        var r = Xfer([ReqFw], read: true) ?? [];
        if (r.Length < 18 || r[1] != 0x02)
            throw new IOException($"Firmware Aura inattendu : {Convert.ToHexString(r.Take(8).ToArray())}");
        return Encoding.ASCII.GetString(r, 2, 16).Trim('\0');
    }

    private byte[] ReadConfig()
    {
        var r = Xfer([ReqCfg], read: true) ?? [];
        if (r.Length < 64 || r[1] != 0x30)
            throw new IOException($"Config Aura inattendue : {Convert.ToHexString(r.Take(8).ToArray())}");
        var cfg = new byte[60];
        Buffer.BlockCopy(r, 4, cfg, 0, 60);
        return cfg;
    }

    public void SendGen1() => Xfer([0x52, 0x53, 0x00, 0x01], read: false);

    public void SendEffect(int channel, byte mode) =>
        Xfer([CmdEffect, (byte)(channel & 0xFF), 0x00, 0x00, mode], read: false);

    public void SendDirect(int device, IReadOnlyList<RgbColor> colors)
    {
        int offset = 0;
        int n = colors.Count;
        while (offset < n)
        {
            int count = Math.Min(20, n - offset);
            bool isLast = (offset + count) >= n;
            var pkt = new byte[4 + count * 3];
            pkt[0] = CtlDirect;
            pkt[1] = (byte)((isLast ? 0x80 : 0x00) | (device & 0x7F));
            pkt[2] = (byte)offset;
            pkt[3] = (byte)count;
            for (int i = 0; i < count; i++)
            {
                var c = colors[offset + i];
                pkt[4 + i * 3] = c.R;
                pkt[4 + i * 3 + 1] = c.G;
                pkt[4 + i * 3 + 2] = c.B;
            }
            Xfer(pkt, read: false);
            offset += count;
        }
    }

    public void SendCommit() => Xfer([CmdCommit, 0x55], read: false);

    public void SetCm(byte r, byte g, byte b)
    {
        var fixedCh = Channels.FirstOrDefault(c => c.Kind == "fixed")
            ?? throw new InvalidOperationException("Pas de LEDs fixes Aura détectées");
        int n = fixedCh.LedCount;

        SendGen1();
        Thread.Sleep(20);
        SendEffect(0, ModeStatic);
        Thread.Sleep(20);

        int mask = (1 << n) - 1;
        var pkt = new byte[4 + n * 3];
        pkt[0] = CmdEffectColor;
        pkt[1] = (byte)((mask >> 8) & 0xFF);
        pkt[2] = (byte)(mask & 0xFF);
        pkt[3] = 0x00;
        for (int i = 0; i < n; i++)
        {
            pkt[4 + i * 3] = r;
            pkt[4 + i * 3 + 1] = g;
            pkt[4 + i * 3 + 2] = b;
        }

        Xfer(pkt, read: false);
        Thread.Sleep(20);
        SendCommit();
        _directFixed = false;
    }

    public void SetCmColors(IReadOnlyList<RgbColor> colors)
    {
        var ch = Channels.FirstOrDefault(c => c.Kind == "fixed")
            ?? throw new InvalidOperationException("Pas de LEDs fixes Aura détectées");

        var cols = new RgbColor[ch.LedCount];
        if (colors.Count == 0)
            Array.Fill(cols, RgbColor.Black);
        else
            for (int i = 0; i < cols.Length; i++)
                cols[i] = i < colors.Count ? colors[i] : colors[^1];

        if (!_directFixed)
        {
            SendEffect(ch.EffectIndex, ModeDirect);
            _directFixed = true;
        }
        SendDirect(ch.DirectIndex, cols);
    }

    public void SetChannelColors(AuraChannel ch, IReadOnlyList<RgbColor> colors, bool refreshMode = false)
    {
        var cols = colors.Count > 0 ? colors : [RgbColor.Black];
        bool alreadyDirect = ch.Kind == "fixed"
            ? _directFixed
            : _directAddr.TryGetValue(ch.DirectIndex, out bool v) && v;

        if (refreshMode || !alreadyDirect)
        {
            SendEffect(ch.EffectIndex, ModeDirect);
            if (!alreadyDirect)
                Thread.Sleep(20);
            if (ch.Kind == "fixed")
                _directFixed = true;
            else
                _directAddr[ch.DirectIndex] = true;
        }

        SendDirect(ch.DirectIndex, cols);
    }

    public void ResetDirectFlags()
    {
        _directFixed = false;
        _directAddr.Clear();
    }

    public void ResetAddrFlag(int directIndex) => _directAddr[directIndex] = false;

    public void Dispose() => _dev.Dispose();
}
