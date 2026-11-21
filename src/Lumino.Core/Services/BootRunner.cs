using System.Text.Json;
using System.Text.Json.Nodes;
using Lumino.Core.Hardware;

namespace Lumino.Core.Services;

/// <summary>
/// Re-applies saved lighting configuration on Windows startup (--boot [--delay 10] [--tries 6] [--check]).
/// Exact port of Lumino apply_boot.py.
/// </summary>
public static class BootRunner
{
    public static int Run(string[] args)
    {
        int delay = 10;
        int tries = 6;
        bool check = false;

        for (int i = 0; i < args.Length; i++)
        {
            if (args[i].Equals("--delay", StringComparison.OrdinalIgnoreCase) && i + 1 < args.Length && int.TryParse(args[i + 1], out int d))
            {
                delay = d;
                i++;
            }
            else if (args[i].Equals("--tries", StringComparison.OrdinalIgnoreCase) && i + 1 < args.Length && int.TryParse(args[i + 1], out int t))
            {
                tries = Math.Max(1, t);
                i++;
            }
            else if (args[i].Equals("--check", StringComparison.OrdinalIgnoreCase))
            {
                check = true;
            }
        }

        LuminoLogger.Log($"BOOT debut (delay={delay}s)");
        if (delay > 0 && !check)
            Thread.Sleep(delay * 1000);

        JsonObject cfg;
        try
        {
            if (!File.Exists(AppPaths.ConfigPath))
            {
                LuminoLogger.Log("BOOT config introuvable");
                return 0;
            }
            string raw = File.ReadAllText(AppPaths.ConfigPath);
            cfg = JsonNode.Parse(raw) as JsonObject ?? throw new InvalidDataException("JSON invalide");
        }
        catch (Exception ex)
        {
            LuminoLogger.Log($"BOOT config illisible: {ex.Message}");
            return 1;
        }

        if (check)
        {
            LuminoLogger.Log($"BOOT check cfg gpu={cfg.ContainsKey("r")} cm={cfg.ContainsKey("cm_last")} corsair={cfg.ContainsKey("corsair_last")}");
            var summary = new JsonObject();
            foreach (string k in new[] { "r", "g", "b", "brightness", "cm_last", "corsair_last" })
                summary[k] = cfg[k]?.DeepClone();
            try { Console.WriteLine(summary.ToJsonString(new JsonSerializerOptions { WriteIndented = true })); } catch { }
            return 0;
        }

        if (cfg.ContainsKey("r"))
            BootGpu(cfg, tries);

        var cmLast = LuminoConfig.GetObject(cfg, "cm_last");
        if (cmLast is not null && cmLast.ContainsKey("r"))
            BootAura("cm_last", cmLast, tries);

        var glow = LuminoConfig.GetObject(cfg, "ambiglow_last");
        if (glow is not null && glow.ContainsKey("r"))
        {
            try
            {
                using var _ = HardwareHold.Acquire();
                byte r = (byte)Math.Clamp(LuminoConfig.GetInt(glow, "r"), 0, 255);
                byte g = (byte)Math.Clamp(LuminoConfig.GetInt(glow, "g"), 0, 255);
                byte b = (byte)Math.Clamp(LuminoConfig.GetInt(glow, "b"), 0, 255);
                int bri = Math.Clamp(LuminoConfig.GetInt(glow, "brightness", 100), 0, 100);
                AmbiglowController.ApplyUsbColor(r, g, b, bri);
                LuminoLogger.Log($"BOOT ambiglow r={r} g={g} b={b} bri={bri}");
            }
            catch (Exception ex)
            {
                LuminoLogger.Log($"BOOT ambiglow erreur: {ex.Message}");
            }
        }

        var coLast = LuminoConfig.GetObject(cfg, "corsair_last");
        bool hasLcd = LuminoConfig.GetBool(cfg, "lcd_image_on") || LuminoConfig.GetBool(cfg, "lcd_temp");
        if ((coLast is not null && coLast.ContainsKey("r")) || hasLcd)
        {
            try
            {
                bool ok = KeepaliveWorker.EnsureRunning();
                LuminoLogger.Log($"BOOT corsair keepalive={(ok ? "ON" : "KO")}");
            }
            catch (Exception ex)
            {
                LuminoLogger.Log($"BOOT corsair erreur: {ex.Message}");
            }
        }

        LuminoLogger.Log("BOOT fin");
        return 0;
    }

