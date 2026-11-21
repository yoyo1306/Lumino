using System.Text;

namespace Lumino.Core.Hardware;

public sealed record LinkEndpoint(
    byte Type,
    byte Model,
    string Name,
    int Leds,
    string Id,
    bool IsLcd,
    bool IsImplicit = false);

public sealed record LinkLedSpan(string Role, int Leds);

/// <summary>
/// Corsair iCUE LINK System Hub (1B1C:0C3F, usage_page 0xFF42, usage 0x01).
/// Exact port of Lumino corsair_link.py.
/// </summary>
public sealed class CorsairLinkHub : IDisposable
{
    public const ushort Vid = 0x1B1C;
    public const ushort Pid = 0x0C3F;
    public const ushort UsagePage = 0xFF42;
    public const ushort Usage = 0x01;

    private const int BufWrite = 513;
    private const int BufRead = 512;
    private const int WriteHeader = 4;
    private const int MaxPerRequest = 508;

    private static readonly byte[] CmdOpenEndpoint = [0x0D, 0x01];
    private static readonly byte[] CmdOpenColorEndpoint = [0x0D, 0x00];
    private static readonly byte[] CmdCloseEndpoint = [0x05, 0x01, 0x01];
    private static readonly byte[] CmdGetFirmware = [0x02, 0x13];
    private static readonly byte[] CmdSoftwareMode = [0x01, 0x03, 0x00, 0x02];
    private static readonly byte[] CmdHardwareMode = [0x01, 0x03, 0x00, 0x01];
    private static readonly byte[] CmdWriteColor = [0x06, 0x00];
    private static readonly byte[] CmdWriteColorNext = [0x07, 0x00];
    private static readonly byte[] CmdRead = [0x08, 0x01];

    private static readonly byte[] ModeGetDevices = [0x36];
    private static readonly byte[] ModeSetColor = [0x22];
    private static readonly byte[] DtypeGetDevices = [0x21, 0x00];
    private static readonly byte[] DtypeSetColor = [0x12, 0x00];

    private static readonly Dictionary<(byte Type, byte Model), (string Name, int Leds)> KnownDevices = new()
    {
        [(0x11, 0x00)] = ("iCUE LINK TITAN 240", 20),
        [(0x11, 0x04)] = ("iCUE LINK TITAN 240", 20),
        [(0x11, 0x01)] = ("iCUE LINK TITAN 280", 20),
        [(0x11, 0x02)] = ("iCUE LINK TITAN 360", 20),
        [(0x11, 0x05)] = ("iCUE LINK TITAN 360", 20),
        [(0x11, 0x03)] = ("iCUE LINK TITAN 420", 20),
        [(0x07, 0x00)] = ("iCUE LINK H100i RGB", 20),
        [(0x07, 0x04)] = ("iCUE LINK H100i RGB", 20),
        [(0x07, 0x01)] = ("iCUE LINK H115i RGB", 20),
        [(0x07, 0x02)] = ("iCUE LINK H150i RGB", 20),
        [(0x07, 0x05)] = ("iCUE LINK H150i RGB", 20),
        [(0x07, 0x03)] = ("iCUE LINK H170i RGB", 20),
        [(0x0F, 0x00)] = ("iCUE LINK RX RGB", 8),
        [(0x03, 0x00)] = ("iCUE LINK RX RGB MAX", 8),
        [(0x02, 0x00)] = ("iCUE LINK LX RGB", 18),
        [(0x01, 0x00)] = ("iCUE LINK QX RGB", 34),
        [(0x06, 0x00)] = ("iCUE LINK COOLER PUMP LCD", 24),
        [(0x05, 0x02)] = ("iCUE LINK 5000T RGB", 160),
    };

    private readonly HidDevice _dev;
    private bool _disposed;

    public string Firmware { get; }
    public List<LinkEndpoint> Endpoints { get; }
    public int TotalLeds { get; }

    public CorsairLinkHub(string? path = null)
    {
        var devs = HidDevice.Enumerate(Vid, Pid);
        if (devs.Count == 0)
            throw new IOException("System Hub iCUE LINK (1B1C:0C3F) introuvable");

        devs.Sort((a, b) =>
        {
            int sa = (a.UsagePage == UsagePage && a.Usage == Usage) ? 0 : 1;
            int sb = (b.UsagePage == UsagePage && b.Usage == Usage) ? 0 : 1;
            return sa.CompareTo(sb);
        });

        path ??= devs[0].Path;
        _dev = HidDevice.OpenPath(path);
        try
        {
            Firmware = ReadFirmware();
            EnterSoftwareMode();
            Endpoints = ReadDevices();
            TotalLeds = Endpoints.Sum(e => e.Leds);
        }
        catch
        {
            _dev.Dispose();
            throw;
        }
    }

    private byte[] Send(byte[] cmd, byte[] data, byte[]? waitFor = null)
    {
        var buf = new byte[BufWrite];
        buf[0] = 0x00;
        buf[2] = 0x01;
        Buffer.BlockCopy(cmd, 0, buf, 3, cmd.Length);
        if (data.Length > 0)
            Buffer.BlockCopy(data, 0, buf, 3 + cmd.Length, data.Length);

        int written = _dev.Write(buf);
        if (written != BufWrite)
            throw new IOException("hid_write incomplet (Hub LINK)");

        Thread.Sleep(30);
        byte[] res = _dev.Read(BufRead, 1000);

        if (waitFor is { Length: 2 })
        {
            for (int i = 0; i < 5; i++)
            {
                if (res.Length >= 6 && res[4] == waitFor[0])
                    return res;
                res = _dev.Read(BufRead, 1000);
            }
        }
        return res;
    }

