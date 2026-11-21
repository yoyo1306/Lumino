namespace Lumino.Core;

public static class BuildInfo
{
    public const string AppName = "Lumino";
    public const string Version = "2.0.0";
    public const string ShortVersion = "v2.0.0";
    public const string SingleInstanceMutexName = "Local\\Lumino_WinUI3_SingleInstance_Mutex";
    public const string ShowWindowPipeName = "Lumino_WinUI3_FocusPipe";
    public const string KeepalivePipeName = "Lumino_WinUI3_KeepalivePipe";
    public const string KeepaliveMutexName = "Local\\Lumino_Keepalive_Worker_Mutex";
}
