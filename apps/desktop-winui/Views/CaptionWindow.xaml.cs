using System.Collections.ObjectModel;
using System.Text;
using LiveCaptions.Desktop.Models;
using LiveCaptions.Desktop.ViewModels;
using LiveCaptions.Desktop.Services;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Windows.Foundation;
using Windows.Graphics;

namespace LiveCaptions.Desktop.Views;

public sealed partial class CaptionWindow : Window
{
    public sealed record Row(string Key, string Time, string Text, bool Pending, string SourceKey, int SourceOffset,
                             string Translation = "");
    private readonly DesktopViewModel model;
    private readonly Func<(string? Microphone, string? System)> devices;
    private readonly ObservableCollection<Row> rows = [];
    private readonly Dictionary<string, List<string>> splitCache = [];
    private readonly Dictionary<string, double> glyphWidths = [];
    private readonly TextBlock measure = new() { FontSize = 26 };
    private bool expanded, following = true, disposed, rendering, dirty;
    private double lastWidth;
    private ScrollViewer? scroll;
    private (string Key, int Offset)? reflowAnchor;
    private readonly CaptionSurface surface;
    private readonly CaptionChromeState chrome = new();
    private readonly DispatcherTimer hoverTimer = new() { Interval = TimeSpan.FromMilliseconds(100) };
    private DateTime? outsideSince;
    private bool chromeVisible = true, initialized;

    public CaptionWindow(DesktopViewModel model, Func<(string?, string?)> devices)
    {
        this.model = model; this.devices = devices;
        InitializeComponent();
        AppIcons.Apply(this);
        ExtendsContentIntoTitleBar = true; SetTitleBar(DragRegion);
        AppWindow.Resize(new SizeInt32(1100, 440));
        if (AppWindow.Presenter is OverlappedPresenter presenter) presenter.IsAlwaysOnTop = true;
        surface = new CaptionSurface(this);
        Root.PointerEntered += (_, _) => { if (initialized) UpdateHover(immediate: true); };
        Root.PointerMoved += (_, _) => { if (initialized && !chromeVisible) UpdateHover(immediate: true); };
        foreach (var picker in new[] { SourcePicker, LanguagePicker })
        {
            picker.DropDownOpened += (_, _) => { chrome.OpenInteractions++; UpdateHover(immediate: true); };
            picker.DropDownClosed += (_, _) => { chrome.OpenInteractions--; UpdateHover(immediate: true); };
        }
        History.ItemsSource = rows;
        model.Changed += Changed;
        Root.Loaded += (_, _) =>
        {
            ResizeWindow(center: true);
            initialized = true;
            hoverTimer.Tick += (_, _) => UpdateHover();
            hoverTimer.Start();
            UpdateHover(immediate: true);
            scroll = FindScroll(History);
            if (scroll is not null) scroll.ViewChanged += (_, _) =>
                following = scroll.ScrollableHeight - scroll.VerticalOffset < 36;
            Changed("captions");
        };
        Closed += async (_, _) =>
        {
            disposed = true; model.Changed -= Changed;
            hoverTimer.Stop(); surface.Dispose();
            if (model.Active && model.Mode == "captions")
                try { await model.Backend.CallAsync("session.stop"); } catch (Exception) { }
        };
    }

    public void ApplyTheme(ElementTheme theme)
    {
        Root.RequestedTheme = theme;
        AppWindow.TitleBar.ButtonBackgroundColor = Microsoft.UI.Colors.Transparent;
        AppWindow.TitleBar.ButtonForegroundColor = theme == ElementTheme.Dark ? Microsoft.UI.Colors.White : Microsoft.UI.Colors.Black;
        if (initialized) ApplyChrome();
    }

    private void ResizeWindow(bool center = false)
    {
        var scale = Root.XamlRoot?.RasterizationScale ?? 1;
        var work = DisplayArea.GetFromWindowId(AppWindow.Id, DisplayAreaFallback.Primary).WorkArea;
        var width = Math.Min(work.Width - 40, (int)(780 * scale));
        var height = Math.Min(work.Height - 40, (int)((expanded || chrome.OpenInteractions > 0 ? 600 : 290) * scale));
        var centerX = center ? work.X + work.Width / 2 : AppWindow.Position.X + AppWindow.Size.Width / 2;
        var centerY = center ? work.Y + work.Height / 2 : AppWindow.Position.Y + AppWindow.Size.Height / 2;
        AppWindow.MoveAndResize(new RectInt32(
            Math.Clamp(centerX - width / 2, work.X, work.X + work.Width - width),
            Math.Clamp(centerY - height / 2, work.Y, work.Y + work.Height - height), width, height));
    }

