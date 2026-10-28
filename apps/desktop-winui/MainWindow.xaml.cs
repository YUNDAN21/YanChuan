using System.Diagnostics;
using System.Text.Json;
using LiveCaptions.Desktop.Models;
using LiveCaptions.Desktop.ViewModels;
using LiveCaptions.Desktop.Views;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Windows.Graphics;
using Windows.Storage.Pickers;
using Windows.ApplicationModel.DataTransfer;
using Windows.System;
using Windows.UI.Core;

namespace LiveCaptions.Desktop;

public sealed partial class MainWindow : Window
{
    public DesktopViewModel Model { get; }
    private readonly string root;
    private readonly string? verifyFile, reportPath;
    private readonly bool verifyCaptions;
    private CaptionWindow? captions;
    private string? microphoneId, systemId;
    private bool updating, closing, closed;
    private bool transcriptFollowing = true, summaryFollowing = true;
    private ScrollViewer? transcriptScroll, summaryScroll;
    private ScrollViewer? chatScroll;
    private bool chatOpen, chatFollowing = true;
    private readonly DispatcherTimer copyClock = new() { Interval = TimeSpan.FromSeconds(3) };
    private readonly DispatcherTimer startupClock = new() { Interval = TimeSpan.FromSeconds(1) };
    private DateTime progressStarted;
    private string progressStatus = "";
    private readonly DispatcherTimer contextClock = new() { Interval = TimeSpan.FromMilliseconds(350) };
    private long contextRevision;
    private bool chatSending, contextRequestRunning, contextDirty, changingTheme, deletingMeeting;

    public MainWindow(DesktopViewModel model, string root, string? verifyFile = null, string? reportPath = null,
                      bool verifyCaptions = false)
    {
        Model = model; this.root = root; this.verifyFile = verifyFile; this.reportPath = reportPath;
        this.verifyCaptions = verifyCaptions;
        InitializeComponent(); Root.DataContext = model;
        contextClock.Tick += async (_, _) =>
        {
            contextClock.Stop();
            await RefreshContextUsage();
        };
        Services.AppIcons.Apply(this);
        copyClock.Tick += (_, _) => { copyClock.Stop(); CopyNotice.Text = ""; };
        model.Post = action => DispatcherQueue.TryEnqueue(() => action());
        model.Changed += Refresh;
        AppWindow.Resize(new SizeInt32(1500, 1000));
        if (AppWindow.Presenter is OverlappedPresenter presenter) presenter.PreferredMinimumWidth = 1040;
        AppWindow.Closing += Closing;
        Root.Loaded += Loaded;
        startupClock.Tick += (_, _) =>
        {
            if (Model.State == "preparing")
                Title = $"言川 · {progressStatus} · {(DateTime.Now - progressStarted).TotalSeconds:F0}s";
        };
    }

    private async void Loaded(object sender, RoutedEventArgs args)
    {
        Root.Loaded -= Loaded;
        var scale = Root.XamlRoot.RasterizationScale;
        var work = DisplayArea.GetFromWindowId(AppWindow.Id, DisplayAreaFallback.Primary).WorkArea;
        var size = new SizeInt32(Math.Min(work.Width - 40, (int)(1360 * scale)),
                                Math.Min(work.Height - 40, (int)(880 * scale)));
        AppWindow.MoveAndResize(new RectInt32(work.X + (work.Width - size.Width) / 2,
            work.Y + (work.Height - size.Height) / 2, size.Width, size.Height));
        if (AppWindow.Presenter is OverlappedPresenter presenter)
        {
            presenter.PreferredMinimumWidth = (int)(1040 * scale);
            presenter.PreferredMinimumHeight = (int)(640 * scale);
        }
        transcriptScroll = FindScroll(TranscriptList); summaryScroll = FindScroll(SummaryList);
        chatScroll = FindScroll(ChatList);
        if (chatScroll is not null) chatScroll.ViewChanged += (_, _) =>
            chatFollowing = chatScroll.ScrollableHeight - chatScroll.VerticalOffset < 40;
        if (transcriptScroll is not null) transcriptScroll.ViewChanged += (_, _) =>
            transcriptFollowing = transcriptScroll.ScrollableHeight - transcriptScroll.VerticalOffset < 40;
        if (summaryScroll is not null) summaryScroll.ViewChanged += (_, _) =>
            summaryFollowing = summaryScroll.ScrollableHeight - summaryScroll.VerticalOffset < 40;
        await Run(Model.ConnectAsync);
        if (verifyFile is not null)
        {
            if (verifyCaptions) await VerifyCaptionsAsync();
            else await VerifyAsync(verifyFile);
        }
    }

