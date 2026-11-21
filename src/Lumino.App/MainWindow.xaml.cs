using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.WindowsRuntime;
using System.Text;
using System.Text.Json.Nodes;
using Lumino.Core;
using Lumino.Core.Hardware;
using Lumino.Core.Services;
using Microsoft.UI;
using Microsoft.UI.Input;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Documents;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using Microsoft.UI.Xaml.Shapes;
using Windows.Graphics;
using Windows.Storage.Pickers;
using Windows.System;
using Windows.UI;
using WinRT.Interop;
using Color = Windows.UI.Color;
using Path = System.IO.Path;

namespace Lumino.App;

public sealed partial class MainWindow : Window
{
    private const int DefaultWidthDips = 1040;
    private const int DefaultHeightDips = 696;
    private const int PreviewSegments = 24;
    private const int LcdPreviewPx = 216;

    private static readonly (string Key, string Label)[] NavDevices =
    [
        ("all", "Tout"),
        ("gpu", "GPU"),
        ("cm", "Carte mère"),
        ("corsair", "Waterblock"),
        ("fans", "Ventilos"),
        ("screen", "Ambiglow"),
        ("lcd", "Image LCD"),
    ];

    private static readonly Dictionary<string, (string Title, string Subtitle, string ApplyLabel)> PageMeta = new()
    {
        ["all"] = ("Tout synchroniser", "Même couleur et même effet, en même temps, sur le GPU, la carte mère, le waterblock, les ventilos et l'Ambiglow.", "Tout appliquer"),
        ["gpu"] = ("Carte graphique", "Gigabyte RTX 5080 Blackwell (6 zones). En statique, la couleur est gravée sur la carte.", "Appliquer le GPU"),
        ["cm"] = ("Carte mère", "ASUS Aura USB (LEDs fixes + headers ARGB). En statique, la couleur est gravée sur la carte.", "Appliquer la CM"),
        ["corsair"] = ("Waterblock", "Pompe Corsair iCUE LINK Titan et anneau LCD. Les deux ventilateurs RX ont leur page dédiée.", "Appliquer le waterblock"),
        ["fans"] = ("Ventilos Corsair", "Les deux ventilateurs RX ensemble. Même effet que le waterblock, couleur et luminosité indépendantes.", "Appliquer les ventilos"),
    };

    private sealed class NavItemRefs
    {
        public required string Key { get; init; }
        public required Button Button { get; init; }
        public required Border Indicator { get; init; }
        public required Ellipse ColorDot { get; init; }
        public required TextBlock NameText { get; init; }
        public required TextBlock BadgeText { get; init; }
    }

    private sealed class DeviceState
    {
        public string Hex { get; set; } = "#e66922";
        public int Brightness { get; set; } = 50;
    }

    private readonly IntPtr _hwnd;
    private readonly AppWindow _appWindow;
    private readonly CancellationTokenSource _pipeCts = new();
    private readonly Stopwatch _animClock = Stopwatch.StartNew();

    private readonly Dictionary<string, DeviceState> _devices = new();
    private readonly Dictionary<string, NavItemRefs> _navItems = new();
    private readonly List<(string Hex, Button Btn, Border Ring, Border Swatch)> _presetButtons = [];
    private readonly Dictionary<string, Button> _modeButtons = new();
    private readonly Dictionary<int, Button> _speedButtons = new();
    private readonly Dictionary<int, Button> _ambiModeButtons = new();
    private readonly Dictionary<int, Button> _ambiColorButtons = new();
    private readonly Dictionary<int, Button> _ambiBriButtons = new();
    private readonly Dictionary<int, Button> _ambiSpeedButtons = new();
    private readonly Border[] _previewBars = new Border[PreviewSegments];

    private DispatcherTimer? _previewTimer;
    private DispatcherTimer? _pollTimer;
    private DispatcherTimer? _lcdGifTimer;

    private string _selectedKey = "all";
    private string _effectMode = EffectsEngine.ModeStatic;
    private int _effectSpeed = 4;
    private bool _isDark = true;
    private bool _optionsOpen;
    private bool _busy;
    private bool _updatingUi = true;
    private bool _keepaliveActive;
    private string _statusKind = "info";

    // Ambiglow OSD state
    private int? _ambiMode;
    private int? _ambiColor;
    private int? _ambiBri;
    private int? _ambiSpeed;
    private bool _ambiUsbAvailable;
    private bool _ambiLoadedOnce;

    // LCD state
    private string _lcdImagePath = string.Empty;
    private int _lcdImageScale = 100;
    private int _lcdBrightness = 100;
    private bool _lcdImageOn;
    private bool _lcdTempOn;
    private List<(byte[] Bgra, int DelayMs)> _lcdPreviewFrames = [];
    private int _lcdPreviewFrameIndex;
    private WriteableBitmap? _lcdBitmap;

    public MainWindow()
    {
        InitializeComponent();

        _hwnd = WindowNative.GetWindowHandle(this);
        var windowId = Win32Interop.GetWindowIdFromWindow(_hwnd);
        _appWindow = AppWindow.GetFromWindowId(windowId);

        SystemBackdrop = new MicaBackdrop();
        ExtendsContentIntoTitleBar = true;
        SetTitleBar(TitleBarDragArea);

        LoadInitialConfig();
        ConfigureWindowChrome();
        LoadWindowIcon();

        BuildDynamicControls();
        SidebarVersionText.Text = $"Lumino {BuildInfo.ShortVersion}";

        _appWindow.Closing += AppWindow_Closing;

        _updatingUi = false;
        ApplyTheme(persist: false);
        SelectNav("all");
        RefreshKeepaliveIndicator();
        PopulateOptionsState();
        StartTimers();
        StartFocusPipeServer();
    }

    // -------------------------------------------------------------------------
    // Window Chrome, Icon, Single-instance focus pipe
    // -------------------------------------------------------------------------

    private void ConfigureWindowChrome()
    {
        double scale = GetDpiScale();
        int widthPx = (int)Math.Round(DefaultWidthDips * scale);
        int heightPx = (int)Math.Round(DefaultHeightDips * scale);

        if (_appWindow.Presenter is OverlappedPresenter presenter)
        {
            presenter.IsResizable = true;
            presenter.IsMaximizable = true;
            presenter.PreferredMinimumWidth = (int)Math.Round(880 * scale);
            presenter.PreferredMinimumHeight = (int)Math.Round(580 * scale);
        }

        _appWindow.Resize(new SizeInt32(widthPx, heightPx));
        CenterOnPrimaryDisplay();

        if (AppWindowTitleBar.IsCustomizationSupported())
        {
            var tb = _appWindow.TitleBar;
            tb.ButtonBackgroundColor = Colors.Transparent;
            tb.ButtonInactiveBackgroundColor = Colors.Transparent;

            if (tb.RightInset > 0)
            {
                double rightDips = tb.RightInset / scale;
                CaptionButtonsSpacerColumn.Width = new GridLength(Math.Max(112, Math.Ceiling(rightDips)));
            }
        }
    }

    private void CenterOnPrimaryDisplay()
    {
        var displayArea = DisplayArea.GetFromWindowId(_appWindow.Id, DisplayAreaFallback.Primary);
        if (displayArea is null)
            return;

        var work = displayArea.WorkArea;
        var size = _appWindow.Size;
        int x = work.X + (work.Width - size.Width) / 2;
        int y = work.Y + (work.Height - size.Height) / 2;
        _appWindow.Move(new PointInt32(Math.Max(work.X, x), Math.Max(work.Y, y)));
    }

    private void LoadWindowIcon()
    {
        var iconPath = AppPaths.ResolveAssetPath("lumino.ico");
        if (!File.Exists(iconPath))
            return;

        try
        {
            _appWindow.SetIcon(iconPath);
            TitleBarIcon.Source = new BitmapImage(new Uri(iconPath));
        }
        catch { }
    }

    private double GetDpiScale()
    {
        uint dpi = GetDpiForWindow(_hwnd);
        return dpi > 0 ? dpi / 96.0 : 1.0;
    }

    private void TitleBarGrid_Loaded(object sender, RoutedEventArgs e)
    {
        UpdateTitleBarPassthroughRegions();
        DispatcherQueue.TryEnqueue(UpdateTitleBarPassthroughRegions);
    }
    private void TitleBarGrid_SizeChanged(object sender, SizeChangedEventArgs e) => UpdateTitleBarPassthroughRegions();

