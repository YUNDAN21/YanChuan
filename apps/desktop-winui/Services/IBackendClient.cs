using System.Text.Json;
using LiveCaptions.Desktop.Models;

namespace LiveCaptions.Desktop.Services;

public interface IBackendClient : IAsyncDisposable
{
    event Action<BackendEvent>? EventReceived;
    event Action<string>? Disconnected;
    Task<Hello> ConnectAsync();
    Task<JsonElement> CallAsync(string method, object? parameters = null);
}
