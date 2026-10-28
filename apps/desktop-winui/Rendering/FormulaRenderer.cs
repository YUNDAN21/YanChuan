using CSharpMath.Atom;
using CSharpMath.SkiaSharp;
using CSharpMath.Rendering.FrontEnd;
using SkiaSharp;

namespace LiveCaptions.Desktop.Rendering;

public sealed record FormulaImage(byte[] Png, double Width, double Height);

/// <summary>Local LaTeX typesetting; bounded cache and bitmap dimensions, no UI or network.</summary>
public static class FormulaRenderer
{
    private const int Scale = 3, Capacity = 96;
    private static readonly object Gate = new();
    private static readonly Dictionary<(string, bool, bool), FormulaImage> Cache = [];
    private static readonly Queue<(string, bool, bool)> Order = [];

    public static FormulaImage? Render(string latex, bool display, bool dark)
    {
        if (string.IsNullOrWhiteSpace(latex) || latex.Length > 12000) return null;
        var key = (latex, display, dark);
        lock (Gate)
        {
            if (Cache.TryGetValue(key, out var cached)) return cached;
            try
            {
                var painter = new MathPainter
                {
                    LaTeX = latex, FontSize = (display ? 21 : 17) * Scale,
                    LineStyle = display ? LineStyle.Display : LineStyle.Text,
                    TextColor = dark ? new SKColor(240, 240, 240) : new SKColor(32, 32, 32),
                    DisplayErrorInline = false,
                };
                var size = painter.Measure();
                if (painter.ErrorMessage is not null || !float.IsFinite(size.Width) || !float.IsFinite(size.Height)
                    || size.Width <= 0 || size.Height <= 0 || size.Width > 8192 || size.Height > 3072)
                    return null;
                var width = (int)Math.Ceiling(size.Width) + 12;
                var height = (int)Math.Ceiling(size.Height) + 12;
                using var surface = SKSurface.Create(new SKImageInfo(width, height));
                if (surface is null) return null;
                surface.Canvas.Clear(SKColors.Transparent);
                painter.Draw(surface.Canvas, TextAlignment.TopLeft, offsetX: 6, offsetY: 6);
                using var snapshot = surface.Snapshot();
                using var png = snapshot.Encode(SKEncodedImageFormat.Png, 100);
                var rendered = new FormulaImage(png.ToArray(), width / (double)Scale, height / (double)Scale);
                while (Order.Count >= Capacity || (Order.Count > 0 && Cache.Values.Sum(image => image.Png.Length) + rendered.Png.Length > 8 * 1024 * 1024))
                    Cache.Remove(Order.Dequeue());
                Cache[key] = rendered; Order.Enqueue(key);
                return rendered;
            }
            catch (Exception error) when (error is not OutOfMemoryException)
            {
                // Unsupported formula stays selectable as source in the view.
                return null;
            }
        }
    }
}