    private void UpdateTitleBarPassthroughRegions()
    {
        if (TitleBarActionsPanel.ActualWidth <= 0 || TitleBarActionsPanel.ActualHeight <= 0)
            return;

        try
        {
            double scale = RootGrid.XamlRoot?.RasterizationScale ?? GetDpiScale();
            var transform = TitleBarActionsPanel.TransformToVisual(null);
            var bounds = transform.TransformBounds(new Windows.Foundation.Rect(
                0, 0, TitleBarActionsPanel.ActualWidth, TitleBarActionsPanel.ActualHeight));

            var rect = new RectInt32(
                (int)Math.Floor(bounds.X * scale),
                (int)Math.Floor(bounds.Y * scale),
                (int)Math.Ceiling(bounds.Width * scale),
                (int)Math.Ceiling(bounds.Height * scale));

            var nonClient = InputNonClientPointerSource.GetForWindowId(_appWindow.Id);
            nonClient.SetRegionRects(NonClientRegionKind.Passthrough, [rect]);
        }
        catch { }
    }

    private void StartFocusPipeServer()
    {
        var thread = new Thread(() =>
        {
            var security = HardwareHold.CreateAuthenticatedUserPipeSecurity();
            while (!_pipeCts.IsCancellationRequested)
            {
                try
                {
                    using var server = NamedPipeServerStreamAcl.Create(
                        BuildInfo.ShowWindowPipeName,
                        PipeDirection.In,
                        NamedPipeServerStream.MaxAllowedServerInstances,
                        PipeTransmissionMode.Byte,
                        PipeOptions.Asynchronous,
                        0,
                        0,
                        security);

                    server.WaitForConnectionAsync(_pipeCts.Token).GetAwaiter().GetResult();
                    using var reader = new StreamReader(server, Encoding.UTF8);
                    _ = reader.ReadLine();

                    DispatcherQueue.TryEnqueue(BringToForeground);
                }
                catch (OperationCanceledException)
                {
                    break;
                }
                catch
                {
                    Thread.Sleep(250);
                }
            }
        })
        {
            IsBackground = true,
            Name = "FocusPipe"
        };
        thread.Start();
    }

    private void BringToForeground()
    {
        try
        {
            if (_appWindow.Presenter is OverlappedPresenter p && p.State == OverlappedPresenterState.Minimized)
                p.Restore();
            ShowWindow(_hwnd, SW_RESTORE);
            SetForegroundWindow(_hwnd);
            Activate();
        }
        catch { }
    }

    private void AppWindow_Closing(AppWindow sender, AppWindowClosingEventArgs args)
    {
        _pipeCts.Cancel();
        _previewTimer?.Stop();
        _pollTimer?.Stop();
        _lcdGifTimer?.Stop();
    }

    // -------------------------------------------------------------------------
    // Initial Config Loading
    // -------------------------------------------------------------------------

    private void LoadInitialConfig()
    {
        var cfg = LuminoConfig.LoadRaw();
        string themeStr = LuminoConfig.GetString(cfg, "theme", "dark");
        _isDark = !themeStr.Equals("light", StringComparison.OrdinalIgnoreCase);

        _effectMode = LuminoConfig.GetEffectMode(cfg);
        _effectSpeed = LuminoConfig.GetEffectSpeed(cfg);

        // GPU (root r/g/b/brightness)
        string gpuHex = "#e66922";
        int gpuBri = 50;
        if (cfg.ContainsKey("r") && cfg.ContainsKey("g") && cfg.ContainsKey("b"))
        {
            var c = new RgbColor(
                (byte)Math.Clamp(LuminoConfig.GetInt(cfg, "r", 230), 0, 255),
                (byte)Math.Clamp(LuminoConfig.GetInt(cfg, "g", 105), 0, 255),
                (byte)Math.Clamp(LuminoConfig.GetInt(cfg, "b", 34), 0, 255));
            gpuHex = c.ToHex();
            gpuBri = Math.Clamp(LuminoConfig.GetInt(cfg, "brightness", 50), 0, 100);
        }

        var (cmHex, cmBri) = LuminoConfig.StoredColor(LuminoConfig.GetObject(cfg, "cm_last"), gpuHex, gpuBri);
        var (coHex, coBri) = LuminoConfig.StoredColor(LuminoConfig.GetObject(cfg, "corsair_last"), gpuHex, gpuBri);
        var (fanHex, fanBri) = LuminoConfig.StoredColor(LuminoConfig.GetObject(cfg, "corsair_fans_last"), coHex, coBri);
        var (scHex, scBri) = LuminoConfig.StoredColor(LuminoConfig.GetObject(cfg, "ambiglow_last"), gpuHex, gpuBri);

        _devices["all"] = new DeviceState { Hex = gpuHex, Brightness = gpuBri };
        _devices["gpu"] = new DeviceState { Hex = gpuHex, Brightness = gpuBri };
        _devices["cm"] = new DeviceState { Hex = cmHex, Brightness = cmBri };
        _devices["corsair"] = new DeviceState { Hex = coHex, Brightness = coBri };
        _devices["fans"] = new DeviceState { Hex = fanHex, Brightness = fanBri };
        _devices["screen"] = new DeviceState { Hex = scHex, Brightness = scBri };

        _lcdImagePath = LuminoConfig.GetString(cfg, "lcd_image_path", "");
        _lcdImageScale = Math.Clamp(LuminoConfig.GetInt(cfg, "lcd_image_scale", 100), 25, 200);
        _lcdBrightness = Math.Clamp(LuminoConfig.GetInt(cfg, "lcd_brightness", 100), 0, 100);
        _lcdImageOn = LuminoConfig.GetBool(cfg, "lcd_image_on", false);
        _lcdTempOn = LuminoConfig.GetBool(cfg, "lcd_temp", false);
    }

    // -------------------------------------------------------------------------
    // Dynamic Control Construction
    // -------------------------------------------------------------------------

    private void BuildDynamicControls()
    {
        // 1. Sidebar Nav Items
        foreach (var (key, label) in NavDevices)
        {
            var indicator = new Border
            {
                Width = 3,
                Height = 18,
                CornerRadius = new CornerRadius(2),
                VerticalAlignment = VerticalAlignment.Center
            };

            var dot = new Ellipse
            {
                Width = 10,
                Height = 10,
                VerticalAlignment = VerticalAlignment.Center,
                StrokeThickness = 1
            };

            var nameTxt = new TextBlock
            {
                Text = label,
                FontSize = 13.5,
                VerticalAlignment = VerticalAlignment.Center,
                TextTrimming = TextTrimming.CharacterEllipsis
            };

            var badgeTxt = new TextBlock
            {
                Text = "",
                FontSize = 11.5,
                Width = 42,
                TextAlignment = TextAlignment.Right,
                VerticalAlignment = VerticalAlignment.Center
            };
            Typography.SetNumeralAlignment(badgeTxt, FontNumeralAlignment.Tabular);

            var grid = new Grid { ColumnSpacing = 9 };
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });

            Grid.SetColumn(indicator, 0);
            Grid.SetColumn(dot, 1);
            Grid.SetColumn(nameTxt, 2);
            Grid.SetColumn(badgeTxt, 3);

            grid.Children.Add(indicator);
            grid.Children.Add(dot);
            grid.Children.Add(nameTxt);
            grid.Children.Add(badgeTxt);

            var btn = new Button
            {
                Content = grid,
                HorizontalAlignment = HorizontalAlignment.Stretch,
                HorizontalContentAlignment = HorizontalAlignment.Stretch,
                Height = 38,
                Padding = new Thickness(6, 0, 10, 0),
                CornerRadius = new CornerRadius(8),
                BorderThickness = new Thickness(1)
            };

            string capturedKey = key;
            btn.Click += (_, _) => SelectNav(capturedKey);

