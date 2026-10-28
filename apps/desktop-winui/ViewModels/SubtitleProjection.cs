using LiveCaptions.Desktop.Models;

namespace LiveCaptions.Desktop.ViewModels;

public static class SubtitleProjection
{
    public static TranslationResult? Matching(TranslationUnit unit, CaptionSettings settings,
        IReadOnlyDictionary<string, TranslationResult> results) =>
        results.TryGetValue(unit.Key, out var result) && result.SourceText == unit.Text
        && result.TargetLanguage == settings.TargetLanguage ? result : null;

    public static (string Top, string Bottom) Compact(CaptionSettings settings,
        IReadOnlyList<TranslationUnit> units, IReadOnlyDictionary<string, TranslationResult> results)
    {
        var ready = units.Where(unit => Matching(unit, settings, results)?.State == "ready").ToList();
        if (settings.DisplayMode == "translated")
        {
            var tail = ready.TakeLast(2).Select(unit => Matching(unit, settings, results)!.Text).ToList();
            return (tail.Count > 1 ? tail[0] : "", tail.Count > 0 ? tail[^1]
                : units.Count > 0 ? "翻译中…" : "译文将在这里显示");
        }
        var current = ready.LastOrDefault() ?? units.LastOrDefault();
        return (current?.Text ?? "", current is null ? "字幕和译文将在这里显示"
            : Matching(current, settings, results)?.State == "ready"
                ? Matching(current, settings, results)!.Text : "翻译中…");
    }
}
