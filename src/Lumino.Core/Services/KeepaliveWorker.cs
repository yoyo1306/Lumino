using System.Diagnostics;
using System.Globalization;
using System.IO.Pipes;
using System.Text;
using System.Text.Json.Nodes;
using Lumino.Core.Hardware;

namespace Lumino.Core.Services;

public sealed record LcdImageSpec(string Path, int Scale);

public sealed record KeepaliveSetup(
    string Mode,
    int Speed,
    ColorBlock? Corsair,
    ColorBlock? Fans,
    ColorBlock? Gpu,
    ColorBlock? Cm,
    ColorBlock? Screen,
    bool LcdTemp,
    LcdImageSpec? LcdImage,
    int LcdBrightness);

/// <summary>
/// Background worker (--corsair-keepalive):
/// - Keeps Corsair iCUE LINK Hub in software mode and pushes per-role (waterblock / fans) effects
/// - Synchronizes dynamic effects on GPU (Blackwell RTX 5080), Motherboard (ASUS Aura USB) and Ambiglow USB (ENE)
/// - Feeds animated GIF / static image / GPU temperature to Corsair LCD (480x480)
/// - Restores static lighting cleanly on return to static or worker stop
/// Exact port of Lumino corsair_keep.py with Named Pipe wakeup support.
/// </summary>
public static class KeepaliveWorker
{
    public static (int? Pid, double? Timestamp) ReadLock()
    {
        try
        {
            if (!File.Exists(AppPaths.KeepaliveLockPath))
                return (null, null);

            string raw = File.ReadAllText(AppPaths.KeepaliveLockPath, Encoding.UTF8).Trim();
            var parts = raw.Split(':');
            if (parts.Length == 2 &&
                int.TryParse(parts[0], out int pid) &&
                double.TryParse(parts[1], NumberStyles.Float, CultureInfo.InvariantCulture, out double ts))
            {
                return (pid, ts);
            }
        }
        catch
        {
            // Ignore
        }
        return (null, null);
    }

    private static bool IsPidAlive(int pid)
    {
        if (pid <= 0)
            return false;
        try
        {
            using var proc = Process.GetProcessById(pid);
            return !proc.HasExited;
        }
        catch
        {
            return false;
        }
    }

    public static bool IsRunning()
    {
        var (pid, _) = ReadLock();
        if (pid.HasValue && IsPidAlive(pid.Value))
            return true;

        try
        {
            if (Mutex.TryOpenExisting(BuildInfo.KeepaliveMutexName, out var existing))
            {
                existing.Dispose();
                return true;
            }
        }
        catch
        {
            // Ignore
        }
        return false;
    }

    public static void NotifyConfigChanged()
    {
        HardwareHold.TrySendPipeCommand(BuildInfo.KeepalivePipeName, "RELOAD", 150);
    }

    public static bool EnsureRunning()
    {
        if (IsRunning())
        {
            NotifyConfigChanged();
            return true;
        }

        try
        {
            try
            {
                if (File.Exists(AppPaths.StopFlagPath))
                    File.Delete(AppPaths.StopFlagPath);
            }
            catch { }

            var psi = new ProcessStartInfo
            {
                FileName = AppPaths.ExePath,
                Arguments = "--corsair-keepalive",
                WorkingDirectory = AppPaths.ExeDir,
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden
            };
            Process.Start(psi);

            for (int i = 0; i < 16; i++)
            {
                Thread.Sleep(200);
                if (IsRunning())
                    return true;
            }
        }
        catch (Exception ex)
        {
            LuminoLogger.Log($"KEEP corsair lancement impossible: {ex.Message}");
        }

        return IsRunning();
    }

