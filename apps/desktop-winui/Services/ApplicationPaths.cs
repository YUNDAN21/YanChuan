namespace LiveCaptions.Desktop.Services;

public static class ApplicationPaths
{
    public const string RuntimeMarker = "yanchuan.runtime.json";
    public static string? FindMarker(string root)
    {
        foreach (var name in new[] { RuntimeMarker, "livecaptions.runtime.json" })
        {
            var path = Path.Combine(root, name);
            if (File.Exists(path)) return path;
        }
        return null;
    }

    public static string DataRoot(string root, string localAppData, string? explicitRoot = null)
    {
        if (explicitRoot is not null) return explicitRoot;
        if (FindMarker(root) is null) return root;
        // Existing installations keep their configuration, API credentials, meetings and model paths.
        var legacy = Path.Combine(localAppData, "LiveCaptions");
        return Directory.Exists(legacy) ? legacy : Path.Combine(localAppData, "YanChuan");
    }
}
