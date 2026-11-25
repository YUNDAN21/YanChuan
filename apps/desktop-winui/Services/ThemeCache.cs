namespace LiveCaptions.Desktop.Services;

// The backend owns the appearance setting but only reports it after it has started, which takes a
// moment. The window remembers the last theme it was told so the first frame already matches; the
// backend's value still wins as soon as it arrives.
public static class ThemeCache
{
    public static string? Read(string path)
    {
        try
        {
            var mode = File.ReadAllText(path).Trim();
            return mode is "light" or "dark" ? mode : null;
        }
        catch (IOException) { return null; }
        catch (UnauthorizedAccessException) { return null; }
    }

    public static void Write(string path, string mode)
    {
        if (mode is not ("light" or "dark") || Read(path) == mode) return;
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            File.WriteAllText(path, mode);
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }
}