    public static bool StopRunning()
    {
        var (pid, _) = ReadLock();
        bool alive = pid.HasValue && IsPidAlive(pid.Value);
        if (!alive && !IsRunning())
        {
            TryDeleteFile(AppPaths.KeepaliveLockPath);
            TryDeleteFile(AppPaths.StopFlagPath);
            return false;
        }

        try
        {
            Directory.CreateDirectory(AppPaths.DataDir);
            File.WriteAllText(AppPaths.StopFlagPath, "1", Encoding.UTF8);
        }
        catch { }

        HardwareHold.TrySendPipeCommand(BuildInfo.KeepalivePipeName, "STOP", 250);

        for (int i = 0; i < 40; i++)
        {
            Thread.Sleep(200);
            if ((pid.HasValue && !IsPidAlive(pid.Value)) || (!pid.HasValue && !IsRunning()))
            {
                LuminoLogger.Log($"KEEP corsair stop pid={pid}");
                TryDeleteFile(AppPaths.KeepaliveLockPath);
                TryDeleteFile(AppPaths.StopFlagPath);
                return true;
            }
        }

        if (pid.HasValue)
        {
            try
            {
                using var proc = Process.GetProcessById(pid.Value);
                proc.Kill(entireProcessTree: true);
                proc.WaitForExit(1500);
                LuminoLogger.Log($"KEEP corsair stop force pid={pid}");
            }
            catch { }
        }

        TryDeleteFile(AppPaths.KeepaliveLockPath);
        TryDeleteFile(AppPaths.StopFlagPath);
        return true;
    }

    public static bool WaitStatic(double timeoutSeconds = 5.0)
    {
        if (!IsRunning())
            return true;

        double wantMtime = LuminoConfig.GetConfigMtimeUnix();
        var sw = Stopwatch.StartNew();
        while (sw.Elapsed.TotalSeconds < timeoutSeconds)
        {
            double? idle = HardwareHold.ReadIdleMtime();
            if (idle.HasValue && idle.Value + 0.05 >= wantMtime)
                return true;
            Thread.Sleep(100);
        }
        return false;
    }

    public static KeepaliveSetup? ReadSetup()
    {
        var cfg = LuminoConfig.LoadRaw();
        return SetupFromConfig(cfg);
    }

    public static KeepaliveSetup? SetupFromConfig(JsonObject cfg)
    {
        var coBlock = LuminoConfig.ReadBlockColor(LuminoConfig.GetObject(cfg, "corsair_last"), legacyScaled: true);
        var fansBlock = LuminoConfig.ReadBlockColor(LuminoConfig.GetObject(cfg, "corsair_fans_last"), legacyScaled: true);
        var cmBlock = LuminoConfig.ReadBlockColor(LuminoConfig.GetObject(cfg, "cm_last"), legacyScaled: true);
        var screenBlock = LuminoConfig.ReadBlockColor(LuminoConfig.GetObject(cfg, "ambiglow_last"), legacyScaled: true);

        ColorBlock? gpuBlock = null;
        if (cfg.ContainsKey("r") && cfg.ContainsKey("g") && cfg.ContainsKey("b"))
        {
            var rgb = new RgbColor(
                (byte)Math.Clamp(LuminoConfig.GetInt(cfg, "r", 0), 0, 255),
                (byte)Math.Clamp(LuminoConfig.GetInt(cfg, "g", 0), 0, 255),
                (byte)Math.Clamp(LuminoConfig.GetInt(cfg, "b", 0), 0, 255));
            int bri = Math.Clamp(LuminoConfig.GetInt(cfg, "brightness", 100), 0, 100);
            gpuBlock = new ColorBlock(rgb, bri);
        }

        bool lcdTemp = LuminoConfig.GetBool(cfg, "lcd_temp", false);
        bool lcdImgOn = LuminoConfig.GetBool(cfg, "lcd_image_on", false);
        string lcdImgPath = LuminoConfig.GetString(cfg, "lcd_image_path", "");
        int lcdScale = Math.Clamp(LuminoConfig.GetInt(cfg, "lcd_image_scale", 100), 25, 200);
        int lcdBri = Math.Clamp(LuminoConfig.GetInt(cfg, "lcd_brightness", 100), 0, 100);

        LcdImageSpec? lcdImage = (lcdImgOn && !string.IsNullOrWhiteSpace(lcdImgPath))
            ? new LcdImageSpec(lcdImgPath, lcdScale)
            : null;

        if (coBlock is null && fansBlock is null && gpuBlock is null && cmBlock is null && screenBlock is null && !lcdTemp && lcdImage is null)
            return null;

        string mode = LuminoConfig.GetEffectMode(cfg);
        int speed = LuminoConfig.GetEffectSpeed(cfg);

        return new KeepaliveSetup(mode, speed, coBlock, fansBlock, gpuBlock, cmBlock, screenBlock, lcdTemp, lcdImage, lcdBri);
    }

