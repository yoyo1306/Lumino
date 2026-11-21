namespace Lumino.Core;

public sealed class EffectState
{
    public double? LastT { get; set; }
}

/// <summary>
/// iCUE-style lighting effects engine (exact port of Lumino effects.py).
/// Supports static, rainbow, rainbow_wave, color_wave, color_pulse, ripple, temperature.
/// </summary>
public static class EffectsEngine
{
    public const string ModeStatic = "static";
    public const string ModeRainbow = "rainbow";
    public const string ModeRainbowWave = "rainbow_wave";
    public const string ModeColorWave = "color_wave";
    public const string ModeColorPulse = "color_pulse";
    public const string ModeRipple = "ripple";
    public const string ModeTemperature = "temperature";

    public static readonly (string Code, string Label)[] Modes =
    [
        (ModeStatic, "Statique"),
        (ModeRainbow, "Arc-en-ciel"),
        (ModeRainbowWave, "Vague arc-en-ciel"),
        (ModeColorWave, "Vague couleur"),
        (ModeColorPulse, "Pulsation"),
        (ModeRipple, "Ondulation"),
        (ModeTemperature, "Température"),
    ];

    private static readonly (double TempC, RgbColor Color)[] TempStops =
    [
        (30, new RgbColor(0, 150, 255)),
        (45, new RgbColor(0, 255, 150)),
        (60, new RgbColor(255, 220, 0)),
        (72, new RgbColor(255, 120, 0)),
        (82, new RgbColor(255, 0, 0)),
    ];

    public static bool IsValidMode(string? mode) =>
        mode is not null && Modes.Any(m => m.Code == mode);

    public static string NormalizeMode(string? mode) =>
        IsValidMode(mode) ? mode! : ModeStatic;

    public static string GetModeLabel(string? mode)
    {
        foreach (var (code, label) in Modes)
        {
            if (code == mode)
                return label;
        }
        return "Statique";
    }

    public static RgbColor HueToRgb(double h)
    {
        h = ((h % 1.0) + 1.0) % 1.0;
        double h6 = h * 6.0;
        int sector = (int)Math.Floor(h6);
        double f = h6 - sector;
        double q = 1.0 - f;

        (double r, double g, double b) = (sector % 6) switch
        {
            0 => (1.0, f, 0.0),
            1 => (q, 1.0, 0.0),
            2 => (0.0, 1.0, f),
            3 => (0.0, q, 1.0),
            4 => (f, 0.0, 1.0),
            _ => (1.0, 0.0, q),
        };

        return new RgbColor(
            (byte)Math.Clamp((int)Math.Round(r * 255.0), 0, 255),
            (byte)Math.Clamp((int)Math.Round(g * 255.0), 0, 255),
            (byte)Math.Clamp((int)Math.Round(b * 255.0), 0, 255));
    }

    public static RgbColor Lerp(RgbColor c1, RgbColor c2, double k)
    {
        return new RgbColor(
            (byte)Math.Clamp((int)Math.Round(c1.R + (c2.R - c1.R) * k), 0, 255),
            (byte)Math.Clamp((int)Math.Round(c1.G + (c2.G - c1.G) * k), 0, 255),
            (byte)Math.Clamp((int)Math.Round(c1.B + (c2.B - c1.B) * k), 0, 255));
    }

    public static RgbColor TempColor(double? tempC)
    {
        double t = tempC ?? 40.0;
        if (t <= TempStops[0].TempC)
            return TempStops[0].Color;

        for (int i = 0; i < TempStops.Length - 1; i++)
        {
            var (t0, c0) = TempStops[i];
            var (t1, c1) = TempStops[i + 1];
            if (t <= t1)
            {
                double k = (t - t0) / (t1 - t0);
                return Lerp(c0, c1, k);
            }
        }
        return TempStops[^1].Color;
    }

