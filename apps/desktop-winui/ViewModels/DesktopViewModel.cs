using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Text.Json;
using LiveCaptions.Desktop.Models;
using LiveCaptions.Desktop.Services;

namespace LiveCaptions.Desktop.ViewModels;

public sealed class DesktopViewModel : INotifyPropertyChanged
{
    public IBackendClient Backend { get; }
    public Action<Action> Post { get; set; } = action => action();
    public event PropertyChangedEventHandler? PropertyChanged;
    public event Action<string>? Changed;
    public ObservableCollection<TranscriptEntry> Entries { get; } = [];
    public ObservableCollection<SummaryEntry> Summaries { get; } = [];
    public ObservableCollection<CaptionEntry> Captions { get; } = [];
    public ObservableCollection<MeetingItem> Meetings { get; } = [];
    public ObservableCollection<ChatMessage> ChatMessages { get; } = [];
    public string MeetingId { get; private set; } = "";
    public bool ChatBusy => ChatMessages.Any(m => m.Status == "pending");
    public List<DeviceInfo> Devices { get; private set; } = [];
    public Configuration Configuration { get; private set; } = new();
    public List<TranslationUnit> TranslationUnits { get; } = [];
    public Dictionary<string, TranslationResult> Translations { get; } = [];
    public string TranslationStatus { get; private set; } = "";
    public CaptionEntry? Pending { get; private set; }
    public string Directory { get; private set; } = "";
    public string MeetingTitle { get; private set; } = "";
    public string State { get; private set; } = "idle";
    public string Mode { get; private set; } = "idle";
    public bool Active { get; private set; }
    public bool SummaryBusy { get; private set; }
    public bool Connected { get; private set; }
    public string Status { get; private set; } = "正在连接本地后端…";
    public string SummaryStatus { get; private set; } = "只总结新增内容，原始转写自动保存";
    public string Error { get; private set; } = "";
    public string LiveText => Pending?.Text ?? (Active ? "正在聆听…" : "等待开始");
    private long sequence;

    public DesktopViewModel(IBackendClient backend)
    {
        Backend = backend;
        backend.EventReceived += value => Post(() => Apply(value));
        backend.Disconnected += message => Post(() =>
        {
            Connected = false; Active = false; SummaryBusy = false;
            Status = "后端已断开"; Error = message; Notify("connection");
        });
    }

    public async Task ConnectAsync()
    {
        var hello = await Backend.ConnectAsync();
        Configuration = hello.Configuration;
        Load(hello.Snapshot);
        Connected = true; Error = ""; Notify("configuration");
        await RefreshMeetingsAsync();
        await RefreshDevicesAsync();
    }

    public async Task RefreshDevicesAsync()
    {
        Devices = WireJson.Read<List<DeviceInfo>>(await Backend.CallAsync("devices.list"));
        Notify("devices");
    }

    public async Task RefreshMeetingsAsync()
    {
        var meetings = WireJson.Read<List<MeetingItem>>(await Backend.CallAsync("meetings.list"));
        Meetings.Clear(); foreach (var item in meetings) Meetings.Add(item);
    }

    public async Task StartAsync(string mode, string title, string source, string language,
                                 string? microphoneId, string? systemId, string? file = null)
    {
        Error = "";
        await Backend.CallAsync("session.start", new { mode, title, file,
            selection = new { source, language, microphone_id = microphoneId, system_id = systemId } });
    }

    public async Task OpenAsync(MeetingItem item)
    {
        LoadMeeting(WireJson.Read<MeetingData>(await Backend.CallAsync("meetings.open", new { item.Directory })));
    }

    public async Task SummarizeAsync()
    {
        var requested = await Backend.CallAsync("summary.request");
        if (requested.ValueKind == JsonValueKind.False)
        { SummaryStatus = "暂无未总结的新内容"; Notify("summary"); }
    }

    public async Task RenameAsync(string title)
    {
        await Backend.CallAsync("meetings.rename", new { title });
        await RefreshMeetingsAsync();
    }

    public async Task DeleteAsync(MeetingItem item)
    {
        var deleted = WireJson.Read<DeletedMeeting>(
            await Backend.CallAsync("meetings.delete", new { item.Directory }));
        ApplyDeletion(deleted);
        Notify("meeting.deleted");
    }