    public static RgbColor[] RenderLink(
        IReadOnlyList<LinkLedSpan> spans,
        string mode,
        double t,
        ColorBlock? block,
        ColorBlock? fans,
        int speed,
        EffectState? state = null,
        double? tempC = null)
    {
        block ??= fans;
        fans ??= block;
        if (block is null)
            return [];

        var groups = new List<(string Role, int Leds)>();
        foreach (var s in spans)
        {
            if (s.Leds <= 0)
                continue;
            if (groups.Count > 0 && groups[^1].Role == s.Role)
                groups[^1] = (s.Role, groups[^1].Leds + s.Leds);
            else
                groups.Add((s.Role, s.Leds));
        }

        var output = new List<RgbColor>(spans.Sum(s => s.Leds));
        foreach (var (role, count) in groups)
        {
            var src = role == "fans" ? fans! : block;
            string drawMode = src.Brightness <= 0 ? EffectsEngine.ModeStatic : mode;
            var cols = EffectsEngine.Render(count, drawMode, t, src.Color, src.Brightness, speed, state, tempC);
            output.AddRange(cols);
        }
        return output.ToArray();
    }

    private static void WriteHeartbeat()
    {
        try
        {
            Directory.CreateDirectory(AppPaths.DataDir);
            double now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0;
            File.WriteAllText(
                AppPaths.KeepaliveLockPath,
                $"{Environment.ProcessId}:{now.ToString("F3", CultureInfo.InvariantCulture)}",
                Encoding.UTF8);
        }
        catch { }
    }

    private static void TryDeleteFile(string path)
    {
        try
        {
            if (File.Exists(path))
                File.Delete(path);
        }
        catch { }
    }

    public static int RunLoop(double staticIntervalSeconds = 1.5)
    {
        using var mutex = new Mutex(initiallyOwned: true, BuildInfo.KeepaliveMutexName, out bool createdNew);
        if (!createdNew)
            return 0;

        TryDeleteFile(AppPaths.StopFlagPath);
        WriteHeartbeat();
        LuminoLogger.Log("KEEP corsair start");

        using var stopCts = new CancellationTokenSource();
        using var wakeEvent = new AutoResetEvent(false);

        var pipeThread = new Thread(() => PipeServerLoop(stopCts, wakeEvent))
        {
            IsBackground = true,
            Name = "KeepalivePipe"
        };
        pipeThread.Start();

        var clock = Stopwatch.StartNew();
        var sync = new DeviceSync(clock);
        var lcdFeed = new LcdFeed();
        sync.Start();

        CorsairLinkHub? hub = null;
        List<LinkLedSpan> spans = [];
        var fxState = new EffectState();
        KeepaliveSetup? lastSetup = null;
        string? lastStaticSig = null;
        int lastTempRounded = -999;
        int errCount = 0;
        double lastPushTime = -10.0;

        try
        {
            while (!stopCts.IsCancellationRequested)
            {
                if (File.Exists(AppPaths.StopFlagPath))
                {
                    LuminoLogger.Log("KEEP corsair arret demande");
                    break;
                }

                WriteHeartbeat();
                var setup = ReadSetup() ?? lastSetup;
                if (setup is not null)
                    lastSetup = setup;

                double t = clock.Elapsed.TotalSeconds;

                if (setup is not null)
                {
                    // Service LCD screen (GIF / static image / GPU temperature)
                    lcdFeed.Service(setup.LcdTemp, setup.LcdImage, sync.GetTemp(), setup.LcdBrightness, WriteHeartbeat);

                    // Service ASUS Aura motherboard sync on main loop (keeps USB serialized)
                    sync.ServiceCm(setup, t);

                    // Service Corsair iCUE LINK Hub
                    if (setup.Corsair is not null || setup.Fans is not null)
                    {
                        bool isDynamic = setup.Mode != EffectsEngine.ModeStatic;
                        bool needPush = isDynamic || (t - lastPushTime) >= staticIntervalSeconds;

                        if (!isDynamic)
                        {
                            string sig = $"{setup.Corsair?.Color.ToHex()}:{setup.Corsair?.Brightness}|{setup.Fans?.Color.ToHex()}:{setup.Fans?.Brightness}";
                            if (sig != lastStaticSig)
                            {
                                needPush = true;
                                lastStaticSig = sig;
                            }
                        }
                        else if (setup.Mode == EffectsEngine.ModeTemperature)
                        {
                            int curTemp = (int)Math.Round(sync.GetTemp() ?? 40.0);
                            if (curTemp != lastTempRounded)
                            {
                                needPush = true;
                                lastTempRounded = curTemp;
                            }
                            else if ((t - lastPushTime) < 0.5)
                            {
                                needPush = false;
                            }
                        }
                        else
                        {
                            lastStaticSig = null;
                        }

                        if (needPush && !HardwareHold.IsHeld())
                        {
                            try
                            {
                                if (hub is null)
                                {
                                    hub = new CorsairLinkHub();
                                    spans = hub.GetLedSpans();
                                    if (errCount > 0)
                                    {
                                        LuminoLogger.Log("KEEP corsair repris");
                                        errCount = 0;
                                    }
                                }

                                var cols = RenderLink(spans, setup.Mode, t, setup.Corsair, setup.Fans, setup.Speed, fxState, sync.GetTemp());
                                if (cols.Length == hub.TotalLeds && cols.Length > 0)
                                {
                                    hub.SetSegments(cols, settle: !isDynamic);
                                    lastPushTime = clock.Elapsed.TotalSeconds;
                                }
                            }
                            catch (Exception ex)
                            {
                                errCount++;
                                if (errCount == 1 || errCount % 15 == 0)
                                    LuminoLogger.Log($"KEEP corsair erreur x{errCount}: {ex.Message}");
                                try { hub?.Dispose(); } catch { }
                                hub = null;
                                spans = [];
                                wakeEvent.WaitOne(1000);
                                continue;
                            }
                        }
                    }
                }

                // Choose responsive sleep depending on whether animations/GIFs are active
                int waitMs = 250;
                if (setup is not null)
                {
                    if (lcdFeed.IsAnimatedGif)
                        waitMs = 15;
                    else if (setup.Mode != EffectsEngine.ModeStatic)
                        waitMs = 40;
                }
                wakeEvent.WaitOne(waitMs);
            }
        }
        finally
        {
            stopCts.Cancel();
            try { lcdFeed.Release(); } catch { }
            try { sync.Stop(); } catch { }
            try { hub?.Close(hardwareMode: true); } catch { }
            HardwareHold.ClearIdle();
            TryDeleteFile(AppPaths.KeepaliveLockPath);
            TryDeleteFile(AppPaths.StopFlagPath);
        }

        GC.KeepAlive(mutex);
        return 0;
    }

