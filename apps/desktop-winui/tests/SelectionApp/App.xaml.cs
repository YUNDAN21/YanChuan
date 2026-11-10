using System.Text.Json;
using LiveCaptions.Desktop.Views;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Documents;
using Windows.Graphics;

namespace LiveCaptions.SelectionTests;

public partial class App : Application
{
    private Window? window;
    private MarkdownView view = null!;
    private readonly List<string> checks = [];
    private const string Example = """
        ## 跨段落选择验证

        第一段：鼠标从这里开始，拖过下面的列表和段落。
        这一行属于同一段落，需要自然换行后仍能继续选择。这是用于验证窄窗口自动折行的长句，包含中文、English 和日本語。

        第二段：这一段应当和第一段一起被选中。

        - 列表甲：讨论问题
        - 列表乙：确认结论
          - 子列表丙：安排下一步

        > 引用丁：保留引用内容。

        ```text
        code_one = 1
        code_two = 2
        ```

        | 表格列甲 | 表格列乙 |
        | --- | --- |
        | 单元格一 | 单元格二 |
        | 单元格三 | 单元格四 |

        结尾戊：选择应包含上方代码的两行。
        """;

    public App()
    {
        UnhandledException += (_, args) =>
        {
            var report = Environment.GetCommandLineArgs().Skip(1).FirstOrDefault();
            if (report is not null)
                File.WriteAllText(report, JsonSerializer.Serialize(new { success = false, error = args.Exception.ToString() }));
        };
        InitializeComponent();
    }

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        window = new Window { Title = "言川 · Markdown 选择验证" };
        view = new MarkdownView();
        view.Text = Example;
        var panel = new Grid
        {
            Padding = new Thickness(24),
            Background = new Microsoft.UI.Xaml.Media.SolidColorBrush(Microsoft.UI.Colors.White),
            RequestedTheme = ElementTheme.Light
        };
        var conversation = new StackPanel();
        conversation.Children.Add(new MarkdownView { Text = "其他回复：这条消息不应该被一起选中。" });
        conversation.Children.Add(view);
        panel.Children.Add(new ScrollViewer { Content = conversation, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled });
        window.Content = panel;
        window.AppWindow.Resize(new SizeInt32(820, 820));
        panel.Loaded += async (_, _) =>
        {
            var reportPath = Environment.GetCommandLineArgs().Skip(1).FirstOrDefault();
            try
            {
                await Task.Delay(150);
                CheckSelection("initial");
                view.Width = 390;
                view.UpdateLayout();
                CheckSelection("wrapped");
                view.Width = double.NaN;
                panel.RequestedTheme = ElementTheme.Dark;
                panel.Background = new Microsoft.UI.Xaml.Media.SolidColorBrush(Microsoft.UI.Colors.Black);
                await Task.Delay(150);
                CheckSelection("dark");
                view.Text = "### 替换标题\n\n替换正文\n\n7. 第七项\n8. 第八项\n\n末段";
                view.UpdateLayout();
                var replaced = SelectAll();
                Require(replaced.Contains("替换标题") && replaced.Contains("替换正文")
                    && replaced.Contains("7.") && replaced.Contains("8.") && replaced.Contains("末段")
                    && !replaced.Contains("列表甲"), "replacement and ordered list");
                view.Text = "公式前 $x_i^2$ 公式后。\n\n$$\nE=mc^2\n$$\n\n结束。";
                await Task.Delay(300);
                view.UpdateLayout();
                var mathBody = (RichTextBlock)view.Content;
                mathBody.SelectAll();
                var mathCopy = view.SelectionText();
                Require(mathCopy.Contains("公式前 $x_i^2$ 公式后。")
                    && mathCopy.Contains("$$\nE=mc^2\n$$") && mathCopy.Contains("结束。"),
                    "selected inline and display formulas preserve LaTeX");
                var mathParagraphs = mathBody.Blocks.OfType<Paragraph>().ToList();
                var mathImage = mathParagraphs[0].Inlines.OfType<InlineUIContainer>().Single();
                mathBody.Select(mathImage.ElementStart, mathImage.ElementEnd);
                Require(view.SelectionText() == "$x_i^2$", "formula-only selection");
                var plain = mathParagraphs[0].Inlines.OfType<Run>().First();
                mathBody.Select(plain.ContentStart, plain.ContentEnd);
                Require(view.SelectionText() == plain.Text, "partial text excludes adjacent formula");
                var display = (Image)mathParagraphs[1].Inlines.OfType<InlineUIContainer>().Single().Child;
                Require(display.Source is not null && display.ActualWidth > 0 && display.ActualHeight > 0,
                    "display formula has a loaded bitmap and nonzero layout");
                view.Text = Example;
                panel.RequestedTheme = ElementTheme.Light;
                panel.Background = new Microsoft.UI.Xaml.Media.SolidColorBrush(Microsoft.UI.Colors.White);
                await Task.Delay(150);
                var body = (RichTextBlock)view.Content;
                body.Select(body.ContentStart, body.ContentStart);
                if (reportPath is not null)
                    await File.WriteAllTextAsync(reportPath, JsonSerializer.Serialize(new { success = true, checks }));
            }
            catch (Exception error)
            {
                if (reportPath is not null)
                    await File.WriteAllTextAsync(reportPath, JsonSerializer.Serialize(new { success = false, error = error.ToString(), checks }));
                window.Title = "选择验证失败";
            }
            finally
            {
                if (Environment.GetCommandLineArgs().Contains("--close")) window.Close();
            }
        };
        if (Environment.GetCommandLineArgs().Contains("--close"))
        {
            window.AppWindow.Move(new PointInt32(-30000, -30000));
            window.AppWindow.Show(false);
        }
        else window.Activate();
    }

    private string SelectAll()
    {
        var body = (RichTextBlock)view.Content;
        body.SelectAll();
        return body.SelectedText;
    }

    private void CheckSelection(string stage)
    {
        var selected = SelectAll();
        Require(view.SelectionText().Replace("\r", "").TrimEnd() == selected.Replace("\r", "").TrimEnd(),
            $"{stage}: clipboard serializer matches native selected text");
        var previous = -1;
        foreach (var token in new[] { "跨段落选择验证", "第一段", "第二段", "列表甲", "列表乙",
            "子列表丙", "引用丁", "code_one = 1", "code_two = 2",
            "表格列甲", "表格列乙", "单元格一", "单元格二", "单元格三", "单元格四", "结尾戊" })
        {
            var current = selected.IndexOf(token, StringComparison.Ordinal);
            Require(current > previous, $"{stage}: selected text contains {token} in order");
            previous = current;
        }
        Require(!selected.Contains("其他回复"), $"{stage}: select all is scoped to one reply");
        var body = (RichTextBlock)view.Content;
        var from = body.Blocks.OfType<Paragraph>().First(p => p.Inlines.OfType<Run>().Any(r => r.Text.Contains("第二段")));
        var to = body.Blocks.OfType<Paragraph>().First(p => p.Inlines.OfType<Run>().Any(r => r.Text.Contains("列表乙")));
        body.Select(from.ContentStart, to.ContentEnd);
        Require(body.SelectedText.Contains("第二段") && body.SelectedText.Contains("列表甲")
            && body.SelectedText.Contains("列表乙") && !body.SelectedText.Contains("第一段：鼠标")
            && !body.SelectedText.Contains("结尾戊"), $"{stage}: partial cross-paragraph selection");
    }

    private void Require(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException(message);
        checks.Add(message);
    }
}
