using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;

namespace Lumino.Core.Hardware;

public sealed record LcdFrame(byte[] JpegBytes, double? DelaySeconds);

/// <summary>
/// Corsair LCD Screen (Elite / LINK Titan, VID 0x1B1C, PIDs 0x0C4E / 0x0C39 / 0x0C33 / 0x0C42).
/// Exact port of Lumino corsair_lcd.py using GDI+ for image/GIF composition and GPU temperature JPEG rendering.
/// </summary>
public sealed class CorsairLcdScreen : IDisposable
{
    public const ushort Vid = 0x1B1C;
    public static readonly ushort[] Pids = [0x0C4E, 0x0C39, 0x0C33, 0x0C42];

    private const byte ImgTxReportId = 0x02;
    private const int PacketSize = 1024;
    private const int HeaderSize = 8;
    private const int PropertyTagFrameDelay = 0x5100;

    private readonly HidDevice _dev;
    public ushort ProductId { get; }

    public CorsairLcdScreen()
    {
        Exception? last = null;
        foreach (ushort pid in Pids)
        {
            try
            {
                _dev = HidDevice.OpenFirst(Vid, pid);
                ProductId = pid;
                return;
            }
            catch (Exception ex)
            {
                last = ex;
            }
        }
        throw new IOException($"Écran LCD Corsair introuvable ({last?.Message ?? "aucun périphérique"})");
    }

    public static byte BrightnessRaw(int percent)
    {
        int p = Math.Clamp(percent, 0, 100);
        return (byte)((p * 64 + 50) / 100);
    }

    public static List<byte[]> BuildPackets(byte[] jpeg)
    {
        if (jpeg.Length == 0)
            throw new ArgumentException("JPEG vide", nameof(jpeg));

        int chunkCap = PacketSize - HeaderSize;
        var packets = new List<byte[]>((jpeg.Length + chunkCap - 1) / chunkCap);
        ushort part = 0;

        for (int offset = 0; offset < jpeg.Length; offset += chunkCap)
        {
            int len = Math.Min(chunkCap, jpeg.Length - offset);
            byte isEnd = (offset + len >= jpeg.Length) ? (byte)1 : (byte)0;

            var pkt = new byte[PacketSize];
            pkt[0] = ImgTxReportId;
            pkt[1] = 0x05;
            pkt[2] = 0x40;
            pkt[3] = isEnd;
            pkt[4] = (byte)(part & 0xFF);
            pkt[5] = (byte)((part >> 8) & 0xFF);
            pkt[6] = (byte)(len & 0xFF);
            pkt[7] = (byte)((len >> 8) & 0xFF);
            Buffer.BlockCopy(jpeg, offset, pkt, HeaderSize, len);
            packets.Add(pkt);
            part++;
        }

        return packets;
    }

    private void SendFeature(byte[] payload)
    {
        var pkt = new byte[32];
        pkt[0] = 0x03;
        Buffer.BlockCopy(payload, 0, pkt, 1, Math.Min(payload.Length, 31));
        if (!_dev.SendFeatureReport(pkt))
            throw new IOException("Feature report LCD refusé");
    }

    public void SetBrightness(int percent, bool persist = false)
    {
        byte raw = BrightnessRaw(percent);
        SendFeature([0x0B, raw]);
        if (persist)
            SendFeature([0x19, raw]);
    }

    public void RestoreHardware()
    {
        Exception? last = null;
        byte[][] cmds = [[0x1E, 0x01, 0x01], [0x1D, 0x00, 0x01]];
        foreach (var cmd in cmds)
        {
            try
            {
                SendFeature(cmd);
            }
            catch (Exception ex)
            {
                last = ex;
            }
        }
        if (last is not null)
            throw last;
    }

    public void SendJpeg(byte[] jpeg)
    {
        foreach (var pkt in BuildPackets(jpeg))
        {
            int written = _dev.Write(pkt);
            if (written != pkt.Length)
                throw new IOException($"Écriture LCD incomplète ({written}/{pkt.Length})");
        }
    }

    public static void ReleaseToHardware()
    {
        using var lcd = new CorsairLcdScreen();
        lcd.RestoreHardware();
    }

    public void Dispose() => _dev.Dispose();

    // -------------------------------------------------------------------------
    // Image / Animated GIF composition & GPU temperature JPEG rendering (GDI+)
    // -------------------------------------------------------------------------

    private static readonly ImageCodecInfo JpegCodec =
        ImageCodecInfo.GetImageEncoders().First(c => c.FormatID == ImageFormat.Jpeg.Guid);

    private static byte[] EncodeJpeg(Bitmap bmp, long quality)
    {
        using var ms = new MemoryStream();
        using var ep = new EncoderParameters(1);
        ep.Param[0] = new EncoderParameter(Encoder.Quality, quality);
        bmp.Save(ms, JpegCodec, ep);
        return ms.ToArray();
    }