    private static void PipeServerLoop(CancellationTokenSource stopCts, AutoResetEvent wakeEvent)
    {
        var security = HardwareHold.CreateAuthenticatedUserPipeSecurity();
        while (!stopCts.IsCancellationRequested)
        {
            try
            {
                using var server = NamedPipeServerStreamAcl.Create(
                    BuildInfo.KeepalivePipeName,
                    PipeDirection.In,
                    NamedPipeServerStream.MaxAllowedServerInstances,
                    PipeTransmissionMode.Byte,
                    PipeOptions.Asynchronous,
                    0,
                    0,
                    security);

                var waitTask = server.WaitForConnectionAsync(stopCts.Token);
                waitTask.GetAwaiter().GetResult();

                using var reader = new StreamReader(server, Encoding.UTF8);
                string? cmd = reader.ReadLine()?.Trim().ToUpperInvariant();
                if (cmd == "STOP")
                {
                    stopCts.Cancel();
                    wakeEvent.Set();
                    break;
                }
                if (cmd == "RELOAD")
                {
                    wakeEvent.Set();
                }
            }
            catch (OperationCanceledException)
            {
                break;
            }
            catch
            {
                Thread.Sleep(200);
            }
        }
    }

    // -------------------------------------------------------------------------
    // DeviceSync: synchronizes GPU, Aura CM, and Ambiglow USB with Hub clock
    // -------------------------------------------------------------------------
    private sealed class DeviceSync
    {
        private readonly Stopwatch _clock;
        private readonly CancellationTokenSource _cts = new();
        private readonly Thread _thread;
        private readonly object _tempLock = new();
        private double? _tempC;
        private double _lastTempRead = -100;

        private NvApiClient? _nv;
        private IntPtr _gpuHandle;
        private bool _gpuActive;
        private bool _gpuLit = true;
        private string? _gpuWaveKey;

        private AsusAuraUsb? _aura;
        private bool _cmActive;
        private bool _cmLit = true;
        private bool _cmStrips;

        private AmbiglowController.EvniaUsbSession? _screen;
        private bool _screenActive;
        private bool _screenLit = true;

        private KeepaliveSetup? _lastSnap;
        private readonly Dictionary<string, int> _errCounts = new();

        public DeviceSync(Stopwatch clock)
        {
            _clock = clock;
            _thread = new Thread(Run) { IsBackground = true, Name = "lumino-sync" };
        }

