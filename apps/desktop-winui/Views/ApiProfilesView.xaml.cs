using LiveCaptions.Desktop.Models;
using LiveCaptions.Desktop.Services;
using Microsoft.UI.Text;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace LiveCaptions.Desktop.Views;

public sealed partial class ApiProfilesView : UserControl
{
    private readonly IBackendClient backend;
    private List<ModelOption> availableModels = [];
    private int catalogRevision;
    private bool loadingFields;
    private bool connected = true;

    private ApiProfile? Draft => (Profiles.SelectedItem as ListViewItem)?.Tag as ApiProfile;
    private bool ChoosingConnection => NewConnection.Visibility == Visibility.Visible;
    public event Action? Finished;

    public ApiProfilesView(Configuration configuration, IBackendClient backend)
    {
        this.backend = backend;
        InitializeComponent();
        AddPresetGroup("服务商预设", ApiProfilePreset.Providers);
        AddPresetGroup("通用 API", ApiProfilePreset.Protocols);
        AddPresetGroup("本地 Agent", ApiProfilePreset.Agents);

        foreach (var profile in configuration.ApiProfiles) AddRow(profile.Copy());
        Profiles.SelectedItem = Profiles.Items.OfType<ListViewItem>().FirstOrDefault(row =>
            ((ApiProfile)row.Tag).Id == configuration.SelectedApiProfile) ?? Profiles.Items.FirstOrDefault();
        if (Draft is null) SetChoosingConnection(true);
        Loaded += (_, _) => ReadAgentConfiguration();
        Unloaded += (_, _) => catalogRevision++;
    }

    public void SetConnected(bool available)
    {
        if (connected == available) return;
        connected = available;
        ResetCatalog();
        UpdateActions();
        ReadAgentConfiguration();
    }

    private void AddPresetGroup(string title, IReadOnlyList<ApiProfilePreset> presets)
    {
        var group = new StackPanel { Spacing = 8 };
        group.Children.Add(new TextBlock { Text = title, FontWeight = FontWeights.SemiBold });
        group.Children.Add(new ItemsRepeater
        {
            ItemsSource = presets,
            ItemTemplate = (DataTemplate)Resources["ConnectionOption"],
            Layout = new UniformGridLayout
            {
                MinItemWidth = 158, MinColumnSpacing = 8, MinRowSpacing = 8,
                MaximumRowsOrColumns = 3, ItemsStretch = UniformGridLayoutItemsStretch.Fill,
            },
        });
        PresetGroups.Children.Add(group);
    }

    private ListViewItem AddRow(ApiProfile draft)
    {
        var row = new ListViewItem
        {
            Tag = draft,
            Content = new TextBlock { Text = draft.Name, TextTrimming = TextTrimming.CharacterEllipsis },
        };
        Profiles.Items.Add(row);
        return row;
    }

    private void UpdateActions()
    {
        SaveButton.IsEnabled = connected && !ChoosingConnection && Draft is not null;
        DeleteButton.IsEnabled = Profiles.Items.Count > 1;
        BackToDetails.IsEnabled = Draft is not null;
    }

    private void SetChoosingConnection(bool choosing)
    {
        NewConnection.Visibility = choosing ? Visibility.Visible : Visibility.Collapsed;
        Details.Visibility = choosing ? Visibility.Collapsed : Visibility.Visible;
        SaveError.IsOpen = false;
        ResetCatalog();
        UpdateActions();
    }

    private void ShowNewConnection(object sender, RoutedEventArgs args)
    {
        SetChoosingConnection(true);
        BackToDetails.Focus(FocusState.Programmatic);
    }

    private void CancelNewConnection(object sender, RoutedEventArgs args)
    {
        SetChoosingConnection(false);
        NewButton.Focus(FocusState.Programmatic);
        ReadAgentConfiguration();
    }

    private void CreateProfile(object sender, RoutedEventArgs args)
    {
        if (sender is not FrameworkElement { DataContext: ApiProfilePreset preset }) return;
        Profiles.SelectedItem = AddRow(preset.CreateDraft());
        ProfileName.Focus(FocusState.Programmatic);
        ProfileName.SelectAll();
    }

    private void ProfileChanged(object sender, SelectionChangedEventArgs args)
    {
        if (Draft is not { } profile)
        {
            SetChoosingConnection(true);
            return;
        }

        loadingFields = true;
        try
        {
            ProfileName.Text = profile.Name;
            ApiUrl.Text = profile.BaseUrl;
            ApiKey.Password = profile.ApiKey;
            Executable.Text = profile.Executable;
            ModelInput.Text = profile.Model;
        }
        finally { loadingFields = false; }

        ConnectionType.Text = ApiProfilePreset.TypeLabel(profile);
        HttpFields.Visibility = profile.UsesLocalAgent ? Visibility.Collapsed : Visibility.Visible;
        AgentFields.Visibility = profile.UsesLocalAgent ? Visibility.Visible : Visibility.Collapsed;
        SetChoosingConnection(false);
        ReadAgentConfiguration();
    }

    private void NameChanged(object sender, TextChangedEventArgs args)
    {
        if (loadingFields || Profiles.SelectedItem is not ListViewItem { Tag: ApiProfile draft, Content: TextBlock title }) return;
        draft.Name = ProfileName.Text;
        title.Text = string.IsNullOrWhiteSpace(draft.Name) ? "未命名连接" : draft.Name;
    }

    private void ConnectionFieldChanged(object sender, RoutedEventArgs args)
    {
        if (loadingFields || Draft is not { } draft) return;
        draft.BaseUrl = ApiUrl.Text;
        draft.ApiKey = ApiKey.Password;
        draft.Executable = Executable.Text.Trim();
        ResetCatalog();
    }

