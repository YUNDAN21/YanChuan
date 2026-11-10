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
using System.Text;
using Windows.ApplicationModel.DataTransfer;
using Windows.System;
using Windows.UI.Core;

namespace LiveCaptions.Desktop.Views;

/// <summary>Native, offline Markdown presentation shared by summaries and meeting chat.</summary>
public sealed class MarkdownView : UserControl
{
    // A single native text document owns the selection for the whole answer.
    // Separate RichTextBlocks per Markdown block prevent dragging across paragraphs.
    private readonly RichTextBlock document = CreateDocument();
    private readonly Dictionary<InlineUIContainer, string> formulas = [];
    private readonly List<(Image Image, double Width, double Height)> displayImages = [];
    private readonly SelectionAutoScroll selectionScroll;
    public static readonly DependencyProperty TextProperty = DependencyProperty.Register(
        nameof(Text), typeof(string), typeof(MarkdownView), new PropertyMetadata("", Changed));
    public string Text { get => (string)GetValue(TextProperty); set => SetValue(TextProperty, value); }

    public MarkdownView()
    {
        Content = document;
        selectionScroll = new SelectionAutoScroll(document);
        document.SizeChanged += (_, _) => FitDisplayImages();
        document.ContextFlyout = CopyMenu();
        document.SelectionFlyout = CopyMenu();
        document.PreviewKeyDown += (_, args) =>
        {
            var control = (Microsoft.UI.Input.InputKeyboardSource.GetKeyStateForCurrentThread(VirtualKey.Control)
                & CoreVirtualKeyStates.Down) != 0;
            if (control && args.Key is VirtualKey.C or VirtualKey.Insert)
            { CopySelection(); args.Handled = true; }
            else if (control && args.Key == VirtualKey.A)
            { document.SelectAll(); args.Handled = true; }
        };
        ActualThemeChanged += (_, _) => Render();
    }

    private MenuFlyout CopyMenu()
    {
        var menu = new MenuFlyout();
        var copy = new MenuFlyoutItem { Text = "复制", Icon = new SymbolIcon(Symbol.Copy) };
        copy.Click += (_, _) => CopySelection();
        var all = new MenuFlyoutItem { Text = "全选此条回复" };
        all.Click += (_, _) => document.SelectAll();
        menu.Items.Add(copy); menu.Items.Add(all);
        return menu;
    }

    public void CopySelection()
    {
        var value = SelectionText();
        if (value.Length == 0) return;
        var data = new DataPackage(); data.SetText(value);
        Clipboard.SetContent(data); Clipboard.Flush();
    }

    // Native SelectedText omits InlineUIContainer images. Serialize the selected
    // document range ourselves and substitute each selected formula's LaTeX.
    public string SelectionText()
    {
        int start = document.SelectionStart.Offset, end = document.SelectionEnd.Offset;
        if (start == end) return "";
        var result = new StringBuilder();
        foreach (var paragraph in document.Blocks.OfType<Paragraph>())
        {
            Append(paragraph.Inlines);
            if (start < paragraph.ContentEnd.Offset && end > paragraph.ContentEnd.Offset)
                result.AppendLine();
        }
        return result.ToString();

        void Append(InlineCollection inlines)
        {
            foreach (var inline in inlines)
            {
                if (inline is Span span) { Append(span.Inlines); continue; }
                if (inline is InlineUIContainer formula)
                {
                    if (end > formula.ElementStart.Offset && start < formula.ElementEnd.Offset
                        && formulas.TryGetValue(formula, out var latex)) result.Append(latex);
                    continue;
                }
                if (end <= inline.ContentStart.Offset || start >= inline.ContentEnd.Offset) continue;
                if (inline is Run run)
                {
                    int from = Math.Clamp(start - run.ContentStart.Offset, 0, run.Text.Length);
                    int to = Math.Clamp(end - run.ContentStart.Offset, 0, run.Text.Length);
                    if (to > from) result.Append(run.Text.AsSpan(from, to - from));
                }
                else if (inline is LineBreak) result.AppendLine();
            }
        }
    }

