using LiveCaptions.Desktop.Models;
using LiveCaptions.Desktop.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;

namespace LiveCaptions.Desktop.Views;

/// <summary>Edits a chat-only draft; the backend snapshots it at the next request.</summary>
public static class ChatOptionsFlyout
{
    private sealed record Choice(ModelOption Option, string ProfileId)
    {
        public string Model => Option.Id;
        public override string ToString() => Option.ToString();
    }

    private static readonly (string Id, string Label)[] Efforts =
    [
        ("default", "默认"), ("none", "关闭"), ("low", "低"), ("medium", "中"),
        ("high", "高"), ("xhigh", "超高"), ("max", "最高")
    ];
    public static string EffortLabel(string id) => Efforts.FirstOrDefault(item => item.Id == id).Label ?? "默认";

    private static Button Icon(string glyph, string label)
    {
        var button = new Button
        {
            Content = new FontIcon { Glyph = glyph, FontSize = 16 },
            Width = 36, Height = 36, Padding = new Thickness(8),
            Background = new Microsoft.UI.Xaml.Media.SolidColorBrush(Microsoft.UI.Colors.Transparent),
            BorderThickness = new Thickness(0), CornerRadius = new CornerRadius(18)
        };
        ToolTipService.SetToolTip(button, label);
        AutomationProperties.SetName(button, label);
        return button;
    }

