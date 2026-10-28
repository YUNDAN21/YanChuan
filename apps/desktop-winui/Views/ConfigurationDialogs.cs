using LiveCaptions.Desktop.Models;
using LiveCaptions.Desktop.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace LiveCaptions.Desktop.Views;

public static class ConfigurationDialogs
{
    private static TextBox Field(string header, string text) => new()
        { Header = header, Text = text, HorizontalAlignment = HorizontalAlignment.Stretch };
    private static Button Action(string label, RoutedEventHandler handler)
    {
        var glyph = label switch { "新建" => "\uE710", "复制" => "\uE8C8",
            "删除" => "\uE74D", "恢复正文" => "\uE777", _ => "\uE713" };
        var button = IconActions.Create(glyph, label); button.Click += handler; return button;
    }
    private static ContentDialog Dialog(XamlRoot root, string title, UIElement content) => new()
    {
        XamlRoot = root, Title = title, Content = content, PrimaryButtonText = "保存并使用",
        CloseButtonText = "取消", DefaultButton = ContentDialogButton.Primary,
        RequestedTheme = ((FrameworkElement)root.Content).ActualTheme,
    };

    public static async Task ProfilesAsync(XamlRoot root, Configuration configuration, IBackendClient backend)
    {
        var drafts = configuration.ApiProfiles.Select(p => p.Copy()).ToList();
        var list = new ListView { Width = 190, MinHeight = 320, DisplayMemberPath = "Name" };
        var name = Field("配置名称", ""); var url = Field("API 地址", "");
        var model = new AutoSuggestBox { Header = "模型", PlaceholderText = "获取列表后选择，或手动输入模型 ID",
            HorizontalAlignment = HorizontalAlignment.Stretch };
        var fetchModels = IconActions.Create("\uE72C", "获取模型列表");
        var modelNotice = new TextBlock { TextWrapping = TextWrapping.Wrap,
            Text = "使用当前填写的地址、协议和密钥获取，无需先保存。" };
        var modelStatus = new StackPanel { Spacing = 6 };
        modelStatus.Children.Add(modelNotice);
        var key = new PasswordBox { Header = "API 密钥", PasswordRevealMode = PasswordRevealMode.Hidden };
        var protocol = new ComboBox { Header = "协议", HorizontalAlignment = HorizontalAlignment.Stretch };
        var protocols = new[] { "chat_completions", "responses", "anthropic" };
        foreach (var label in new[] { "OpenAI Chat Completions", "OpenAI Responses", "Anthropic Messages" })
            protocol.Items.Add(label);
        var error = new TextBlock { TextWrapping = TextWrapping.Wrap, Foreground = new Microsoft.UI.Xaml.Media.SolidColorBrush(Microsoft.UI.Colors.OrangeRed) };
        ApiProfile? current = null;
        var availableModels = new List<string>();
        var revision = 0;
        var closed = false;
        void InvalidateModels()
        {
            revision++;
            availableModels.Clear();
            model.ItemsSource = null;
            model.IsSuggestionListOpen = false;
            modelNotice.Text = "使用当前填写的地址、协议和密钥获取，无需先保存。";
        }
        url.TextChanged += (_, _) => InvalidateModels();
        key.PasswordChanged += (_, _) => InvalidateModels();
        protocol.SelectionChanged += (_, _) => InvalidateModels();
        model.TextChanged += (_, args) =>
        {
            if (args.Reason != AutoSuggestionBoxTextChangeReason.UserInput) return;
            model.ItemsSource = availableModels.Where(id => id.Contains(model.Text, StringComparison.OrdinalIgnoreCase)).ToList();
        };
        model.SuggestionChosen += (_, args) => model.Text = (string)args.SelectedItem;
        model.GotFocus += (_, _) =>
        {
            if (availableModels.Count == 0) return;
            model.ItemsSource = availableModels;
            model.IsSuggestionListOpen = true;
        };
        fetchModels.Click += async (_, _) =>
        {
            var requestedRevision = revision;
            fetchModels.IsEnabled = false;
            IconActions.Describe(fetchModels, "正在获取模型列表…");
            modelNotice.Text = "正在获取模型列表…";
            try
            {
                var result = await backend.CallAsync("profiles.models", new {
                    base_url = url.Text.Trim(), api_key = key.Password.Trim(),
                    protocol = protocols[Math.Max(0, protocol.SelectedIndex)] });
                if (closed || revision != requestedRevision) return;
                availableModels = WireJson.Read<List<string>>(result);
                model.ItemsSource = availableModels;
                modelNotice.Text = availableModels.Count == 0 ? "服务返回的模型列表为空，可手动填写模型。"
                    : $"已获取 {availableModels.Count} 个模型，点击模型框选择或输入关键词筛选。";
                model.Focus(FocusState.Programmatic);
                model.IsSuggestionListOpen = availableModels.Count > 0;
            }
            catch (Exception failure)
            {
                if (!closed && revision == requestedRevision)
                    modelNotice.Text = failure.Message;
            }
            finally
            {
                fetchModels.IsEnabled = true;
                IconActions.Describe(fetchModels, "获取模型列表");
            }
        };
        void Commit()
        {
            if (current is null) return;
            current.Name = name.Text; current.BaseUrl = url.Text; current.Model = model.Text;
            current.ApiKey = key.Password; current.Protocol = protocols[Math.Max(0, protocol.SelectedIndex)];
        }
        void Refresh(ApiProfile selected)
        {
            current = null; list.ItemsSource = null; list.ItemsSource = drafts.ToList(); list.SelectedItem = selected;
        }
        list.SelectionChanged += (_, _) =>
        {
            InvalidateModels();
            Commit(); current = list.SelectedItem as ApiProfile;
            if (current is null) return;
            name.Text = current.Name; url.Text = current.BaseUrl; model.Text = current.Model;
            key.Password = current.ApiKey; protocol.SelectedIndex = Array.IndexOf(protocols, current.Protocol);
        };
        var buttons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        buttons.Children.Add(Action("新建", (_, _) => { Commit(); var item = new ApiProfile(); drafts.Add(item); Refresh(item); }));
        buttons.Children.Add(Action("复制", (_, _) =>
        {
            Commit(); if (current is null) return;
            var item = current.Copy(); item.Id = Guid.NewGuid().ToString("N"); item.Name += " 副本";
            drafts.Add(item); Refresh(item);
        }));
        buttons.Children.Add(Action("删除", (_, _) =>
        {
            if (drafts.Count == 1) { error.Text = "至少保留一个 API 配置"; return; }
            if (current is not null) drafts.Remove(current); Refresh(drafts[0]);
        }));
        var editor = new StackPanel { Spacing = 12, Width = 400 };
        foreach (var field in new UIElement[] { name, protocol, url, key, IconActions.WithField(model, fetchModels), modelStatus, buttons, error }) editor.Children.Add(field);
        var layout = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 20 };
        layout.Children.Add(list); layout.Children.Add(new ScrollViewer { Content = editor, MaxHeight = 560 });
        var dialog = Dialog(root, "API 配置", layout);
        dialog.Resources["ContentDialogMaxWidth"] = 780d;
        dialog.Closed += (_, _) => { closed = true; revision++; };
        dialog.PrimaryButtonClick += async (_, args) =>
        {
            var deferral = args.GetDeferral(); Commit();
            try { await backend.CallAsync("profiles.save", new { profiles = drafts, selected_id = current?.Id }); }
            catch (Exception failure) { error.Text = failure.Message; args.Cancel = true; }
            finally { deferral.Complete(); }
        };
        Refresh(drafts.First(p => p.Id == configuration.SelectedApiProfile));
        await dialog.ShowAsync();
    }

    public static async Task TemplatesAsync(XamlRoot root, Configuration configuration, IBackendClient backend)
    {
        var drafts = configuration.PromptTemplates.Select(p => p.Copy()).ToList();
        var defaults = WireJson.Read<List<PromptTemplate>>(await backend.CallAsync("templates.defaults"));
        var list = new ListView { Width = 180, MinHeight = 360, DisplayMemberPath = "Name" };
        var name = Field("模板名称", "");
        var prompt = Field("总结提示词", ""); prompt.AcceptsReturn = true; prompt.TextWrapping = TextWrapping.Wrap;
        prompt.Height = 320;
        ScrollViewer.SetVerticalScrollBarVisibility(prompt, ScrollBarVisibility.Auto);
        var error = new TextBlock { TextWrapping = TextWrapping.Wrap };
        PromptTemplate? current = null;
        void Commit() { if (current is not null) { current.Name = name.Text; current.Prompt = prompt.Text; } }
        void Refresh(PromptTemplate selected)
        { current = null; list.ItemsSource = null; list.ItemsSource = drafts.ToList(); list.SelectedItem = selected; }
        list.SelectionChanged += (_, _) =>
        { Commit(); current = list.SelectedItem as PromptTemplate; if (current is not null) { name.Text = current.Name; prompt.Text = current.Prompt; } };
        var buttons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        buttons.Children.Add(Action("新建", (_, _) =>
        { Commit(); var item = new PromptTemplate { Prompt = defaults[0].Prompt }; drafts.Add(item); Refresh(item); }));
        buttons.Children.Add(Action("复制", (_, _) =>
        {
            Commit(); if (current is null) return;
            var item = current.Copy(); item.Id = Guid.NewGuid().ToString("N"); item.Name += " 副本";
            drafts.Add(item); Refresh(item);
        }));
        buttons.Children.Add(Action("删除", (_, _) =>
        {
            if (drafts.Count == 1) { error.Text = "至少保留一个模板"; return; }
            if (current is not null) drafts.Remove(current); Refresh(drafts[0]);
        }));
        buttons.Children.Add(Action("恢复正文", (_, _) =>
        { prompt.Text = defaults.FirstOrDefault(p => p.Id == current?.Id)?.Prompt ?? defaults[0].Prompt; }));
        var editor = new StackPanel { Width = 450, Spacing = 12 };
        foreach (var field in new UIElement[] { name, prompt, buttons, error }) editor.Children.Add(field);
        var layout = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 20 };
        layout.Children.Add(list); layout.Children.Add(editor);
        var dialog = Dialog(root, "提示词模板", layout);
        dialog.Resources["ContentDialogMaxWidth"] = 820d;
        dialog.PrimaryButtonClick += async (_, args) =>
        {
            var deferral = args.GetDeferral(); Commit();
            try { await backend.CallAsync("templates.save", new { templates = drafts, selected_id = current?.Id }); }
            catch (Exception failure) { error.Text = failure.Message; args.Cancel = true; }
            finally { deferral.Complete(); }
        };
        Refresh(drafts.First(p => p.Id == configuration.SelectedPromptTemplate));
        await dialog.ShowAsync();
    }

    public static async Task<(string? Microphone, string? System)?> DevicesAsync(
        XamlRoot root, List<DeviceInfo> devices, string? microphone, string? system)
    {
        ComboBox Picker(string header, string kind, string? selected)
        {
            var items = new List<DeviceInfo> { new("", "跟随系统默认设备", kind) };
            items.AddRange(devices.Where(d => d.Kind == kind));
            return new ComboBox { Header = header, ItemsSource = items, DisplayMemberPath = "Name",
                SelectedItem = items.FirstOrDefault(d => d.Id == selected) ?? items[0],
                HorizontalAlignment = HorizontalAlignment.Stretch };
        }
        var mic = Picker("麦克风", "microphone", microphone);
        var sys = Picker("系统声音 · WASAPI 回环", "system", system);
        var layout = new StackPanel { Width = 450, Spacing = 20 }; layout.Children.Add(mic); layout.Children.Add(sys);
        var dialog = Dialog(root, "音频设备", layout); dialog.PrimaryButtonText = "使用所选设备";
        if (await dialog.ShowAsync() != ContentDialogResult.Primary) return null;
        static string? Id(ComboBox box) => box.SelectedItem is DeviceInfo d && d.Id.Length > 0 ? d.Id : null;
        return (Id(mic), Id(sys));
    }
}
