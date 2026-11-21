using Lumino.Core;
using Lumino.Core.Services;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;

namespace Lumino.App;

public static class Program
{
    [STAThread]
    public static int Main(string[] args)
    {
        // Headless CLI modes: execute directly without initializing WinUI 3 / XAML
        if (args.Length > 0)
        {
            string first = args[0];
            if (first.Equals("--boot", StringComparison.OrdinalIgnoreCase))
                return BootRunner.Run(args[1..]);

            if (first.Equals("--corsair-keepalive", StringComparison.OrdinalIgnoreCase))
                return KeepaliveWorker.RunLoop();

            if (first.Equals("--corsair-stop", StringComparison.OrdinalIgnoreCase))
            {
                bool stopped = KeepaliveWorker.StopRunning();
                try { Console.WriteLine($"stop:{stopped}"); } catch { }
                return 0;
            }
        }

        // Single-instance guard for the GUI window
        using var mutex = new Mutex(initiallyOwned: true, BuildInfo.SingleInstanceMutexName, out bool createdNew);
        if (!createdNew)
        {
            HardwareHold.TrySendPipeCommand(BuildInfo.ShowWindowPipeName, "SHOW", 500);
            return 0;
        }

        WinRT.ComWrappersSupport.InitializeComWrappers();
        Application.Start(p =>
        {
            var context = new DispatcherQueueSynchronizationContext(DispatcherQueue.GetForCurrentThread());
            SynchronizationContext.SetSynchronizationContext(context);
            _ = new App();
        });

        GC.KeepAlive(mutex);
        return 0;
    }
}