    private byte[] ReadEndpoint(byte[] mode, byte[] dtype)
    {
        Send(CmdCloseEndpoint, mode);
        Send(CmdOpenEndpoint, mode);
        byte[] res = Send(CmdRead, [], waitFor: dtype);
        Send(CmdCloseEndpoint, mode);
        return res;
    }

    private string ReadFirmware()
    {
        byte[] r = Send(CmdGetFirmware, []);
        if (r.Length < 8)
            return "?";
        int patch = r[6] | (r[7] << 8);
        return $"v{r[4]}.{r[5]}.{patch}";
    }

    public void EnterSoftwareMode() => Send(CmdSoftwareMode, []);

    private List<LinkEndpoint> ReadDevices()
    {
        byte[] r = ReadEndpoint(ModeGetDevices, DtypeGetDevices);
        if (r.Length < 8)
            throw new IOException($"GET_DEVICES réponse courte ({r.Length} o)");

        int nChannels = r[6];
        var eps = new List<LinkEndpoint>();
        int pos = 7;

        for (int c = 0; c < nChannels; c++)
        {
            if (pos + 8 > r.Length)
                break;

            byte typ = r[pos + 2];
            byte model = r[pos + 3];
            int idLen = r[pos + 7];

            if (idLen == 0)
            {
                pos += 8;
                continue;
            }
            if (pos + 8 + idLen > r.Length)
                break;

            string epId = Encoding.ASCII.GetString(r, pos + 8, idLen).Trim('\0');
            if (!KnownDevices.TryGetValue((typ, model), out var known))
            {
                pos += 8 + idLen;
                continue;
            }

            if (known.Leds > 0)
            {
                eps.Add(new LinkEndpoint(typ, model, known.Name, known.Leds, epId, typ == 0x06));
            }
            pos += 8 + idLen;
        }

        // OpenRGB/Lumino rule: TITAN/H-series AIO pumps implicitly include the 24-LED LCD ring if not explicitly listed
        bool hasAio = eps.Any(e => e.Type is 0x07 or 0x11);
        bool hasLcd = eps.Any(e => e.IsLcd);
        if (hasAio && !hasLcd)
        {
            eps.Insert(Math.Min(1, eps.Count), new LinkEndpoint(
                0x06,
                0x00,
                "iCUE LINK COOLER PUMP LCD (anneau, implicite)",
                24,
                string.Empty,
                IsLcd: true,
                IsImplicit: true));
        }

        return eps;
    }

    public List<LinkLedSpan> GetLedSpans()
    {
        var spans = new List<LinkLedSpan>(Endpoints.Count);
        foreach (var e in Endpoints)
        {
            string role = (e.IsLcd || e.Type is 0x06 or 0x07 or 0x11) ? "block" : "fans";
            spans.Add(new LinkLedSpan(role, e.Leds));
        }
        return spans;
    }

    public void SetSegments(IReadOnlyList<RgbColor> segments, bool settle = true)
    {
        if (segments.Count != TotalLeds)
            throw new InvalidOperationException($"segments={segments.Count}, attendu={TotalLeds}");

        int colorBytes = segments.Count * 3;
        int dataLen = colorBytes + 2;
        var payload = new byte[WriteHeader + DtypeSetColor.Length + colorBytes];
        payload[0] = (byte)(dataLen & 0xFF);
        payload[1] = (byte)((dataLen >> 8) & 0xFF);
        Buffer.BlockCopy(DtypeSetColor, 0, payload, WriteHeader, DtypeSetColor.Length);

        int offset = WriteHeader + DtypeSetColor.Length;
        for (int i = 0; i < segments.Count; i++)
        {
            var c = segments[i];
            payload[offset + i * 3] = c.R;
            payload[offset + i * 3 + 1] = c.G;
            payload[offset + i * 3 + 2] = c.B;
        }

        Send(CmdCloseEndpoint, ModeSetColor);
        Send(CmdOpenColorEndpoint, ModeSetColor);

        for (int i = 0; i < payload.Length; i += MaxPerRequest)
        {
            int len = Math.Min(MaxPerRequest, payload.Length - i);
            var chunk = new byte[len];
            Buffer.BlockCopy(payload, i, chunk, 0, len);
            Send(i == 0 ? CmdWriteColor : CmdWriteColorNext, chunk);
        }

        Send(CmdCloseEndpoint, ModeSetColor);
        if (settle)
            Thread.Sleep(50);
    }

    public void SetStatic(byte r, byte g, byte b)
    {
        if (TotalLeds <= 0)
            throw new InvalidOperationException("Aucune LED LINK RGB détectée");

        var cols = new RgbColor[TotalLeds];
        Array.Fill(cols, new RgbColor(r, g, b));
        SetSegments(cols, settle: true);
    }

    public void Close(bool hardwareMode = false)
    {
        if (_disposed)
            return;
        _disposed = true;

        try
        {
            if (hardwareMode)
                Send(CmdHardwareMode, []);
        }
        catch
        {
            // Ignore errors during close
        }

        _dev.Dispose();
    }

    public void Dispose() => Close(hardwareMode: false);
}
