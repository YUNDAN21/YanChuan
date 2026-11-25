using LiveCaptions.Desktop.Models;
using LiveCaptions.Desktop.ViewModels;

static class MeetingListChecks
{
    static void Check(bool condition, string reason)
    { if (!condition) throw new Exception(reason); }

    public static void Run()
    {
        var now = DateTimeOffset.Parse("2026-11-24T09:00:00Z");
        MeetingItem[] meetings =
        [
            new("d1", "产品周会", "2026-11-24T06:05:00Z", "ended", "2026-11-24T06:51:00Z"),
            new("d2", "客户访谈", "2026-11-24T02:30:00Z", "recording"),
            new("d3", "播客选题", "2026-11-20T12:10:00Z", "ended", "2026-11-20T13:15:00Z"),
        ];
        var groups = MeetingListProjection.Build(meetings, "", "d1", recording: false, locked: false, now);
        Check(groups.Select(g => g.Key).SequenceEqual([MeetingListProjection.Today, MeetingListProjection.Earlier]),
            "Meetings were not grouped by today/earlier in display time");
        Check(groups[0][0].Detail == "14:05 · 46 分钟", "Today row should show time and duration");
        Check(groups[0][1].Detail == "10:30", "An open meeting must not show a duration");
        Check(groups[1][0].Detail == "11-20 20:10 · 1 小时 5 分钟", "Earlier row should show date, time and duration");
        Check(groups[0][0].Current && !groups[0][1].Current, "Current meeting was not marked");

        var live = MeetingListProjection.Build(meetings, "", "d1", recording: true, locked: true, now);
        Check(live[0][0].Recording && live[0][0].Detail == "录制中" && live[0][0].Opacity == 1,
            "Recording meeting should stay prominent");
        Check(live[0][1].Opacity < 1 && live[1][0].Opacity < 1, "Other meetings must fade while switching is locked");

        var found = MeetingListProjection.Build(meetings, "  播客 ", "", recording: false, locked: false, now);
        Check(found.Count == 1 && found[0].Key == MeetingListProjection.Earlier && found[0].Single().Title == "播客选题",
            "Search did not filter by title");
        Check(MeetingListProjection.Build(meetings, "不存在", "", false, false, now).Count == 0,
            "Unmatched search should produce no groups");
        Check(MeetingTime.Format(TimeSpan.FromSeconds(20)) == "不到 1 分钟"
            && MeetingTime.Format(TimeSpan.FromMinutes(120)) == "2 小时", "Duration formatting changed");
    }
}