    /// <summary>
    /// Aura Sync sawtooth rainbow wave (used for ASUS motherboard LEDs and ARGB headers).
    /// </summary>
    public static RgbColor[] RenderAuraHueWave(int n, double period, double phase, int brightness100, double shift = 0.0)
    {
        period = Math.Max(1.0, period);
        var result = new RgbColor[Math.Max(0, n)];
        for (int i = 0; i < result.Length; i++)
        {
            double h = (((i + phase) / period) + shift) % 1.0;
            result[i] = HueToRgb(h).Scaled(brightness100);
        }
        return result;
    }

    public static RgbColor[] Render(
        int n,
        string mode,
        double t,
        RgbColor color,
        int brightness100 = 100,
        int speed = 4,
        EffectState? state = null,
        double? tempC = null,
        double? rate = null)
    {
        if (n <= 0)
            return [];

        mode = NormalizeMode(mode);
        double cps = rate.HasValue
            ? Math.Max(0.005, rate.Value)
            : Math.Clamp(speed, 1, 10) * 0.03;

        if (mode == ModeRainbow && !rate.HasValue)
            cps *= 0.5;

        RgbColor baseColor = color.Scaled(brightness100);
        var outColors = new RgbColor[n];

        switch (mode)
        {
            case ModeStatic:
                Array.Fill(outColors, baseColor);
                return outColors;

            case ModeRainbow:
            {
                RgbColor col = HueToRgb((t * cps) % 1.0).Scaled(brightness100);
                Array.Fill(outColors, col);
                return outColors;
            }

            case ModeRainbowWave:
            {
                double denom = Math.Max(1, n);
                for (int i = 0; i < n; i++)
                {
                    outColors[i] = HueToRgb((i / denom) + t * cps).Scaled(brightness100);
                }
                return outColors;
            }

            case ModeColorWave:
            {
                double denom = Math.Max(1, n);
                for (int i = 0; i < n; i++)
                {
                    double w = 0.5 + 0.5 * Math.Sin(2.0 * Math.PI * ((i / denom) - t * cps));
                    double k = 0.08 + 0.92 * Math.Pow(w, 1.5);
                    outColors[i] = new RgbColor(
                        (byte)Math.Clamp((int)Math.Round(baseColor.R * k), 0, 255),
                        (byte)Math.Clamp((int)Math.Round(baseColor.G * k), 0, 255),
                        (byte)Math.Clamp((int)Math.Round(baseColor.B * k), 0, 255));
                }
                return outColors;
            }

            case ModeColorPulse:
            {
                double w = 0.5 + 0.5 * Math.Sin(2.0 * Math.PI * t * cps * 0.5);
                double k = 0.1 + 0.9 * Math.Pow(w, 1.5);
                var col = new RgbColor(
                    (byte)Math.Clamp((int)Math.Round(baseColor.R * k), 0, 255),
                    (byte)Math.Clamp((int)Math.Round(baseColor.G * k), 0, 255),
                    (byte)Math.Clamp((int)Math.Round(baseColor.B * k), 0, 255));
                Array.Fill(outColors, col);
                return outColors;
            }

            case ModeRipple:
            {
                double p = ((t * cps) % 1.0 + 1.0) % 1.0;
                double denom = Math.Max(1, n - 1);
                for (int i = 0; i < n; i++)
                {
                    double d = Math.Abs((i / denom) - 0.5);
                    double g = Math.Exp(-Math.Pow((d - p * 0.5) / 0.045, 2.0));
                    double k = Math.Min(1.0, g);
                    outColors[i] = new RgbColor(
                        (byte)Math.Clamp((int)Math.Round(baseColor.R * k), 0, 255),
                        (byte)Math.Clamp((int)Math.Round(baseColor.G * k), 0, 255),
                        (byte)Math.Clamp((int)Math.Round(baseColor.B * k), 0, 255));
                }
                return outColors;
            }

            case ModeTemperature:
            {
                RgbColor col = TempColor(tempC).Scaled(brightness100);
                Array.Fill(outColors, col);
                return outColors;
            }

            default:
                Array.Fill(outColors, baseColor);
                return outColors;
        }
    }
}
