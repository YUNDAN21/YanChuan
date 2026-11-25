using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using WinUIEx;

namespace LiveCaptions.Desktop.Services;

/// <summary>Owns only notification-area presentation; exit is delegated to the main window.</summary>
public sealed class DesktopTray : IDisposable
{
    private TrayIcon? icon;
    private readonly Action restore, exit;
    private readonly Func<ElementTheme> theme;
    public DesktopTray(Action restore, Action exit, Func<ElementTheme> theme)
    { this.restore = restore; this.exit = exit; this.theme = theme; }

    public void Show()
    {
        if (icon is null)
        {
            icon = new TrayIcon(1, Path.Combine(AppContext.BaseDirectory, "Assets", "AppIcon.ico"), "言川");
            icon.Selected += (_, args) => { args.Handled = true; Open(); };
            icon.LeftDoubleClick += (_, args) => { args.Handled = true; Open(); };
            icon.ContextMenu += (_, args) =>
            {
                var menu = new MenuFlyout();
                var open = new MenuFlyoutItem { Text = "打开言川", Icon = new SymbolIcon(Symbol.OpenPane) };
                var quit = new MenuFlyoutItem { Text = "退出", Icon = new FontIcon { Glyph = "\uE7E8" } };
                open.Click += (_, _) => Open();
                quit.Click += (_, _) => { icon?.CloseFlyout(); exit(); };
                open.RequestedTheme = quit.RequestedTheme = theme();
                menu.Items.Add(open); menu.Items.Add(new MenuFlyoutSeparator()); menu.Items.Add(quit);
                args.Flyout = menu;
            };
        }
        icon.IsVisible = true;
    }

    private void Open() { icon?.CloseFlyout(); restore(); }

    public void Update(string status)
    {
        var text = "言川 · " + status;
        if (icon is not null) icon.Tooltip = text[..Math.Min(120, text.Length)];
    }

    public void Dispose()
    {
        var previous = icon;
        icon = null;
        previous?.Dispose();
    }
}
