using Microsoft.Win32;

namespace Lumino.Core;

/// <summary>
/// Manages Windows startup registration (HKCU\Software\Microsoft\Windows\CurrentVersion\Run\Lumino)
/// with "--boot --delay 10", cleaning up any legacy Lumino.lnk in the Startup folder.
/// </summary>
public static class StartupManager
{
    private const string RunKeyPath = @"Software\Microsoft\Windows\CurrentVersion\Run";
    private const string RunValueName = "Lumino";
    private const string LegacyStartupLnkName = "Lumino.lnk";

    public static string BootCommand => $"\"{AppPaths.ExePath}\" --boot --delay 10";

    private static string LegacyLnkPath => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.Startup),
        LegacyStartupLnkName);

    public static bool IsEnabled()
    {
        if (File.Exists(LegacyLnkPath))
            return true;

        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(RunKeyPath, writable: false);
            var val = key?.GetValue(RunValueName) as string;
            return !string.IsNullOrWhiteSpace(val);
        }
        catch
        {
            return false;
        }
    }

    public static void SetEnabled(bool enabled)
    {
        RemoveLegacyShortcut();

        using var key = Registry.CurrentUser.CreateSubKey(RunKeyPath, writable: true);
        if (enabled)
        {
            key.SetValue(RunValueName, BootCommand, RegistryValueKind.String);
        }
        else
        {
            key.DeleteValue(RunValueName, throwOnMissingValue: false);
        }
    }

    private static void RemoveLegacyShortcut()
    {
        try
        {
            if (File.Exists(LegacyLnkPath))
                File.Delete(LegacyLnkPath);
        }
        catch
        {
            // Ignore
        }
    }
}
