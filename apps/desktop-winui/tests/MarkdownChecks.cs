using LiveCaptions.Desktop.Rendering;
using Markdig.Extensions.Mathematics;
using Markdig.Extensions.Tables;
using Markdig.Syntax;
using Markdig.Syntax.Inlines;
using SkiaSharp;
using MarkdownDocument = LiveCaptions.Desktop.Rendering.MarkdownDocument;

static class MarkdownChecks
{
    public static void Run(string output)
    {
        static void Check(bool condition, string reason)
        { if (!condition) throw new Exception(reason); }
        var source = """
            ## 会议结论

            **准确率**与 *时延*，行内公式 $x_i^2$，另一种 \(a+b\)。

            \[
            \frac{1}{n}\sum_{i=1}^{n}x_i
            \]

            - 第一项
              - 嵌套项
            - [x] 已确认

            | 指标 | 数值 |
            | --- | --- |
            | 时延 | 1 秒 |

            `\(code\)` 和 `$raw$` 保留为代码。

            ```text
            \[not math\]
            ```
            """;
        var document = MarkdownDocument.Parse(source);
        Check(document.OfType<HeadingBlock>().Count() == 1, "Markdown headings not parsed");
        Check(document.OfType<ListBlock>().Any(), "Markdown lists missing");
        Check(document.OfType<Table>().Single().Count == 2, "Markdown table missing");
        Check(document.OfType<MathBlock>().Single().Lines.ToString().Contains(@"\frac"), "Display math not parsed");
        var paragraph = document.OfType<ParagraphBlock>().First();
        Check(paragraph.Inline!.OfType<MathInline>().Count() == 2, "Inline math delimiters not normalized");
        Check(document.OfType<FencedCodeBlock>().Single(code => code is not MathBlock).Lines.ToString() == @"\[not math\]", "Code fence was modified");
        Check(MarkdownDocument.NormalizeMathDelimiters(@"`\(x\)` \\(x\) \(unclosed") == @"`\(x\)` \\(x\) \(unclosed",
            "Code, escaped or unclosed math was modified");
        Check(MarkdownDocument.Parse("$$\nx^2+y^2=z^2\n$$").OfType<MathBlock>().Any(), "Dollar display math missing");
        var formulas = new[]
        {
            @"E=mc^2", @"\frac{a+b}{c_d}", @"\sum_{i=1}^{n} x_i",
            @"\int_0^\infty e^{-x}\,dx=1", @"\begin{pmatrix}a&b\\c&d\end{pmatrix}",
            @"\begin{aligned}a&=b+c\\d&=e-f\end{aligned}"
        };
        Directory.CreateDirectory(output);
        for (var index = 0; index < formulas.Length; index++)
        foreach (var dark in new[] { false, true })
        {
            var image = FormulaRenderer.Render(formulas[index], true, dark);
            Check(image is { Width: > 1, Height: > 1 }, $"Formula not rendered: {formulas[index]}");
            using var bitmap = SKBitmap.Decode(image!.Png);
            Check(bitmap is not null && bitmap.Width >= image.Width * 2, "Formula DPI resolution missing");
            Check(bitmap!.GetPixel(0, 0).Alpha == 0, "Formula background not transparent");
            Check(bitmap.Pixels.Any(pixel => pixel.Alpha > 0), "Formula image is blank");
            Check(bitmap.Pixels.Where(pixel => pixel.Alpha > 0).Any(pixel => dark ? pixel.Red > 200 : pixel.Red < 60),
                "Formula foreground does not follow theme");
            File.WriteAllBytes(Path.Combine(output, $"{index}-{(dark ? "dark" : "light")}.png"), image.Png);
        }
        var inline = FormulaRenderer.Render(@"\frac{x}{y}", false, false)!;
        Check(inline.Height < FormulaRenderer.Render(@"\frac{x}{y}", true, false)!.Height, "Inline math size not adapted");
        Check(FormulaRenderer.Render(@"\invalidcommand{", true, false) is null, "Invalid math did not preserve source fallback");
        Console.WriteLine($"Markdown AST and 12 formula image checks passed: {output}");
    }
}