    private void Refresh(string kind)
    {
        updating = true;
        StartIcon.Glyph = Model.Active ? "\uE71A" : "\uE7C8";
        IconActions.Describe(StartButton, Model.Active ? Model.Mode == "captions" ? "字幕运行中" : "结束会议" : "开始会议");
        StartButton.IsEnabled = Model.Connected && (!Model.Active || Model.Mode != "captions");
        ImportButton.IsEnabled = Model.Connected && !Model.Active;
        ScheduleButton.IsEnabled = Model.Connected;
        ThemeButton.IsEnabled = Model.Connected && !changingTheme;
        TitleEdit.IsEnabled = Model.Connected;
        SourcePicker.IsEnabled = LanguagePicker.IsEnabled = !Model.Active;
        RenameButton.IsEnabled = Model.Connected && Model.Directory.Length > 0;
        CopyAllButton.IsEnabled = Model.Connected && Model.Entries.Count + Model.Summaries.Count > 0;
        CopyTranscriptButton.IsEnabled = Model.Connected && Model.Entries.Count > 0;
        CopySummaryButton.IsEnabled = Model.Connected && Model.Summaries.Count > 0;
        ChatSendButton.IsEnabled = Model.Connected && Model.MeetingId.Length > 0 && !Model.ChatBusy && !chatSending;
        ChatCancelButton.Visibility = Model.ChatBusy ? Visibility.Visible : Visibility.Collapsed;
        ChatCancelButton.IsEnabled = Model.Connected;
        HistoryList.IsEnabled = Model.Connected && !Model.Active && !Model.SummaryBusy && !deletingMeeting;
        SummaryButton.IsEnabled = Model.Connected && Model.Entries.Count > 0 && !Model.SummaryBusy;
        SummaryIcon.Glyph = Model.SummaryBusy ? "\uE916" : "\uE945";
        ToolTipService.SetToolTip(SummaryButton, Model.SummaryBusy ? "总结中…" : "手动总结");
        FolderButton.IsEnabled = Model.Directory.Length > 0;
        ReconnectButton.Visibility = Model.Connected ? Visibility.Collapsed : Visibility.Visible;
        if (Model.Error.Length > 0) { ErrorBar.Message = Model.Error; ErrorBar.IsOpen = true; }
        if (kind is "configuration" or "appearance")
        {
            Automatic.IsChecked = Model.Configuration.AutomaticSummary;
            Interval.Value = Model.Configuration.IntervalMinutes;
            IconActions.Describe(ScheduleButton, Model.Configuration.AutomaticSummary
                ? $"自动总结 · 每 {Model.Configuration.IntervalMinutes} 分钟" : "自动总结 · 已关闭");
            ChatEffortLabel.Text = ChatOptionsFlyout.EffortLabel(Model.Configuration.ChatReasoningEffort);
            var chatProfile = Model.Configuration.ApiProfiles.FirstOrDefault(p => p.Id ==
                (Model.Configuration.ChatApiProfile ?? Model.Configuration.SelectedApiProfile));
            var chatModel = string.IsNullOrWhiteSpace(Model.Configuration.ChatModel)
                ? chatProfile?.Model : Model.Configuration.ChatModel;
            ToolTipService.SetToolTip(ChatOptionsButton,
                $"模型：{(string.IsNullOrWhiteSpace(chatModel) ? "未选择" : chatModel)}\n思考强度：{ChatEffortLabel.Text}");
            var dark = Model.Configuration.Appearance == "dark";
            ThemeIcon.Glyph = dark ? "\uE706" : "\uE708";
            IconActions.Describe(ThemeButton, dark ? "切换浅色模式" : "切换深色模式");
            ApplyTheme();
        }
        if (kind is "meeting" or "meeting.loaded" or "meeting.renamed" or "meeting.deleted") TitleEdit.Text = Model.MeetingTitle;
        if (kind == "progress") { progressStarted = DateTime.Now; progressStatus = Model.Status; startupClock.Start(); }
        if (Model.State != "preparing") { startupClock.Stop(); Title = "言川 · 会议转写"; }
        if (kind == "transcript" && transcriptFollowing && Model.Entries.Count > 0)
            TranscriptList.ScrollIntoView(Model.Entries[^1]);
        if (kind == "summary.summary" && summaryFollowing && Model.Summaries.Count > 0)
            SummaryList.ScrollIntoView(Model.Summaries[^1]);
        if (kind == "chat" && chatFollowing && Model.ChatMessages.Count > 0)
            ChatList.ScrollIntoView(Model.ChatMessages[^1]);
        if (kind is "configuration" or "meeting" or "meeting.loaded" or "meeting.deleted" or "transcript"
            or "chat" or "chat.updated" or "summary.summary" or "state" or "snapshot" or "disconnected")
            ScheduleContextUsage();
        updating = false;
    }