        public void Start() => _thread.Start();

        public void Stop()
        {
            _cts.Cancel();
            if (!_thread.Join(3000))
                LuminoLogger.Log("KEEP sync thread encore occupe");

            try
            {
                var snap = ReadSetup() ?? _lastSnap;
                if (snap is not null)
                {
                    if (_gpuActive) RestoreGpu(snap);
                    if (_cmActive) RestoreCm(snap);
                    if (_screenActive) RestoreScreen(snap);
                }
            }
            catch (Exception ex)
            {
                LuminoLogger.Log($"KEEP sync retour a l'arret: {ex.Message}");
            }
            finally
            {
                CloseAura();
                CloseScreen();
            }
        }

        public double? GetTemp()
        {
            lock (_tempLock) return _tempC;
        }

        private void SetTemp(double? value)
        {
            lock (_tempLock) _tempC = value;
        }

        private void Run()
        {
            while (!_cts.IsCancellationRequested)
            {
                try
                {
                    Cycle();
                }
                catch (Exception ex)
                {
                    LuminoLogger.Log($"KEEP sync boucle: {ex.Message}");
                    Thread.Sleep(500);
                }
            }
        }

        private void Cycle()
        {
            var snap = ReadSetup();
            if (snap is not null)
                _lastSnap = snap;
            else
                snap = _lastSnap;

            if (snap is null)
            {
                Thread.Sleep(250);
                return;
            }

            double t = _clock.Elapsed.TotalSeconds;
            bool needTemp = snap.LcdTemp || snap.Mode == EffectsEngine.ModeTemperature;
            if (needTemp)
                RefreshTemp(t);

            if (snap.Mode == EffectsEngine.ModeStatic)
            {
                if (_gpuActive && !HardwareHold.IsHeld())
                {
                    Guard("gpu", () => RestoreGpu(snap));
                    _gpuActive = false;
                    _gpuWaveKey = null;
                }
                if (_screenActive && !HardwareHold.IsHeld())
                {
                    Guard("screen", () => RestoreScreen(snap));
                    _screenActive = false;
                }

                if (!_gpuActive && !_cmActive && !_screenActive)
                    HardwareHold.PublishIdle(LuminoConfig.GetConfigMtimeUnix());

                Thread.Sleep(250);
                return;
            }

            HardwareHold.ClearIdle();

            if (!HardwareHold.IsHeld())
            {
                Guard("gpu", () =>
                {
                    if (PushGpu(snap, t, GetTemp()))
                        _gpuActive = true;
                });

                Guard("screen", () =>
                {
                    if (PushScreen(snap, t, GetTemp()))
                        _screenActive = true;
                });
            }

            Thread.Sleep(200);
        }

        public void ServiceCm(KeepaliveSetup snap, double t)
        {
            if (HardwareHold.IsHeld())
                return;

            if (snap.Mode == EffectsEngine.ModeStatic)
            {
                if (_cmActive)
                {
                    Guard("cm", () => RestoreCm(snap));
                    _cmActive = false;
                }
                return;
            }

            Guard("cm", () =>
            {
                if (PushCm(snap, t, GetTemp()))
                    _cmActive = true;
            });
        }

        private void Guard(string label, Action fn)
        {
            try
            {
                fn();
                _errCounts[label] = 0;
            }
            catch (Exception ex)
            {
                int count = _errCounts.GetValueOrDefault(label) + 1;
                _errCounts[label] = count;
                if (count == 1 || count % 15 == 0)
                    LuminoLogger.Log($"KEEP sync {label}: {ex.Message}");

                if (label == "gpu")
                {
                    _nv = null;
                    _gpuHandle = IntPtr.Zero;
                    _gpuWaveKey = null;
                }
                else if (label == "cm")
                {
                    CloseAura();
                }
                else if (label == "screen")
                {
                    CloseScreen();
                }
            }
        }

        private void RefreshTemp(double t)
        {
            if ((t - _lastTempRead) < 2.0)
                return;
            _lastTempRead = t;

            try
            {
                EnsureNv();
                int? temp = _nv!.GetGpuTemp(_gpuHandle);
                if (temp.HasValue)
                    SetTemp(temp.Value);
                _errCounts["temp"] = 0;
            }
            catch (Exception ex)
            {
                int count = _errCounts.GetValueOrDefault("temp") + 1;
                _errCounts["temp"] = count;
                if (count == 1 || count % 15 == 0)
                    LuminoLogger.Log($"KEEP sync sonde: {ex.Message}");
            }
        }

