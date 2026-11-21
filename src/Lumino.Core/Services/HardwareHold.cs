using System.IO.Pipes;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;

namespace Lumino.Core.Services;

/// <summary>
/// Coordinates temporary hardware access (GPU I2C, Aura HID, DisplayPort DDC) between the UI
/// and the background keepalive worker using a file hold + Named Pipe wakeup.
/// </summary>
public static class HardwareHold
{
    private static int _localHoldDepth;

    public static IDisposable Acquire()
    {
        Interlocked.Increment(ref _localHoldDepth);
        try
        {
            Directory.CreateDirectory(AppPaths.DataDir);
            File.WriteAllText(AppPaths.HwHoldPath, Environment.ProcessId.ToString(), Encoding.UTF8);
        }
        catch
        {
            // Ignore hold file error
        }

        return new HoldLease();
    }

    public static bool IsHeld()
    {
        if (Volatile.Read(ref _localHoldDepth) > 0)
            return true;

        try
        {
            if (!File.Exists(AppPaths.HwHoldPath))
                return false;

            var age = DateTime.UtcNow - File.GetLastWriteTimeUtc(AppPaths.HwHoldPath);
            return age.TotalSeconds < 8.0;
        }
        catch
        {
            return false;
        }
    }

    public static void PublishIdle(double mtime)
    {
        try
        {
            Directory.CreateDirectory(AppPaths.DataDir);
            string tmp = AppPaths.SyncIdlePath + ".tmp";
            File.WriteAllText(tmp, mtime.ToString("R", System.Globalization.CultureInfo.InvariantCulture), Encoding.UTF8);
            File.Move(tmp, AppPaths.SyncIdlePath, overwrite: true);
        }
        catch
        {
            // Ignore
        }
    }

    public static void ClearIdle()
    {
        try
        {
            if (File.Exists(AppPaths.SyncIdlePath))
                File.Delete(AppPaths.SyncIdlePath);
        }
        catch
        {
            // Ignore
        }
    }

    public static double? ReadIdleMtime()
    {
        try
        {
            if (!File.Exists(AppPaths.SyncIdlePath))
                return null;
            string s = File.ReadAllText(AppPaths.SyncIdlePath, Encoding.UTF8).Trim();
            return double.TryParse(s, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out double v)
                ? v
                : null;
        }
        catch
        {
            return null;
        }
    }

    public static PipeSecurity CreateAuthenticatedUserPipeSecurity()
    {
        var ps = new PipeSecurity();
        var authUsers = new SecurityIdentifier(WellKnownSidType.AuthenticatedUserSid, null);
        ps.AddAccessRule(new PipeAccessRule(authUsers, PipeAccessRights.ReadWrite, AccessControlType.Allow));
        var currentUser = WindowsIdentity.GetCurrent().User;
        if (currentUser is not null)
            ps.AddAccessRule(new PipeAccessRule(currentUser, PipeAccessRights.FullControl, AccessControlType.Allow));
        return ps;
    }

    public static bool TrySendPipeCommand(string pipeName, string command, int timeoutMs = 300)
    {
        try
        {
            using var client = new NamedPipeClientStream(".", pipeName, PipeDirection.Out, PipeOptions.None);
            client.Connect(timeoutMs);
            byte[] bytes = Encoding.UTF8.GetBytes(command + "\n");
            client.Write(bytes, 0, bytes.Length);
            client.Flush();
            return true;
        }
        catch
        {
            return false;
        }
    }

    private sealed class HoldLease : IDisposable
    {
        private int _disposed;

        public void Dispose()
        {
            if (Interlocked.Exchange(ref _disposed, 1) != 0)
                return;

            if (Interlocked.Decrement(ref _localHoldDepth) <= 0)
            {
                try
                {
                    if (File.Exists(AppPaths.HwHoldPath))
                        File.Delete(AppPaths.HwHoldPath);
                }
                catch
                {
                    // Ignore
                }
            }
        }
    }
}