    private void ApplyTheme()
    {
        Root.RequestedTheme = Model.Configuration.Appearance == "dark" ? ElementTheme.Dark : ElementTheme.Light;
        var dark = Root.RequestedTheme == ElementTheme.Dark;
        var background = dark ? Microsoft.UI.ColorHelper.FromArgb(255, 32, 32, 32) : Microsoft.UI.ColorHelper.FromArgb(255, 243, 243, 243);
        var foreground = dark ? Microsoft.UI.Colors.White : Microsoft.UI.Colors.Black;
        AppWindow.TitleBar.BackgroundColor = AppWindow.TitleBar.ButtonBackgroundColor = background;
        AppWindow.TitleBar.InactiveBackgroundColor = AppWindow.TitleBar.ButtonInactiveBackgroundColor = background;
        AppWindow.TitleBar.ForegroundColor = AppWindow.TitleBar.ButtonForegroundColor = foreground;
        captions?.ApplyTheme(Root.RequestedTheme);
    }

    public async Task Run(Func<Task> action)
    { try { await action(); } catch (Exception error) { Model.ShowError(error); } }
    private static string Tag(ComboBox box) => (string)((ComboBoxItem)box.SelectedItem).Tag;
    private async void ToggleMeeting(object sender, RoutedEventArgs args) => await Run(async () =>
    {
        if (Model.Active) await Model.Backend.CallAsync("session.stop");
        else await Model.StartAsync("meeting", TitleEdit.Text, Tag(SourcePicker), Tag(LanguagePicker), microphoneId, systemId);
    });
    private async void ImportFile(object sender, RoutedEventArgs args) => await Run(async () =>
    {
        var picker = new FileOpenPicker();
        WinRT.Interop.InitializeWithWindow.Initialize(picker, WinRT.Interop.WindowNative.GetWindowHandle(this));
        picker.FileTypeFilter.Add(".wav"); picker.FileTypeFilter.Add(".flac");
        var file = await picker.PickSingleFileAsync();
        if (file is not null) await Model.StartAsync("file", Path.GetFileNameWithoutExtension(file.Name),
            Tag(SourcePicker), Tag(LanguagePicker), microphoneId, systemId, file.Path);
    });
    private async void OpenHistory(object sender, ItemClickEventArgs args)
    {
        if (!deletingMeeting) await Run(() => Model.OpenAsync((MeetingItem)args.ClickedItem));
    }
    private async void DeleteMeeting(object sender, RoutedEventArgs args)
    {
        if (deletingMeeting || !Model.Connected || sender is not Button { Tag: MeetingItem item }) return;
        if (Model.Active || Model.SummaryBusy)
        {
            Model.ShowError(new InvalidOperationException("请先结束录音或等待总结完成，再删除会议。"));
            return;
        }
        deletingMeeting = true;
        try
        {
            var dialog = new ContentDialog
            {
                XamlRoot = Root.XamlRoot, RequestedTheme = Root.RequestedTheme,
                Title = "删除会议？",
                Content = new TextBlock
                {
                    Text = $"“{item.Title}”的转写、AI 总结、问答记录和导出文件将永久删除。",
                    TextWrapping = TextWrapping.Wrap, MaxWidth = 400
                },
                PrimaryButtonText = "删除", CloseButtonText = "取消", DefaultButton = ContentDialogButton.Close
            };
            if (await dialog.ShowAsync() == ContentDialogResult.Primary)
            {
                await Run(() => Model.DeleteAsync(item));
            }
        }
        catch (Exception error) { Model.ShowError(error); }
        finally { deletingMeeting = false; Refresh("deletion.completed"); }
    }
    private async void Summarize(object sender, RoutedEventArgs args) => await Run(Model.SummarizeAsync);
    private async void RenameMeeting(object sender, RoutedEventArgs args) =>
        await Run(() => Model.RenameAsync(TitleEdit.Text));

