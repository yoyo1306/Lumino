using System.Text.Json.Nodes;
using Lumino.Core.Hardware;

namespace Lumino.Core.Services;

public sealed record OperationResult(bool Ok, string Message);

/// <summary>
/// High-level lighting controller used by the WinUI 3 UI.
/// Mirrors every single apply / off / sync / LCD / Ambiglow flow from Lumino v1.2.2.
/// </summary>
public static class LuminoController
{
    private static bool HardwareTakenBySync(string prevMode, string newMode)
    {
        if (!KeepaliveWorker.IsRunning())
            return false;
        if (newMode != EffectsEngine.ModeStatic)
            return true;
        if (prevMode != EffectsEngine.ModeStatic)
            return KeepaliveWorker.WaitStatic(5.0);
        return false;
    }

    public static void ApplyGpuDirect(RgbColor rgb, int brightness100, bool doSave = true)
    {
        int level = BlackwellGpu.LevelFromPercent(brightness100);
        byte sr = level <= 0 ? (byte)0 : rgb.R;
        byte sg = level <= 0 ? (byte)0 : rgb.G;
        byte sb = level <= 0 ? (byte)0 : rgb.B;
        int hw = level <= 0 ? 1 : level;

        using var _ = HardwareHold.Acquire();
        var nv = new NvApiClient();
        nv.Initialize();
        var gpus = nv.EnumGpus();
        if (gpus.Length == 0)
            throw new InvalidOperationException("Aucun GPU NVIDIA détecté");

        var (ok, detail) = BlackwellGpu.Probe(nv, gpus[0], port: 1);
        if (!ok)
            throw new InvalidOperationException($"Contrôleur LED 0x75 non détecté ({detail})");

        var (bval, zoneStatuses, saveStatus) = BlackwellGpu.ApplyStatic(nv, gpus[0], sr, sg, sb, hw, doSave);

        var zonesArr = new JsonArray();
        for (int i = 0; i < zoneStatuses.Length; i++)
            zonesArr.Add(new JsonObject { ["zone"] = i, ["status"] = zoneStatuses[i] });

        LuminoConfig.Patch(cfg =>
        {
            cfg["r"] = rgb.R;
            cfg["g"] = rgb.G;
            cfg["b"] = rgb.B;
            cfg["brightness"] = Math.Clamp(brightness100, 0, 100);
            cfg["save"] = doSave;
            cfg["detail"] = new JsonObject
            {
                ["brightness_raw"] = bval,
                ["zones"] = zonesArr,
                ["save_status"] = saveStatus ?? 0
            };
        });
    }

    public static void ApplyCmDirect(RgbColor rgb, int brightness100)
    {
        var send = rgb.ScaledFloor(brightness100);
        using var _ = HardwareHold.Acquire();
        using var aura = new AsusAuraUsb();
        aura.SetCm(send.R, send.G, send.B);

        LuminoConfig.Patch(cfg =>
        {
            cfg["cm_last"] = LuminoConfig.BuildSavedColorBlock(rgb, brightness100);
        });
    }

    public static (bool KeepaliveOk, string? LiveError) ApplyCorsairConfig(
        string mode,
        int speed,
        (RgbColor Color, int Brightness)? block,
        (RgbColor Color, int Brightness)? fans)
    {
        mode = EffectsEngine.NormalizeMode(mode);
        speed = Math.Clamp(speed, 1, 10);

        LuminoConfig.Patch(cfg =>
        {
            cfg["effect"] = LuminoConfig.BuildEffectBlock(mode, speed);
            if (block.HasValue)
                cfg["corsair_last"] = LuminoConfig.BuildSavedColorBlock(block.Value.Color, block.Value.Brightness, mode, speed);
            if (fans.HasValue)
                cfg["corsair_fans_last"] = LuminoConfig.BuildSavedColorBlock(fans.Value.Color, fans.Value.Brightness, mode, speed);
        });

        bool keepOk = KeepaliveWorker.EnsureRunning();
        string? liveErr = null;

        if (!keepOk)
        {
            try
            {
                var setup = KeepaliveWorker.ReadSetup();
                using var hub = new CorsairLinkHub();
                var spans = hub.GetLedSpans();
                var cols = KeepaliveWorker.RenderLink(spans, EffectsEngine.ModeStatic, 0, setup?.Corsair, setup?.Fans, speed);
                if (cols.Length == hub.TotalLeds && cols.Length > 0)
                    hub.SetSegments(cols, settle: true);
            }
            catch (Exception ex)
            {
                liveErr = ex.Message;
            }
        }

        return (keepOk, liveErr);
    }

