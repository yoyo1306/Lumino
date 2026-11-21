using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace Lumino.Core;

public readonly record struct RgbColor(byte R, byte G, byte B)
{
    public static readonly RgbColor Black = new(0, 0, 0);
    public static readonly RgbColor White = new(255, 255, 255);

    public string ToHex() => $"#{R:x2}{G:x2}{B:x2}";

    public RgbColor Scaled(int brightness100)
    {
        int b = Math.Clamp(brightness100, 0, 100);
        double f = b / 100.0;
        return new RgbColor(
            (byte)Math.Clamp((int)Math.Round(R * f), 0, 255),
            (byte)Math.Clamp((int)Math.Round(G * f), 0, 255),
            (byte)Math.Clamp((int)Math.Round(B * f), 0, 255));
    }

    public RgbColor ScaledFloor(int brightness100)
    {
        int b = Math.Clamp(brightness100, 0, 100);
        return new RgbColor(
            (byte)(R * b / 100),
            (byte)(G * b / 100),
            (byte)(B * b / 100));
    }
}

public sealed record ColorBlock(RgbColor Color, int Brightness);

public sealed record DeviceLightingState(string Hex, int Brightness, string Mode = "static", int Speed = 4);

/// <summary>
/// Reads and atomically patches config.json while preserving full compatibility with Lumino v1.2.2.
/// </summary>
public static class LuminoConfig
{
    private static readonly object FileSync = new();
    private static readonly JsonSerializerOptions WriteOptions = new()
    {
        WriteIndented = true
    };

    public static JsonObject LoadRaw()
    {
        lock (FileSync)
        {
            try
            {
                if (!File.Exists(AppPaths.ConfigPath))
                    return new JsonObject();

                string text = File.ReadAllText(AppPaths.ConfigPath);
                return JsonNode.Parse(text) as JsonObject ?? new JsonObject();
            }
            catch
            {
                return new JsonObject();
            }
        }
    }

    public static DateTime GetConfigLastWriteUtc()
    {
        try
        {
            return File.Exists(AppPaths.ConfigPath)
                ? File.GetLastWriteTimeUtc(AppPaths.ConfigPath)
                : DateTime.MinValue;
        }
        catch
        {
            return DateTime.MinValue;
        }
    }

    public static double GetConfigMtimeUnix()
    {
        try
        {
            if (!File.Exists(AppPaths.ConfigPath))
                return 0;
            var utc = File.GetLastWriteTimeUtc(AppPaths.ConfigPath);
            return new DateTimeOffset(utc).ToUnixTimeMilliseconds() / 1000.0;
        }
        catch
        {
            return 0;
        }
    }

    public static void Patch(Action<JsonObject> mutator)
    {
        lock (FileSync)
        {
            Directory.CreateDirectory(AppPaths.DataDir);
            JsonObject data = LoadRaw();
            mutator(data);

            string json = data.ToJsonString(WriteOptions);
            string tmp = $"{AppPaths.ConfigPath}.{Environment.ProcessId}.{Guid.NewGuid():N}.tmp";
            File.WriteAllText(tmp, json);
            File.Move(tmp, AppPaths.ConfigPath, overwrite: true);
        }
    }

    public static string GetString(JsonObject obj, string key, string fallback = "")
    {
        if (obj.TryGetPropertyValue(key, out var node) && node is JsonValue val && val.TryGetValue<string>(out var s))
            return s;
        return fallback;
    }

    public static int GetInt(JsonObject obj, string key, int fallback = 0)
    {
        if (obj.TryGetPropertyValue(key, out var node) && node is JsonValue val)
        {
            if (val.TryGetValue<int>(out int i))
                return i;
            if (val.TryGetValue<double>(out double d))
                return (int)Math.Round(d);
            if (val.TryGetValue<string>(out var s) && int.TryParse(s, out int parsed))
                return parsed;
        }
        return fallback;
    }

    public static bool GetBool(JsonObject obj, string key, bool fallback = false)
    {
        if (obj.TryGetPropertyValue(key, out var node) && node is JsonValue val)
        {
            if (val.TryGetValue<bool>(out bool b))
                return b;
            if (val.TryGetValue<int>(out int i))
                return i != 0;
        }
        return fallback;
    }

    public static JsonObject? GetObject(JsonObject obj, string key)
    {
        if (obj.TryGetPropertyValue(key, out var node) && node is JsonObject child)
            return child;
        return null;
    }

    public static JsonObject BuildSavedColorBlock(RgbColor rgb, int brightness, string? mode = null, int? speed = null)
    {
        var block = new JsonObject
        {
            ["r"] = rgb.R,
            ["g"] = rgb.G,
            ["b"] = rgb.B,
            ["brightness"] = Math.Clamp(brightness, 0, 100),
            ["unscaled"] = true
        };
        if (mode is not null)
            block["mode"] = mode;
        if (speed.HasValue)
            block["speed"] = Math.Clamp(speed.Value, 1, 10);
        return block;
    }

    public static JsonObject BuildEffectBlock(string mode, int speed)
    {
        return new JsonObject
        {
            ["mode"] = EffectsEngine.NormalizeMode(mode),
            ["speed"] = Math.Clamp(speed, 1, 10)
        };
    }

