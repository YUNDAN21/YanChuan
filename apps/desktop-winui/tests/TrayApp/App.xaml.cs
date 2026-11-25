using System.Reflection;
using System.Runtime.InteropServices;
using System.Text.Json;
using LiveCaptions.Desktop.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace LiveCaptions.TrayTests;

public partial class App : Application
{
    public App() { InitializeComponent(); }
    protected override async void OnLaunched(LaunchActivatedEventArgs args)
    {
        var report = Environment.GetCommandLineArgs()[1];
        var window = new Window { Content = new Grid() };
        int restored = 0;
        using var tray = new DesktopTray(() => restored++, () => { }, () => ElementTheme.Dark);
        try
        {
            tray.Show();
            tray.Update("正在转写");
            var icon = typeof(DesktopTray).GetField("icon", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(tray)!;
            var handle = (nint)icon.GetType().GetField("_windowHandle", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(icon)!;
            var identity = new Identifier { Size = (uint)Marshal.SizeOf<Identifier>(), Window = handle, Id = 1 };
            if (Shell_NotifyIconGetRect(ref identity, out _) != 0)
                throw new InvalidOperationException("Tray icon was not registered with the shell");
            SendMessage(handle, 0x8765, 0, (nint)((1 << 16) | 0x400));
            if (restored != 1) throw new InvalidOperationException("Tray selection did not restore the window");
            var lifetime = new WindowLifetime();
            int shutdowns = 0;
            await lifetime.CloseAsync(true, () => window.AppWindow.Hide(),
                () => { shutdowns++; return Task.CompletedTask; }, () => { });
            if (window.AppWindow.IsVisible || shutdowns != 0) throw new InvalidOperationException("Hide closed backend");
            tray.Dispose();
            if (Shell_NotifyIconGetRect(ref identity, out _) == 0)
                throw new InvalidOperationException("Tray icon survived disposal");
            tray.Show(); tray.Dispose();
            await File.WriteAllTextAsync(report, JsonSerializer.Serialize(new {
                success = true, tray_registered = true, tray_removed = true, recreate = true, selection_callback = true,
                hidden_without_shutdown = true, devices_opened = false, audio_played = false }));
        }
        catch (Exception error)
        {
            await File.WriteAllTextAsync(report, JsonSerializer.Serialize(new { success = false,
                error = error.GetType().Name, message = error.Message }));
        }
        finally { tray.Dispose(); window.Close(); Exit(); }
    }
    [StructLayout(LayoutKind.Sequential)]
    private struct Identifier { public uint Size; public nint Window; public uint Id; public Guid Guid; }
    [StructLayout(LayoutKind.Sequential)]
    private struct Rect { public int Left, Top, Right, Bottom; }
    [DllImport("shell32.dll")]
    private static extern int Shell_NotifyIconGetRect(ref Identifier identifier, out Rect rectangle);
    [DllImport("user32.dll", EntryPoint = "SendMessageW")]
    private static extern nint SendMessage(nint window, uint message, nuint wParam, nint lParam);
}