    public static OperationResult ApplyAll(RgbColor rgb, int brightness100, string mode, int speed)
    {
        mode = EffectsEngine.NormalizeMode(mode);
        speed = Math.Clamp(speed, 1, 10);
        brightness100 = Math.Clamp(brightness100, 0, 100);
        string hx = rgb.ToHex();
        int? screenIdx = AmbiglowController.IndexForHex(hx);
        string prevMode = LuminoConfig.GetEffectMode();
        var errors = new List<string>();

        LuminoConfig.Patch(cfg =>
        {
            cfg["r"] = rgb.R;
            cfg["g"] = rgb.G;
            cfg["b"] = rgb.B;
            cfg["brightness"] = brightness100;
            cfg["cm_last"] = LuminoConfig.BuildSavedColorBlock(rgb, brightness100);
            cfg["ambiglow_last"] = LuminoConfig.BuildSavedColorBlock(rgb, brightness100);
            cfg["effect"] = LuminoConfig.BuildEffectBlock(mode, speed);
            cfg["corsair_last"] = LuminoConfig.BuildSavedColorBlock(rgb, brightness100, mode, speed);
            cfg["corsair_fans_last"] = LuminoConfig.BuildSavedColorBlock(rgb, brightness100, mode, speed);
        });

        LuminoLogger.Log($"SYNC {mode} r={rgb.R} g={rgb.G} b={rgb.B} bri={brightness100} v={speed}");

        bool taken = HardwareTakenBySync(prevMode, mode);

        if (!taken)
        {
            try
            {
                ApplyGpuDirect(rgb, brightness100, doSave: true);
                LuminoLogger.Log($"GPU r={rgb.R} g={rgb.G} b={rgb.B} bri={brightness100}");
            }
            catch (Exception ex)
            {
                LuminoLogger.Log($"GPU erreur: {ex.Message}");
                errors.Add($"GPU : {ex.Message}");
            }

            try
            {
                ApplyCmDirect(rgb, brightness100);
                LuminoLogger.Log($"AURA cm r={rgb.R} g={rgb.G} b={rgb.B} bri={brightness100}");
            }
            catch (Exception ex)
            {
                LuminoLogger.Log($"AURA erreur: {ex.Message}");
                errors.Add($"Carte mère : {ex.Message}");
            }

            try
            {
                if (brightness100 <= 0)
                {
                    AmbiglowController.SetLightMode(0);
                    LuminoLogger.Log("AMBIGLOW éteint");
                }
                else if (screenIdx.HasValue)
                {
                    AmbiglowController.SetStaticColor(screenIdx.Value);
                    LuminoLogger.Log($"AMBIGLOW couleur {screenIdx.Value} {hx}");
                }
                else
                {
                    using var _ = HardwareHold.Acquire();
                    AmbiglowController.ApplyUsbColor(rgb.R, rgb.G, rgb.B, brightness100);
                }
            }
            catch (Exception ex)
            {
                LuminoLogger.Log($"AMBIGLOW erreur: {ex.Message}");
                // Ambiglow USB absent is non-fatal when only DP is connected with a custom color
                if (screenIdx.HasValue || brightness100 <= 0)
                    errors.Add($"Ambiglow : {ex.Message}");
            }
        }

        try
        {
            var (keepOk, liveErr) = ApplyCorsairConfig(mode, speed, (rgb, brightness100), (rgb, brightness100));
            LuminoLogger.Log($"CORSAIR {mode} v={speed} keep={keepOk}");
            if (!keepOk)
            {
                if (liveErr is not null)
                    errors.Add($"Corsair enregistré, direct : {liveErr}");
                else
                    errors.Add("Corsair enregistré, fond non démarré");
            }
        }
        catch (Exception ex)
        {
            LuminoLogger.Log($"CORSAIR erreur: {ex.Message}");
            errors.Add($"Corsair : {ex.Message}");
        }

        string modeLabel = EffectsEngine.GetModeLabel(mode);
        if (errors.Count == 0)
            return new OperationResult(true, $"Tout appliqué ({hx} à {brightness100} %, {modeLabel})");

        return new OperationResult(false, $"Appliqué en partie — {string.Join(" · ", errors)}");
    }

