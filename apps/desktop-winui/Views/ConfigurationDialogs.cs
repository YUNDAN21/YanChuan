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
}