    public static void Show(FrameworkElement anchor, Configuration configuration, IBackendClient backend,
        Action<Exception> onError)
    {
        var profiles = configuration.ApiProfiles.Select(profile => profile.Copy()).ToList();
        var currentId = configuration.ChatApiProfile ?? configuration.SelectedApiProfile;
        var current = profiles.FirstOrDefault(profile => profile.Id == currentId)
            ?? profiles.FirstOrDefault();
        var selectedProfile = current;
        var showConnection = profiles.Any(profile => profile.UsesLocalAgent);
        List<Choice> ConfiguredModels() => profiles
            .Where(profile => !string.IsNullOrWhiteSpace(profile.Model)
                && (!showConnection || profile.Id == selectedProfile?.Id))
            .OrderBy(profile => profile.Id == current?.Id ? 0 : 1)
            .Select(profile => new Choice(new ModelOption(profile.Model, profile.Model), profile.Id))
            .DistinctBy(choice => choice.Model).ToList();
        var choices = ConfiguredModels();
        var model = new AutoSuggestBox
        {
            Header = "模型", PlaceholderText = "选择或输入模型", Width = 260,
            TextMemberPath = nameof(Choice.Model),
            Text = string.IsNullOrWhiteSpace(configuration.ChatModel) ? current?.Model ?? "" : configuration.ChatModel
        };
        var effort = new ComboBox { Header = "思考强度", HorizontalAlignment = HorizontalAlignment.Stretch };
        var fetch = Icon("\uE72C", "获取模型列表");
        var apply = Icon("\uE73E", "应用模型与思考强度");
        var notice = new TextBlock { FontSize = 12, TextWrapping = TextWrapping.Wrap, MaxWidth = 304 };
        var flyout = new Flyout { Placement = Microsoft.UI.Xaml.Controls.Primitives.FlyoutPlacementMode.TopEdgeAlignedRight };
        var alive = true;
        var requestVersion = 0;
        flyout.Closed += (_, _) => { alive = false; requestVersion++; };
        void UpdateConnection(string effortId)
        {
            var usesAgent = selectedProfile?.UsesLocalAgent == true;
            model.PlaceholderText = usesAgent ? "留空使用 CLI 默认模型" : "选择或输入模型";
            ToolTipService.SetToolTip(model, null);
            fetch.IsEnabled = true;
            effort.Items.Clear();
            foreach (var item in Efforts.Where(item => selectedProfile?.Connection != "agy" || item.Id != "none"))
                effort.Items.Add(new ComboBoxItem { Content = item.Label, Tag = item.Id });
            effort.SelectedItem = effort.Items.OfType<ComboBoxItem>()
                .FirstOrDefault(item => (string)item.Tag == effortId) ?? effort.Items[0];
        }
        UpdateConnection(configuration.ChatReasoningEffort);
        void Filter()
        {
            model.ItemsSource = choices.Where(choice =>
                choice.ToString().Contains(model.Text, StringComparison.OrdinalIgnoreCase)).ToList();
        }
        model.TextChanged += (_, args) =>
        {
            if (args.Reason != AutoSuggestionBoxTextChangeReason.UserInput) return;
            Filter();
        };
        model.GotFocus += (_, _) => { model.ItemsSource = choices; model.IsSuggestionListOpen = choices.Count > 0; };
        model.SuggestionChosen += (_, args) =>
        {
            var choice = (Choice)args.SelectedItem;
            selectedProfile = profiles.First(profile => profile.Id == choice.ProfileId);
            model.Text = choice.Model;
        };
        async Task LoadModelsAsync(bool includeModels)
        {
            if (selectedProfile is null) { notice.Text = "请先在左侧配置 API。"; return; }
            var requested = ++requestVersion;
            var profile = selectedProfile;
            fetch.IsEnabled = false;
            notice.Text = includeModels ? "正在获取模型…" : "正在读取 CLI 配置…";
            try
            {
                var catalog = await ModelCatalogClient.FetchAsync(backend, profile, includeModels);
                if (!alive || requested != requestVersion || selectedProfile?.Id != profile.Id) return;
                if (profile.UsesLocalAgent)
                {
                    model.PlaceholderText = catalog.DefaultModelHint;
                    ToolTipService.SetToolTip(model, catalog.Executable);
                }
                notice.Text = catalog.Warning;
                if (includeModels)
                {
                    choices = catalog.Models.Select(option => new Choice(option, profile.Id)).Concat(ConfiguredModels())
                        .DistinctBy(choice => choice.Model).ToList();
                    model.ItemsSource = choices;
                    if (notice.Text.Length == 0 && catalog.Models.Count == 0)
                        notice.Text = "列表为空，可手动输入模型。";
                    model.Focus(FocusState.Programmatic);
                    model.IsSuggestionListOpen = choices.Count > 0;
                }
            }
            catch (Exception error) { if (alive && requested == requestVersion) notice.Text = error.Message; }
            finally { if (alive && requested == requestVersion) fetch.IsEnabled = true; }
        }
        fetch.Click += async (_, _) => await LoadModelsAsync(includeModels: true);
        apply.Click += async (_, _) =>
        {
            if (selectedProfile is null) { notice.Text = "请先在左侧配置 API。"; return; }
            if (!selectedProfile.UsesLocalAgent && string.IsNullOrWhiteSpace(model.Text))
            { notice.Text = "请选择或输入模型。"; return; }
            requestVersion++;
            apply.IsEnabled = model.IsEnabled = effort.IsEnabled = fetch.IsEnabled = false;
            try
            {
                await backend.CallAsync("chat.configure", new
                {
                    api_profile_id = selectedProfile.Id, model = model.Text.Trim(),
                    reasoning_effort = (string)((ComboBoxItem)effort.SelectedItem).Tag
                });
                flyout.Hide();
            }
            catch (Exception error)
            {
                if (alive) notice.Text = error.Message;
                else onError(error);
            }
            finally { apply.IsEnabled = model.IsEnabled = effort.IsEnabled = fetch.IsEnabled = true; }
        };
        var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        row.Children.Add(model); row.Children.Add(fetch);
        var footer = new Grid();
        footer.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        footer.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var hint = new TextBlock { Text = "应用于下一次提问", FontSize = 12, Opacity = .6, VerticalAlignment = VerticalAlignment.Center };
        footer.Children.Add(hint); Grid.SetColumn(apply, 1); footer.Children.Add(apply);
        var body = new StackPanel { Width = 304, Spacing = 14, RequestedTheme = anchor.ActualTheme };
        if (showConnection)
        {
            var connection = new ComboBox { Header = "连接", DisplayMemberPath = "Name",
                ItemsSource = profiles, SelectedItem = selectedProfile,
                HorizontalAlignment = HorizontalAlignment.Stretch };
            connection.SelectionChanged += (_, _) =>
            {
                if (connection.SelectedItem is not ApiProfile profile || profile.Id == selectedProfile?.Id) return;
                selectedProfile = profile;
                requestVersion++;
                choices = ConfiguredModels();
                model.Text = profile.Model;
                model.ItemsSource = choices;
                model.IsSuggestionListOpen = false;
                notice.Text = "";
                UpdateConnection((effort.SelectedItem as ComboBoxItem)?.Tag as string ?? "default");
                if (profile.UsesLocalAgent) _ = LoadModelsAsync(includeModels: false);
            };
            body.Children.Add(connection);
        }
        body.Children.Add(row); body.Children.Add(effort); body.Children.Add(notice); body.Children.Add(footer);
        flyout.Content = body;
        flyout.ShowAt(anchor);
        if (current?.UsesLocalAgent == true) _ = LoadModelsAsync(includeModels: false);
    }
}