    private void SetClipboard(string text)
    {
        if (string.IsNullOrWhiteSpace(text)) return;
        var data = new DataPackage(); data.SetText(text); Clipboard.SetContent(data); Clipboard.Flush();
        CopyNotice.Text = "已复制到剪贴板"; copyClock.Stop(); copyClock.Start();
    }
    private async void CopyContent(object sender, RoutedEventArgs args) => await Run(async () =>
        await CopyContentAsync((string)((Button)sender).Tag));
    private async Task CopyContentAsync(string kind)
    {
        var result = await Model.Backend.CallAsync("meetings.content", new { kind });
        SetClipboard(result.GetProperty("text").GetString() ?? "");
    }
    private void CopyChatMessage(object sender, RoutedEventArgs args) =>
        SetClipboard(((Button)sender).Tag as string ?? "");
    private void ToggleChat(object sender, RoutedEventArgs args)
    {
        chatOpen = !chatOpen;
        ChatPanel.Visibility = chatOpen ? Visibility.Visible : Visibility.Collapsed;
        ChatToggleIcon.Glyph = chatOpen ? "\uE72B" : "\uE8F2";
        ToolTipService.SetToolTip(ChatToggle, chatOpen ? "返回会议" : "会议问答");
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(ChatToggle, chatOpen ? "返回会议" : "会议问答");
        ArrangeWorkspace();
        if (chatOpen) { ChatInput.Focus(FocusState.Programmatic); ScheduleContextUsage(); }
    }
    private void WorkspaceResized(object sender, SizeChangedEventArgs args) => ArrangeWorkspace();
    private void ContentTabChanged(object sender, SelectionChangedEventArgs args) => ArrangeWorkspace();
    private void ArrangeWorkspace()
    {
        if (ChatPanel is null || Workspace is null || ContentTabs is null) return;
        var compact = !chatOpen && Workspace.ActualWidth < 900;
        TranscriptColumn.Width = chatOpen ? new GridLength(0) : new GridLength(1, GridUnitType.Star);
        ChatColumn.Width = chatOpen ? new GridLength(1, GridUnitType.Star) : new GridLength(0);
        SummaryColumn.Width = chatOpen || compact ? new GridLength(0) : new GridLength(1, GridUnitType.Star);
        ContentTabs.Visibility = compact ? Visibility.Visible : Visibility.Collapsed;
        TranscriptPanel.Visibility = !chatOpen && (!compact || ContentTabs.SelectedIndex == 0) ? Visibility.Visible : Visibility.Collapsed;
        SummaryPanel.Visibility = !chatOpen && (!compact || ContentTabs.SelectedIndex == 1) ? Visibility.Visible : Visibility.Collapsed;
        Grid.SetColumn(SummaryPanel, compact ? 0 : 1);
        Workspace.ColumnSpacing = chatOpen ? 0 : 16;
        WorkspaceTitle.Text = chatOpen ? "会议问答" : "会议记录";
        MeetingControls.Visibility = ScheduleButton.Visibility = chatOpen ? Visibility.Collapsed : Visibility.Visible;
        ContentArea.RowSpacing = chatOpen ? 0 : 16;
    }
    private async void SendChat(object sender, RoutedEventArgs args) => await SendChatInput();
    private async Task SendChatInput()
    {
        if (chatSending || !ChatSendButton.IsEnabled || string.IsNullOrWhiteSpace(ChatInput.Text)) return;
        chatSending = true; ChatSendButton.IsEnabled = false;
        var text = ChatInput.Text;
        try { await Run(async () => { await Model.SendChatAsync(text); if (ChatInput.Text == text) ChatInput.Text = ""; }); }
        finally { chatSending = false; Refresh("chat"); }
    }
    private async void ChatInputKeyDown(object sender, Microsoft.UI.Xaml.Input.KeyRoutedEventArgs args)
    {
        if (args.Key == VirtualKey.Enter && (Microsoft.UI.Input.InputKeyboardSource.GetKeyStateForCurrentThread(VirtualKey.Control) & CoreVirtualKeyStates.Down) != 0)
        { args.Handled = true; await SendChatInput(); }
    }
    private void ChatInputChanged(object sender, TextChangedEventArgs args) => ScheduleContextUsage();
    private void ScheduleContextUsage()
    {
        contextRevision++;
        contextDirty = true;
        if (ChatContextButton is null || Model is null) return;
        ChatContextButton.IsEnabled = Model.Connected;
        if (!Model.Connected)
        {
            ChatContextRing.Value = 0;
            ToolTipService.SetToolTip(ChatContextButton, "后端未连接，上下文用量暂不可用");
            return;
        }
        if (!chatOpen || closed || closing) return;
        contextClock.Stop(); contextClock.Start();
    }
    private async Task RefreshContextUsage()
    {
        if (contextRequestRunning || !Model.Connected || !chatOpen || closed || closing) return;
        contextRequestRunning = true;
        contextDirty = false;
        var revision = contextRevision;
        var meeting = Model.MeetingId;
        try
        {
            var usage = WireJson.Read<ChatContextUsage>(await Model.Backend.CallAsync("chat.usage",
                new { question = ChatInput.Text }));
            if (closed || closing || revision != contextRevision || (usage.MeetingId ?? "") != Model.MeetingId
                || meeting != Model.MeetingId) return;
            ChatContextRing.Value = usage.Percent;
            ChatContextRing.Foreground = usage.OverBudget
                ? new SolidColorBrush(Microsoft.UI.Colors.OrangeRed)
                : (Microsoft.UI.Xaml.Media.Brush)Root.Resources["ContextRingBrush"];
            ToolTipService.SetToolTip(ChatContextButton, usage.Description);
            Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(ChatContextButton, usage.Description);
        }
        catch (Exception)
        {
            if (!closed && revision == contextRevision)
                ToolTipService.SetToolTip(ChatContextButton,
                    $"上下文上限：{Model.Configuration.ChatContextTokens:N0} token\n用量获取失败，点击调整上限");
        }
        finally
        {
            contextRequestRunning = false;
            if (contextDirty && !closed && !closing && chatOpen)
            { contextClock.Stop(); contextClock.Start(); }
        }
    }
    private async void CancelChat(object sender, RoutedEventArgs args) => await Run(async () =>
        await Model.Backend.CallAsync("chat.cancel"));
    private async void RetryChat(object sender, RoutedEventArgs args) =>
        await Run(() => Model.RetryChatAsync((string)((Button)sender).Tag));
    private void OpenChatOptions(object sender, RoutedEventArgs args) =>
        ChatOptionsFlyout.Show(ChatOptionsButton, Model.Configuration, Model.Backend, Model.ShowError);
    private async void ChatCapacity(object sender, RoutedEventArgs args) => await Run(async () =>
    {
        var input = new NumberBox { Value = Model.Configuration.ChatContextTokens, Minimum = 8192,
            Header = "模型上下文容量（token）", SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Compact };
        var body = new StackPanel { Spacing = 12, Width = 360 };
        body.Children.Add(input);
        body.Children.Add(new TextBlock { Text = "全文和聊天历史一并提交，不自动截断。预算采用估算值，实际容量由 API 服务决定。", TextWrapping = TextWrapping.Wrap, MaxWidth = 360 });
        var dialog = new ContentDialog { XamlRoot = Root.XamlRoot, Title = "聊天上下文", Content = body,
            PrimaryButtonText = "保存", CloseButtonText = "取消", RequestedTheme = Root.RequestedTheme };
        if (await dialog.ShowAsync() == ContentDialogResult.Primary)
        {
            if (!double.IsFinite(input.Value) || input.Value != Math.Truncate(input.Value) || input.Value > int.MaxValue)
                throw new InvalidOperationException("请输入有效的整数容量");
            await Model.Backend.CallAsync("chat.configure", new { context_tokens = (int)input.Value });
        }
    });
    private async void ScheduleChanged(object sender, RoutedEventArgs args) => await SaveSchedule();
    private async void IntervalChanged(NumberBox sender, NumberBoxValueChangedEventArgs args) => await SaveSchedule();
    private async Task SaveSchedule()
    {
        if (updating || !Model.Connected || !double.IsFinite(Interval.Value)) return;
        await Run(async () => await Model.Backend.CallAsync("schedule.set",
            new { minutes = (int)Math.Round(Interval.Value), automatic = Automatic.IsChecked == true }));
    }
    private async void ToggleTheme(object sender, RoutedEventArgs args)
    {
        if (changingTheme || !Model.Connected) return;
        changingTheme = true; ThemeButton.IsEnabled = false;
        try
        {
            var mode = Model.Configuration.Appearance == "dark" ? "light" : "dark";
            await Run(async () => await Model.Backend.CallAsync("appearance.set", new { mode }));
        }
        finally { changingTheme = false; ThemeButton.IsEnabled = Model.Connected; }
    }
    private async void EditProfiles(object sender, RoutedEventArgs args) =>
        await Run(() => ConfigurationDialogs.ProfilesAsync(Root.XamlRoot, Model.Configuration, Model.Backend));
    private async void ManageModels(object sender, RoutedEventArgs args) =>
        await Run(() => ModelManagementDialog.ShowAsync(this, Root, Model));
    private async void EditTemplates(object sender, RoutedEventArgs args) =>
        await Run(() => ConfigurationDialogs.TemplatesAsync(Root.XamlRoot, Model.Configuration, Model.Backend));
    private async void EditDevices(object sender, RoutedEventArgs args) => await Run(async () =>
    {
        await Model.RefreshDevicesAsync();
        var selection = await ConfigurationDialogs.DevicesAsync(Root.XamlRoot, Model.Devices, microphoneId, systemId);
        if (selection is { } value) { microphoneId = value.Microphone; systemId = value.System; }
    });
    private void OpenCaptions(object sender, RoutedEventArgs args)
    {
        if (captions is null)
        {
            captions = new CaptionWindow(Model, () => (microphoneId, systemId));
            captions.Closed += (_, _) => captions = null;
        }
        captions.ShowControls(); captions.ApplyTheme(Root.RequestedTheme); captions.Activate();
    }
    private void OpenFolder(object sender, RoutedEventArgs args)
    {
        if (System.IO.Directory.Exists(Model.Directory)) Process.Start(new ProcessStartInfo(Model.Directory) { UseShellExecute = true });
    }
    private async void Reconnect(object sender, RoutedEventArgs args) => await Run(Model.ConnectAsync);

