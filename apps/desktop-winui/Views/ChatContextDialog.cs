using LiveCaptions.Desktop.Models;
using LiveCaptions.Desktop.ViewModels;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace LiveCaptions.Desktop.Views;

public static class ChatContextDialog
{
    public static async Task ShowAsync(XamlRoot root, ElementTheme theme, DesktopViewModel model, string question)
    {
        var meetingId = model.MeetingId;
        var usage = WireJson.Read<ChatContextUsage>(await model.Backend.CallAsync("chat.usage", new { question }));
        if (meetingId != model.MeetingId) return;
        var context = model.ChatContext;
        var capacity = new NumberBox { Value = model.Configuration.ChatContextTokens, Minimum = 8192,
            Header = "模型上下文容量（token）", SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Compact };
        var body = new StackPanel { Spacing = 12, Width = 380 };
        body.Children.Add(new TextBlock { Text = usage.Description, TextWrapping = TextWrapping.Wrap });
        body.Children.Add(capacity);
        body.Children.Add(new TextBlock { Text = "压缩只处理早期聊天，最近两轮完整保留。使用当前问答模型，可恢复完整聊天历史。",
            TextWrapping = TextWrapping.Wrap, FontSize = 12, Opacity = .75 });
        body.Children.Add(new TextBlock { Text = "会议原文由助手按需搜索和读取；整场问题分批查阅。完整转写和导出保持不变。",
            TextWrapping = TextWrapping.Wrap, FontSize = 12, Opacity = .75 });
        if (context.Summary.Length > 0)
            body.Children.Add(new Expander { Header = "查看聊天历史摘要", HorizontalAlignment = HorizontalAlignment.Stretch,
                Content = new ScrollViewer { MaxHeight = 180, Content = new TextBlock {
                    Text = context.Summary, TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true, MaxWidth = 330 } } });
        var restore = new Button { Content = "恢复完整聊天历史", IsEnabled = context.Summary.Length > 0 && !model.ChatBusy };
        var error = new TextBlock { TextWrapping = TextWrapping.Wrap };
        body.Children.Add(restore); body.Children.Add(error);
        var dialog = new ContentDialog { XamlRoot = root, RequestedTheme = theme, Title = "上下文设置",
            Content = new ScrollViewer { Content = body, MaxHeight = 560 },
            PrimaryButtonText = "保存", SecondaryButtonText = "保存并压缩", CloseButtonText = "取消",
            IsSecondaryButtonEnabled = meetingId.Length > 0 && !model.ChatBusy && usage.CanCompress };
        restore.Click += async (_, _) =>
        {
            restore.IsEnabled = false;
            try
            {
                await model.Backend.CallAsync("chat.context.reset", new { meeting_id = meetingId });
                dialog.Hide();
            }
            catch (Exception exception) { error.Text = exception.Message; restore.IsEnabled = true; }
        };
        async Task SaveAsync(bool compress)
        {
            if (!double.IsFinite(capacity.Value) || capacity.Value != Math.Truncate(capacity.Value)
                || capacity.Value < 8192 || capacity.Value > int.MaxValue)
                throw new InvalidOperationException("请输入有效的整数容量");
            if (model.MeetingId != meetingId) throw new InvalidOperationException("会议已切换，请重新打开上下文设置");
            await model.Backend.CallAsync("chat.configure", new { context_tokens = (int)capacity.Value });
            if (compress)
                await model.Backend.CallAsync("chat.compress", new { meeting_id = meetingId });
        }
        async void Save(ContentDialog sender, ContentDialogButtonClickEventArgs args, bool compress)
        {
            var deferral = args.GetDeferral();
            restore.IsEnabled = false;
            try { await SaveAsync(compress); }
            catch (Exception exception) { error.Text = exception.Message; args.Cancel = true; }
            finally
            {
                restore.IsEnabled = model.ChatContext.Summary.Length > 0 && !model.ChatBusy;
                deferral.Complete();
            }
        }
        dialog.PrimaryButtonClick += (sender, args) => Save(sender, args, false);
        dialog.SecondaryButtonClick += (sender, args) => Save(sender, args, true);
        await dialog.ShowAsync();
    }
}
