using LiveCaptions.Desktop.Rendering;
using Markdig.Extensions.Mathematics;
using Markdig.Extensions.Tables;
using Markdig.Extensions.TaskLists;
using Markdig.Syntax;
using Markdig.Syntax.Inlines;
using Microsoft.UI.Text;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Documents;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using Windows.Storage.Streams;
using Windows.UI.Text;
using MarkdownDocument = LiveCaptions.Desktop.Rendering.MarkdownDocument;
using Block = Markdig.Syntax.Block;

namespace LiveCaptions.Desktop.Views;

/// <summary>Native, offline Markdown presentation shared by summaries and meeting chat.</summary>
public sealed class MarkdownView : UserControl
{
    private readonly StackPanel document = new() { Spacing = 8, HorizontalAlignment = HorizontalAlignment.Stretch };
    public static readonly DependencyProperty TextProperty = DependencyProperty.Register(
        nameof(Text), typeof(string), typeof(MarkdownView), new PropertyMetadata("", Changed));
    public string Text { get => (string)GetValue(TextProperty); set => SetValue(TextProperty, value); }

    public MarkdownView()
    {
        Content = document;
        ActualThemeChanged += (_, _) => Render();
    }

    private static void Changed(DependencyObject sender, DependencyPropertyChangedEventArgs args) =>
        ((MarkdownView)sender).Render();
    private Brush Brush(string name) => (Brush)Application.Current.Resources[name];

    private void Render()
    {
        document.Children.Clear();
        foreach (var block in MarkdownDocument.Parse(Text)) document.Children.Add(RenderBlock(block));
    }

    private FrameworkElement RenderBlock(Block block) => block switch
    {
        MathBlock math => Formula(math.Lines.ToString(), true),
        HeadingBlock heading => Rich(heading.Inline, heading.Level switch { 1 => 25, 2 => 21, 3 => 18, _ => 16 }, true),
        ParagraphBlock paragraph => Rich(paragraph.Inline),
        FencedCodeBlock code when code.Info?.ToString().Trim() is "math" or "latex" or "tex" => Formula(code.Lines.ToString(), true),
        CodeBlock code => Code(code.Lines.ToString()),
        QuoteBlock quote => new Border
        {
            BorderBrush = Brush("AccentFillColorDefaultBrush"), BorderThickness = new Thickness(3, 0, 0, 0),
            Padding = new Thickness(12, 4, 0, 4), Child = Blocks(quote)
        },
        ListBlock list => List(list),
        Table table => TableView(table),
        ThematicBreakBlock => new Border
        {
            Height = 1, Margin = new Thickness(0, 8, 0, 8), Background = Brush("DividerStrokeColorDefaultBrush")
        },
        HtmlBlock html => Plain(html.Lines.ToString()),
        ContainerBlock container => Blocks(container),
        LeafBlock leaf when leaf.Inline is not null => Rich(leaf.Inline),
        LeafBlock leaf => Plain(leaf.Lines.ToString()),
        _ => new TextBlock()
    };

    private StackPanel Blocks(ContainerBlock blocks)
    {
        var stack = new StackPanel { Spacing = 8 };
        foreach (var block in blocks) stack.Children.Add(RenderBlock(block));
        return stack;
    }

    private FrameworkElement List(ListBlock list)
    {
        var stack = new StackPanel { Spacing = 6 };
        var number = int.TryParse(list.OrderedStart, out var start) ? start : 1;
        foreach (var item in list)
        {
            var row = new Grid { ColumnSpacing = 8 };
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            var marker = Plain(list.IsOrdered ? $"{number++}." : "•");
            marker.MinWidth = 16;
            var content = RenderBlock(item);
            Grid.SetColumn(content, 1);
            row.Children.Add(marker); row.Children.Add(content); stack.Children.Add(row);
        }
        return stack;
    }

    private FrameworkElement TableView(Table table)
    {
        var grid = new Grid();
        var columns = table.OfType<TableRow>().Select(row => row.Count).DefaultIfEmpty(0).Max();
        for (var column = 0; column < columns; column++)
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var index = 0;
        foreach (var row in table.OfType<TableRow>())
        {
            grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            for (var column = 0; column < row.Count; column++)
            {
                var content = Blocks((TableCell)row[column]);
                content.MaxWidth = 420;
                if (row.IsHeader)
                    foreach (var text in content.Children.OfType<RichTextBlock>()) text.FontWeight = FontWeights.SemiBold;
                var border = new Border
                {
                    BorderBrush = Brush("DividerStrokeColorDefaultBrush"), BorderThickness = new Thickness(0, 0, 1, 1),
                    Padding = new Thickness(10, 8, 10, 8), Child = content,
                    Background = row.IsHeader ? Brush("SubtleFillColorSecondaryBrush") : null
                };
                Grid.SetRow(border, index); Grid.SetColumn(border, column); grid.Children.Add(border);
            }
            index++;
        }
        return HorizontalScroll(grid);
    }

