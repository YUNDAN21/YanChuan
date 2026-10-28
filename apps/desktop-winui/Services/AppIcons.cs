using Microsoft.UI.Xaml;

namespace LiveCaptions.Desktop.Services;

public static class AppIcons
{
    public static void Apply(Window window) =>
        window.AppWindow.SetIcon(Path.Combine(AppContext.BaseDirectory, "Assets", "AppIcon.ico"));
}
