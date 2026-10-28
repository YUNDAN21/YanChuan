using System.Diagnostics;
using System.Text.Json;
using LiveCaptions.Desktop.Models;
using LiveCaptions.Desktop.Services;
using LiveCaptions.Desktop.ViewModels;

static void Check(bool condition, string reason)
{ if (!condition) throw new Exception(reason); }

var root = Path.GetFullPath(args[0]);
var brandingRoot = Path.Combine(Path.GetTempPath(), "yanchuan-paths-" + Guid.NewGuid().ToString("N"));
var bundle = Path.Combine(brandingRoot, "bundle");
var localData = Path.Combine(brandingRoot, "local");
Directory.CreateDirectory(bundle);
Check(ApplicationPaths.DataRoot(bundle, localData) == bundle, "Development data path changed");
File.WriteAllText(Path.Combine(bundle, "yanchuan.runtime.json"), "{}");
Check(ApplicationPaths.DataRoot(bundle, localData) == Path.Combine(localData, "YanChuan"), "New brand data path missing");
var legacyData = Path.Combine(localData, "LiveCaptions");
Directory.CreateDirectory(legacyData);
File.WriteAllText(Path.Combine(legacyData, "retained.txt"), "keep");
Check(ApplicationPaths.DataRoot(bundle, localData) == legacyData, "Existing data was not reused after rename");
Check(ApplicationPaths.DataRoot(bundle, localData, "explicit") == "explicit", "Explicit data root was overridden");
File.Delete(Path.Combine(bundle, "yanchuan.runtime.json"));
File.WriteAllText(Path.Combine(bundle, "livecaptions.runtime.json"), "{}");
Check(ApplicationPaths.FindMarker(bundle) is not null, "Legacy runtime marker not recognized");
Check(Path.GetFullPath(brandingRoot).StartsWith(Path.GetFullPath(Path.GetTempPath()), StringComparison.OrdinalIgnoreCase),
    "Temporary branding fixture escaped its workspace");
Directory.Delete(brandingRoot, recursive: true);
MarkdownChecks.Run(Path.Combine(Path.GetTempPath(), "livecaptions-markdown-checks"));
var contextUsage = new ChatContextUsage("m", 4000, 4096, 8096, 8192, false);
Check(contextUsage.Percent > 98 && contextUsage.Percent < 99, "Context ring ratio omitted output reserve");
Check(contextUsage.Description.Contains("8,192") && contextUsage.Description.Contains("4,096"), "Context tooltip missing limit/reserve");
Check((contextUsage with { TotalTokens = 10000, OverBudget = true }).Percent == 100, "Over-budget ring must be clamped");
var fake = new MeetingBackend();
var viewModel = new DesktopViewModel(fake);
await viewModel.ConnectAsync();
var sampleMeeting = new MeetingData("meeting-a", new("a", "原名", "2026-10-27", null, "ended", 0), [], []);
fake.CurrentMeeting = "a";
fake.Emit("meeting.loaded", sampleMeeting);
fake.Emit("chat.updated", new ChatConversation("a", [new(1, "req", "assistant", "", "pending", "", "model", "", 0)]));
Check(viewModel.ChatBusy, "Chat pending state missing");
fake.Emit("chat.updated", new ChatConversation("other", [new(1, "req", "assistant", "wrong meeting", "completed", "", "model", "", 0)]));
Check(viewModel.ChatBusy && viewModel.ChatMessages[0].Text == "", "Cross-meeting answer leaked");
fake.Emit("chat.updated", new ChatConversation("a", [new(1, "req", "assistant", "正确回答", "completed", "", "model", "", 0)]));
fake.Emit("chat.updated", new ChatConversation("a", [new(1, "req", "assistant", "", "pending", "", "model", "", 0)]));
Check(!viewModel.ChatBusy && viewModel.ChatMessages.Count == 1 && viewModel.ChatMessages[0].Text == "正确回答", "Stale history replaced final answer");
fake.Emit("meeting.renamed", sampleMeeting.Metadata with { Title = "新名" });
Check(viewModel.MeetingTitle == "新名", "Rename event was not projected");
viewModel.Meetings.Add(new("meeting-a", "新名", "2026-10-27", "ended"));
viewModel.Meetings.Add(new("meeting-b", "另一个会议", "2026-10-27", "ended"));
fake.Emit("meeting.deleted", new DeletedMeeting("b", "meeting-b", "另一个会议", false));
Check(viewModel.MeetingId == "a" && viewModel.Meetings.Count == 1, "Deleting history changed current meeting");
fake.Emit("meeting.deleted", new DeletedMeeting("a", "meeting-a", "新名", true));
Check(viewModel.MeetingId == "" && viewModel.Directory == "" && viewModel.ChatMessages.Count == 0
    && viewModel.Meetings.Count == 0 && viewModel.Entries.Count == 0 && viewModel.Summaries.Count == 0,
    "Deleting current meeting retained stale content");
