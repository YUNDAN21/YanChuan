namespace LiveCaptions.Desktop.Models;

internal sealed record ApiProfilePreset(string Name, string BaseUrl = "", string Protocol = "chat_completions",
    string Connection = "http")
{
    public static IReadOnlyList<ApiProfilePreset> Providers { get; } =
    [
        new("DeepSeek", "https://api.deepseek.com/v1"),
        new("硅基流动", "https://api.siliconflow.cn/v1"),
        new("OpenAI", "https://api.openai.com/v1", "responses"),
        new("Anthropic", "https://api.anthropic.com/v1", "anthropic"),
        new("OpenRouter", "https://openrouter.ai/api/v1"),
    ];

    public static IReadOnlyList<ApiProfilePreset> Protocols { get; } =
    [
        new("Chat Completions"),
        new("Responses", Protocol: "responses"),
        new("Anthropic Messages", Protocol: "anthropic"),
    ];

    public static IReadOnlyList<ApiProfilePreset> Agents { get; } =
    [
        new("Codex", Connection: "codex"),
        new("Antigravity (agy)", Connection: "agy"),
    ];

    public static string TypeLabel(ApiProfile profile) => profile.UsesLocalAgent
        ? Agents.FirstOrDefault(agent => agent.Connection == profile.Connection)?.Name ?? profile.Connection
        : Protocols.FirstOrDefault(protocol => protocol.Protocol == profile.Protocol)?.Name ?? profile.Protocol;

    public ApiProfile CreateDraft() => new()
    {
        Name = Name, BaseUrl = BaseUrl, Protocol = Protocol, Connection = Connection,
    };
}
