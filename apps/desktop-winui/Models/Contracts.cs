using System.Text.Json;

namespace LiveCaptions.Desktop.Models;

public static class WireJson
{
    public static readonly JsonSerializerOptions Options = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        PropertyNameCaseInsensitive = true,
    };
    public static T Read<T>(JsonElement value) =>
        value.Deserialize<T>(Options) ?? throw new InvalidDataException("后端响应为空");
}

public sealed record DeviceInfo(string Id, string Name, string Kind);
public sealed record TranscriptEntry(int Id, string SourceKey, double StartSeconds,
                                     double EndSeconds, string Text)
{
    public string Time => $"{Format(StartSeconds)} — {Format(EndSeconds)}";
    public static string Format(double seconds) => TimeSpan.FromSeconds(Math.Max(0, seconds))
        .ToString(@"hh\:mm\:ss");
}
public sealed record SummaryEntry(int Id, int FirstEntry, int LastEntry, double StartSeconds,
                                  double EndSeconds, string Text, string Model, string CreatedAt)
{
    public string Time => $"{TranscriptEntry.Format(StartSeconds)} — {TranscriptEntry.Format(EndSeconds)}";
}
public sealed record CaptionEntry(string Key, string Text, double StartSeconds, bool Pending = false);
public sealed record TranslationUnit(string Key, string Text, double StartSeconds, string SourceLanguage, bool Pending);
public sealed record TranslationResult(string Key, string SourceText, string Text, string TargetLanguage,
    double StartSeconds, bool Pending, string State, string Error, int Generation);
public sealed class CaptionSettings
{
    public bool Enabled { get; set; } = true;
    public string DisplayMode { get; set; } = "bilingual";
    public string TargetLanguage { get; set; } = "zh";
    public string? ApiProfileId { get; set; }
}
public sealed record MeetingMetadata(string Id, string Title, string StartedAt, string? EndedAt,
                                     string Status, int SummaryCursor);
public sealed record MeetingData(string Directory, MeetingMetadata Metadata,
                                 List<TranscriptEntry> Entries, List<SummaryEntry> Summaries);
public sealed record DeletedMeeting(string Id, string Directory, string Title, bool Current);
public sealed record MeetingItem(string Directory, string Title, string StartedAt, string Status)
{
    public string DisplayTime => DateTimeOffset.TryParse(StartedAt, out var time)
        ? time.ToOffset(TimeSpan.FromHours(8)).ToString("MM-dd HH:mm") : StartedAt;
}

public sealed record ChatMessage(int Id, string RequestId, string Role, string Text, string Status,
    string Error, string Model, string CreatedAt, int ThroughEntry)
{
    public string RoleLabel => Role == "user" ? "你" : "会议助手";
    public string StateText => Status switch {
        "pending" => "正在阅读会议并回答…", "failed" or "cancelled" or "interrupted" => Error, _ => "" };
    public bool CanRetry => Role == "assistant" && Status is "failed" or "cancelled" or "interrupted";
    public bool CanAddToNotes => Role == "assistant" && Status == "completed" && !string.IsNullOrWhiteSpace(Text);
}
public sealed record NoteGroup(string Id, string Title, string? MeetingId, string CreatedAt,
    string UpdatedAt, int NoteCount = 0);
public sealed record NoteEntry(string Id, string GroupId, string Title, string Content,
    string? SourceRequestId, string CreatedAt, string UpdatedAt);
public sealed record ChatConversation(string? MeetingId, List<ChatMessage> Messages);
public sealed record ChatContextUsage(string? MeetingId, long EstimatedTokens, long ReservedTokens,
    long TotalTokens, long ContextTokens, bool OverBudget)
{
    public double Percent => ContextTokens > 0 ? Math.Clamp(100.0 * TotalTokens / ContextTokens, 0, 100) : 0;
    public string Description => $"上下文预计占用：{TotalTokens:N0} / {ContextTokens:N0} token（{Percent:F1}%）\n"
        + $"输入约 {EstimatedTokens:N0}，预留回答 {ReservedTokens:N0}\n"
        + (OverBudget ? "已超出上限，点击调整上下文长度" : "点击调整上下文长度");
}

public sealed class ApiProfile
{
    public string Id { get; set; } = Guid.NewGuid().ToString("N");
    public string Name { get; set; } = "新 API";
    public string BaseUrl { get; set; } = "http://127.0.0.1:1234/v1";
    public string Model { get; set; } = "";
    public string ApiKey { get; set; } = "";
    public string Protocol { get; set; } = "chat_completions";
    public ApiProfile Copy() => (ApiProfile)MemberwiseClone();
    public override string ToString() => Name;
}
public sealed class PromptTemplate
{
    public string Id { get; set; } = Guid.NewGuid().ToString("N");
    public string Name { get; set; } = "新模板";
    public string Prompt { get; set; } = "";
    public PromptTemplate Copy() => (PromptTemplate)MemberwiseClone();
    public override string ToString() => Name;
}
public sealed class Configuration
{
    public int IntervalMinutes { get; set; } = 5;
    public bool AutomaticSummary { get; set; } = true;
    public List<ApiProfile> ApiProfiles { get; set; } = [];
    public string SelectedApiProfile { get; set; } = "default";
    public List<PromptTemplate> PromptTemplates { get; set; } = [];
    public string SelectedPromptTemplate { get; set; } = "general";
    public string Appearance { get; set; } = "light";
    public CaptionSettings Captions { get; set; } = new();
    public string? ChatApiProfile { get; set; }
    public int ChatContextTokens { get; set; } = 272000;
    public string ChatModel { get; set; } = "";
    public string ChatReasoningEffort { get; set; } = "default";
}
public sealed record AudioSourceState(string Source, string? RequestedSource = null,
                                     bool Switching = false, string Error = "");
public sealed record Snapshot(long Sequence, string? SessionId, string State, string Mode,
                              bool Active, bool SummaryBusy, string SummaryState,
                              MeetingData? Meeting, List<CaptionEntry> Captions, CaptionEntry? Pending,
                              List<TranslationUnit>? TranslationUnits = null,
                              List<TranslationResult>? Translations = null, string TranslationState = "",
                              AudioSourceState? AudioSource = null);
public sealed record Hello(int Protocol, Configuration Configuration, Snapshot Snapshot);
public sealed record BackendEvent(long Seq, string? SessionId, string Name, JsonElement Payload);
public sealed class ModelPreferences
{
    public string Device { get; set; } = "vulkan";
    public string SelectedModel { get; set; } = "qwen-0.6b";
    public string Source { get; set; } = "huggingface";
    public string? Directory { get; set; }
    public string? ModelFile { get; set; }
}
public sealed record ModelAsset(string Id, string Name, string Kind, bool Ready, string Path)
{
    public override string ToString() => Name + (Ready ? " · 已下载" : " · 未下载");
}
public sealed record ModelDownload(string State, string Message, double Current = 0, double Total = 0);
public sealed record ModelInventory(ModelPreferences Settings, string Directory, List<ModelAsset> Assets,
    ModelDownload Download, List<string>? Sources = null, string Runtime = "crispasr-0.8.45-lc1");