    public static Bitmap ComposeBitmap(Image src, int scale, int size = 480)
    {
        scale = Math.Clamp(scale, 25, 200);
        double cover = Math.Max((double)size / src.Width, (double)size / src.Height);
        double factor = cover * (scale / 100.0);
        int nw = Math.Max(1, (int)Math.Round(src.Width * factor));
        int nh = Math.Max(1, (int)Math.Round(src.Height * factor));

        var canvas = new Bitmap(size, size, PixelFormat.Format24bppRgb);
        using var g = Graphics.FromImage(canvas);
        g.Clear(Color.Black);
        g.InterpolationMode = InterpolationMode.HighQualityBicubic;
        g.PixelOffsetMode = PixelOffsetMode.HighQuality;
        g.SmoothingMode = SmoothingMode.HighQuality;
        g.CompositingQuality = CompositingQuality.HighQuality;

        int x = (size - nw) / 2;
        int y = (size - nh) / 2;
        g.DrawImage(src, x, y, nw, nh);
        return canvas;
    }

    public static List<LcdFrame> LoadLcdFrames(string path, int scale, int size = 480, Action? onTick = null)
    {
        using var img = Image.FromFile(path);
        var dimension = new FrameDimension(img.FrameDimensionsList[0]);
        int frameCount = Math.Max(1, img.GetFrameCount(dimension));
        bool animated = frameCount > 1 && img.RawFormat.Equals(ImageFormat.Gif);

        var outFrames = new List<LcdFrame>(frameCount);
        if (!animated)
        {
            using var composed = ComposeBitmap(img, scale, size);
            outFrames.Add(new LcdFrame(EncodeJpeg(composed, 85), null));
            return outFrames;
        }

        byte[]? delayBytes = null;
        try
        {
            if (img.PropertyIdList.Contains(PropertyTagFrameDelay))
                delayBytes = img.GetPropertyItem(PropertyTagFrameDelay)?.Value;
        }
        catch
        {
            delayBytes = null;
        }

        for (int i = 0; i < frameCount; i++)
        {
            if (onTick is not null && i % 4 == 0)
                onTick();

            img.SelectActiveFrame(dimension, i);
            int durMs = 100;
            if (delayBytes is not null && (i * 4 + 3) < delayBytes.Length)
            {
                int cs = BitConverter.ToInt32(delayBytes, i * 4);
                durMs = cs > 0 ? cs * 10 : 100;
            }
            durMs = Math.Max(20, durMs);

            using var composed = ComposeBitmap(img, scale, size);
            outFrames.Add(new LcdFrame(EncodeJpeg(composed, 85), durMs / 1000.0));
        }

        if (outFrames.Count == 0)
            throw new InvalidOperationException("Image vide");

        return outFrames;
    }

    public static byte[] RenderImageJpeg(string path, int scale, int size = 480)
    {
        using var img = Image.FromFile(path);
        using var composed = ComposeBitmap(img, scale, size);
        return EncodeJpeg(composed, 85);
    }

    public static byte[] RenderTempJpeg(double? tempC, int size = 480)
    {
        var rgb = EffectsEngine.TempColor(tempC);
        var accent = Color.FromArgb(rgb.R, rgb.G, rgb.B);

        using var bmp = new Bitmap(size, size, PixelFormat.Format24bppRgb);
        using var g = Graphics.FromImage(bmp);
        g.Clear(Color.FromArgb(8, 10, 16));
        g.SmoothingMode = SmoothingMode.AntiAlias;
        g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.AntiAliasGridFit;

        const float margin = 36f;
        using (var pen = new Pen(accent, 18f))
        {
            g.DrawEllipse(pen, margin, margin, size - margin * 2f, size - margin * 2f);
        }

        string label = tempC.HasValue ? ((int)Math.Round(tempC.Value)).ToString() : "--";
        using var sf = new StringFormat
        {
            Alignment = StringAlignment.Center,
            LineAlignment = StringAlignment.Center
        };

        using var bigFont = new Font("Segoe UI", 126f, FontStyle.Bold, GraphicsUnit.Pixel);
        using var smallFont = new Font("Segoe UI", 34f, FontStyle.Regular, GraphicsUnit.Pixel);
        using var accentBrush = new SolidBrush(accent);
        using var mutedBrush = new SolidBrush(Color.FromArgb(210, 214, 220));

        g.DrawString(label, bigFont, accentBrush, new PointF(size / 2f, size / 2f - 18f), sf);
        g.DrawString("GPU  °C", smallFont, mutedBrush, new PointF(size / 2f, size / 2f + 114f), sf);

        return EncodeJpeg(bmp, 80);
    }
}
