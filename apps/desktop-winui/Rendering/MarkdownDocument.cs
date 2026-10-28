using System.Text;
using System.Text.RegularExpressions;
using Markdig;

namespace LiveCaptions.Desktop.Rendering;

/// <summary>Presentation-only parsing. Never evaluates HTML or fetches resources.</summary>
public static class MarkdownDocument
{
    private static readonly MarkdownPipeline Pipeline = new MarkdownPipelineBuilder()
        .UsePipeTables().UseEmphasisExtras().UseTaskLists().UseMathematics().Build();

    public static Markdig.Syntax.MarkdownDocument Parse(string? text) =>
        Markdown.Parse(NormalizeMathDelimiters(text ?? ""), Pipeline);

    // AI APIs also emit \( \) and \[ \]; Markdig's math extension uses $ and $$.
    // Leave code fences, inline code, escaped slashes and unclosed delimiters alone.
    public static string NormalizeMathDelimiters(string text)
    {
        var result = new StringBuilder(text.Length);
        char fence = '\0'; int fenceLength = 0;
        for (var index = 0; index < text.Length;)
        {
            if (index == 0 || text[index - 1] == '\n')
            {
                var end = text.IndexOf('\n', index);
                if (end < 0) end = text.Length;
                var line = text[index..end];
                var match = Regex.Match(line, @"^ {0,3}(`{3,}|~{3,})(.*)$");
                if (match.Success)
                {
                    var run = match.Groups[1].Value;
                    if (fence == '\0') { fence = run[0]; fenceLength = run.Length; }
                    else if (run[0] == fence && run.Length >= fenceLength && string.IsNullOrWhiteSpace(match.Groups[2].Value))
                        fence = '\0';
                    result.Append(line); index = end;
                    if (index < text.Length) result.Append(text[index++]);
                    continue;
                }
                if (fence != '\0')
                {
                    result.Append(line); index = end;
                    if (index < text.Length) result.Append(text[index++]);
                    continue;
                }
            }
            if (text[index] == '`')
            {
                var start = index;
                while (index < text.Length && text[index] == '`') index++;
                var marker = text[start..index];
                var close = text.IndexOf(marker, index, StringComparison.Ordinal);
                if (close >= 0)
                { result.Append(text[start..(close + marker.Length)]); index = close + marker.Length; continue; }
                result.Append(marker); continue;
            }
            if (text[index] == '\\' && index + 1 < text.Length)
            {
                if (text[index + 1] == '\\')
                { result.Append(@"\\"); index += 2; continue; }
                if (text[index + 1] is '(' or '[')
                {
                    bool display = text[index + 1] == '[';
                    var ending = display ? @"\]" : @"\)";
                    int close = text.IndexOf(ending, index + 2, StringComparison.Ordinal);
                    if (close >= 0)
                    {
                        var content = text[(index + 2)..close].Trim();
                        result.Append(display ? "\n$$\n" : "$").Append(content).Append(display ? "\n$$\n" : "$");
                        index = close + 2; continue;
                    }
                }
            }
            result.Append(text[index++]);
        }
        return result.ToString();
    }
}