        private void EnsureNv()
        {
            if (_nv is not null && _gpuHandle != IntPtr.Zero)
                return;

            var nv = new NvApiClient();
            nv.Initialize();
            var gpus = nv.EnumGpus();
            if (gpus.Length == 0)
                throw new InvalidOperationException("Aucun GPU NVIDIA");

            IntPtr h = gpus[0];
            var (ok, detail) = BlackwellGpu.Probe(nv, h, port: 1);
            if (!ok)
                throw new InvalidOperationException($"Sonde 0x75 KO ({detail})");

            _nv = nv;
            _gpuHandle = h;
        }

        private bool PushGpu(KeepaliveSetup snap, double t, double? temp)
        {
            if (snap.Gpu is null || HardwareHold.IsHeld())
                return false;

            EnsureNv();
            if (snap.Gpu.Brightness <= 0)
            {
                _gpuWaveKey = null;
                if (_gpuLit)
                {
                    BlackwellGpu.ApplyDirect(_nv!, _gpuHandle, [RgbColor.Black], brightness10: 1);
                    _gpuLit = false;
                    return true;
                }
                return false;
            }

            int level = Math.Max(1, BlackwellGpu.LevelFromPercent(snap.Gpu.Brightness));
            if (snap.Mode == EffectsEngine.ModeRainbowWave)
            {
                string key = $"wave:{level}:{snap.Speed}";
                if (_gpuWaveKey != key)
                {
                    BlackwellGpu.ApplyWave(_nv!, _gpuHandle, level, snap.Speed);
                    _gpuWaveKey = key;
                    _gpuLit = true;
                    LuminoLogger.Log($"KEEP sync gpu vague arc-en-ciel v={snap.Speed}");
                }
                return true;
            }

            _gpuWaveKey = null;
            var cols = EffectsEngine.Render(6, snap.Mode, t, snap.Gpu.Color, snap.Gpu.Brightness, snap.Speed, null, temp);
            if (snap.Mode is EffectsEngine.ModeColorWave or EffectsEngine.ModeRipple)
            {
                var mid = cols[cols.Length / 2];
                cols = Enumerable.Repeat(mid, 6).ToArray();
            }
            BlackwellGpu.ApplyDirect(_nv!, _gpuHandle, cols, brightness10: 10);
            _gpuLit = true;
            return true;
        }

        private void RestoreGpu(KeepaliveSetup snap)
        {
            if (snap.Gpu is null || HardwareHold.IsHeld())
                return;

            EnsureNv();
            int level = BlackwellGpu.LevelFromPercent(snap.Gpu.Brightness);
            if (level <= 0)
                BlackwellGpu.ApplyStatic(_nv!, _gpuHandle, 0, 0, 0, 1, doSave: false);
            else
                BlackwellGpu.ApplyStatic(_nv!, _gpuHandle, snap.Gpu.Color.R, snap.Gpu.Color.G, snap.Gpu.Color.B, level, doSave: false);
            _gpuLit = level > 0;
        }

        private void EnsureAura()
        {
            _aura ??= new AsusAuraUsb();
        }

        private void CloseAura()
        {
            try { _aura?.Dispose(); } catch { }
            _aura = null;
        }

        private void StripsSolid(RgbColor color)
        {
            if (_aura is null)
                return;
            var cols = Enumerable.Repeat(color, 120).ToArray();
            foreach (var ch in _aura.Channels.Where(c => c.Kind == "addr"))
            {
                _aura.ResetAddrFlag(ch.DirectIndex);
                _aura.SetChannelColors(ch, cols, refreshMode: false);
            }
        }

