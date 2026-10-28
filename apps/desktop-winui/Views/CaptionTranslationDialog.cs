using LiveCaptions.Desktop.Models;
using LiveCaptions.Desktop.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace LiveCaptions.Desktop.Views;

public static class CaptionTranslationDialog
{
    public static async Task ShowAsync(XamlRoot root, Configuration configuration, IBackendClient backend)
    {
        var current = configuration.Captions;
        var enabled = new CheckBox { Content = "自动翻译实时字幕", IsChecked = current.Enabled };
        var mode = new ComboBox { Header = "显示方式", HorizontalAlignment = HorizontalAlignment.Stretch };
        string[] modes = ["original", "bilingual", "translated"];
        foreach (var text in new[] { "仅原文", "双语 · 上原文，下译文", "仅译文" }) mode.Items.Add(text);
        mode.SelectedIndex = Array.IndexOf(modes, current.DisplayMode);
        var language = new ComboBox { Header = "目标语言", HorizontalAlignment = HorizontalAlignment.Stretch };
        string[] languages = ["zh", "ja", "en"];
        foreach (var text in new[] { "简体中文", "日本語", "English" }) language.Items.Add(text);
        language.SelectedIndex = Array.IndexOf(languages, current.TargetLanguage);
        var api = new ComboBox { Header = "翻译 API", DisplayMemberPath = "Name", HorizontalAlignment = HorizontalAlignment.Stretch };
        var profiles = new List<ApiProfile> { new() { Id = "", Name = "跟随当前 API 选择" } };
        profiles.AddRange(configuration.ApiProfiles);
        api.ItemsSource = profiles;
        api.SelectedItem = profiles.FirstOrDefault(p => p.Id == (current.ApiProfileId ?? "")) ?? profiles[0];
        var error = new TextBlock { TextWrapping = TextWrapping.Wrap };
        var content = new StackPanel { Width = 420, Spacing = 16 };
        foreach (var control in new UIElement[] { enabled, mode, language, api,
            new TextBlock { Text = "翻译在后台独立进行，保留原始字幕。紧凑模式下，鼠标移出时自动隐藏背景，移入时显示控件；展开历史时保持背景显示。", TextWrapping = TextWrapping.Wrap, Opacity = .65 }, error }) content.Children.Add(control);
        var dialog = new ContentDialog { XamlRoot = root, Title = "字幕翻译", Content = content,
            PrimaryButtonText = "保存", CloseButtonText = "取消", DefaultButton = ContentDialogButton.Primary,
            RequestedTheme = ((FrameworkElement)root.Content).ActualTheme };
        dialog.PrimaryButtonClick += async (_, args) =>
        {
            var deferral = args.GetDeferral();
            try
            {
                await backend.CallAsync("captions.configure", new CaptionSettings {
                    Enabled = enabled.IsChecked == true, DisplayMode = modes[Math.Max(0, mode.SelectedIndex)],
                    TargetLanguage = languages[Math.Max(0, language.SelectedIndex)],
                    ApiProfileId = api.SelectedItem is ApiProfile profile && profile.Id.Length > 0 ? profile.Id : null });
            }
            catch (Exception failure) { error.Text = failure.Message; args.Cancel = true; }
            finally { deferral.Complete(); }
        };
        await dialog.ShowAsync();
    }
}