fake.Emit("chat.updated", new ChatConversation("a", [new(2, "late", "assistant", "late", "completed", "", "", "", 0)]));
Check(viewModel.ChatMessages.Count == 0, "Deleted meeting received a late answer");
fake.Disconnect();
await viewModel.ConnectAsync();
Check(viewModel.MeetingId == "" && viewModel.ChatMessages.Count == 0 && !viewModel.Active, "Reconnect retained obsolete meeting state");
var chrome = new CaptionChromeState();
Check(!chrome.Visible, "Compact captions must hide chrome outside the pointer");
chrome.PointerInside = true; Check(chrome.Visible, "Hover did not reveal chrome");
chrome.Expanded = true; chrome.PointerInside = false; Check(chrome.Visible, "History must keep chrome visible");
chrome.Expanded = false; chrome.OpenInteractions = 1; Check(chrome.Visible, "Open dialogs must keep chrome visible");
chrome.OpenInteractions = 0; Check(!chrome.Visible, "Closing history/dialog must restore auto transparency");
var subtitleSettings = new CaptionSettings { TargetLanguage = "zh", DisplayMode = "bilingual" };
var units = new List<TranslationUnit> { new("a", "first", 0, "en", false), new("b", "second", 1, "en", false) };
var translationResults = new Dictionary<string, TranslationResult> {
    ["b"] = new("b", "second", "第二句", "zh", 1, false, "ready", "", 0) };