        private bool PushCm(KeepaliveSetup snap, double t, double? temp)
        {
            if (snap.Cm is null || HardwareHold.IsHeld())
                return false;

            EnsureAura();
            if (snap.Cm.Brightness <= 0)
            {
                if (_cmLit)
                {
                    _aura!.SetCm(0, 0, 0);
                    if (_cmStrips)
                    {
                        StripsSolid(RgbColor.Black);
                        _cmStrips = false;
                    }
                    CloseAura();
                    _cmLit = false;
                    return true;
                }
                return false;
            }

            if (snap.Mode == EffectsEngine.ModeRainbowWave)
            {
                double cps = Math.Clamp(snap.Speed, 1, 10) * 0.03;
                double shift = (t * cps) % 1.0;
                var fixedCh = _aura!.Channels.FirstOrDefault(c => c.Kind == "fixed");
                var strips = _aura.Channels.Where(c => c.Kind == "addr").ToList();

                if (fixedCh is not null)
                {
                    var fixedCols = EffectsEngine.RenderAuraHueWave(fixedCh.LedCount, 16, 0, snap.Cm.Brightness, shift);
                    _aura.SetChannelColors(fixedCh, fixedCols, refreshMode: true);
                }
                foreach (var ch in strips)
                {
                    var stripCols = EffectsEngine.RenderAuraHueWave(120, 22, 0, snap.Cm.Brightness, shift);
                    _aura.SetChannelColors(ch, stripCols, refreshMode: true);
                }
                _cmStrips = strips.Count > 0;
                _cmLit = true;
                return true;
            }

            var fixedChannel = _aura!.Channels.FirstOrDefault(c => c.Kind == "fixed");
            int nFixed = fixedChannel?.LedCount ?? 1;
            var cols = EffectsEngine.Render(nFixed, snap.Mode, t, snap.Cm.Color, snap.Cm.Brightness, snap.Speed, null, temp);
            _aura.SetCmColors(cols);
            _cmLit = true;
            return true;
        }

        private void RestoreCm(KeepaliveSetup snap)
        {
            if (snap.Cm is null || HardwareHold.IsHeld())
                return;

            EnsureAura();
            var scaled = snap.Cm.Brightness <= 0 ? RgbColor.Black : snap.Cm.Color.ScaledFloor(snap.Cm.Brightness);
            _aura!.SetCm(scaled.R, scaled.G, scaled.B);
            if (_cmStrips)
            {
                StripsSolid(scaled);
                _cmStrips = false;
            }
            CloseAura();
            _cmLit = snap.Cm.Brightness > 0;
        }

        private void EnsureScreen()
        {
            _screen ??= new AmbiglowController.EvniaUsbSession();
        }

        private void CloseScreen()
        {
            try { _screen?.Dispose(); } catch { }
            _screen = null;
        }

        private bool PushScreen(KeepaliveSetup snap, double t, double? temp)
        {
            if (snap.Screen is null || HardwareHold.IsHeld())
                return false;

            EnsureScreen();
            if (snap.Screen.Brightness <= 0)
            {
                if (_screenLit)
                {
                    _screen!.SetColors(Enumerable.Repeat(RgbColor.Black, AmbiglowController.LedCount).ToArray());
                    _screenLit = false;
                    return true;
                }
                return false;
            }

            var cols = EffectsEngine.Render(AmbiglowController.LedCount, snap.Mode, t, snap.Screen.Color, snap.Screen.Brightness, snap.Speed, null, temp);
            _screen!.SetColors(cols);
            _screenLit = true;
            return true;
        }

        private void RestoreScreen(KeepaliveSetup snap)
        {
            if (snap.Screen is null || HardwareHold.IsHeld())
                return;

            EnsureScreen();
            var scaled = snap.Screen.Brightness <= 0 ? RgbColor.Black : snap.Screen.Color.ScaledFloor(snap.Screen.Brightness);
            _screen!.SetColors(Enumerable.Repeat(scaled, AmbiglowController.LedCount).ToArray());
            CloseScreen();
            _screenLit = snap.Screen.Brightness > 0;
        }
    }

    // -------------------------------------------------------------------------
    // LcdFeed: feeds GIF animation, static image, or GPU temp to Corsair LCD
    // -------------------------------------------------------------------------
    private sealed class LcdFeed
    {
        private CorsairLcdScreen? _lcd;
        private int? _lastTemp;
        private int? _lastBrightness;
        private string? _cachedImageKey;
        private List<LcdFrame>? _frames;
        private int _frameIndex;
        private double _nextFrameTime;
        private bool _staticImageSent;
        private int _errCount;
        private bool _inSoftwareMode;

        public bool IsAnimatedGif => _frames is { Count: > 1 };

        public void Close()
        {
            try { _lcd?.Dispose(); } catch { }
            _lcd = null;
        }