    private static void Changed(DependencyObject sender, DependencyPropertyChangedEventArgs args) =>
        ((MarkdownView)sender).Render();
    private Brush Brush(string name) => (Brush)Application.Current.Resources[name];

    private void Render()
    {
        document.Blocks.Clear();
        formulas.Clear();
        displayImages.Clear();
        AddBlocks(MarkdownDocument.Parse(Text), document);
    }

    private static RichTextBlock CreateDocument() => new()
    {
        FontSize = 15, IsTextSelectionEnabled = true, TextWrapping = TextWrapping.Wrap,
        HorizontalAlignment = HorizontalAlignment.Stretch
    };

    private void AddBlocks(ContainerBlock blocks, RichTextBlock target, double indent = 0)
    {
        foreach (var block in blocks) AddBlock(block, target, indent);
    }

    private void AddBlock(Block block, RichTextBlock target, double indent)
    {
        switch (block)
        {
            case MathBlock math:
                AddFormula(NewParagraph(target, indent).Inlines, math.Lines.ToString(), true);
                break;
            case HeadingBlock heading:
                AddParagraph(target, heading.Inline, indent,
                    heading.Level switch { 1 => 25, 2 => 21, 3 => 18, _ => 16 }, true);
                break;
            case ParagraphBlock paragraph:
                AddParagraph(target, paragraph.Inline, indent);
                break;
            case FencedCodeBlock code when code.Info?.ToString().Trim() is "math" or "latex" or "tex":
                AddFormula(NewParagraph(target, indent).Inlines, code.Lines.ToString(), true);
                break;
            case CodeBlock code:
                var codeParagraph = NewParagraph(target, indent + 12, 13);
                codeParagraph.FontFamily = new FontFamily("Cascadia Mono, Consolas");
                codeParagraph.Inlines.Add(new Run { Text = code.Lines.ToString() });
                break;
            case QuoteBlock quote:
                var first = target.Blocks.Count;
                AddBlocks(quote, target, indent + 16);
                for (var index = first; index < target.Blocks.Count; index++)
                    target.Blocks[index].Foreground = new SolidColorBrush(ActualTheme == ElementTheme.Dark
                        ? Microsoft.UI.ColorHelper.FromArgb(255, 200, 200, 200)
                        : Microsoft.UI.ColorHelper.FromArgb(255, 80, 80, 80));
                break;
            case ListBlock list:
                AddList(list, target, indent);
                break;
            case Table table:
                AddTable(table, target, indent);
                break;
            case ThematicBreakBlock:
                AddVisual(target, new Border
                {
                    Width = 160, Height = 1, Margin = new Thickness(0, 8, 0, 8),
                    Background = Brush("DividerStrokeColorDefaultBrush")
                }, indent);
                break;
            case ContainerBlock container:
                AddBlocks(container, target, indent);
                break;
            case LeafBlock leaf when leaf.Inline is not null:
                AddParagraph(target, leaf.Inline, indent);
                break;
            case LeafBlock leaf:
                NewParagraph(target, indent).Inlines.Add(new Run { Text = leaf.Lines.ToString() });
                break;
        }
    }

    private static Paragraph NewParagraph(RichTextBlock target, double indent, double size = 15)
    {
        var paragraph = new Paragraph
        {
            FontSize = size, LineHeight = size * 1.8, Margin = new Thickness(indent, 0, 0, 8)
        };
        target.Blocks.Add(paragraph);
        return paragraph;
    }

    private void AddParagraph(RichTextBlock target, ContainerInline? inlines,
        double indent, double size = 15, bool bold = false)
    {
        var paragraph = NewParagraph(target, indent, size);
        paragraph.FontWeight = bold ? FontWeights.SemiBold : FontWeights.Normal;
        if (inlines is not null) AddInlines(inlines, paragraph.Inlines);
    }