var pair = SubtitleProjection.Compact(subtitleSettings, units, translationResults);
Check(pair.Top == "second" && pair.Bottom == "第二句", "Bilingual pairing failed");
translationResults["a"] = new("a", "first", "第一句", "zh", 0, false, "ready", "", 0);
Check(SubtitleProjection.Compact(subtitleSettings, units, translationResults) == pair, "Late translation regressed captions");
subtitleSettings.DisplayMode = "translated";
pair = SubtitleProjection.Compact(subtitleSettings, units, translationResults);
Check(pair.Top == "第一句" && pair.Bottom == "第二句", "Translation-only view exposed source text");
subtitleSettings.TargetLanguage = "ja";
pair = SubtitleProjection.Compact(subtitleSettings, units, translationResults);
Check(pair.Top == "" && pair.Bottom == "翻译中…", "Target language switch showed stale translation");
translationResults["b"] = new("b", "obsolete source", "stale", "ja", 1, false, "ready", "", 1);
Check(SubtitleProjection.Compact(subtitleSettings, units, translationResults).Bottom == "翻译中…", "Revision mismatch showed stale translation");
var temporary = Path.Combine(Path.GetTempPath(), "livecaptions-client-" + Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(temporary);
await using (var client = new BackendClient(root, temporary))
{
    var events = new List<BackendEvent>();
    client.EventReceived += message => { lock (events) events.Add(message); };
    var hello = await client.ConnectAsync();
    var emptyUsage = WireJson.Read<ChatContextUsage>(await client.CallAsync("chat.usage", new { question = "draft 中文" }));
    Check(emptyUsage.MeetingId is null && emptyUsage.ContextTokens == 272000 && emptyUsage.TotalTokens > 4096,
        "Context estimate JSONL contract failed");
    Check(hello.Protocol == 1 && !hello.Snapshot.Active, "Handshake failed");
    await client.CallAsync("chat.configure", new { model = "independent-model", reasoning_effort = "high" });
    await client.CallAsync("chat.configure", new { context_tokens = 16384 });
    var chatOptions = WireJson.Read<Configuration>(await client.CallAsync("configuration.get"));
    Check(chatOptions.ChatModel == "independent-model" && chatOptions.ChatReasoningEffort == "high"
        && chatOptions.ChatContextTokens == 16384, "Partial chat options lost model/effort");
    await client.CallAsync("captions.configure", new CaptionSettings { Enabled = false,
        DisplayMode = "translated", TargetLanguage = "ja" });
    var captionConfiguration = WireJson.Read<Configuration>(await client.CallAsync("configuration.get"));
    Check(captionConfiguration.Captions.DisplayMode == "translated" && captionConfiguration.Captions.TargetLanguage == "ja", "Caption settings roundtrip failed");
    var calls = await Task.WhenAll(Enumerable.Range(0, 12).Select(_ => client.CallAsync("snapshot")));
    Check(calls.All(result => !result.GetProperty("active").GetBoolean()), "Response correlation failed");
    await client.CallAsync("appearance.set", new { mode = "dark" });
    var configuration = WireJson.Read<Configuration>(await client.CallAsync("configuration.get"));
    Check(configuration.Appearance == "dark", "UTF8 configuration failed");
    var templates = configuration.PromptTemplates.Select(t => t.Copy()).ToList();
    templates.Add(new PromptTemplate { Id = "custom", Name = "中文模板", Prompt = "日本語と English 的测试" });
    await client.CallAsync("templates.save", new { templates, selected_id = "custom" });
    configuration = WireJson.Read<Configuration>(await client.CallAsync("configuration.get"));
    Check(configuration.PromptTemplates[^1].Name == "中文模板", "Chinese template roundtrip failed");
    var draft = configuration.PromptTemplates[^1].Copy(); draft.Name = "discarded";
    Check(configuration.PromptTemplates[^1].Name == "中文模板", "Draft isolation failed");
    try { await client.CallAsync("session.start", new { mode = "invalid" }); throw new Exception("Expected validation error"); }
    catch (InvalidOperationException) { }
    var split = WireJson.Read<List<string>>(await client.CallAsync("subtitle.split", new {
        text = "今天确认字幕，明天讨论翻译。", width = 100,
        glyph_widths = "今天确认字幕，明讨论翻译。".Distinct().ToDictionary(c => c.ToString(), _ => 20d) }));
    Check(split.Count > 1 && string.Concat(split) == "今天确认字幕，明天讨论翻译。", "Subtitle preservation failed");
    Check(events.Any(e => e.Name == "appearance") && events.Any(e => e.Name == "configuration"), "Events missing");
}
// A fresh child uses the saved configuration and remains idle.
await using (var client = new BackendClient(root, temporary))
{
    var hello = await client.ConnectAsync();
    Check(hello.Configuration.Appearance == "dark" && !hello.Snapshot.Active, "Restart persistence failed");
    Check(hello.Configuration.ChatModel == "independent-model"
        && hello.Configuration.ChatReasoningEffort == "high", "Chat options did not survive restart");
    var disconnected = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    client.Disconnected += _ => disconnected.TrySetResult();
    await client.CallAsync("shutdown");
    await disconnected.Task.WaitAsync(TimeSpan.FromSeconds(10));
    try { await client.CallAsync("snapshot"); throw new Exception("Expected disconnected error"); }
    catch (IOException) { }
    var reconnect = await client.ConnectAsync();
    Check(!reconnect.Snapshot.Active, "Reconnect must not auto-record");
}
Console.WriteLine("PASS: real C# / Python process, correlation, UTF8, draft isolation, events, semantic layout, restart and reconnect");

sealed class MeetingBackend : IBackendClient
{
    public event Action<BackendEvent>? EventReceived;
    public event Action<string>? Disconnected;
    public string CurrentMeeting = "";
    private long sequence;
    public Task<Hello> ConnectAsync() => Task.FromResult(new Hello(1, new Configuration(),
        new Snapshot(sequence, null, "idle", "idle", false, false, "", null, [], null)));
    public Task<JsonElement> CallAsync(string method, object? parameters = null) => Task.FromResult(
        method == "chat.list" ? JsonSerializer.SerializeToElement(new ChatConversation(CurrentMeeting, []), WireJson.Options)
        : JsonSerializer.SerializeToElement(Array.Empty<object>()));
    public void Emit(string name, object value) => EventReceived?.Invoke(new BackendEvent(++sequence,
        null, name, JsonSerializer.SerializeToElement(value, WireJson.Options)));
    public void Disconnect() => Disconnected?.Invoke("test disconnect");
    public ValueTask DisposeAsync() => ValueTask.CompletedTask;
}