    private void ApplyDeletion(DeletedMeeting deleted)
    {
        foreach (var item in Meetings.Where(item =>
            string.Equals(item.Directory, deleted.Directory, StringComparison.OrdinalIgnoreCase)).ToList())
            Meetings.Remove(item);
        if (MeetingId != deleted.Id) return;
        Directory = MeetingTitle = MeetingId = "";
        Entries.Clear(); Summaries.Clear(); ChatMessages.Clear();
        SummaryBusy = false; SummaryStatus = ""; Error = "";
    }

    public async Task RefreshChatAsync()
    {
        var conversation = WireJson.Read<ChatConversation>(await Backend.CallAsync("chat.list"));
        LoadChat(conversation);
    }

    public async Task SendChatAsync(string question) =>
        await Backend.CallAsync("chat.send", new { question });

    public async Task RetryChatAsync(string requestId)
    {
        var question = ChatMessages.FirstOrDefault(m => m.RequestId == requestId && m.Role == "user");
        if (question is not null) await SendChatAsync(question.Text);
    }

    private void LoadChat(ChatConversation conversation)
    {
        if (conversation.MeetingId != MeetingId) return;
        foreach (var message in conversation.Messages)
        {
            var old = ChatMessages.FirstOrDefault(m => m.Id == message.Id);
            if (old is null) ChatMessages.Add(message);
            else if (old.Status != "pending" && message.Status == "pending") continue;
            else if (old != message) ChatMessages[ChatMessages.IndexOf(old)] = message;
        }
        Notify("chat");
    }

    public void ShowError(Exception error)
    {
        Error = error.Message; Notify("error");
    }

    private void Apply(BackendEvent value)
    {
        if (value.Seq <= sequence) return;
        sequence = value.Seq;
        switch (value.Name)
        {
            case "state":
                State = value.Payload.GetProperty("state").GetString()!;
                Mode = value.Payload.GetProperty("mode").GetString()!;
                Active = value.Payload.GetProperty("active").GetBoolean();
                Status = StateLabel(State); break;
            case "meeting.loaded": LoadMeeting(WireJson.Read<MeetingData>(value.Payload)); break;
            case "meeting.renamed":
                var metadata = WireJson.Read<MeetingMetadata>(value.Payload);
                if (metadata.Id == MeetingId) MeetingTitle = metadata.Title;
                break;
            case "meeting.deleted": ApplyDeletion(WireJson.Read<DeletedMeeting>(value.Payload)); break;
            case "chat.updated": LoadChat(WireJson.Read<ChatConversation>(value.Payload)); break;
            case "transcript": Entries.Add(WireJson.Read<TranscriptEntry>(value.Payload)); break;
            case "captions":
                foreach (var item in WireJson.Read<List<CaptionEntry>>(value.Payload.GetProperty("confirmed")))
                    Captions.Add(item);
                var pending = value.Payload.GetProperty("pending");
                Pending = pending.ValueKind == JsonValueKind.Null ? null : WireJson.Read<CaptionEntry>(pending);
                break;
            case "translation.units":
                var removed = WireJson.Read<List<string>>(value.Payload.GetProperty("removed"));
                TranslationUnits.RemoveAll(unit => removed.Contains(unit.Key));
                foreach (var unit in WireJson.Read<List<TranslationUnit>>(value.Payload.GetProperty("units")))
                {
                    TranslationUnits.RemoveAll(old => old.Key == unit.Key);
                    TranslationUnits.Add(unit);
                }
                break;
            case "translation":
                var result = WireJson.Read<TranslationResult>(value.Payload);
                Translations[result.Key] = result;
                TranslationStatus = result.Error;
                break;
            case "translation.reset": Translations.Clear(); TranslationStatus = ""; break;
            case "translation.status": TranslationStatus = value.Payload.GetString() ?? ""; break;
            case "summary.summary":
                Summaries.Add(WireJson.Read<SummaryEntry>(value.Payload)); SummaryStatus = "总结已保存"; break;
            case "summary.busy": SummaryBusy = value.Payload.GetBoolean();
                if (SummaryBusy) SummaryStatus = "正在总结…"; break;
            case "summary.error": SummaryStatus = value.Payload.GetString() ?? "总结失败"; break;
            case "error": Error = value.Payload.GetString() ?? "操作失败"; break;
            case "configuration": Configuration = WireJson.Read<Configuration>(value.Payload); break;
            case "appearance": Configuration.Appearance = value.Payload.GetString()!; break;
            case "progress":
                var stage = value.Payload.GetProperty("stage").GetString() ?? "";
                var phase = value.Payload.GetProperty("state").GetString();
                var seconds = value.Payload.GetProperty("seconds").GetDouble();
                Status = $"{StageLabel(stage)} · {(phase == "reused" ? "已复用" : phase == "completed" ? $"{seconds:F1}s" : "进行中…")}";
                break;
            case "file.progress":
                Status = "已转写 " + TranscriptEntry.Format(value.Payload.GetProperty("processed_seconds").GetDouble());
                break;
            case "finished":
                Pending = null;
                _ = RefreshAfterFinishAsync(); break;
        }
        Notify(value.Name);
    }

