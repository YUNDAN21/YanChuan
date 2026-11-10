using System.Text.Json;
using LiveCaptions.Desktop.Models;
using LiveCaptions.Desktop.Services;
using LiveCaptions.Desktop.ViewModels;

static class SourceSwitchChecks
{
    private static void Check(bool value, string reason)
    { if (!value) throw new Exception(reason); }

    public static async Task RunAsync()
    {
        var backend = new SourceBackend();
        var model = new DesktopViewModel(backend);
        await model.ConnectAsync();
        Check(model.SessionId == "meeting-session" && model.AudioSource.Source == "system",
            "Source snapshot was not restored");
        var change = model.SetSourceAsync("both");
        Check(model.SourceSwitching && !change.IsCompleted, "Pending source must disable the picker");
        Check(backend.Request.GetProperty("source").GetString() == "both"
            && backend.Request.GetProperty("session_id").GetString() == model.SessionId,
            "Switch must identify the originating session");
        backend.Emit(new("system", "both", true));
        Check(model.AudioSource.RequestedSource == "both", "Pending event lost the requested source");
        backend.Emit(new("both"));
        backend.Complete(new("system", "both", true)); // response may lag behind completion event
        await change;
        Check(model.AudioSource.Source == "both" && !model.SourceSwitching,
            "A stale response overwrote the confirmed source");
        change = model.SetSourceAsync("microphone");
        backend.Emit(new("both", "microphone", true));
        backend.Emit(new("both", Error: "麦克风不可用"));
        backend.Complete(new("both"));
        await change;
        Check(model.AudioSource.Source == "both" && model.Error == "麦克风不可用"
            && !model.SourceSwitching, "Failed device switch did not roll back");
        backend.Emit(new("microphone"), "older-session");
        Check(model.AudioSource.Source == "both", "A stale session changed current input");
        change = model.SetSourceAsync("system");
        backend.Reject();
        try { await change; throw new Exception("Expected rejection"); }
        catch (InvalidOperationException) { }
        Check(!model.SourceSwitching && model.AudioSource.Source == "both",
            "Rejected request left the source picker locked");
        Console.WriteLine("PASS: source switching snapshot, session ownership, pending, rollback and event/response order");
    }

    private sealed class SourceBackend : IBackendClient
    {
        public event Action<BackendEvent>? EventReceived;
        public event Action<string>? Disconnected { add { } remove { } }
        private long sequence;
        private TaskCompletionSource<JsonElement>? pending;
        public JsonElement Request { get; private set; }
        public Task<Hello> ConnectAsync() => Task.FromResult(new Hello(1, new(),
            new(0, "meeting-session", "recording", "meeting", true, false, "", null, [], null,
                AudioSource: new("system"))));
        public Task<JsonElement> CallAsync(string method, object? parameters = null)
        {
            if (method != "session.source")
                return Task.FromResult(JsonSerializer.SerializeToElement(Array.Empty<object>()));
            Request = JsonSerializer.SerializeToElement(parameters, WireJson.Options);
            pending = new(TaskCreationOptions.RunContinuationsAsynchronously);
            return pending.Task;
        }
        public void Emit(AudioSourceState value, string session = "meeting-session") =>
            EventReceived?.Invoke(new(++sequence, session, "session.source",
                JsonSerializer.SerializeToElement(value, WireJson.Options)));
        public void Complete(AudioSourceState value) =>
            pending!.SetResult(JsonSerializer.SerializeToElement(value, WireJson.Options));
        public void Reject() => pending!.SetException(new InvalidOperationException("录音正在结束"));
        public ValueTask DisposeAsync() => ValueTask.CompletedTask;
    }
}