    private static void BootGpu(JsonObject cfg, int tries)
    {
        byte r = (byte)Math.Clamp(LuminoConfig.GetInt(cfg, "r"), 0, 255);
        byte g = (byte)Math.Clamp(LuminoConfig.GetInt(cfg, "g"), 0, 255);
        byte b = (byte)Math.Clamp(LuminoConfig.GetInt(cfg, "b"), 0, 255);
        int level = BlackwellGpu.LevelFromPercent(LuminoConfig.GetInt(cfg, "brightness", 100));
        byte sr = level <= 0 ? (byte)0 : r;
        byte sg = level <= 0 ? (byte)0 : g;
        byte sb = level <= 0 ? (byte)0 : b;
        int hw = level <= 0 ? 1 : level;

        Exception? lastErr = null;
        for (int attempt = 1; attempt <= tries; attempt++)
        {
            try
            {
                using var _ = HardwareHold.Acquire();
                var nv = new NvApiClient();
                nv.Initialize();
                var gpus = nv.EnumGpus();
                if (gpus.Length == 0)
                    throw new InvalidOperationException("Aucun GPU NVIDIA (driver pas prêt ?)");

                var (ok, detail) = BlackwellGpu.Probe(nv, gpus[0], port: 1);
                if (!ok)
                    throw new InvalidOperationException($"Sonde 0x75 KO ({detail})");

                BlackwellGpu.ApplyStatic(nv, gpus[0], sr, sg, sb, hw, doSave: false);
                LuminoLogger.Log($"BOOT gpu r={sr} g={sg} b={sb} niveau={hw} (essai {attempt})");
                return;
            }
            catch (Exception ex)
            {
                lastErr = ex;
                LuminoLogger.Log($"BOOT gpu essai {attempt}/{tries} erreur: {ex.Message}");
                Thread.Sleep(5000);
            }
        }

        LuminoLogger.Log($"BOOT gpu erreur definitive: {lastErr?.Message}");
    }

    private static void BootAura(string key, JsonObject last, int tries)
    {
        byte r = (byte)Math.Clamp(LuminoConfig.GetInt(last, "r"), 0, 255);
        byte g = (byte)Math.Clamp(LuminoConfig.GetInt(last, "g"), 0, 255);
        byte b = (byte)Math.Clamp(LuminoConfig.GetInt(last, "b"), 0, 255);

        RgbColor send;
        if (LuminoConfig.GetBool(last, "unscaled", false))
        {
            int bri = Math.Clamp(LuminoConfig.GetInt(last, "brightness", 100), 0, 100);
            send = new RgbColor(r, g, b).ScaledFloor(bri);
        }
        else
        {
            send = new RgbColor(r, g, b);
        }

        Exception? lastErr = null;
        for (int attempt = 1; attempt <= tries; attempt++)
        {
            try
            {
                using var _ = HardwareHold.Acquire();
                using var aura = new AsusAuraUsb();
                aura.SetCm(send.R, send.G, send.B);
                LuminoLogger.Log($"BOOT {key} r={send.R} g={send.G} b={send.B} (essai {attempt})");
                return;
            }
            catch (Exception ex)
            {
                lastErr = ex;
                LuminoLogger.Log($"BOOT {key} essai {attempt}/{tries} erreur: {ex.Message}");
                Thread.Sleep(5000);
            }
        }

        LuminoLogger.Log($"BOOT {key} erreur definitive: {lastErr?.Message}");
    }
}
