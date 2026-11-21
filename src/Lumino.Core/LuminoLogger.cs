using System.Text;

namespace Lumino.Core;

public static class LuminoLogger
{
    private static readonly object Sync = new();

    public static void Log(string message)
    {
        try
        {
            lock (Sync)
            {
                Directory.CreateDirectory(AppPaths.DataDir);
                string line = $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} {message}{Environment.NewLine}";
                File.AppendAllText(AppPaths.LogPath, line, Encoding.UTF8);
            }
        }
        catch
        {
            // Logging must never crash the application or keepalive worker
        }
    }

    public static string ReadTail(int maxLines = 120)
    {
        try
        {
            if (!File.Exists(AppPaths.LogPath))
                return "Aucun journal enregistré.";

            var lines = File.ReadAllLines(AppPaths.LogPath, Encoding.UTF8);
            if (lines.Length <= maxLines)
                return string.Join(Environment.NewLine, lines);

            return string.Join(Environment.NewLine, lines.Skip(lines.Length - maxLines));
        }
        catch (Exception ex)
        {
            return $"Lecture du journal impossible : {ex.Message}";
        }
    }
}
