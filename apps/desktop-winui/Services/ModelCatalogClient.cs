using LiveCaptions.Desktop.Models;

namespace LiveCaptions.Desktop.Services;

public static class ModelCatalogClient
{
    public static async Task<ProfileModelCatalog> FetchAsync(
        IBackendClient backend, ApiProfile profile, bool includeModels = true)
    {
        if (profile.UsesLocalAgent)
            return WireJson.Read<ProfileModelCatalog>(await backend.CallAsync("profiles.agent_catalog", new
            {
                connection = profile.Connection,
                executable = profile.Executable.Trim(),
                include_models = includeModels,
            }));

        var models = WireJson.Read<List<string>>(await backend.CallAsync("profiles.models", new
        {
            base_url = profile.BaseUrl.Trim(),
            api_key = profile.ApiKey.Trim(),
            protocol = profile.Protocol,
        }));
        return new("", "", models.Select(id => new ModelOption(id, id)).ToList());
    }
}