    public static OperationResult ApplySingleDevice(string key, RgbColor rgb, int brightness100, string mode, int speed)
    {
        mode = EffectsEngine.NormalizeMode(mode);
        speed = Math.Clamp(speed, 1, 10);
        brightness100 = Math.Clamp(brightness100, 0, 100);
        string hx = rgb.ToHex();
        string prevMode = LuminoConfig.GetEffectMode();

        try
        {
            switch (key)
            {
                case "gpu":
                    LuminoConfig.Patch(cfg =>
                    {
                        cfg["r"] = rgb.R;
                        cfg["g"] = rgb.G;
                        cfg["b"] = rgb.B;
                        cfg["brightness"] = brightness100;
                        cfg["effect"] = LuminoConfig.BuildEffectBlock(mode, speed);
                    });
                    if (!HardwareTakenBySync(prevMode, mode))
                        ApplyGpuDirect(rgb, brightness100, doSave: true);
                    else
                        KeepaliveWorker.NotifyConfigChanged();
                    return new OperationResult(true, $"GPU appliqué ({hx} à {brightness100} %)");

                case "cm":
                    LuminoConfig.Patch(cfg =>
                    {
                        cfg["cm_last"] = LuminoConfig.BuildSavedColorBlock(rgb, brightness100);
                        cfg["effect"] = LuminoConfig.BuildEffectBlock(mode, speed);
                    });
                    if (!HardwareTakenBySync(prevMode, mode))
                        ApplyCmDirect(rgb, brightness100);
                    else
                        KeepaliveWorker.NotifyConfigChanged();
                    return new OperationResult(true, $"Carte mère appliquée ({hx} à {brightness100} %)");

                case "corsair":
                {
                    var (keepOk, liveErr) = ApplyCorsairConfig(mode, speed, block: (rgb, brightness100), fans: null);
                    if (!keepOk && liveErr is not null)
                        return new OperationResult(false, $"Waterblock : {liveErr}");
                    return new OperationResult(true, $"Waterblock appliqué ({hx} à {brightness100} %)");
                }

                case "fans":
                {
                    var (keepOk, liveErr) = ApplyCorsairConfig(mode, speed, block: null, fans: (rgb, brightness100));
                    if (!keepOk && liveErr is not null)
                        return new OperationResult(false, $"Ventilos : {liveErr}");
                    return new OperationResult(true, $"Ventilos appliqués ({hx} à {brightness100} %)");
                }

                default:
                    return ApplyAll(rgb, brightness100, mode, speed);
            }
        }
        catch (Exception ex)
        {
            LuminoLogger.Log($"APPLY {key} erreur: {ex.Message}");
            return new OperationResult(false, $"{key.ToUpperInvariant()} : {ex.Message}");
        }
    }

    public static OperationResult TurnOffSingleDevice(string key, RgbColor currentRgb, string mode, int speed)
    {
        return ApplySingleDevice(key, currentRgb, 0, mode, speed);
    }

