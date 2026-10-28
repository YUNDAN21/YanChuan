using System.Runtime.InteropServices;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using WinUIEx;

namespace LiveCaptions.Desktop.Services;

/// <summary>Transparent, borderless native surface; hover never resizes or recreates it.</summary>
public sealed class CaptionSurface : IDisposable
{
    private readonly Window window;
    public CaptionSurface(Window window)
    {
        this.window = window;
        if (window.AppWindow.Presenter is OverlappedPresenter presenter)
        {
            presenter.SetBorderAndTitleBar(false, false);
            presenter.IsResizable = false;
            presenter.IsAlwaysOnTop = true;
        }
        window.SystemBackdrop = new TransparentTintBackdrop();
        var handle = WinRT.Interop.WindowNative.GetWindowHandle(window);
        // WS_THICKFRAME/WS_CAPTION can leave a classic light edge after nonclient rendering
        // is disabled. A dedicated client-area grip handles resizing instead.
        var style = GetWindowLongPtr(handle, -16).ToInt64();
        SetWindowLongPtr(handle, -16, new nint(style & ~0x00c40000L));
        SetWindowPos(handle, 0, 0, 0, 0, 0, 0x0037);
        uint borderColor = 0xfffffffe; // DWMWA_COLOR_NONE
        uint cornerPreference = 1; // DWMWCP_DONOTROUND
        uint renderingPolicy = 1; // DWMNCRP_DISABLED: suppress native frame/shadow
        DwmSetWindowAttribute(handle, 34, ref borderColor, sizeof(uint));
        DwmSetWindowAttribute(handle, 33, ref cornerPreference, sizeof(uint));
        DwmSetWindowAttribute(handle, 2, ref renderingPolicy, sizeof(uint));
    }

    public bool ContainsPointer()
    {
        var handle = WinRT.Interop.WindowNative.GetWindowHandle(window);
        return GetCursorPos(out var point) && GetWindowRect(handle, out var rect)
            && point.X >= rect.Left && point.X < rect.Right && point.Y >= rect.Top && point.Y < rect.Bottom;
    }

    public void Dispose() { window.SystemBackdrop = null; }

    [StructLayout(LayoutKind.Sequential)]
    private struct Point { public int X, Y; }
    [StructLayout(LayoutKind.Sequential)]
    private struct Rect { public int Left, Top, Right, Bottom; }
    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetCursorPos(out Point point);
    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetWindowRect(nint window, out Rect rect);
    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(nint window, int attribute, ref uint value, int size);
    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW")]
    private static extern nint GetWindowLongPtr(nint window, int index);
    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW")]
    private static extern nint SetWindowLongPtr(nint window, int index, nint value);
    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetWindowPos(nint window, nint insertAfter, int x, int y, int width, int height, uint flags);
}