    private async Task RefreshAfterFinishAsync()
    {
        try { await RefreshMeetingsAsync(); } catch (Exception error) { ShowError(error); }
    }

    private void Load(Snapshot value)
    {
        sequence = value.Sequence; State = value.State; Mode = value.Mode; Active = value.Active;
        SummaryBusy = value.SummaryBusy; SummaryStatus = string.IsNullOrEmpty(value.SummaryState)
            ? "只总结新增内容，原始转写自动保存" : value.SummaryState;
        Status = StateLabel(State); Pending = value.Pending;
        Captions.Clear(); foreach (var caption in value.Captions) Captions.Add(caption);
        TranslationUnits.Clear(); TranslationUnits.AddRange(value.TranslationUnits ?? []);
        Translations.Clear(); foreach (var translation in value.Translations ?? []) Translations[translation.Key] = translation;
        TranslationStatus = value.TranslationState;
        if (value.Meeting is not null) LoadMeeting(value.Meeting);
        else
        {
            Directory = MeetingTitle = MeetingId = "";
            Entries.Clear(); Summaries.Clear(); ChatMessages.Clear();
        }
        Notify("snapshot");
    }

    private void LoadMeeting(MeetingData value)
    {
        Directory = value.Directory; MeetingTitle = value.Metadata.Title; MeetingId = value.Metadata.Id;
        ChatMessages.Clear();
        Entries.Clear(); Summaries.Clear();
        foreach (var entry in value.Entries) Entries.Add(entry);
        foreach (var summary in value.Summaries) Summaries.Add(summary);
        Notify("meeting");
        _ = RefreshChatAfterOpenAsync();
    }

    private async Task RefreshChatAfterOpenAsync()
    {
        try { await RefreshChatAsync(); } catch (Exception error) { ShowError(error); }
    }

    private void Notify(string kind)
    {
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(null));
        Changed?.Invoke(kind);
    }

    private static string StateLabel(string state) => state switch
    {
        "idle" => "就绪 · 本地识别，自动保存", "preparing" => "正在准备识别模型…",
        "recording" => "正在转写", "draining" => "正在收齐尾句…", "summarizing" => "正在整理最后一段…",
        "ended" => "本次转写已结束", "error" => "转写已停止，请查看提示", _ => state,
    };

    private static string StageLabel(string stage) => stage switch
    {
        "startup_total" => "准备完成", "capture_import" => "导入采集依赖",
        "recognizer_import" => "导入识别接口", "endpoint_import" => "导入检测接口",
        "segmentation_prepare" => "准备分段模型", "vad_prepare" => "加载 Silero",
        "endpoint_prepare" => "加载 Smart Turn", "recognizer_prepare" => "准备识别模型",
        "engine_load" => "加载识别模型", "native_model_validate" => "校验 GGUF 格式",
        "native_library" => "加载原生运行库", "native_weights" => "将模型装入设备",
        "native_model" => "复用原生模型",
        "warmup" => "首次推理预热",
        "stream_setup" => "创建识别会话", "audio_start" => "打开音频设备", _ => stage,
    };
}