    private void ModelChanged(AutoSuggestBox box, AutoSuggestBoxTextChangedEventArgs args)
    {
        if (loadingFields || Draft is not { } draft) return;
        draft.Model = box.Text;
        if (args.Reason != AutoSuggestionBoxTextChangeReason.UserInput) return;
        box.ItemsSource = availableModels.Where(option =>
            option.ToString().Contains(box.Text, StringComparison.OrdinalIgnoreCase)).ToList();
    }

    private void ModelChosen(AutoSuggestBox box, AutoSuggestBoxSuggestionChosenEventArgs args) =>
        box.Text = ((ModelOption)args.SelectedItem).Id;

    private void ShowModelSuggestions(object sender, RoutedEventArgs args)
    {
        if (availableModels.Count == 0) return;
        ModelInput.ItemsSource = availableModels;
        ModelInput.IsSuggestionListOpen = true;
    }

    private void SetModelNotice(string text)
    {
        ModelNotice.Text = text;
        ModelNotice.Visibility = text.Length == 0 ? Visibility.Collapsed : Visibility.Visible;
    }

    private void ResetCatalog()
    {
        catalogRevision++;
        availableModels = [];
        ModelInput.ItemsSource = null;
        ModelInput.IsSuggestionListOpen = false;
        FetchModels.IsEnabled = connected;
        ResolvedExecutable.Visibility = Visibility.Collapsed;
        ModelInput.PlaceholderText = Draft?.UsesLocalAgent == true ? "留空使用 CLI 默认模型"
            : "获取列表后选择，或手动输入模型 ID";
        SetModelNotice("");
    }

    private void ReadAgentConfiguration()
    {
        if (Draft?.UsesLocalAgent == true) _ = LoadModelsAsync(includeModels: false);
    }

    private async void FetchModelList(object sender, RoutedEventArgs args) => await LoadModelsAsync(includeModels: true);

    private async Task LoadModelsAsync(bool includeModels)
    {
        if (!IsLoaded || !connected || ChoosingConnection || Draft is not { } profile) return;
        var requestedRevision = ++catalogRevision;
        var draft = profile.Copy();
        FetchModels.IsEnabled = false;
        SetModelNotice(includeModels ? "正在获取模型列表…" : "正在读取 CLI 配置…");
        try
        {
            var catalog = await ModelCatalogClient.FetchAsync(backend, draft, includeModels);
            if (catalogRevision != requestedRevision) return;
            if (draft.UsesLocalAgent)
            {
                ResolvedExecutable.Text = catalog.Executable;
                ResolvedExecutable.Visibility = catalog.Executable.Length > 0 && catalog.Executable != draft.Executable
                    ? Visibility.Visible : Visibility.Collapsed;
                ModelInput.PlaceholderText = catalog.DefaultModelHint;
            }
            SetModelNotice(catalog.Warning);
            if (!includeModels) return;
            availableModels = catalog.Models;
            ModelInput.ItemsSource = availableModels;
            if (catalog.Warning.Length == 0)
                SetModelNotice(availableModels.Count > 0 ? $"已获取 {availableModels.Count} 个模型"
                    : "模型列表为空，可手动输入模型 ID。");
            ModelInput.Focus(FocusState.Programmatic);
            ModelInput.IsSuggestionListOpen = availableModels.Count > 0;
        }
        catch (Exception failure)
        {
            if (catalogRevision == requestedRevision) SetModelNotice(failure.Message);
        }
        finally
        {
            if (catalogRevision == requestedRevision) FetchModels.IsEnabled = true;
        }
    }

    private void CopyProfile(object sender, RoutedEventArgs args)
    {
        if (Draft is not { } profile) return;
        var copy = profile.Copy();
        copy.Id = Guid.NewGuid().ToString("N");
        copy.Name += " 副本";
        Profiles.SelectedItem = AddRow(copy);
    }

    private void DeleteProfile(object sender, RoutedEventArgs args)
    {
        var index = Profiles.SelectedIndex;
        if (index < 0 || Profiles.Items.Count <= 1) return;
        Profiles.Items.RemoveAt(index);
        Profiles.SelectedIndex = Math.Min(index, Profiles.Items.Count - 1);
    }

    private void CancelEdit(object sender, RoutedEventArgs args) => Finished?.Invoke();

    private void ShowSaveError(string message)
    {
        SaveError.Message = message;
        SaveError.IsOpen = true;
    }

    private async void SaveProfiles(object sender, RoutedEventArgs args)
    {
        if (!SaveButton.IsEnabled || Draft is not { } profile) return;
        var rows = Profiles.Items.Cast<ListViewItem>().ToArray();
        var missingAddress = rows.FirstOrDefault(row => row.Tag is ApiProfile { UsesLocalAgent: false } draft
            && string.IsNullOrWhiteSpace(draft.BaseUrl));
        if (missingAddress is not null)
        {
            Profiles.SelectedItem = missingAddress;
            ApiUrl.Focus(FocusState.Programmatic);
            ShowSaveError("请填写这条连接的 API 地址。");
            return;
        }
        var profiles = rows.Select(row => (ApiProfile)row.Tag).ToArray();
        SaveError.IsOpen = false;
        IsEnabled = false;
        try
        {
            await backend.CallAsync("profiles.save", new { profiles, selected_id = profile.Id });
            if (IsLoaded) Finished?.Invoke();
        }
        catch (Exception failure)
        {
            if (IsLoaded) ShowSaveError(failure.Message);
        }
        finally { IsEnabled = true; }
    }
}
