using Markdig.Extensions.Mathematics;
using Markdig.Syntax;
using Markdig.Syntax.Inlines;

namespace LiveCaptions.Desktop.Rendering;

/// <summary>Clipboard math convention, independent of XAML and the model backend.</summary>
public static class MarkdownClipboard
{
    public static string Formula(string latex, bool display)
    {
        return display ? "$$\n" + latex.Trim() + "\n$$" : "$" + latex.Trim() + "$";
    }

    public static string Format(string source)
    {
        var text = MarkdownDocument.NormalizeMathDelimiters(source);
        var edits = new List<(int Start, int Length, string Text)>();
        Visit(MarkdownDocument.Parse(text));
        foreach (var edit in edits.OrderByDescending(e => e.Start))
            text = text.Remove(edit.Start, edit.Length).Insert(edit.Start, edit.Text);
        return text;

        void Inlines(ContainerInline items)
        {
            foreach (var item in items)
                if (item is MathInline math)
                    edits.Add((math.Span.Start, math.Span.Length, Formula(math.Content.ToString(), false)));
                else if (item is ContainerInline nested) Inlines(nested);
        }
        void Visit(ContainerBlock blocks)
        {
            foreach (var block in blocks)
                if (block is MathBlock math)
                    edits.Add((math.Span.Start, math.Span.Length, Formula(math.Lines.ToString(), true)));
                else if (block is FencedCodeBlock code && code.Info?.Trim() is "math" or "latex" or "tex")
                    edits.Add((code.Span.Start, code.Span.Length, Formula(code.Lines.ToString(), true)));
                else if (block is ContainerBlock nested) Visit(nested);
                else if (block is LeafBlock { Inline: { } inline }) Inlines(inline);
        }
    }
}
