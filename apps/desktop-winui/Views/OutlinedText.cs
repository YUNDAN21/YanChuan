using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace LiveCaptions.Desktop.Views;

public sealed class OutlinedText : Grid
{
    public static readonly DependencyProperty TextProperty = DependencyProperty.Register(nameof(Text),
        typeof(string), typeof(OutlinedText), new PropertyMetadata("", Changed));
    public static readonly DependencyProperty WatchingProperty = DependencyProperty.Register(nameof(Watching),
        typeof(bool), typeof(OutlinedText), new PropertyMetadata(false, Changed));
    public string Text { get => (string)GetValue(TextProperty); set => SetValue(TextProperty, value); }
    public bool Watching { get => (bool)GetValue(WatchingProperty); set => SetValue(WatchingProperty, value); }
    private readonly List<TextBlock> outlines = [];
    private readonly TextBlock center = new() { FontSize = 26, TextWrapping = TextWrapping.Wrap, TextAlignment = TextAlignment.Center,
        FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, IsTextSelectionEnabled = true };

    public OutlinedText()
    {
        foreach (var (x, y) in new[] { (-1.5,-1.5), (0d,-1.5), (1.5,-1.5), (-1.5,0d), (1.5,0d), (-1.5,1.5), (0d,1.5), (1.5,1.5) })
        {
            var layer = new TextBlock { FontSize = 26, TextWrapping = TextWrapping.Wrap, TextAlignment = TextAlignment.Center,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, IsHitTestVisible = false,
                Foreground = new SolidColorBrush(Microsoft.UI.Colors.Black),
                RenderTransform = new TranslateTransform { X = x, Y = y }, Visibility = Visibility.Collapsed };
            outlines.Add(layer); Children.Add(layer);
            Microsoft.UI.Xaml.Automation.AutomationProperties.SetAccessibilityView(layer,
                Microsoft.UI.Xaml.Automation.Peers.AccessibilityView.Raw);
        }
        Children.Add(center);
        ActualThemeChanged += (_, _) => Update();
    }

    private static void Changed(DependencyObject target, DependencyPropertyChangedEventArgs args) => ((OutlinedText)target).Update();
    private void Update()
    {
        center.Text = Text ?? "";
        center.Foreground = new SolidColorBrush(Watching || ActualTheme == ElementTheme.Dark
            ? Microsoft.UI.Colors.White : Microsoft.UI.Colors.Black);
        foreach (var layer in outlines) { layer.Text = center.Text; layer.Visibility = Watching ? Visibility.Visible : Visibility.Collapsed; }
    }
}
