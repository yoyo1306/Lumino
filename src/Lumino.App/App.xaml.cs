using Lumino.Core;
using Microsoft.UI.Xaml;

namespace Lumino.App;

public partial class App : Application
{
    private MainWindow? _window;

    public App()
    {
        InitializeComponent();
        UnhandledException += (_, e) =>
        {
            LuminoLogger.Log($"UI UNHANDLED: {e.Message} | {e.Exception}");
        };
    }

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        try
        {
            _window = new MainWindow();
            _window.Activate();
        }
        catch (Exception ex)
        {
            LuminoLogger.Log($"UI LAUNCH ERROR: {ex}");
            throw;
        }
    }
}
