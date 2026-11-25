using LiveCaptions.Desktop.Models;

namespace LiveCaptions.Desktop.ViewModels;

/// <summary>One sidebar history row. Presentation only; the meeting itself is unchanged.</summary>
public sealed record MeetingRow(MeetingItem Item, string Detail, bool Current, bool Recording, bool Locked)
{
    public string Title => Item.Title;
    public bool NotRecording => !Recording;
    /// <summary>While a meeting records or summarizes, other rows fade because switching is blocked.</summary>
    public double Opacity => Locked && !Current ? 0.45 : 1;
    /// <summary>List items are announced by ToString; read the title and detail, never the folder path.</summary>
    public override string ToString() => $"{Title}，{Detail}";
}

public sealed class MeetingGroup(string key, IEnumerable<MeetingRow> rows) : List<MeetingRow>(rows)
{
    public string Key { get; } = key;
}

public static class MeetingListProjection
{
    public const string Today = "今天", Earlier = "更早";

    /// <param name="recording">True only while the current meeting is capturing audio.</param>
    /// <param name="locked">True while switching meetings is not allowed (recording, summarizing, deleting).</param>
    public static List<MeetingGroup> Build(IEnumerable<MeetingItem> meetings, string query, string currentDirectory,
        bool recording, bool locked, DateTimeOffset now)
    {
        var today = now.ToOffset(MeetingItem.DisplayOffset).Date;
        var text = query.Trim();
        var rows = meetings
            .Where(item => text.Length == 0 || item.Title.Contains(text, StringComparison.CurrentCultureIgnoreCase))
            .Select(item =>
            {
                var current = currentDirectory.Length > 0
                    && string.Equals(item.Directory, currentDirectory, StringComparison.OrdinalIgnoreCase);
                var live = current && recording;
                return new MeetingRow(item, live ? "录制中" : item.Detail(now), current, live, locked);
            })
            .ToList();
        var groups = new List<MeetingGroup>();
        var todayRows = rows.Where(row => row.Item.Started?.Date == today).ToList();
        var earlierRows = rows.Where(row => row.Item.Started?.Date != today).ToList();
        if (todayRows.Count > 0) groups.Add(new MeetingGroup(Today, todayRows));
        if (earlierRows.Count > 0) groups.Add(new MeetingGroup(Earlier, earlierRows));
        return groups;
    }
}