        public void Release()
        {
            try
            {
                if (_inSoftwareMode)
                {
                    if (_lcd is not null)
                        _lcd.RestoreHardware();
                    else
                        CorsairLcdScreen.ReleaseToHardware();
                    LuminoLogger.Log("KEEP lcd fond hardware");
                }
            }
            catch (Exception ex)
            {
                LuminoLogger.Log($"KEEP lcd fond: {ex.Message}");
            }
            finally
            {
                Close();
                _inSoftwareMode = false;
                _lastTemp = null;
                _lastBrightness = null;
                _staticImageSent = false;
                _frames = null;
                _cachedImageKey = null;
            }
        }

        private bool ApplyBrightnessIfChanged(int percent)
        {
            int p = Math.Clamp(percent, 0, 100);
            if (_lastBrightness == p)
                return false;

            _lcd!.SetBrightness(p, persist: false);
            _lastBrightness = p;
            return true;
        }

        public void Service(bool tempEnabled, LcdImageSpec? image, double? tempC, int brightness, Action heartbeat)
        {
            if (image is not null)
            {
                ServiceImage(image, brightness, heartbeat);
                return;
            }

            _frames = null;
            _cachedImageKey = null;
            _staticImageSent = false;

            if (!tempEnabled)
            {
                if (_inSoftwareMode)
                    Release();
                return;
            }

            try
            {
                bool justOpened = false;
                if (_lcd is null)
                {
                    _lcd = new CorsairLcdScreen();
                    _inSoftwareMode = true;
                    _lastTemp = null;
                    _lastBrightness = null;
                    justOpened = true;
                    LuminoLogger.Log($"KEEP lcd temperature pid={_lcd.ProductId:x4}");
                }

                bool briChanged = ApplyBrightnessIfChanged(brightness);
                int rounded = tempC.HasValue ? (int)Math.Round(tempC.Value) : -999;

                if (justOpened || briChanged || rounded != _lastTemp)
                {
                    byte[] jpeg = CorsairLcdScreen.RenderTempJpeg(tempC);
                    _lcd.SendJpeg(jpeg);
                    _lastTemp = rounded;
                }
                _errCount = 0;
            }
            catch (Exception ex)
            {
                _errCount++;
                if (_errCount == 1 || _errCount % 15 == 0)
                    LuminoLogger.Log($"KEEP lcd: {ex.Message}");
                Close();
            }
        }

        private void ServiceImage(LcdImageSpec image, int brightness, Action heartbeat)
        {
            if (!File.Exists(image.Path))
            {
                _errCount++;
                if (_errCount == 1 || _errCount % 15 == 0)
                    LuminoLogger.Log($"KEEP lcd image absente: {image.Path}");
                return;
            }

            try
            {
                long mtime = File.GetLastWriteTimeUtc(image.Path).Ticks;
                string key = $"{image.Path}|{image.Scale}|{mtime}";

                if (_cachedImageKey != key || _frames is null)
                {
                    _frames = CorsairLcdScreen.LoadLcdFrames(image.Path, image.Scale, 480, heartbeat);
                    _cachedImageKey = key;
                    _frameIndex = 0;
                    _nextFrameTime = 0;
                    _staticImageSent = false;
                    if (_frames.Count > 1)
                        LuminoLogger.Log($"KEEP lcd gif n={_frames.Count}");
                }

                bool justOpened = false;
                if (_lcd is null)
                {
                    _lcd = new CorsairLcdScreen();
                    _inSoftwareMode = true;
                    _lastBrightness = null;
                    justOpened = true;
                    LuminoLogger.Log($"KEEP lcd image pid={_lcd.ProductId:x4}");
                }

                bool briChanged = ApplyBrightnessIfChanged(brightness);

                if (_frames.Count == 1)
                {
                    if (justOpened || briChanged || !_staticImageSent)
                    {
                        _lcd.SendJpeg(_frames[0].JpegBytes);
                        _staticImageSent = true;
                    }
                }
                else
                {
                    double now = Environment.TickCount64 / 1000.0;
                    if (justOpened || briChanged || now >= _nextFrameTime)
                    {
                        var frame = _frames[_frameIndex];
                        _lcd.SendJpeg(frame.JpegBytes);
                        double delay = frame.DelaySeconds ?? 0.1;
                        _nextFrameTime = now + Math.Max(0.02, delay);
                        _frameIndex = (_frameIndex + 1) % _frames.Count;
                    }
                }

                _errCount = 0;
            }
            catch (Exception ex)
            {
                _errCount++;
                if (_errCount == 1 || _errCount % 15 == 0)
                    LuminoLogger.Log($"KEEP lcd image: {ex.Message}");
                Close();
            }
        }
    }
}