    public static OperationResult TurnOffAll(
        RgbColor gpuRgb,
        RgbColor cmRgb,
        RgbColor corsairRgb,
        RgbColor fansRgb,
        RgbColor screenRgb,
        string mode,
        int speed)
    {
        mode = EffectsEngine.NormalizeMode(mode);
        speed = Math.Clamp(speed, 1, 10);
        string prevMode = LuminoConfig.GetEffectMode();
        var errors = new List<string>();

        LuminoConfig.Patch(cfg =>
        {
            cfg["r"] = gpuRgb.R;
            cfg["g"] = gpuRgb.G;
            cfg["b"] = gpuRgb.B;
            cfg["brightness"] = 0;
            cfg["cm_last"] = LuminoConfig.BuildSavedColorBlock(cmRgb, 0);
            cfg["ambiglow_last"] = LuminoConfig.BuildSavedColorBlock(screenRgb, 0);
            cfg["effect"] = LuminoConfig.BuildEffectBlock(mode, speed);
            cfg["corsair_last"] = LuminoConfig.BuildSavedColorBlock(corsairRgb, 0, mode, speed);
            cfg["corsair_fans_last"] = LuminoConfig.BuildSavedColorBlock(fansRgb, 0, mode, speed);
        });

        bool taken = HardwareTakenBySync(prevMode, mode);
        if (!taken)
        {
            try { ApplyGpuDirect(gpuRgb, 0, doSave: true); LuminoLogger.Log("OFF gpu"); }
            catch (Exception ex) { LuminoLogger.Log($"OFF gpu erreur: {ex.Message}"); errors.Add($"GPU : {ex.Message}"); }

            try { ApplyCmDirect(cmRgb, 0); LuminoLogger.Log("OFF cm"); }
            catch (Exception ex) { LuminoLogger.Log($"OFF cm erreur: {ex.Message}"); errors.Add($"Carte mère : {ex.Message}"); }

            try { AmbiglowController.SetLightMode(0); LuminoLogger.Log("OFF ambiglow"); }
            catch (Exception ex) { LuminoLogger.Log($"OFF ambiglow erreur: {ex.Message}"); errors.Add($"Ambiglow : {ex.Message}"); }
        }
        else
        {
            LuminoLogger.Log("OFF gpu+cm+ambiglow via fond");
        }

        try
        {
            var (keepOk, liveErr) = ApplyCorsairConfig(mode, speed, (corsairRgb, 0), (fansRgb, 0));
            LuminoLogger.Log("OFF corsair");
            if (!keepOk)
                errors.Add(liveErr is not null ? $"Corsair : {liveErr}" : "Corsair : fond non démarré");
        }
        catch (Exception ex)
        {
            LuminoLogger.Log($"OFF corsair erreur: {ex.Message}");
            errors.Add($"Corsair : {ex.Message}");
        }

        if (errors.Count == 0)
            return new OperationResult(true, "Tout éteint. Couleurs gardées, luminosité à 0.");

        return new OperationResult(false, $"Éteint en partie — {string.Join(" · ", errors)}");
    }

    public static OperationResult ApplyLcdImage(string path, int scale, int brightness)
    {
        try
        {
            if (!File.Exists(path))
                return new OperationResult(false, "Fichier image introuvable.");

            string stored = LuminoConfig.StoreLcdCopy(path);
            LuminoConfig.Patch(cfg =>
            {
                cfg["lcd_image_path"] = stored;
                cfg["lcd_image_scale"] = Math.Clamp(scale, 25, 200);
                cfg["lcd_brightness"] = Math.Clamp(brightness, 0, 100);
                cfg["lcd_image_on"] = true;
                cfg["lcd_temp"] = false;
            });

            bool ok = KeepaliveWorker.EnsureRunning();
            return ok
                ? new OperationResult(true, "Image envoyée sur l’écran LCD.")
                : new OperationResult(false, "Image enregistrée, mais le fond Corsair n’a pas démarré.");
        }
        catch (Exception ex)
        {
            LuminoLogger.Log($"LCD image erreur: {ex.Message}");
            return new OperationResult(false, $"Écran LCD : {ex.Message}");
        }
    }

    public static OperationResult OffLcdImage()
    {
        try
        {
            LuminoConfig.Patch(cfg =>
            {
                cfg["lcd_image_on"] = false;
                cfg["lcd_temp"] = false;
            });

            if (KeepaliveWorker.IsRunning())
            {
                KeepaliveWorker.NotifyConfigChanged();
            }
            else
            {
                try { CorsairLcdScreen.ReleaseToHardware(); } catch { }
            }

            return new OperationResult(true, "Écran LCD rendu au fond matériel.");
        }
        catch (Exception ex)
        {
            return new OperationResult(false, $"Écran LCD : {ex.Message}");
        }
    }

    public static OperationResult SetLcdTempEnabled(bool enabled)
    {
        try
        {
            LuminoConfig.Patch(cfg =>
            {
                cfg["lcd_temp"] = enabled;
                if (enabled)
                    cfg["lcd_image_on"] = false;
            });

            if (enabled)
            {
                bool ok = KeepaliveWorker.EnsureRunning();
                return ok
                    ? new OperationResult(true, "Température GPU affichée sur l’écran LCD.")
                    : new OperationResult(false, "Mode température activé, mais le fond n’a pas démarré.");
            }
            else
            {
                if (KeepaliveWorker.IsRunning())
                    KeepaliveWorker.NotifyConfigChanged();
                else
                    try { CorsairLcdScreen.ReleaseToHardware(); } catch { }
                return new OperationResult(true, "Température GPU désactivée sur le LCD.");
            }
        }
        catch (Exception ex)
        {
            return new OperationResult(false, $"Écran LCD : {ex.Message}");
        }
    }
}