    public void ShowControls() { if (initialized) UpdateHover(immediate: true); }
    private void CloseCaption(object sender, RoutedEventArgs args) => Close();
    private void ResizeGrip(object sender, Microsoft.UI.Xaml.Controls.Primitives.DragDeltaEventArgs args)
    {
        var scale = Root.XamlRoot?.RasterizationScale ?? 1;
        AppWindow.Resize(new SizeInt32(
            Math.Max((int)(520 * scale), AppWindow.Size.Width + (int)(args.HorizontalChange * scale)),
            Math.Max((int)(240 * scale), AppWindow.Size.Height + (int)(args.VerticalChange * scale))));
    }
    private void UpdateHover(bool immediate = false)
    {
        var inside = surface.ContainsPointer();
        if (inside || expanded || chrome.OpenInteractions > 0)
        { outsideSince = null; chrome.PointerInside = inside; }
        else
        {
            outsideSince ??= DateTime.UtcNow;
            if (!immediate && DateTime.UtcNow - outsideSince.Value < TimeSpan.FromMilliseconds(180)) return;
            chrome.PointerInside = false;
        }
        chrome.Expanded = expanded;
        if (immediate || chromeVisible != chrome.Visible) ApplyChrome();
    }
    private void ApplyChrome()
    {
        chromeVisible = chrome.Visible;
        Root.Background = !chromeVisible ? new SolidColorBrush(Microsoft.UI.Colors.Transparent)
            : new SolidColorBrush(Root.ActualTheme == ElementTheme.Dark
                ? Microsoft.UI.ColorHelper.FromArgb(255, 32, 32, 32) : Microsoft.UI.ColorHelper.FromArgb(255, 243, 243, 243));
        foreach (var control in new UIElement[] { HeaderChrome, CaptureControls, Footer })
        {
            control.Opacity = chromeVisible ? 1 : 0;
            control.IsHitTestVisible = chromeVisible;
        }
        // Preserve layout and native bounds across hover: entering controls must not move the text.
        Line1.Watching = Line2.Watching = !chromeVisible;
    }

    private async void TranslationSettings(object sender, RoutedEventArgs args)
    {
        chrome.OpenInteractions++; UpdateHover(immediate: true);
        ResizeWindow();
        try { await CaptionTranslationDialog.ShowAsync(Root.XamlRoot, model.Configuration, model.Backend); }
        catch (Exception error) { Status.Text = error.Message; }
        finally { chrome.OpenInteractions--; UpdateHover(immediate: true); ResizeWindow(); }
    }
    private async void ToggleCapture(object sender, RoutedEventArgs args)
    {
        try
        {
            if (model.Active) await model.Backend.CallAsync("session.stop");
            else
            {
                var selected = devices();
                await model.StartAsync("captions", "", (string)((ComboBoxItem)SourcePicker.SelectedItem).Tag,
                    (string)((ComboBoxItem)LanguagePicker.SelectedItem).Tag, selected.Microphone, selected.System);
            }
        }
        catch (Exception error) { Status.Text = error.Message; }
    }

    private void Changed(string kind)
    {
        StartButton.IsEnabled = model.Connected && (!model.Active || model.Mode == "captions");
        var running = model.Active && model.Mode == "captions";
        StartIcon.Glyph = running ? "\uE71A" : "\uE768";
        IconActions.Describe(StartButton, running ? "停止字幕" : "开始字幕");
        SourcePicker.IsEnabled = LanguagePicker.IsEnabled = !model.Active;
        Status.Text = model.Error.Length > 0 ? model.Error : model.TranslationStatus.Length > 0 ? model.TranslationStatus : model.Status;
        if (kind is "captions" or "snapshot" or "finished" or "translation" or "translation.units"
            or "translation.reset" or "configuration") RequestRender();
    }

    private void WidthChanged(object sender, SizeChangedEventArgs args)
    {
        if (Math.Abs(args.NewSize.Width - lastWidth) < 2) return;
        if (expanded && !following && History.ItemsPanelRoot is ItemsStackPanel panel &&
            panel.FirstVisibleIndex >= 0 && panel.FirstVisibleIndex < rows.Count)
        {
            var row = rows[panel.FirstVisibleIndex];
            reflowAnchor = (row.SourceKey, row.SourceOffset);
        }
        lastWidth = args.NewSize.Width; splitCache.Clear(); RequestRender();
    }
    private void ToggleHistory(object sender, RoutedEventArgs args)
    {
        expanded = !expanded;
        Compact.Visibility = expanded ? Visibility.Collapsed : Visibility.Visible;
        History.Visibility = expanded ? Visibility.Visible : Visibility.Collapsed;
        HistoryIcon.Glyph = expanded ? "\uE70E" : "\uE81C";
        IconActions.Describe(HistoryButton, expanded ? "收起历史" : "展开历史");
        UpdateHover(immediate: true);
        ResizeWindow();
        following = true; splitCache.Clear(); RequestRender();
    }