    private async void Closing(AppWindow sender, AppWindowClosingEventArgs args)
    {
        if (closed) return;
        args.Cancel = true;
        if (closing) return;
        closing = true; contextClock.Stop(); contextRevision++;
        StartButton.IsEnabled = false; ImportButton.IsEnabled = false;
        Title = "言川 · 正在保存并退出…";
        try
        {
            await Model.Backend.DisposeAsync();
            closed = true; captions?.Close(); Close();
        }
        catch (Exception error) { closing = false; Model.ShowError(error); }
    }

    private async Task VerifyAsync(string file)
    {
        var completed = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        void Changed(string kind) { if (kind == "finished") completed.TrySetResult(); }
        Model.Changed += Changed;
        try
        {
            await Model.StartAsync("file", "WinUI 无声功能验证", "system", "zh", null, null, file);
            await completed.Task.WaitAsync(TimeSpan.FromMinutes(8));
            if (Model.Entries.Count == 0) throw new InvalidOperationException("真实文件没有转写结果");
            if (Model.Summaries.Count == 0)
            {
                await Model.SummarizeAsync();
                var deadline = DateTime.UtcNow.AddMinutes(3);
                while (Model.SummaryBusy || Model.Summaries.Count == 0)
                { if (DateTime.UtcNow > deadline) throw new TimeoutException("总结未完成"); await Task.Delay(200); }
            }
            var meetingDirectory = Model.Directory;
            await Model.RenameAsync("WinUI 会议问答无声验证");
            var content = await Model.Backend.CallAsync("meetings.content", new { kind = "all" });
            var copied = content.GetProperty("text").GetString() ?? "";
            if (!copied.Contains(Model.Entries[0].Text) || !copied.Contains(Model.Summaries[0].Text))
                throw new InvalidOperationException("完整会议内容导出缺少转写或总结");
            await CopyContentAsync("all");
            var clipboardText = await Clipboard.GetContent().GetTextAsync();
            if (clipboardText != copied) throw new InvalidOperationException("剪贴板内容与完整会议不一致");
            if (!chatOpen) ToggleChat(this, new RoutedEventArgs());
            await Model.SendChatAsync("原文主要说了什么？请简短回答，并引用时间戳。");
            await WaitForChatAsync(1);
            await Model.SendChatAsync("请用更简短的一句话重述你刚才的回答，保留时间戳。");
            await WaitForChatAsync(2);
            await Model.Backend.CallAsync("meetings.open", new { directory = meetingDirectory });
            var snapshot = WireJson.Read<Snapshot>(await Model.Backend.CallAsync("snapshot"));
            if (snapshot.Meeting?.Entries.Count != Model.Entries.Count) throw new InvalidOperationException("历史重开校验失败");
            var history = WireJson.Read<ChatConversation>(await Model.Backend.CallAsync("chat.list"));
            if (history.Messages.Count(m => m.Role == "assistant" && m.Status == "completed") != 2)
                throw new InvalidOperationException("重新打开会议后聊天记录不完整");
            await File.WriteAllTextAsync(reportPath ?? Path.Combine(root, "data", "winui-verification.json"),
                JsonSerializer.Serialize(new { success = true, meeting_directory = meetingDirectory,
                    entries = Model.Entries.Count, summaries = Model.Summaries.Count,
                    snapshot.Meeting!.Metadata.SummaryCursor, renamed_title = snapshot.Meeting.Metadata.Title,
                    copy_characters = copied.Length, clipboard_verified = true, chat_turns = 2, chat_reopened = true,
                    chat_answers = history.Messages.Where(m => m.Role == "assistant").Select(m => m.Text),
                    silent = true, device_opened = false }, WireJson.Options));
        }
        catch (Exception error)
        {
            Model.ShowError(error);
            if (reportPath is not null) await File.WriteAllTextAsync(reportPath,
                JsonSerializer.Serialize(new { success = false, error = error.Message }));
        }
        finally { Model.Changed -= Changed; }
    }