    private RichTextBlock Rich(ContainerInline? inlines, double size = 15, bool bold = false)
    {
        var text = new RichTextBlock
        {
            FontSize = size, IsTextSelectionEnabled = true, TextWrapping = TextWrapping.Wrap,
            FontWeight = bold ? FontWeights.SemiBold : FontWeights.Normal,
            HorizontalAlignment = HorizontalAlignment.Stretch
        };
        var paragraph = new Paragraph { LineHeight = size * 1.8 };
        if (inlines is not null) AddInlines(inlines, paragraph.Inlines);
        text.Blocks.Add(paragraph);
        return text;
    }

    private void AddInlines(ContainerInline source, InlineCollection target)
    {
        foreach (var inline in source)
        {
            switch (inline)
            {
                case MathInline math:
                    target.Add(new InlineUIContainer { Child = Formula(math.Content.ToString(), false) });
                    break;
                case LiteralInline literal:
                    target.Add(new Run { Text = literal.Content.ToString() });
                    break;
                case CodeInline code:
                    target.Add(new Run { Text = code.Content, FontFamily = new FontFamily("Cascadia Mono, Consolas") });
                    break;
                case LineBreakInline line:
                    if (line.IsHard) target.Add(new LineBreak());
                    else target.Add(new Run { Text = " " });
                    break;
                case EmphasisInline emphasis:
                    var span = new Span();
                    if (emphasis.DelimiterChar == '~') span.TextDecorations = TextDecorations.Strikethrough;
                    else
                    {
                        if (emphasis.DelimiterCount >= 2) span.FontWeight = FontWeights.Bold;
                        if (emphasis.DelimiterCount % 2 == 1) span.FontStyle = FontStyle.Italic;
                    }
                    AddInlines(emphasis, span.Inlines); target.Add(span);
                    break;
                case LinkInline link when !link.IsImage && Uri.TryCreate(link.Url, UriKind.Absolute, out var uri)
                    && uri.Scheme is "http" or "https" or "mailto":
                    var hyperlink = new Hyperlink { NavigateUri = uri };
                    AddInlines(link, hyperlink.Inlines); target.Add(hyperlink);
                    break;
                case TaskList task:
                    target.Add(new Run { Text = task.Checked ? "☑ " : "☐ " });
                    break;
                case HtmlInline html:
                    target.Add(new Run { Text = html.Tag });
                    break;
                case AutolinkInline autolink:
                    if (Uri.TryCreate((autolink.IsEmail ? "mailto:" : "") + autolink.Url, UriKind.Absolute, out var address)
                        && address.Scheme is "http" or "https" or "mailto")
                        target.Add(new Hyperlink { NavigateUri = address, Inlines = { new Run { Text = autolink.Url } } });
                    else target.Add(new Run { Text = autolink.Url });
                    break;
                case ContainerInline container:
                    AddInlines(container, target);
                    break;
            }
        }
    }

    private TextBlock Plain(string text) => new()
    {
        Text = text, FontSize = 15, TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true
    };

    private FrameworkElement Code(string code) => new Border
    {
        Background = Brush("SubtleFillColorSecondaryBrush"), CornerRadius = new CornerRadius(6),
        Padding = new Thickness(12), Child = HorizontalScroll(new TextBlock
        {
            Text = code, FontFamily = new FontFamily("Cascadia Mono, Consolas"), FontSize = 13,
            IsTextSelectionEnabled = true
        })
    };

    private static ScrollViewer HorizontalScroll(UIElement child) => new()
    {
        Content = child, HorizontalScrollBarVisibility = ScrollBarVisibility.Auto,
        HorizontalScrollMode = ScrollMode.Enabled, VerticalScrollMode = ScrollMode.Disabled,
        VerticalScrollBarVisibility = ScrollBarVisibility.Disabled
    };

    private FrameworkElement Formula(string latex, bool display)
    {
        var rendered = FormulaRenderer.Render(latex, display, ActualTheme == ElementTheme.Dark);
        if (rendered is null) return Plain(display ? $"$$\n{latex}\n$$" : $"${latex}$");
        var image = new Image
        {
            Width = rendered.Width, Height = rendered.Height, Stretch = Stretch.Uniform,
            HorizontalAlignment = HorizontalAlignment.Left, VerticalAlignment = VerticalAlignment.Center
        };
        AutomationProperties.SetName(image, "公式 " + latex);
        ToolTipService.SetToolTip(image, latex);
        SetSource(image, rendered.Png);
        if (!display) return image;
        var scroll = HorizontalScroll(image);
        scroll.Margin = new Thickness(0, 8, 0, 8);
        return scroll;
    }

    private static async void SetSource(Image image, byte[] bytes)
    {
        try
        {
            using var stream = new InMemoryRandomAccessStream();
            using (var writer = new DataWriter(stream))
            {
                writer.WriteBytes(bytes);
                await writer.StoreAsync();
                writer.DetachStream();
            }
            stream.Seek(0);
            var bitmap = new BitmapImage();
            await bitmap.SetSourceAsync(stream);
            image.Source = bitmap;
        }
        catch (Exception error) when (error is not OutOfMemoryException)
        {
            AutomationProperties.SetHelpText(image, "公式图像加载失败，可通过复制按钮获取原文。");
        }
    }
}