    private static void AddVisual(RichTextBlock target, FrameworkElement element, double indent)
    {
        // Inline UI is measured independently of the surrounding text. Keep wide
        // formulas scrollable within the document instead of expanding it.
        if (element is TextBlock fallback)
        {
            NewParagraph(target, indent).Inlines.Add(new Run { Text = fallback.Text });
            return;
        }
        element.SetBinding(MaxWidthProperty, new Microsoft.UI.Xaml.Data.Binding
        {
            Source = target, Path = new PropertyPath(nameof(ActualWidth))
        });
        NewParagraph(target, indent).Inlines.Add(new InlineUIContainer { Child = element });
    }

    private void AddList(ListBlock list, RichTextBlock target, double indent)
    {
        var number = int.TryParse(list.OrderedStart, out var start) ? start : 1;
        foreach (var item in list.OfType<ListItemBlock>())
        {
            var first = target.Blocks.Count;
            AddBlocks(item, target, indent + 24);
            if (target.Blocks.Count == first) NewParagraph(target, indent + 24);
            var paragraph = (Paragraph)target.Blocks[first];
            paragraph.TextIndent = -24;
            paragraph.Inlines.Insert(0, new Run { Text = list.IsOrdered ? $"{number++}.  " : "•  " });
        }
    }

    private void AddTable(Table table, RichTextBlock target, double indent)
    {
        // Keep every cell in the same text document. Embedded grids introduce
        // independent selections and omit their text when a whole answer is copied.
        foreach (var row in table.OfType<TableRow>())
        {
            var paragraph = NewParagraph(target, indent);
            paragraph.FontWeight = row.IsHeader ? FontWeights.SemiBold : FontWeights.Normal;
            for (var column = 0; column < row.Count; column++)
            {
                if (column > 0) paragraph.Inlines.Add(new Run { Text = "    │    " });
                AddCell((TableCell)row[column], paragraph.Inlines);
            }
        }
    }

    private void AddCell(ContainerBlock cell, InlineCollection target)
    {
        foreach (var block in cell)
        {
            if (block != cell.FirstOrDefault()) target.Add(new Run { Text = " " });
            if (block is LeafBlock { Inline: { } inline }) AddInlines(inline, target);
            else if (block is LeafBlock leaf) target.Add(new Run { Text = leaf.Lines.ToString() });
            else if (block is ContainerBlock nested) AddCell(nested, target);
        }
    }

    private void AddInlines(ContainerInline source, InlineCollection target)
    {
        foreach (var inline in source)
        {
            switch (inline)
            {
                case MathInline math:
                    AddFormula(target, math.Content.ToString(), false);
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

    private void AddFormula(InlineCollection target, string latex, bool display)
    {
        var copy = MarkdownClipboard.Formula(latex, display);
        var rendered = FormulaRenderer.Render(latex, display, ActualTheme == ElementTheme.Dark);
        if (rendered is null) { target.Add(new Run { Text = copy }); return; }
        var image = new Image
        {
            Width = rendered.Width, Height = rendered.Height, Stretch = Stretch.Uniform,
            HorizontalAlignment = HorizontalAlignment.Left, VerticalAlignment = VerticalAlignment.Center
        };
        AutomationProperties.SetName(image, "公式 " + latex);
        ToolTipService.SetToolTip(image, latex);
        SetSource(image, rendered.Png);
        // Explicit dimensions prevent the inline's measure pass from collapsing
        // an Auto-sized ScrollViewer (which made display formulas invisible).
        var container = new InlineUIContainer { Child = image };
        target.Add(container);
        formulas[container] = copy;
        if (display)
        {
            displayImages.Add((image, rendered.Width, rendered.Height));
            FitDisplayImages();
        }
        image.Tapped += (_, args) =>
        {
            document.Select(container.ElementStart, container.ElementEnd);
            document.Focus(FocusState.Programmatic);
            args.Handled = true;
        };
    }

    private void FitDisplayImages()
    {
        if (document.ActualWidth <= 0) return;
        foreach (var (image, width, height) in displayImages)
        {
            var factor = Math.Min(1, Math.Max(1, document.ActualWidth - 24) / width);
            image.Width = width * factor;
            image.Height = height * factor;
        }
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
