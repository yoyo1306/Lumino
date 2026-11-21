using System.Diagnostics;

namespace Lumino.Core;

/// <summary>
/// Resolves application and data paths.
/// - Portable mode (executable directory is writable and not inside Program Files):
///   stores config.json, lumino.log, lcd_media/, lock files next to the executable.
/// - Installed mode (Program Files / read-only install directory):
///   switches automatically to %LOCALAPPDATA%\Lumino\ and migrates default config/media on first run.
/// </summary>
public static class AppPaths
{
    private static readonly object Sync = new();
    private static string? _exePath;
    private static string? _exeDir;
    private static string? _dataDir;
    private static bool? _isPortable;

    public static string ExePath
    {
        get
        {
            if (_exePath is not null)
                return _exePath;

            var p = Environment.ProcessPath;
            if (string.IsNullOrWhiteSpace(p))
                p = Process.GetCurrentProcess().MainModule?.FileName ?? AppContext.BaseDirectory;
            _exePath = Path.GetFullPath(p);
            return _exePath;
        }
    }

    public static string ExeDir => _exeDir ??= Path.GetDirectoryName(ExePath) ?? AppContext.BaseDirectory;

    public static bool IsPortable
    {
        get
        {
            EnsureInitialized();
            return _isPortable!.Value;
        }
    }

    public static string DataDir
    {
        get
        {
            EnsureInitialized();
            return _dataDir!;
        }
    }

    public static string ConfigPath => Path.Combine(DataDir, "config.json");
    public static string LogPath => Path.Combine(DataDir, "lumino.log");
    public static string LcdMediaDir => Path.Combine(DataDir, "lcd_media");
    public static string KeepaliveLockPath => Path.Combine(DataDir, "corsair_keepalive.lock");
    public static string StopFlagPath => Path.Combine(DataDir, "corsair_keepalive.stop");
    public static string HwHoldPath => Path.Combine(DataDir, "hw_hold.lock");
    public static string SyncIdlePath => Path.Combine(DataDir, "sync_idle.txt");

    public static string ResolveAssetPath(string relativeName)
    {
        var candidate = Path.Combine(ExeDir, relativeName);
        if (File.Exists(candidate))
            return candidate;

        var parent = Directory.GetParent(ExeDir)?.FullName;
        if (parent is not null)
        {
            var up1 = Path.Combine(parent, relativeName);
            if (File.Exists(up1))
                return up1;
        }

        var up3 = Path.GetFullPath(Path.Combine(ExeDir, "..", "..", "..", relativeName));
        if (File.Exists(up3))
            return up3;

        return candidate;
    }

    private static void EnsureInitialized()
    {
        if (_dataDir is not null)
            return;

        lock (Sync)
        {
            if (_dataDir is not null)
                return;

            bool underProgramFiles = IsUnderProgramFiles(ExeDir);
            bool writable = !underProgramFiles && CanWriteDirectory(ExeDir);

            if (writable)
            {
                _isPortable = true;
                _dataDir = ExeDir;
            }
            else
            {
                _isPortable = false;
                var localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                _dataDir = Path.Combine(localAppData, BuildInfo.AppName);
                Directory.CreateDirectory(_dataDir);
                MigrateDefaultsIfNeeded(ExeDir, _dataDir);
            }
        }
    }

    private static bool IsUnderProgramFiles(string dir)
    {
        string normalized = Path.GetFullPath(dir).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
        string pf = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
        string pf86 = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86);

        if (!string.IsNullOrEmpty(pf) && normalized.StartsWith(pf.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
            return true;
        if (!string.IsNullOrEmpty(pf86) && normalized.StartsWith(pf86.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
            return true;
        return false;
    }

    private static bool CanWriteDirectory(string dir)
    {
        try
        {
            string probe = Path.Combine(dir, $".lumino_write_test_{Environment.ProcessId}_{Guid.NewGuid():N}.tmp");
            File.WriteAllText(probe, "ok");
            File.Delete(probe);
            return true;
        }
        catch
        {
            return false;
        }
    }

    private static void MigrateDefaultsIfNeeded(string sourceDir, string targetDir)
    {
        try
        {
            string targetCfg = Path.Combine(targetDir, "config.json");
            string sourceCfg = Path.Combine(sourceDir, "config.json");
            if (!File.Exists(targetCfg) && File.Exists(sourceCfg))
            {
                File.Copy(sourceCfg, targetCfg, overwrite: false);
            }

            string targetMedia = Path.Combine(targetDir, "lcd_media");
            string sourceMedia = Path.Combine(sourceDir, "lcd_media");
            if (Directory.Exists(sourceMedia) && !Directory.Exists(targetMedia))
            {
                Directory.CreateDirectory(targetMedia);
                foreach (var file in Directory.GetFiles(sourceMedia))
                {
                    var dest = Path.Combine(targetMedia, Path.GetFileName(file));
                    if (!File.Exists(dest))
                        File.Copy(file, dest, overwrite: false);
                }
            }
        }
        catch
        {
            // Ignore migration errors
        }
    }
}