    private async Task WaitForChatAsync(int answers)
    {
        var deadline = DateTime.UtcNow.AddMinutes(3);
        while (true)
        {
            var conversation = WireJson.Read<ChatConversation>(await Model.Backend.CallAsync("chat.list"));
            if (conversation.Messages.Any(m => m.Status == "failed"))
                throw new InvalidOperationException("真实会议问答请求失败");
            if (conversation.Messages.Count(m => m.Role == "assistant" && m.Status == "completed") >= answers) return;
            if (DateTime.UtcNow > deadline) throw new TimeoutException("会议问答未完成");
            await Task.Delay(500);
        }
    }

    private async Task VerifyCaptionsAsync()
    {
        var completed = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        void Changed(string kind) { if (kind == "finished") completed.TrySetResult(); }
        Model.Changed += Changed;
        try
        {
            await Model.Backend.CallAsync("captions.configure", new CaptionSettings {
                Enabled = true, DisplayMode = "bilingual", TargetLanguage = "en",
                ApiProfileId = Model.Configuration.Captions.ApiProfileId });
            OpenCaptions(this, new RoutedEventArgs());
            await Model.StartAsync("captions", "", "system", "zh", null, null);
            await completed.Task.WaitAsync(TimeSpan.FromMinutes(8));
            var deadline = DateTime.UtcNow.AddMinutes(2);
            while (!Model.Translations.Values.Any(t => !t.Pending && t.State == "ready"))
            { if (DateTime.UtcNow > deadline) throw new TimeoutException("翻译没有完成"); await Task.Delay(200); }
            var translated = Model.Translations.Values.First(t => !t.Pending && t.State == "ready");
            await File.WriteAllTextAsync(reportPath ?? Path.Combine(root, "data", "caption-verification.json"),
                JsonSerializer.Serialize(new { success = true, silent = true, device_opened = false,
                    original = translated.SourceText, translation = translated.Text,
                    target = translated.TargetLanguage, captions = Model.Captions.Count }, WireJson.Options));
        }
        catch (Exception error)
        {
            Model.ShowError(error);
            if (reportPath is not null) await File.WriteAllTextAsync(reportPath,
                JsonSerializer.Serialize(new { success = false, error = error.Message }));
        }
        finally { Model.Changed -= Changed; }
    }

    private static ScrollViewer? FindScroll(DependencyObject parent)
    {
        if (parent is ScrollViewer scroll) return scroll;
        for (int i = 0; i < VisualTreeHelper.GetChildrenCount(parent); i++)
            if (FindScroll(VisualTreeHelper.GetChild(parent, i)) is { } child) return child;
        return null;
    }
}