    public static (string Hex, int Brightness) StoredColor(JsonObject? block, string fallbackHex, int fallbackBri)
    {
        if (block is null || !block.ContainsKey("r") || !block.ContainsKey("g") || !block.ContainsKey("b"))
            return (fallbackHex, fallbackBri);

        byte r = (byte)Math.Clamp(GetInt(block, "r", 255), 0, 255);
        byte g = (byte)Math.Clamp(GetInt(block, "g", 255), 0, 255);
        byte b = (byte)Math.Clamp(GetInt(block, "b", 255), 0, 255);

        if (GetBool(block, "unscaled", false))
        {
            int bri = Math.Clamp(GetInt(block, "brightness", fallbackBri), 0, 100);
            return (new RgbColor(r, g, b).ToHex(), bri);
        }

        // Legacy scaled format: recover hue and brightness
        int peak = Math.Max(r, Math.Max(g, b));
        if (peak <= 0)
            return (fallbackHex, 0);

        int legacyBri = Math.Clamp((int)Math.Round(peak * 100.0 / 255.0), 0, 100);
        byte ur = (byte)Math.Clamp((int)Math.Round(r * 255.0 / peak), 0, 255);
        byte ug = (byte)Math.Clamp((int)Math.Round(g * 255.0 / peak), 0, 255);
        byte ub = (byte)Math.Clamp((int)Math.Round(b * 255.0 / peak), 0, 255);
        return (new RgbColor(ur, ug, ub).ToHex(), legacyBri);
    }

    public static ColorBlock? ReadBlockColor(JsonObject? block, bool legacyScaled = false)
    {
        if (block is null || !block.ContainsKey("r") || !block.ContainsKey("g") || !block.ContainsKey("b"))
            return null;

        byte r = (byte)Math.Clamp(GetInt(block, "r", 0), 0, 255);
        byte g = (byte)Math.Clamp(GetInt(block, "g", 0), 0, 255);
        byte b = (byte)Math.Clamp(GetInt(block, "b", 0), 0, 255);

        if (GetBool(block, "unscaled", false) || !legacyScaled)
        {
            int bri = Math.Clamp(GetInt(block, "brightness", 100), 0, 100);
            return new ColorBlock(new RgbColor(r, g, b), bri);
        }

        int peak = Math.Max(r, Math.Max(g, b));
        if (peak <= 0)
            return new ColorBlock(RgbColor.Black, 0);

        int legacyBri = Math.Clamp((int)Math.Round(peak * 100.0 / 255.0), 0, 100);
        byte ur = (byte)Math.Clamp((int)Math.Round(r * 255.0 / peak), 0, 255);
        byte ug = (byte)Math.Clamp((int)Math.Round(g * 255.0 / peak), 0, 255);
        byte ub = (byte)Math.Clamp((int)Math.Round(b * 255.0 / peak), 0, 255);
        return new ColorBlock(new RgbColor(ur, ug, ub), legacyBri);
    }

    public static string GetEffectMode(JsonObject? cfg = null)
    {
        cfg ??= LoadRaw();
        var eff = GetObject(cfg, "effect");
        if (eff is not null)
        {
            var m = GetString(eff, "mode", "");
            if (EffectsEngine.IsValidMode(m))
                return m;
        }
        var co = GetObject(cfg, "corsair_last");
        if (co is not null)
        {
            var m = GetString(co, "mode", "");
            if (EffectsEngine.IsValidMode(m))
                return m;
        }
        return EffectsEngine.ModeStatic;
    }

    public static int GetEffectSpeed(JsonObject? cfg = null)
    {
        cfg ??= LoadRaw();
        var eff = GetObject(cfg, "effect");
        if (eff is not null && eff.ContainsKey("speed"))
            return Math.Clamp(GetInt(eff, "speed", 4), 1, 10);
        var co = GetObject(cfg, "corsair_last");
        if (co is not null && co.ContainsKey("speed"))
            return Math.Clamp(GetInt(co, "speed", 4), 1, 10);
        return 4;
    }

    public static bool TryParseHex(string? text, out RgbColor rgb)
    {
        rgb = RgbColor.White;
        if (string.IsNullOrWhiteSpace(text))
            return false;

        string s = text.Trim();
        if (s.StartsWith('#'))
            s = s[1..];
        if (s.Length != 6)
            return false;

        if (byte.TryParse(s[0..2], NumberStyles.HexNumber, CultureInfo.InvariantCulture, out byte r) &&
            byte.TryParse(s[2..4], NumberStyles.HexNumber, CultureInfo.InvariantCulture, out byte g) &&
            byte.TryParse(s[4..6], NumberStyles.HexNumber, CultureInfo.InvariantCulture, out byte b))
        {
            rgb = new RgbColor(r, g, b);
            return true;
        }
        return false;
    }

    public static RgbColor HexToRgb(string? hex, RgbColor? fallback = null)
    {
        return TryParseHex(hex, out var rgb) ? rgb : (fallback ?? new RgbColor(230, 105, 34));
    }

    public static string StoreLcdCopy(string sourcePath)
    {
        Directory.CreateDirectory(AppPaths.LcdMediaDir);
        string srcFull = Path.GetFullPath(sourcePath);
        string fileName = Path.GetFileName(srcFull);
        string destFull = Path.GetFullPath(Path.Combine(AppPaths.LcdMediaDir, fileName));

        if (!string.Equals(srcFull, destFull, StringComparison.OrdinalIgnoreCase))
        {
            File.Copy(srcFull, destFull, overwrite: true);
        }

        // Keep only the active media file in lcd_media (exact v1.2.2 behavior)
        foreach (var existing in Directory.GetFiles(AppPaths.LcdMediaDir))
        {
            if (!string.Equals(Path.GetFullPath(existing), destFull, StringComparison.OrdinalIgnoreCase))
            {
                try { File.Delete(existing); } catch { }
            }
        }

        return destFull;
    }
}