    private async void RequestRender()
    {
        dirty = true;
        if (rendering || disposed || !model.Connected || SubtitleArea.ActualWidth <= 0) return;
        rendering = true;
        try
        {
            while (dirty && !disposed)
            {
                dirty = false;
                if (model.Configuration.Captions.Enabled && model.Configuration.Captions.DisplayMode != "original")
                {
                    RenderTranslations();
                    continue;
                }
                var width = Math.Max(40, SubtitleArea.ActualWidth - (expanded ? 108 : 0));
                var source = model.Captions.ToList();
                if (!expanded) source = source.TakeLast(2).ToList();
                if (model.Pending is { } pending) source.Add(pending);
                var output = new List<Row>();
                foreach (var entry in source)
                {
                    var key = $"{entry.Key}:{entry.Text}";
                    if (!splitCache.TryGetValue(key, out var lines))
                    {
                        foreach (var rune in entry.Text.EnumerateRunes())
                        {
                            var character = rune.ToString();
                            if (glyphWidths.ContainsKey(character)) continue;
                            measure.Text = character; measure.Measure(new Size(double.PositiveInfinity, double.PositiveInfinity));
                            glyphWidths[character] = measure.DesiredSize.Width;
                        }
                        lines = WireJson.Read<List<string>>(await model.Backend.CallAsync("subtitle.split",
                            new { entry.Text, width, glyph_widths = glyphWidths }));
                        if (!entry.Pending) splitCache[key] = lines;
                    }
                    int offset = 0;
                    for (int i = 0; i < lines.Count; i++)
                    {
                        var start = entry.Text.IndexOf(lines[i], offset, StringComparison.Ordinal);
                        if (start < 0) start = offset;
                        output.Add(new Row($"{entry.Key}:{start}", i == 0 ? TranscriptEntry.Format(entry.StartSeconds) : "",
                            lines[i], entry.Pending, entry.Key, start));
                        offset = start + lines[i].Length;
                    }
                }
                if (disposed) break;
                if (expanded)
                {
                    UpdateRows(output);
                }
                else
                {
                    var tail = output.TakeLast(2).Select(row => row.Text).ToList();
                    Line1.Text = tail.Count > 1 ? tail[0] : "";
                    Line2.Text = tail.Count > 0 ? tail[^1] : "字幕将在这里显示";
                }
            }
        }
        catch (Exception error) { if (!disposed) Status.Text = error.Message; }
        finally { rendering = false; }
    }

    private void RenderTranslations()
    {
        var translatedOnly = model.Configuration.Captions.DisplayMode == "translated";
        TranslationResult? Result(TranslationUnit unit) => model.Translations.TryGetValue(unit.Key, out var result)
            && result.SourceText == unit.Text && result.TargetLanguage == model.Configuration.Captions.TargetLanguage
            ? result : null;
        if (expanded)
        {
            var output = model.TranslationUnits.Select(unit =>
            {
                var result = Result(unit);
                var translation = result?.State == "ready" ? result.Text
                    : result?.State == "error" ? "翻译失败" : result?.State == "skipped" ? "未翻译" : "翻译中…";
                return new Row(unit.Key, TranscriptEntry.Format(unit.StartSeconds),
                    translatedOnly ? translation : unit.Text, unit.Pending, unit.Key, 0,
                    translatedOnly ? "" : translation);
            }).ToList();
            UpdateRows(output);
            return;
        }
        var display = SubtitleProjection.Compact(model.Configuration.Captions, model.TranslationUnits, model.Translations);
        Line1.Text = display.Top; Line2.Text = display.Bottom;
    }

    private void UpdateRows(List<Row> output)
    {
        int common = 0;
        while (common < rows.Count && common < output.Count && rows[common] == output[common]) common++;
        while (rows.Count > common) rows.RemoveAt(rows.Count - 1);
        for (int i = common; i < output.Count; i++) rows.Add(output[i]);
        if (following && rows.Count > 0) History.ScrollIntoView(rows[^1]);
        else if (reflowAnchor is { } anchor)
        {
            var row = output.LastOrDefault(r => r.SourceKey == anchor.Key && r.SourceOffset <= anchor.Offset);
            if (row is not null) History.ScrollIntoView(row, ScrollIntoViewAlignment.Leading);
            reflowAnchor = null;
        }
    }

    private static ScrollViewer? FindScroll(DependencyObject parent)
    {
        if (parent is ScrollViewer value) return value;
        for (int i = 0; i < VisualTreeHelper.GetChildrenCount(parent); i++)
            if (FindScroll(VisualTreeHelper.GetChild(parent, i)) is { } child) return child;
        return null;
    }
}