            NavItemsPanel.Children.Add(btn);
            _navItems[key] = new NavItemRefs
            {
                Key = key,
                Button = btn,
                Indicator = indicator,
                ColorDot = dot,
                NameText = nameTxt,
                BadgeText = badgeTxt
            };
        }

        // 2. Preview Strip (24 smooth segments)
        for (int i = 0; i < PreviewSegments; i++)
        {
            PreviewStripGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            var bar = new Border
            {
                CornerRadius = new CornerRadius(4),
                Background = new SolidColorBrush(Colors.Gray)
            };
            Grid.SetColumn(bar, i);
            PreviewStripGrid.Children.Add(bar);
            _previewBars[i] = bar;
        }

        // 3. 13 Preset Swatches evenly distributed across the card width
        for (int i = 0; i < AmbiglowController.PresetSwatches.Length; i++)
        {
            PresetsGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            string hx = AmbiglowController.PresetSwatches[i];
            var rgb = LuminoConfig.HexToRgb(hx);
            var swatch = new Border
            {
                Width = 26,
                Height = 26,
                CornerRadius = new CornerRadius(13),
                Background = new SolidColorBrush(Color.FromArgb(255, rgb.R, rgb.G, rgb.B)),
                BorderThickness = new Thickness(1)
            };
            var ring = new Border
            {
                Width = 34,
                Height = 34,
                CornerRadius = new CornerRadius(17),
                BorderThickness = new Thickness(2),
                Padding = new Thickness(2),
                Child = swatch
            };
            var btn = new Button
            {
                Content = ring,
                Padding = new Thickness(0),
                MinWidth = 0,
                MinHeight = 0,
                Width = 36,
                Height = 36,
                HorizontalAlignment = HorizontalAlignment.Center,
                CornerRadius = new CornerRadius(18),
                Background = new SolidColorBrush(Colors.Transparent),
                BorderThickness = new Thickness(0)
            };
            ToolTipService.SetToolTip(btn, hx.ToUpperInvariant());

            string capturedHex = hx;
            btn.Click += (_, _) => SetEditorHex(capturedHex);

            Grid.SetColumn(btn, i);
            PresetsGrid.Children.Add(btn);
            _presetButtons.Add((hx, btn, ring, swatch));
        }

        // 4. 7 Effect Mode Buttons (2 rows x 4 cols)
        for (int i = 0; i < EffectsEngine.Modes.Length; i++)
        {
            var (code, label) = EffectsEngine.Modes[i];
            var btn = new Button
            {
                Content = label,
                HorizontalAlignment = HorizontalAlignment.Stretch,
                Height = 34,
                CornerRadius = new CornerRadius(7),
                FontSize = 12.5,
                BorderThickness = new Thickness(1)
            };
            string capturedCode = code;
            btn.Click += (_, _) => SetEffectMode(capturedCode);

            Grid.SetRow(btn, i / 4);
            Grid.SetColumn(btn, i % 4);
            ModesGrid.Children.Add(btn);
            _modeButtons[code] = btn;
        }

        // 5. Speed 1..10 Buttons
        for (int s = 1; s <= 10; s++)
        {
            SpeedsGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            var tb = new TextBlock { Text = s.ToString(), FontSize = 12.5, HorizontalAlignment = HorizontalAlignment.Center };
            Typography.SetNumeralAlignment(tb, FontNumeralAlignment.Tabular);
            var btn = new Button
            {
                Content = tb,
                HorizontalAlignment = HorizontalAlignment.Stretch,
                Height = 32,
                Padding = new Thickness(0),
                MinWidth = 0,
                CornerRadius = new CornerRadius(6),
                BorderThickness = new Thickness(1)
            };
            int capturedSpeed = s;
            btn.Click += (_, _) => SetEffectSpeed(capturedSpeed);

            Grid.SetColumn(btn, s - 1);
            SpeedsGrid.Children.Add(btn);
            _speedButtons[s] = btn;
        }

        // 6. Ambiglow OSD Controls
        for (int i = 0; i < AmbiglowController.LightModes.Length; i++)
        {
            var (val, label) = AmbiglowController.LightModes[i];
            var btn = new Button
            {
                Content = new TextBlock
                {
                    Text = label,
                    FontSize = 12,
                    TextTrimming = TextTrimming.CharacterEllipsis
                },
                HorizontalAlignment = HorizontalAlignment.Stretch,
                Height = 36,
                CornerRadius = new CornerRadius(7),
                BorderThickness = new Thickness(1)
            };
            int capturedVal = val;
            btn.Click += (_, _) => SetAmbiModeAsync(capturedVal);
            Grid.SetRow(btn, i / 4);
            Grid.SetColumn(btn, i % 4);
            AmbiModesGrid.Children.Add(btn);
            _ambiModeButtons[val] = btn;
        }

        for (int i = 0; i < AmbiglowController.Colors.Length; i++)
        {
            var (val, label) = AmbiglowController.Colors[i];
            var sp = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 7, VerticalAlignment = VerticalAlignment.Center };
            if (AmbiglowController.ColorHex.TryGetValue(val, out string? hx))
            {
                var rgb = LuminoConfig.HexToRgb(hx);
                sp.Children.Add(new Ellipse
                {
                    Width = 10,
                    Height = 10,
                    Fill = new SolidColorBrush(Color.FromArgb(255, rgb.R, rgb.G, rgb.B)),
                    Stroke = new SolidColorBrush(Color.FromArgb(90, 128, 128, 128)),
                    StrokeThickness = 1
                });
            }
            else
            {
                sp.Children.Add(new FontIcon
                {
                    FontFamily = new Microsoft.UI.Xaml.Media.FontFamily("Segoe Fluent Icons"),
                    Glyph = "\uE790",
                    FontSize = 11
                });
            }
            sp.Children.Add(new TextBlock { Text = label, FontSize = 12, TextTrimming = TextTrimming.CharacterEllipsis });

            var btn = new Button
            {
                Content = sp,
                HorizontalAlignment = HorizontalAlignment.Stretch,
                HorizontalContentAlignment = HorizontalAlignment.Left,
                Padding = new Thickness(10, 0, 8, 0),
                Height = 34,
                CornerRadius = new CornerRadius(7),
                BorderThickness = new Thickness(1)
            };
            int capturedVal = val;
            btn.Click += (_, _) => SetAmbiColorAsync(capturedVal);
            Grid.SetRow(btn, i / 5);
            Grid.SetColumn(btn, i % 5);
            AmbiColorsGrid.Children.Add(btn);
            _ambiColorButtons[val] = btn;
        }

        for (int i = 0; i < AmbiglowController.BrightnessLevels.Length; i++)
        {
            var (val, label) = AmbiglowController.BrightnessLevels[i];
            var btn = new Button
            {
                Content = label,
                HorizontalAlignment = HorizontalAlignment.Stretch,
                Height = 34,
                FontSize = 12,
                CornerRadius = new CornerRadius(7),
                BorderThickness = new Thickness(1)
            };
            int capturedVal = val;
            btn.Click += (_, _) => SetAmbiBrightnessAsync(capturedVal);
            Grid.SetColumn(btn, i);
            AmbiBriGrid.Children.Add(btn);
            _ambiBriButtons[val] = btn;
        }

        for (int i = 0; i < AmbiglowController.SpeedLevels.Length; i++)
        {
            var (val, label) = AmbiglowController.SpeedLevels[i];
            var btn = new Button
            {
                Content = label,
                HorizontalAlignment = HorizontalAlignment.Stretch,
                Height = 34,
                FontSize = 12,
                CornerRadius = new CornerRadius(7),
                BorderThickness = new Thickness(1)
            };
            int capturedVal = val;
            btn.Click += (_, _) => SetAmbiSpeedAsync(capturedVal);
            Grid.SetColumn(btn, i);
            AmbiSpeedGrid.Children.Add(btn);
            _ambiSpeedButtons[val] = btn;
        }
    }

    // -------------------------------------------------------------------------
    // Timers (LED preview animation, Keepalive status poll, LCD GIF preview)
    // -------------------------------------------------------------------------

    private void StartTimers()
    {
        _previewTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(50) };
        _previewTimer.Tick += (_, _) => UpdateLedPreviewStrip();
        _previewTimer.Start();

        _pollTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(2) };
        _pollTimer.Tick += (_, _) => RefreshKeepaliveIndicator();
        _pollTimer.Start();

        _lcdGifTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(80) };
        _lcdGifTimer.Tick += LcdGifTimer_Tick;
    }

    // -------------------------------------------------------------------------
    // Theme & Dynamic Fluent Styling (adapts cleanly to Light AND Dark)
    // -------------------------------------------------------------------------

    private Color AccentColor()
    {
        if (Application.Current.Resources.TryGetValue("SystemAccentColor", out var obj) && obj is Color c)
            return c;
        return _isDark ? Color.FromArgb(255, 139, 92, 246) : Color.FromArgb(255, 109, 40, 217);
    }

    private Color AccentTintFill()
    {
        var a = AccentColor();
        return _isDark
            ? Color.FromArgb(52, a.R, a.G, a.B)
            : Color.FromArgb(32, a.R, a.G, a.B);
    }

    private Color AccentTintBorder()
    {
        var a = AccentColor();
        return _isDark
            ? Color.FromArgb(180, a.R, a.G, a.B)
            : Color.FromArgb(150, a.R, a.G, a.B);
    }

    private Color NeutralControlBg() =>
        _isDark ? Color.FromArgb(20, 255, 255, 255) : Color.FromArgb(14, 0, 0, 0);

    private Color NeutralControlBorder() =>
        _isDark ? Color.FromArgb(32, 255, 255, 255) : Color.FromArgb(26, 0, 0, 0);

    private Color PrimaryTextColor() =>
        _isDark ? Color.FromArgb(255, 243, 244, 246) : Color.FromArgb(255, 17, 24, 39);

    private Color MutedTextColor() =>
        _isDark ? Color.FromArgb(255, 161, 161, 170) : Color.FromArgb(255, 107, 114, 128);

    private void ApplyTheme(bool persist)
    {
        RootGrid.RequestedTheme = _isDark ? ElementTheme.Dark : ElementTheme.Light;
        ThemeIcon.Glyph = _isDark ? "\uE706" : "\uE708"; // Sun in dark mode, Moon in light mode

        if (AppWindowTitleBar.IsCustomizationSupported())
        {
            var tb = _appWindow.TitleBar;
            tb.ButtonForegroundColor = _isDark ? Colors.White : Color.FromArgb(255, 24, 24, 27);
            tb.ButtonHoverForegroundColor = tb.ButtonForegroundColor;
            tb.ButtonPressedForegroundColor = tb.ButtonForegroundColor;
            tb.ButtonHoverBackgroundColor = _isDark ? Color.FromArgb(25, 255, 255, 255) : Color.FromArgb(18, 0, 0, 0);
            tb.ButtonPressedBackgroundColor = _isDark ? Color.FromArgb(40, 255, 255, 255) : Color.FromArgb(30, 0, 0, 0);
            tb.ButtonInactiveForegroundColor = _isDark ? Color.FromArgb(150, 255, 255, 255) : Color.FromArgb(140, 24, 24, 27);
        }

        if (persist)
        {
            LuminoConfig.Patch(cfg => cfg["theme"] = _isDark ? "dark" : "light");
        }

        PaintAllDynamicStyles();
    }

    private void PaintAllDynamicStyles()
    {
        PaintNavItems();
        PaintKeepaliveBadge();
        PaintPresets();
        PaintModeAndSpeedButtons();
        PaintAmbiButtons();
        PaintLcdStateBadge();
        PaintOptionsButtons();
        PaintStatusDot();
        UpdateTitleBarPassthroughRegions();
    }

    private void StyleChoiceButton(Button btn, bool selected, bool enabled = true)
    {
        btn.IsEnabled = enabled && !_busy;
        if (!enabled)
        {
            btn.Background = new SolidColorBrush(Colors.Transparent);
            btn.BorderBrush = new SolidColorBrush(_isDark ? Color.FromArgb(18, 255, 255, 255) : Color.FromArgb(16, 0, 0, 0));
            btn.Foreground = new SolidColorBrush(_isDark ? Color.FromArgb(95, 255, 255, 255) : Color.FromArgb(95, 0, 0, 0));
            btn.FontWeight = Microsoft.UI.Text.FontWeights.Normal;
            return;
        }

        if (selected)
        {
            btn.Background = new SolidColorBrush(AccentTintFill());
            btn.BorderBrush = new SolidColorBrush(AccentTintBorder());
            btn.Foreground = new SolidColorBrush(PrimaryTextColor());
            btn.FontWeight = Microsoft.UI.Text.FontWeights.SemiBold;
        }
        else
        {
            btn.Background = new SolidColorBrush(NeutralControlBg());
            btn.BorderBrush = new SolidColorBrush(NeutralControlBorder());
            btn.Foreground = new SolidColorBrush(PrimaryTextColor());
            btn.FontWeight = Microsoft.UI.Text.FontWeights.Normal;
        }
    }

    private void PaintNavItems()
    {
        var accent = AccentColor();
        foreach (var item in _navItems.Values)
        {
            bool selected = item.Key == _selectedKey;
            item.Button.Background = new SolidColorBrush(selected ? AccentTintFill() : Colors.Transparent);
            item.Button.BorderBrush = new SolidColorBrush(selected ? AccentTintBorder() : Colors.Transparent);
            item.Indicator.Background = new SolidColorBrush(selected ? accent : Colors.Transparent);
            item.NameText.Foreground = new SolidColorBrush(PrimaryTextColor());
            item.NameText.FontWeight = selected ? Microsoft.UI.Text.FontWeights.SemiBold : Microsoft.UI.Text.FontWeights.Normal;
            item.BadgeText.Foreground = new SolidColorBrush(MutedTextColor());

            if (item.Key == "lcd")
            {
                bool active = _lcdImageOn || _lcdTempOn;
                item.ColorDot.Fill = new SolidColorBrush(active ? accent : MutedTextColor());
                item.ColorDot.Stroke = new SolidColorBrush(Colors.Transparent);
                item.BadgeText.Text = _lcdTempOn ? "TEMP" : (_lcdImageOn ? "ON" : "OFF");
            }
            else if (item.Key == "screen")
            {
                bool off = _ambiMode == 0;
                if (_ambiColor.HasValue && AmbiglowController.ColorHex.TryGetValue(_ambiColor.Value, out string? chx) && !off)
                {
                    var rgb = LuminoConfig.HexToRgb(chx);
                    item.ColorDot.Fill = new SolidColorBrush(Color.FromArgb(255, rgb.R, rgb.G, rgb.B));
                }
                else
                {
                    var st = _devices["screen"];
                    var rgb = st.Brightness > 0 ? LuminoConfig.HexToRgb(st.Hex) : new RgbColor(110, 110, 120);
                    item.ColorDot.Fill = new SolidColorBrush(Color.FromArgb(255, rgb.R, rgb.G, rgb.B));
                }
                item.ColorDot.Stroke = new SolidColorBrush(_isDark ? Color.FromArgb(70, 255, 255, 255) : Color.FromArgb(70, 0, 0, 0));
                item.BadgeText.Text = _ambiMode.HasValue ? (_ambiMode == 0 ? "OFF" : "OSD") : "OSD";
            }
            else if (_devices.TryGetValue(item.Key, out var dev))
            {
                var rgb = dev.Brightness > 0 ? LuminoConfig.HexToRgb(dev.Hex) : new RgbColor(100, 100, 110);
                item.ColorDot.Fill = new SolidColorBrush(Color.FromArgb(255, rgb.R, rgb.G, rgb.B));
                item.ColorDot.Stroke = new SolidColorBrush(_isDark ? Color.FromArgb(70, 255, 255, 255) : Color.FromArgb(70, 0, 0, 0));
                item.BadgeText.Text = $"{dev.Brightness} %";
            }
        }
    }

    private void PaintKeepaliveBadge()
    {
        if (_keepaliveActive)
        {
            KeepaliveDot.Fill = new SolidColorBrush(_isDark ? Color.FromArgb(255, 74, 222, 128) : Color.FromArgb(255, 22, 163, 74));
            KeepaliveBadge.Background = new SolidColorBrush(_isDark ? Color.FromArgb(30, 74, 222, 128) : Color.FromArgb(24, 22, 163, 74));
            KeepaliveBadge.BorderBrush = new SolidColorBrush(_isDark ? Color.FromArgb(90, 74, 222, 128) : Color.FromArgb(80, 22, 163, 74));
            KeepaliveText.Foreground = new SolidColorBrush(_isDark ? Color.FromArgb(255, 134, 239, 172) : Color.FromArgb(255, 21, 128, 61));
            KeepaliveText.Text = "Fond actif";
        }
        else
        {
            KeepaliveDot.Fill = new SolidColorBrush(MutedTextColor());
            KeepaliveBadge.Background = new SolidColorBrush(NeutralControlBg());
            KeepaliveBadge.BorderBrush = new SolidColorBrush(NeutralControlBorder());
            KeepaliveText.Foreground = new SolidColorBrush(MutedTextColor());
            KeepaliveText.Text = "Fond arrêté";
        }
    }

    private void PaintPresets()
    {
        string currentHex = GetCurrentLedState().Hex.ToLowerInvariant();
        var accent = AccentColor();
        var borderStroke = _isDark ? Color.FromArgb(80, 255, 255, 255) : Color.FromArgb(70, 0, 0, 0);

        foreach (var (hx, _, ring, swatch) in _presetButtons)
        {
            bool sel = hx.Equals(currentHex, StringComparison.OrdinalIgnoreCase);
            ring.BorderBrush = new SolidColorBrush(sel ? accent : Colors.Transparent);
            swatch.BorderBrush = new SolidColorBrush(borderStroke);
        }
    }

    private void PaintModeAndSpeedButtons()
    {
        foreach (var (code, btn) in _modeButtons)
            StyleChoiceButton(btn, code == _effectMode, enabled: true);

        bool speedEnabled = _effectMode != EffectsEngine.ModeStatic && _effectMode != EffectsEngine.ModeTemperature;
        SpeedLabelText.Foreground = new SolidColorBrush(speedEnabled ? PrimaryTextColor() : MutedTextColor());
        foreach (var (spd, btn) in _speedButtons)
            StyleChoiceButton(btn, spd == _effectSpeed, enabled: speedEnabled);
    }

    private void PaintAmbiButtons()
    {
        bool colorEnabled = !_ambiMode.HasValue || AmbiglowController.ColorModes.Contains(_ambiMode.Value);
        bool speedEnabled = !_ambiMode.HasValue || AmbiglowController.SpeedModes.Contains(_ambiMode.Value);

        AmbiColorTitle.Foreground = new SolidColorBrush(colorEnabled ? PrimaryTextColor() : MutedTextColor());
        AmbiSpeedTitle.Foreground = new SolidColorBrush(speedEnabled ? PrimaryTextColor() : MutedTextColor());

        foreach (var (val, btn) in _ambiModeButtons)
            StyleChoiceButton(btn, _ambiMode == val, enabled: true);

        foreach (var (val, btn) in _ambiColorButtons)
            StyleChoiceButton(btn, _ambiColor == val, enabled: colorEnabled);

        foreach (var (val, btn) in _ambiBriButtons)
            StyleChoiceButton(btn, _ambiBri == val, enabled: true);

        foreach (var (val, btn) in _ambiSpeedButtons)
            StyleChoiceButton(btn, _ambiSpeed == val, enabled: speedEnabled);

        if (_ambiUsbAvailable)
        {
            AmbiUsbDot.Fill = new SolidColorBrush(_isDark ? Color.FromArgb(255, 74, 222, 128) : Color.FromArgb(255, 22, 163, 74));
            AmbiUsbBadge.Background = new SolidColorBrush(_isDark ? Color.FromArgb(26, 74, 222, 128) : Color.FromArgb(22, 22, 163, 74));
            AmbiUsbBadge.BorderBrush = new SolidColorBrush(_isDark ? Color.FromArgb(85, 74, 222, 128) : Color.FromArgb(75, 22, 163, 74));
            AmbiUsbText.Foreground = new SolidColorBrush(_isDark ? Color.FromArgb(255, 134, 239, 172) : Color.FromArgb(255, 21, 128, 61));
            AmbiUsbText.Text = "USB ENE connecté";
        }
        else
        {
            AmbiUsbDot.Fill = new SolidColorBrush(MutedTextColor());
            AmbiUsbBadge.Background = new SolidColorBrush(NeutralControlBg());
            AmbiUsbBadge.BorderBrush = new SolidColorBrush(NeutralControlBorder());
            AmbiUsbText.Foreground = new SolidColorBrush(MutedTextColor());
            AmbiUsbText.Text = "DisplayPort DDC";
        }
    }

    private void PaintLcdStateBadge()
    {
        if (_lcdTempOn)
        {
            LcdStateBadgeText.Text = "Température GPU";
            LcdStateBadge.Background = new SolidColorBrush(AccentTintFill());
            LcdStateBadge.BorderBrush = new SolidColorBrush(AccentTintBorder());
            LcdStateBadgeText.Foreground = new SolidColorBrush(PrimaryTextColor());
        }
        else if (_lcdImageOn)
        {
            LcdStateBadgeText.Text = _lcdPreviewFrames.Count > 1 ? "GIF animé actif" : "Image active";
            LcdStateBadge.Background = new SolidColorBrush(_isDark ? Color.FromArgb(28, 74, 222, 128) : Color.FromArgb(22, 22, 163, 74));
            LcdStateBadge.BorderBrush = new SolidColorBrush(_isDark ? Color.FromArgb(90, 74, 222, 128) : Color.FromArgb(75, 22, 163, 74));
            LcdStateBadgeText.Foreground = new SolidColorBrush(_isDark ? Color.FromArgb(255, 134, 239, 172) : Color.FromArgb(255, 21, 128, 61));
        }
        else
        {
            LcdStateBadgeText.Text = "Fond matériel";
            LcdStateBadge.Background = new SolidColorBrush(NeutralControlBg());
            LcdStateBadge.BorderBrush = new SolidColorBrush(NeutralControlBorder());
            LcdStateBadgeText.Foreground = new SolidColorBrush(MutedTextColor());
        }
    }

    private void PaintOptionsButtons()
    {
        OptionsButton.Background = new SolidColorBrush(_optionsOpen ? AccentTintFill() : Colors.Transparent);
        OptionsButton.BorderBrush = new SolidColorBrush(_optionsOpen ? AccentTintBorder() : Colors.Transparent);

        StyleChoiceButton(OptThemeLightBtn, !_isDark, enabled: true);
        StyleChoiceButton(OptThemeDarkBtn, _isDark, enabled: true);
    }

    private void PaintStatusDot()
    {
        StatusDot.Fill = new SolidColorBrush(_statusKind switch
        {
            "ok" => _isDark ? Color.FromArgb(255, 74, 222, 128) : Color.FromArgb(255, 22, 163, 74),
            "err" => _isDark ? Color.FromArgb(255, 248, 113, 113) : Color.FromArgb(255, 185, 28, 28),
            _ => AccentColor()
        });
    }

    // -------------------------------------------------------------------------
    // Navigation & Editor Sync
    // -------------------------------------------------------------------------

    private DeviceState GetCurrentLedState()
    {
        return _devices.TryGetValue(_selectedKey, out var st) ? st : _devices["all"];
    }

    private void SelectNav(string key)
    {
        _selectedKey = key;

        if (_optionsOpen)
            SetOptionsOpen(false);

        LedEditorView.Visibility = key is "screen" or "lcd" ? Visibility.Collapsed : Visibility.Visible;
        AmbiglowEditorView.Visibility = key == "screen" ? Visibility.Visible : Visibility.Collapsed;
        LcdEditorView.Visibility = key == "lcd" ? Visibility.Visible : Visibility.Collapsed;

        if (key == "screen")
        {
            _lcdGifTimer?.Stop();
            PaintAmbiButtons();
            if (!_ambiLoadedOnce)
                RefreshAmbiglowOsdAsync();
        }
        else if (key == "lcd")
        {
            LoadLcdEditorUi();
        }
        else
        {
            _lcdGifTimer?.Stop();
            LoadLedEditorUi();
        }

        PaintNavItems();
    }

    private void LoadLedEditorUi()
    {
        _updatingUi = true;
        try
        {
            if (PageMeta.TryGetValue(_selectedKey, out var meta))
            {
                LedTitleText.Text = meta.Title;
                LedSubtitleText.Text = meta.Subtitle;
                ApplyButtonText.Text = meta.ApplyLabel;
            }

            DeviceOffButton.Visibility = _selectedKey == "all" ? Visibility.Collapsed : Visibility.Visible;

            var st = GetCurrentLedState();
            var rgb = LuminoConfig.HexToRgb(st.Hex);
            HexInputBox.Text = st.Hex;
            CurrentColorSwatch.Background = new SolidColorBrush(Color.FromArgb(255, rgb.R, rgb.G, rgb.B));
            FluentColorPicker.Color = Color.FromArgb(255, rgb.R, rgb.G, rgb.B);

            BrightnessSlider.Value = st.Brightness;
            BrightnessValueText.Text = $"{st.Brightness} %";
        }
        finally
        {
            _updatingUi = false;
        }

        PaintPresets();
        PaintModeAndSpeedButtons();
        UpdateLedPreviewStrip();
    }

    private void SetEditorHex(string hx)
    {
        if (!LuminoConfig.TryParseHex(hx, out var rgb))
            return;

        string norm = rgb.ToHex();
        var st = GetCurrentLedState();
        st.Hex = norm;

        _updatingUi = true;
        try
        {
            HexInputBox.Text = norm;
            CurrentColorSwatch.Background = new SolidColorBrush(Color.FromArgb(255, rgb.R, rgb.G, rgb.B));
            FluentColorPicker.Color = Color.FromArgb(255, rgb.R, rgb.G, rgb.B);
        }
        finally
        {
            _updatingUi = false;
        }

        PaintPresets();
        PaintNavItems();
        UpdateLedPreviewStrip();
    }

    private void SetEffectMode(string code)
    {
        _effectMode = EffectsEngine.NormalizeMode(code);
        PaintModeAndSpeedButtons();
        UpdateLedPreviewStrip();
    }

    private void SetEffectSpeed(int speed)
    {
        _effectSpeed = Math.Clamp(speed, 1, 10);
        PaintModeAndSpeedButtons();
        UpdateLedPreviewStrip();
    }

    private void UpdateLedPreviewStrip()
    {
        if (LedEditorView.Visibility != Visibility.Visible)
            return;

        var st = GetCurrentLedState();
        var rgb = LuminoConfig.HexToRgb(st.Hex);
        double t = _animClock.Elapsed.TotalSeconds;
        var cols = EffectsEngine.Render(PreviewSegments, _effectMode, t, rgb, st.Brightness, _effectSpeed, tempC: 48.0);

        for (int i = 0; i < PreviewSegments; i++)
        {
            var c = cols[i];
            if (st.Brightness <= 0)
            {
                _previewBars[i].Background = new SolidColorBrush(_isDark ? Color.FromArgb(255, 34, 38, 46) : Color.FromArgb(255, 224, 228, 235));
            }
            else
            {
                _previewBars[i].Background = new SolidColorBrush(Color.FromArgb(255, c.R, c.G, c.B));
            }
        }
    }

    // -------------------------------------------------------------------------
    // LED Editor Events
    // -------------------------------------------------------------------------

    private void BrightnessSlider_ValueChanged(object sender, RangeBaseValueChangedEventArgs e)
    {
        if (_updatingUi)
            return;

        int val = (int)Math.Round(e.NewValue);
        GetCurrentLedState().Brightness = val;
        BrightnessValueText.Text = $"{val} %";
        PaintNavItems();
        UpdateLedPreviewStrip();
    }

    private void HexInputBox_KeyDown(object sender, KeyRoutedEventArgs e)
    {
        if (e.Key == VirtualKey.Enter)
        {
            CommitHexInput();
            e.Handled = true;
        }
    }

    private void HexInputBox_LostFocus(object sender, RoutedEventArgs e) => CommitHexInput();

    private void CommitHexInput()
    {
        if (LuminoConfig.TryParseHex(HexInputBox.Text, out var rgb))
        {
            SetEditorHex(rgb.ToHex());
        }
        else
        {
            HexInputBox.Text = GetCurrentLedState().Hex;
        }
    }

    private void FluentColorPicker_ColorChanged(ColorPicker sender, ColorChangedEventArgs args)
    {
        if (_updatingUi)
            return;
        var c = args.NewColor;
        SetEditorHex(new RgbColor(c.R, c.G, c.B).ToHex());
    }

    private void ColorPickerConfirm_Click(object sender, RoutedEventArgs e) => ColorPickerFlyout.Hide();

    private void ApplyButton_Click(object sender, RoutedEventArgs e)
    {
        CommitHexInput();
        var st = GetCurrentLedState();
        var rgb = LuminoConfig.HexToRgb(st.Hex);
        int bri = st.Brightness;
        string key = _selectedKey;
        string mode = _effectMode;
        int speed = _effectSpeed;

        RunBusyAsync($"Application sur {NavDevices.First(d => d.Key == key).Label}…", () =>
        {
            return key == "all"
                ? LuminoController.ApplyAll(rgb, bri, mode, speed)
                : LuminoController.ApplySingleDevice(key, rgb, bri, mode, speed);
        }, res =>
        {
            if (key == "all" && res.Ok)
            {
                foreach (var k in new[] { "gpu", "cm", "corsair", "fans", "screen" })
                {
                    _devices[k].Hex = rgb.ToHex();
                    _devices[k].Brightness = bri;
                }
            }
            RefreshKeepaliveIndicator();
            PaintNavItems();
        });
    }

    private void DeviceOffButton_Click(object sender, RoutedEventArgs e)
    {
        string key = _selectedKey;
        if (key == "all")
        {
            AllOffButton_Click(sender, e);
            return;
        }

        var st = GetCurrentLedState();
        var rgb = LuminoConfig.HexToRgb(st.Hex);
        string mode = _effectMode;
        int speed = _effectSpeed;

        RunBusyAsync($"Extinction de {NavDevices.First(d => d.Key == key).Label}…",
            () => LuminoController.TurnOffSingleDevice(key, rgb, mode, speed),
            res =>
            {
                st.Brightness = 0;
                LoadLedEditorUi();
                RefreshKeepaliveIndicator();
                PaintNavItems();
            });
    }

    private void AllOffButton_Click(object sender, RoutedEventArgs e)
    {
        var gpuRgb = LuminoConfig.HexToRgb(_devices["gpu"].Hex);
        var cmRgb = LuminoConfig.HexToRgb(_devices["cm"].Hex);
        var coRgb = LuminoConfig.HexToRgb(_devices["corsair"].Hex);
        var fanRgb = LuminoConfig.HexToRgb(_devices["fans"].Hex);
        var scRgb = LuminoConfig.HexToRgb(_devices["screen"].Hex);
        string mode = _effectMode;
        int speed = _effectSpeed;

        RunBusyAsync("Extinction de tous les périphériques…",
            () => LuminoController.TurnOffAll(gpuRgb, cmRgb, coRgb, fanRgb, scRgb, mode, speed),
            _ =>
            {
                foreach (var d in _devices.Values)
                    d.Brightness = 0;
                if (_selectedKey is not ("screen" or "lcd"))
                    LoadLedEditorUi();
                RefreshKeepaliveIndicator();
                PaintNavItems();
            });
    }

    // -------------------------------------------------------------------------
    // Ambiglow OSD Events
    // -------------------------------------------------------------------------

    private void AmbiRefreshButton_Click(object sender, RoutedEventArgs e) => RefreshAmbiglowOsdAsync();

    private void RefreshAmbiglowOsdAsync()
    {
        _ambiLoadedOnce = true;
        RunBusyWithDataAsync("Lecture de l'état Ambiglow (DisplayPort DDC)…", () =>
        {
            var state = AmbiglowController.ReadAllOsd();
            return (state, state.Mode.HasValue
                ? new OperationResult(true, "État Ambiglow synchronisé avec l'écran.")
                : new OperationResult(false, "Écran Philips Evnia non détecté sur le DisplayPort."));
        }, tuple =>
        {
            var s = tuple.Data;
            _ambiMode = s.Mode;
            _ambiColor = s.Color;
            _ambiBri = s.Brightness;
            _ambiSpeed = s.Speed;
            _ambiUsbAvailable = s.UsbAvailable;
            PaintAmbiButtons();
            PaintNavItems();
        });
    }

    private void SetAmbiModeAsync(int val)
    {
        string label = AmbiglowController.LightModes.First(m => m.Value == val).Label;
        RunBusyWithDataAsync($"Ambiglow → {label}…", () =>
        {
            try
            {
                int got = AmbiglowController.SetLightMode(val);
                return (got, new OperationResult(true, $"Ambiglow : {label}"));
            }
            catch (Exception ex)
            {
                return (-1, new OperationResult(false, $"Ambiglow : {ex.Message}"));
            }
        }, tuple =>
        {
            if (tuple.Data >= 0)
            {
                _ambiMode = tuple.Data;
                PaintAmbiButtons();
                PaintNavItems();
            }
        });
    }

    private void SetAmbiColorAsync(int val)
    {
        string label = AmbiglowController.Colors.First(c => c.Value == val).Label;
        RunBusyWithDataAsync($"Ambiglow couleur → {label}…", () =>
        {
            try
            {
                int got = AmbiglowController.SetColor(val);
                return (got, new OperationResult(true, $"Ambiglow couleur : {label}"));
            }
            catch (Exception ex)
            {
                return (-1, new OperationResult(false, $"Ambiglow : {ex.Message}"));
            }
        }, tuple =>
        {
            if (tuple.Data >= 0)
            {
                _ambiColor = tuple.Data;
                PaintAmbiButtons();
                PaintNavItems();
            }
        });
    }

    private void SetAmbiBrightnessAsync(int val)
    {
        string label = AmbiglowController.BrightnessLevels.First(b => b.Value == val).Label;
        RunBusyWithDataAsync($"Ambiglow luminosité → {label}…", () =>
        {
            try
            {
                int got = AmbiglowController.SetBrightness(val);
                return (got, new OperationResult(true, $"Ambiglow luminosité : {label}"));
            }
            catch (Exception ex)
            {
                return (-1, new OperationResult(false, $"Ambiglow : {ex.Message}"));
            }
        }, tuple =>
        {
            if (tuple.Data >= 0)
            {
                _ambiBri = tuple.Data;
                PaintAmbiButtons();
            }
        });
    }

    private void SetAmbiSpeedAsync(int val)
    {
        string label = AmbiglowController.SpeedLevels.First(s => s.Value == val).Label;
        RunBusyWithDataAsync($"Ambiglow vitesse → {label}…", () =>
        {
            try
            {
                int got = AmbiglowController.SetSpeed(val);
                return (got, new OperationResult(true, $"Ambiglow vitesse : {label}"));
            }
            catch (Exception ex)
            {
                return (-1, new OperationResult(false, $"Ambiglow : {ex.Message}"));
            }
        }, tuple =>
        {
            if (tuple.Data >= 0)
            {
                _ambiSpeed = tuple.Data;
                PaintAmbiButtons();
            }
        });
    }

    // -------------------------------------------------------------------------
    // LCD Editor & Live Circular Preview
    // -------------------------------------------------------------------------

    private void LoadLcdEditorUi()
    {
        _updatingUi = true;
        try
        {
            LcdScaleSlider.Value = _lcdImageScale;
            LcdScaleValueText.Text = $"{_lcdImageScale} %";
            LcdBriSlider.Value = _lcdBrightness;
            LcdBriValueText.Text = $"{_lcdBrightness} %";
            LcdTempToggle.IsOn = _lcdTempOn;
            LcdFileNameText.Text = string.IsNullOrWhiteSpace(_lcdImagePath)
                ? "Aucun fichier sélectionné"
                : Path.GetFileName(_lcdImagePath);
        }
        finally
        {
            _updatingUi = false;
        }

        PaintLcdStateBadge();
        RebuildLcdPreviewAsync();
    }

    private async void LcdPickFileButton_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            var picker = new FileOpenPicker();
            InitializeWithWindow.Initialize(picker, _hwnd);
            picker.ViewMode = PickerViewMode.Thumbnail;
            picker.SuggestedStartLocation = PickerLocationId.PicturesLibrary;
            picker.FileTypeFilter.Add(".gif");
            picker.FileTypeFilter.Add(".png");
            picker.FileTypeFilter.Add(".jpg");
            picker.FileTypeFilter.Add(".jpeg");
            picker.FileTypeFilter.Add(".bmp");
            picker.FileTypeFilter.Add(".webp");

            var file = await picker.PickSingleFileAsync();
            if (file is null)
                return;

            _lcdImagePath = file.Path;
            LcdFileNameText.Text = Path.GetFileName(_lcdImagePath);
            RebuildLcdPreviewAsync();
        }
        catch (Exception ex)
        {
            SetStatus($"Sélection d'image impossible : {ex.Message}", "err");
        }
    }

    private void LcdScaleSlider_ValueChanged(object sender, RangeBaseValueChangedEventArgs e)
    {
        if (_updatingUi)
            return;
        _lcdImageScale = (int)Math.Round(e.NewValue);
        LcdScaleValueText.Text = $"{_lcdImageScale} %";
    }

    private void LcdScaleSlider_Commit(object sender, PointerRoutedEventArgs e)
    {
        RebuildLcdPreviewAsync();
        if (_lcdImageOn && !string.IsNullOrWhiteSpace(_lcdImagePath))
        {
            LuminoConfig.Patch(cfg => cfg["lcd_image_scale"] = _lcdImageScale);
            KeepaliveWorker.NotifyConfigChanged();
        }
    }

    private void LcdBriSlider_ValueChanged(object sender, RangeBaseValueChangedEventArgs e)
    {
        if (_updatingUi)
            return;
        _lcdBrightness = (int)Math.Round(e.NewValue);
        LcdBriValueText.Text = $"{_lcdBrightness} %";
    }

    private void LcdBriSlider_Commit(object sender, PointerRoutedEventArgs e)
    {
        LuminoConfig.Patch(cfg => cfg["lcd_brightness"] = _lcdBrightness);
        KeepaliveWorker.NotifyConfigChanged();
    }

    private void LcdApplyButton_Click(object sender, RoutedEventArgs e)
    {
        if (string.IsNullOrWhiteSpace(_lcdImagePath) || !File.Exists(_lcdImagePath))
        {
            SetStatus("Choisis d'abord une image ou un GIF valide.", "err");
            return;
        }

        string path = _lcdImagePath;
        int scale = _lcdImageScale;
        int bri = _lcdBrightness;

        RunBusyAsync("Envoi de l'image sur l'écran LCD…",
            () => LuminoController.ApplyLcdImage(path, scale, bri),
            res =>
            {
                if (res.Ok)
                {
                    _lcdImageOn = true;
                    _lcdTempOn = false;
                    _lcdImagePath = LuminoConfig.GetString(LuminoConfig.LoadRaw(), "lcd_image_path", path);
                    _updatingUi = true;
                    LcdTempToggle.IsOn = false;
                    OptLcdTempToggle.IsOn = false;
                    _updatingUi = false;
                }
                RefreshKeepaliveIndicator();
                PaintLcdStateBadge();
                PaintNavItems();
            });
    }

    private void LcdRestoreButton_Click(object sender, RoutedEventArgs e)
    {
        RunBusyAsync("Retour au fond matériel du LCD…",
            LuminoController.OffLcdImage,
            res =>
            {
                if (res.Ok)
                {
                    _lcdImageOn = false;
                    _lcdTempOn = false;
                    _updatingUi = true;
                    LcdTempToggle.IsOn = false;
                    OptLcdTempToggle.IsOn = false;
                    _updatingUi = false;
                }
                PaintLcdStateBadge();
                PaintNavItems();
                RebuildLcdPreviewAsync();
            });
    }

    private void LcdTempToggle_Toggled(object sender, RoutedEventArgs e)
    {
        if (_updatingUi)
            return;
        ApplyLcdTempToggle(LcdTempToggle.IsOn);
    }

    private void OptLcdTempToggle_Toggled(object sender, RoutedEventArgs e)
    {
        if (_updatingUi)
            return;
        ApplyLcdTempToggle(OptLcdTempToggle.IsOn);
    }

    private void ApplyLcdTempToggle(bool want)
    {
        RunBusyAsync(want ? "Activation de la température GPU sur le LCD…" : "Désactivation de la température GPU…",
            () => LuminoController.SetLcdTempEnabled(want),
            res =>
            {
                if (res.Ok)
                {
                    _lcdTempOn = want;
                    if (want) _lcdImageOn = false;
                }
                _updatingUi = true;
                LcdTempToggle.IsOn = _lcdTempOn;
                OptLcdTempToggle.IsOn = _lcdTempOn;
                _updatingUi = false;
                RefreshKeepaliveIndicator();
                PaintLcdStateBadge();
                PaintNavItems();
                if (_selectedKey == "lcd")
                    RebuildLcdPreviewAsync();
            });
    }

    private async void RebuildLcdPreviewAsync()
    {
        _lcdGifTimer?.Stop();

        bool showTemp = _lcdTempOn;
        string path = _lcdImagePath;
        int scale = _lcdImageScale;

        var frames = await Task.Run(() =>
        {
            var result = new List<(byte[] Bgra, int DelayMs)>();
            try
            {
                if (showTemp)
                {
                    byte[] jpeg = CorsairLcdScreen.RenderTempJpeg(44, LcdPreviewPx);
                    using var ms = new MemoryStream(jpeg);
                    using var bmp = new Bitmap(ms);
                    result.Add((BitmapToBgra(bmp), 0));
                    return result;
                }

                if (string.IsNullOrWhiteSpace(path) || !File.Exists(path))
                    return result;

                using var img = System.Drawing.Image.FromFile(path);
                var dim = new FrameDimension(img.FrameDimensionsList[0]);
                int count = Math.Max(1, img.GetFrameCount(dim));
                bool animated = count > 1 && img.RawFormat.Equals(ImageFormat.Gif);

                byte[]? delayBytes = null;
                if (animated && img.PropertyIdList.Contains(0x5100))
                {
                    try { delayBytes = img.GetPropertyItem(0x5100)?.Value; } catch { }
                }

                int maxPreviewFrames = Math.Min(count, 120);
                for (int i = 0; i < maxPreviewFrames; i++)
                {
                    if (animated)
                        img.SelectActiveFrame(dim, i);
                    int durMs = 100;
                    if (delayBytes is not null && (i * 4 + 3) < delayBytes.Length)
                    {
                        int cs = BitConverter.ToInt32(delayBytes, i * 4);
                        durMs = cs > 0 ? cs * 10 : 100;
                    }
                    durMs = Math.Max(30, durMs);

                    using var composed = CorsairLcdScreen.ComposeBitmap(img, scale, LcdPreviewPx);
                    result.Add((BitmapToBgra(composed), animated ? durMs : 0));
                }
            }
            catch
            {
                // Ignore preview decode error
            }
            return result;
        });

        _lcdPreviewFrames = frames;
        _lcdPreviewFrameIndex = 0;

        if (_lcdPreviewFrames.Count == 0)
        {
            LcdPreviewImage.Source = null;
            LcdPreviewPlaceholder.Visibility = Visibility.Visible;
            return;
        }

        LcdPreviewPlaceholder.Visibility = Visibility.Collapsed;
        RenderLcdPreviewFrame(0);

        if (_lcdPreviewFrames.Count > 1 && _selectedKey == "lcd")
        {
            _lcdGifTimer!.Interval = TimeSpan.FromMilliseconds(_lcdPreviewFrames[0].DelayMs);
            _lcdGifTimer.Start();
        }

        PaintLcdStateBadge();
    }

    private void LcdGifTimer_Tick(object? sender, object e)
    {
        if (_lcdPreviewFrames.Count <= 1 || _selectedKey != "lcd")
        {
            _lcdGifTimer?.Stop();
            return;
        }

        _lcdPreviewFrameIndex = (_lcdPreviewFrameIndex + 1) % _lcdPreviewFrames.Count;
        RenderLcdPreviewFrame(_lcdPreviewFrameIndex);
        _lcdGifTimer!.Interval = TimeSpan.FromMilliseconds(Math.Max(30, _lcdPreviewFrames[_lcdPreviewFrameIndex].DelayMs));
    }

    private void RenderLcdPreviewFrame(int index)
    {
        if (index < 0 || index >= _lcdPreviewFrames.Count)
            return;

        _lcdBitmap ??= new WriteableBitmap(LcdPreviewPx, LcdPreviewPx);
        byte[] bgra = _lcdPreviewFrames[index].Bgra;
        using (var stream = _lcdBitmap.PixelBuffer.AsStream())
        {
            stream.Write(bgra, 0, bgra.Length);
        }
        _lcdBitmap.Invalidate();
        LcdPreviewImage.Source = _lcdBitmap;
    }

    private static byte[] BitmapToBgra(Bitmap src)
    {
        using var bmp32 = new Bitmap(src.Width, src.Height, PixelFormat.Format32bppPArgb);
        using (var g = Graphics.FromImage(bmp32))
            g.DrawImage(src, 0, 0, src.Width, src.Height);

        var rect = new System.Drawing.Rectangle(0, 0, bmp32.Width, bmp32.Height);
        var data = bmp32.LockBits(rect, ImageLockMode.ReadOnly, PixelFormat.Format32bppPArgb);
        try
        {
            byte[] bytes = new byte[Math.Abs(data.Stride) * bmp32.Height];
            Marshal.Copy(data.Scan0, bytes, 0, bytes.Length);
            return bytes;
        }
        finally
        {
            bmp32.UnlockBits(data);
        }
    }

    // -------------------------------------------------------------------------
    // Options Drawer & Header Controls
    // -------------------------------------------------------------------------

    private void ThemeButton_Click(object sender, RoutedEventArgs e)
    {
        _isDark = !_isDark;
        ApplyTheme(persist: true);
    }

    private void OptionsButton_Click(object sender, RoutedEventArgs e) => SetOptionsOpen(!_optionsOpen);
    private void OptionsBackButton_Click(object sender, RoutedEventArgs e) => SetOptionsOpen(false);

    private void SetOptionsOpen(bool open)
    {
        _optionsOpen = open;
        OptionsView.Visibility = open ? Visibility.Visible : Visibility.Collapsed;
        MainWorkspace.Visibility = open ? Visibility.Collapsed : Visibility.Visible;
        if (open)
            PopulateOptionsState();
        PaintOptionsButtons();
    }

    private void PopulateOptionsState()
    {
        _updatingUi = true;
        try
        {
            OptBootToggle.IsOn = StartupManager.IsEnabled();
            OptLcdTempToggle.IsOn = _lcdTempOn;
            OptStorageModeText.Text = AppPaths.IsPortable
                ? "Mode portable (configuration locale)"
                : "Mode installé (données dans AppData)";
            OptDataPathText.Text = AppPaths.DataDir;
            OptLogText.Text = LuminoLogger.ReadTail(80);
        }
        finally
        {
            _updatingUi = false;
        }
        RefreshKeepaliveIndicator();
        PaintOptionsButtons();
    }

    private void OptBootToggle_Toggled(object sender, RoutedEventArgs e)
    {
        if (_updatingUi)
            return;

        try
        {
            StartupManager.SetEnabled(OptBootToggle.IsOn);
            SetStatus(OptBootToggle.IsOn
                ? "Lancement au démarrage de Windows activé."
                : "Lancement au démarrage de Windows désactivé.", "ok");
        }
        catch (Exception ex)
        {
            SetStatus($"Démarrage Windows : {ex.Message}", "err");
        }
    }

    private void OptStartKeepaliveButton_Click(object sender, RoutedEventArgs e)
    {
        RunBusyAsync("Démarrage du processus de fond Corsair…", () =>
        {
            bool ok = KeepaliveWorker.EnsureRunning();
            return ok
                ? new OperationResult(true, "Processus de fond démarré.")
                : new OperationResult(false, "Impossible de démarrer le processus de fond.");
        }, _ => RefreshKeepaliveIndicator());
    }

    private void OptStopKeepaliveButton_Click(object sender, RoutedEventArgs e)
    {
        RunBusyAsync("Arrêt propre du processus de fond Corsair…", () =>
        {
            bool stopped = KeepaliveWorker.StopRunning();
            return new OperationResult(true, stopped
                ? "Processus de fond arrêté (GPU et carte mère revenus au statique)."
                : "Le processus de fond était déjà arrêté.");
        }, _ => RefreshKeepaliveIndicator());
    }

    private void OptThemeLightBtn_Click(object sender, RoutedEventArgs e)
    {
        _isDark = false;
        ApplyTheme(persist: true);
    }

    private void OptThemeDarkBtn_Click(object sender, RoutedEventArgs e)
    {
        _isDark = true;
        ApplyTheme(persist: true);
    }

    private void OptOpenDataDirButton_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            Directory.CreateDirectory(AppPaths.DataDir);
            Process.Start(new ProcessStartInfo("explorer.exe", $"\"{AppPaths.DataDir}\"") { UseShellExecute = true });
        }
        catch { }
    }

    private void OptRefreshLogButton_Click(object sender, RoutedEventArgs e)
    {
        OptLogText.Text = LuminoLogger.ReadTail(80);
    }

    private void RootGrid_KeyDown(object sender, KeyRoutedEventArgs e)
    {
        if (e.Key == VirtualKey.Escape && _optionsOpen)
        {
            SetOptionsOpen(false);
            e.Handled = true;
        }
    }

    // -------------------------------------------------------------------------
    // Status & Busy Helpers
    // -------------------------------------------------------------------------

    private void RefreshKeepaliveIndicator()
    {
        _keepaliveActive = KeepaliveWorker.IsRunning();
        PaintKeepaliveBadge();
        OptKeepaliveStatusText.Text = _keepaliveActive
            ? "État : actif (maintient le Hub LINK, les effets synchronisés et le LCD)"
            : "État : arrêté";
        OptStartKeepaliveButton.IsEnabled = !_keepaliveActive && !_busy;
        OptStopKeepaliveButton.IsEnabled = _keepaliveActive && !_busy;
    }

    private void SetStatus(string message, string kind = "info")
    {
        _statusKind = kind;
        StatusText.Text = message;
        StatusDot.Visibility = Visibility.Visible;
        BusyRing.IsActive = false;
        BusyRing.Visibility = Visibility.Collapsed;
        PaintStatusDot();
    }

    private void SetBusy(bool busy, string? message = null)
    {
        _busy = busy;
        ApplyButton.IsEnabled = !busy;
        DeviceOffButton.IsEnabled = !busy;
        AllOffButton.IsEnabled = !busy;
        LcdApplyButton.IsEnabled = !busy;
        LcdRestoreButton.IsEnabled = !busy;
        AmbiRefreshButton.IsEnabled = !busy;
        PaintModeAndSpeedButtons();
        PaintAmbiButtons();

        if (busy)
        {
            StatusDot.Visibility = Visibility.Collapsed;
            BusyRing.Visibility = Visibility.Visible;
            BusyRing.IsActive = true;
            if (message is not null)
                StatusText.Text = message;
        }
        else
        {
            BusyRing.IsActive = false;
            BusyRing.Visibility = Visibility.Collapsed;
            StatusDot.Visibility = Visibility.Visible;
            PaintStatusDot();
        }
    }

    private async void RunBusyAsync(string busyMessage, Func<OperationResult> work, Action<OperationResult>? onDone = null)
    {
        if (_busy)
            return;

        SetBusy(true, busyMessage);
        try
        {
            var res = await Task.Run(work);
            SetBusy(false);
            SetStatus(res.Message, res.Ok ? "ok" : "err");
            onDone?.Invoke(res);
        }
        catch (Exception ex)
        {
            SetBusy(false);
            SetStatus(ex.Message, "err");
        }
    }

    private async void RunBusyWithDataAsync<T>(string busyMessage, Func<(T Data, OperationResult Result)> work, Action<(T Data, OperationResult Result)> onDone)
    {
        if (_busy)
            return;

        SetBusy(true, busyMessage);
        try
        {
            var tuple = await Task.Run(work);
            SetBusy(false);
            SetStatus(tuple.Result.Message, tuple.Result.Ok ? "ok" : "err");
            onDone(tuple);
        }
        catch (Exception ex)
        {
            SetBusy(false);
            SetStatus(ex.Message, "err");
        }
    }

    // -------------------------------------------------------------------------
    // Win32 Interop
    // -------------------------------------------------------------------------

    private const int SW_RESTORE = 9;

    [DllImport("user32.dll")]
    private static extern uint GetDpiForWindow(IntPtr hwnd);

    [DllImport("user32.dll")]
    private static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

    [DllImport("user32.dll")]
    private static extern bool SetForegroundWindow(IntPtr hWnd);
}
