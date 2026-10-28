using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text;
using System.Text.Json;
using LiveCaptions.Desktop.Models;

namespace LiveCaptions.Desktop.Services;

public sealed class BackendClient(string root, string? serviceRoot = null, string? verificationFile = null,
    string? nativeLibrary = null, string? pythonExecutable = null) : IBackendClient
{
    private Process? process;
    private readonly SemaphoreSlim writes = new(1, 1);
    private readonly object connectionGate = new();
    private bool connected;
    private readonly ConcurrentDictionary<long, TaskCompletionSource<JsonElement>> pending = new();
    private long nextId;
    private bool closing;
    private Task? reader;
    private Task? errors;
    public event Action<BackendEvent>? EventReceived;
    public event Action<string>? Disconnected;

    public async Task<Hello> ConnectAsync()
    {
        if (process is not null && !process.HasExited && connected)
            return WireJson.Read<Hello>(await CallAsync("hello"));
        if (process is not null && !process.HasExited)
        {
            process.StandardInput.Close();
            await process.WaitForExitAsync();
        }
        if (reader is not null) await reader;
        if (errors is not null) await errors;
        process?.Dispose();
        closing = false;
        var python = pythonExecutable ?? Path.Combine(root, "backend", "python", "python.exe");
        if (pythonExecutable is null && !File.Exists(python))
            python = Path.Combine(root, "apps", "desktop-backend", ".venv", "Scripts", "python.exe");
        if (!File.Exists(python)) throw new FileNotFoundException("请先运行启动脚本安装后端依赖。", python);
        var start = new ProcessStartInfo(python)
        {
            WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
            StandardInputEncoding = new UTF8Encoding(false),
            StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8,
        };
        start.ArgumentList.Add("-u"); start.ArgumentList.Add("-m");
        start.ArgumentList.Add("livecaptions_backend"); start.ArgumentList.Add("--root");
        start.ArgumentList.Add(serviceRoot ?? root);
        if (verificationFile is not null)
        { start.ArgumentList.Add("--verify-pcm"); start.ArgumentList.Add(verificationFile); }
        if (nativeLibrary is not null)
        { start.ArgumentList.Add("--native-library"); start.ArgumentList.Add(nativeLibrary); }
        start.Environment["PYTHONUTF8"] = "1";
        start.Environment["PYTHONDONTWRITEBYTECODE"] = "1";
        process = Process.Start(start) ?? throw new IOException("无法启动后端进程");
        lock (connectionGate) connected = true;
        reader = ReadAsync(process);
        errors = DrainErrorsAsync(process);
        var hello = WireJson.Read<Hello>(await CallAsync("hello"));
        if (hello.Protocol != 1) throw new InvalidDataException("后端协议版本不匹配");
        return hello;
    }

    public async Task<JsonElement> CallAsync(string method, object? parameters = null)
    {
        var target = process;
        if (target is null || target.HasExited) throw new IOException("后端未连接，请重新连接。");
        var id = Interlocked.Increment(ref nextId);
        var source = new TaskCompletionSource<JsonElement>(TaskCreationOptions.RunContinuationsAsynchronously);
        lock (connectionGate)
        {
            if (!connected) throw new IOException("后端未连接，请重新连接。");
            pending[id] = source;
        }
        try
        {
            var message = JsonSerializer.Serialize(new { id, method, @params = parameters ?? new { } }, WireJson.Options);
            await writes.WaitAsync();
            try { await target.StandardInput.WriteLineAsync(message); await target.StandardInput.FlushAsync(); }
            finally { writes.Release(); }
            return await source.Task;
        }
        catch { pending.TryRemove(id, out _); throw; }
    }

    private async Task ReadAsync(Process target)
    {
        string message = "后端已退出，已保存的会议记录可重新打开。";
        try
        {
            while (await target.StandardOutput.ReadLineAsync() is { } line)
            {
                using var doc = JsonDocument.Parse(line);
                var value = doc.RootElement;
                if (value.GetProperty("type").GetString() == "event")
                    EventReceived?.Invoke(WireJson.Read<BackendEvent>(value));
                else if (value.TryGetProperty("id", out var identifier) && identifier.TryGetInt64(out var id)
                         && pending.TryRemove(id, out var response))
                {
                    if (value.TryGetProperty("error", out var error))
                        response.TrySetException(new InvalidOperationException(error.GetProperty("message").GetString()));
                    else response.TrySetResult(value.GetProperty("result").Clone());
                }
            }
        }
        catch (Exception ex) when (ex is IOException or JsonException or InvalidOperationException)
        { message = "后端通信中断，请重新连接。"; }
        finally
        {
            lock (connectionGate)
            {
                connected = false;
                foreach (var pair in pending)
                    if (pending.TryRemove(pair.Key, out var source)) source.TrySetException(new IOException(message));
            }
            if (!closing) Disconnected?.Invoke(message);
        }
    }

    private async Task DrainErrorsAsync(Process target)
    {
        var directory = Path.Combine(serviceRoot ?? root, "data", "desktop-backend");
        Directory.CreateDirectory(directory);
        await using var file = new FileStream(Path.Combine(directory, "stderr.log"),
            FileMode.Append, FileAccess.Write, FileShare.ReadWrite);
        await using var log = new StreamWriter(file, Encoding.UTF8);
        // Python's adapter never logs requests or configuration payloads.
        while (await target.StandardError.ReadLineAsync() is { } line)
        { await log.WriteLineAsync(line); await log.FlushAsync(); }
    }

    public async ValueTask DisposeAsync()
    {
        closing = true;
        if (process is { HasExited: false })
        {
            try { if (connected) await CallAsync("shutdown"); } catch (IOException) { }
            process.StandardInput.Close();
            await process.WaitForExitAsync();
        }
        if (reader is not null) await reader;
        if (errors is not null) await errors;
        process?.Dispose();
        process = null;
    }
}
