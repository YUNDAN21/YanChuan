using LiveCaptions.Desktop.Rendering;
using Microsoft.UI.Input;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Documents;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Windows.Foundation;
using Windows.System;
using Windows.UI.Core;

namespace LiveCaptions.Desktop.Views;

/// <summary>Slow edge scrolling for one reply's native text selection.</summary>
internal sealed class SelectionAutoScroll
{
    private readonly RichTextBlock text;
    private readonly DispatcherTimer clock = new() { Interval = TimeSpan.FromMilliseconds(33) };
    private UIElement? root;
    private ScrollViewer? scroll;
    private TextPointer? anchor;
    private Point position;
    private uint pointer;

    public SelectionAutoScroll(RichTextBlock text)
    {
        this.text = text;
        text.AddHandler(UIElement.PointerPressedEvent, new PointerEventHandler(Pressed), true);
        text.Loaded += (_, _) => Attach();
        text.Unloaded += (_, _) => Detach();
        clock.Tick += (_, _) => Tick();
    }

    private void Attach()
    {
        Detach();
        root = text.XamlRoot.Content;
        root.AddHandler(UIElement.PointerMovedEvent, new PointerEventHandler(Moved), true);
        root.AddHandler(UIElement.PointerReleasedEvent, new PointerEventHandler(Released), true);
        root.AddHandler(UIElement.PointerCanceledEvent, new PointerEventHandler(Released), true);
    }

    private void Detach()
    {
        Stop();
        if (root is null) return;
        root.RemoveHandler(UIElement.PointerMovedEvent, new PointerEventHandler(Moved));
        root.RemoveHandler(UIElement.PointerReleasedEvent, new PointerEventHandler(Released));
        root.RemoveHandler(UIElement.PointerCanceledEvent, new PointerEventHandler(Released));
        root = null;
    }

    private void Pressed(object sender, PointerRoutedEventArgs args)
    {
        if (root is null || !args.GetCurrentPoint(text).Properties.IsLeftButtonPressed) return;
        scroll = null;
        for (DependencyObject? parent = VisualTreeHelper.GetParent(text); parent is not null;
            parent = VisualTreeHelper.GetParent(parent))
            if (parent is ScrollViewer viewer) { scroll = viewer; break; }
        if (scroll is null) return;
        anchor = text.GetPositionFromPoint(args.GetCurrentPoint(text).Position);
        pointer = args.Pointer.PointerId;
        position = args.GetCurrentPoint(root).Position;
        clock.Start();
    }

    private void Moved(object sender, PointerRoutedEventArgs args)
    {
        if (anchor is null || root is null || args.Pointer.PointerId != pointer) return;
        if (!args.GetCurrentPoint(root).Properties.IsLeftButtonPressed) { Stop(); return; }
        position = args.GetCurrentPoint(root).Position;
    }
    private void Released(object sender, PointerRoutedEventArgs args)
    {
        if (args.Pointer.PointerId == pointer) Stop();
    }
    private void Stop() { clock.Stop(); anchor = null; scroll = null; }

    private void Tick()
    {
        if (anchor is null || scroll is null || root is null) return;
        if ((InputKeyboardSource.GetKeyStateForCurrentThread(VirtualKey.LeftButton) & CoreVirtualKeyStates.Down) == 0)
        { Stop(); return; }
        var point = root.TransformToVisual(scroll).TransformPoint(position);
        var speed = SelectionScrollSpeed.At(point.Y, scroll.ViewportHeight);
        if (speed == 0) return;
        var offset = Math.Clamp(scroll.VerticalOffset + speed * 0.033, 0, scroll.ScrollableHeight);
        scroll.ChangeView(null, offset, null, true);
        scroll.UpdateLayout();
        var edgePoint = new Point(Math.Clamp(point.X, 0, scroll.ActualWidth),
            Math.Clamp(point.Y, 1, Math.Max(1, scroll.ViewportHeight - 1)));
        var local = scroll.TransformToVisual(text).TransformPoint(edgePoint);
        var end = local.Y <= 0 ? text.ContentStart : local.Y >= text.ActualHeight ? text.ContentEnd
            : text.GetPositionFromPoint(local);
        if (end is not null)
            text.Select(anchor.Offset <= end.Offset ? anchor : end, anchor.Offset <= end.Offset ? end : anchor);
    }
}
