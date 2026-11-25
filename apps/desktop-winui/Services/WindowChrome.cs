using Microsoft.UI;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Windows.UI;

namespace LiveCaptions.Desktop.Services;

/// <summary>Shared title-bar and window background colours for the main and caption windows.</summary>
public static class WindowChrome
{
    /// <summary>Matches SolidBackgroundFillColorBase, used where a window cannot use Mica.</summary>
    public static Color Background(ElementTheme theme) => theme == ElementTheme.Dark
        ? ColorHelper.FromArgb(255, 32, 32, 32) : ColorHelper.FromArgb(255, 243, 243, 243);

    /// <summary>Caption buttons drawn over content that extends into the title bar.</summary>
    public static void ApplyCaptionButtons(AppWindow window, ElementTheme theme)
    {
        if (!AppWindowTitleBar.IsCustomizationSupported()) return;
        var dark = theme == ElementTheme.Dark;
        var bar = window.TitleBar;
        bar.ButtonBackgroundColor = bar.ButtonInactiveBackgroundColor = Colors.Transparent;
        bar.ButtonForegroundColor = bar.ButtonHoverForegroundColor = bar.ButtonPressedForegroundColor =
            dark ? Colors.White : Colors.Black;
        bar.ButtonInactiveForegroundColor = dark
            ? ColorHelper.FromArgb(0x5D, 255, 255, 255) : ColorHelper.FromArgb(0x5C, 0, 0, 0);
        bar.ButtonHoverBackgroundColor = dark
            ? ColorHelper.FromArgb(0x0F, 255, 255, 255) : ColorHelper.FromArgb(0x09, 0, 0, 0);
        bar.ButtonPressedBackgroundColor = dark
            ? ColorHelper.FromArgb(0x0A, 255, 255, 255) : ColorHelper.FromArgb(0x06, 0, 0, 0);
    }
}
