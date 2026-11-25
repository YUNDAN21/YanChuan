using System.Diagnostics;
using System.Globalization;
using System.Text.Json;
using LiveCaptions.Desktop.Models;
using LiveCaptions.Desktop.ViewModels;
using LiveCaptions.Desktop.Views;
using Microsoft.UI.Composition.SystemBackdrops;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Data;
using Microsoft.UI.Xaml.Input;
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
    private readonly string root, themeCache;
    private readonly string? verifyFile, reportPath;
    private readonly bool verifyCaptions;
    private CaptionWindow? captions;
    private string? microphoneId, systemId;
    private bool updating, updatingDevices;
    private bool closing => lifetime.Exiting;
    private bool closed => lifetime.Finished;
    private bool transcriptFollowing = true, summaryFollowing = true;
    private ScrollViewer? transcriptScroll, summaryScroll;
    private ScrollViewer? chatScroll;
    private string view = "meeting";
    private bool chatOpen => view == "chat";
    private bool settingsOpen => view is "settings" or "profiles";
    private bool chatFollowing = true;
    private readonly DispatcherTimer copyClock = new() { Interval = TimeSpan.FromSeconds(3) };
    private readonly DispatcherTimer startupClock = new() { Interval = TimeSpan.FromSeconds(1) };
    private readonly DispatcherTimer recordClock = new() { Interval = TimeSpan.FromSeconds(1) };
    private DateTime progressStarted;
    private string progressStatus = "";
    private DateTimeOffset? recordingSince;
    private readonly DispatcherTimer contextClock = new() { Interval = TimeSpan.FromMilliseconds(350) };
    private long contextRevision;
    private bool chatSending, contextRequestRunning, contextDirty, changingTheme, deletingMeeting;
    private long notesRevision;
    private bool notesDialogOpen;
    private bool renaming, committingRename, startDanger, rowsQueued;
    // Title for the next new meeting, named before recording starts. Opening history never changes it.
    private string pendingTitle = "";
    private string meetingSignature = "", shownError = "";
    private readonly CollectionViewSource meetingGroups;
    private readonly Services.WindowLifetime lifetime = new();
    private readonly Services.DesktopTray tray;
    private bool forceExit;
    private sealed record SidebarNote(string GroupId, string? NoteId, string Title)
    {
        public override string ToString() => Title;
    }

    public MainWindow(DesktopViewModel model, string root, string? verifyFile = null, string? reportPath = null,
                      bool verifyCaptions = false)
    {
        Model = model; this.root = root; this.verifyFile = verifyFile; this.reportPath = reportPath;
        this.verifyCaptions = verifyCaptions;
        InitializeComponent(); Root.DataContext = model;
        meetingGroups = (CollectionViewSource)Root.Resources["MeetingGroups"];
        ExtendsContentIntoTitleBar = true;
        SetTitleBar(AppTitleBar);
        if (AppWindowTitleBar.IsCustomizationSupported())
            AppWindow.TitleBar.PreferredHeightOption = TitleBarHeightOption.Tall;
        if (MicaController.IsSupported()) SystemBackdrop = new MicaBackdrop();
        else BackdropFallback.Visibility = Visibility.Visible;
        tray = new Services.DesktopTray(RestoreFromTray, ExitFromTray, () => Root.ActualTheme);
        Closed += (_, _) => tray.Dispose();
        contextClock.Tick += async (_, _) =>
        {
            contextClock.Stop();
            await RefreshContextUsage();
        };
        Services.AppIcons.Apply(this);
        copyClock.Tick += (_, _) => { copyClock.Stop(); CopyToast.Visibility = Visibility.Collapsed; CopyNotice.Text = ""; };
        recordClock.Tick += (_, _) => UpdateRecordingClock();
        model.Post = action => DispatcherQueue.TryEnqueue(() => action());
        model.Changed += Refresh;
        model.Meetings.CollectionChanged += (_, _) => ScheduleMeetingRows();
        AppWindow.Resize(new SizeInt32(1500, 1000));
        if (AppWindow.Presenter is OverlappedPresenter presenter) presenter.PreferredMinimumWidth = 1040;
        AppWindow.Closing += Closing;
        Root.Loaded += Loaded;
        startupClock.Tick += (_, _) => { if (Model.State == "preparing") UpdateWindowTitle(); };
        var version = typeof(App).Assembly.GetName().Version;
        VersionText.Text = version is null ? "言川" : $"言川 {version.ToString(3)}";
        SettingsNav.SelectedIndex = 0;
        themeCache = Services.ApplicationPaths.ThemeCachePath(root);
        // Paint the first frame in the last known theme; the backend's configuration replaces it on connect.
        if (!Model.Connected && Services.ThemeCache.Read(themeCache) is { } cachedTheme)
            Model.Configuration.Appearance = cachedTheme;
        ApplyTheme();
        RebuildMeetingRows(force: true);
        UpdateHeader(); UpdateCards();
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
        FindChatScroll();
        if (transcriptScroll is not null) transcriptScroll.ViewChanged += (_, _) =>
        {
            var following = transcriptScroll.ScrollableHeight - transcriptScroll.VerticalOffset < 40;
            if (following == transcriptFollowing) return;
            transcriptFollowing = following;
            UpdateCards();
        };
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
        if (kind == "chat.preview")
        {
            RefreshChatPreview();
            return;
        }
        updating = true;
        var stopping = Model.Active && Model.Mode != "captions";
        if (stopping != startDanger)
        {
            startDanger = stopping;
            StartButton.Style = (Style)Application.Current.Resources[stopping ? "DangerButtonStyle" : "AccentButtonStyle"];
        }
        StartIcon.Glyph = Model.Active ? "" : "";
        StartLabel.Text = !Model.Active ? "开始录制" : Model.Mode switch
        { "captions" => "字幕运行中", "file" => "停止转写", _ => "结束会议" };
        AutomationProperties.SetName(StartButton, StartLabel.Text);
        StartButton.IsEnabled = Model.Connected && (!Model.Active || Model.Mode != "captions");
        ImportButton.IsEnabled = Model.Connected && !Model.Active;
        ScheduleButton.IsEnabled = Model.Connected;
        ThemeButton.IsEnabled = ThemeLightOption.IsEnabled = ThemeDarkOption.IsEnabled = Model.Connected && !changingTheme;
        TrayToggle.IsEnabled = Model.Connected && !closing;
        EditProfilesButton.IsEnabled = Model.Connected && !closing;
        (ProfilesPanel.Content as ApiProfilesView)?.SetConnected(Model.Connected);
        tray.Update(Model.Status);
        TitleEdit.IsEnabled = Model.Connected;
        RenameButton.IsEnabled = Model.Connected && (Model.Directory.Length > 0 || !Model.Active);
        LanguagePicker.IsEnabled = !Model.Active;
        SourcePicker.IsEnabled = Model.Connected && !Model.SourceSwitching
            && (!Model.Active || Model.Mode == "meeting" && Model.State == "recording");
        SourceHint.Text = Model.SourceSwitching ? "正在切换音源…"
            : Model.Active && Model.Mode == "meeting" && Model.State == "recording" ? "录音中可直接切换音源" : "";
        if (Model.Active && Model.Mode == "meeting"
            && (kind is "session.source" or "snapshot" or "error"))
            SourcePicker.SelectedItem = SourcePicker.Items.OfType<ComboBoxItem>().FirstOrDefault(
                item => (string)item.Tag == (Model.AudioSource.RequestedSource ?? Model.AudioSource.Source));
        CopyAllButton.IsEnabled = Model.Connected && Model.Entries.Count + Model.Summaries.Count > 0;
        CopyTranscriptButton.IsEnabled = Model.Connected && Model.Entries.Count > 0;
        CopySummaryButton.IsEnabled = Model.Connected && Model.Summaries.Count > 0;
        ChatSendButton.IsEnabled = Model.Connected && Model.MeetingId.Length > 0 && !Model.ChatBusy && !chatSending;
        ChatCancelButton.Visibility = Model.ChatBusy ? Visibility.Visible : Visibility.Collapsed;
        ChatCancelButton.IsEnabled = Model.Connected;
        ChatContextRing.IsIndeterminate = Model.ChatContext.Compressing;
        ChatContextNotice.Text = Model.ChatActivity.Length > 0 ? Model.ChatActivity : Model.ChatContext.StateText;
        ChatContextNotice.Visibility = ChatContextNotice.Text.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        if (kind is "chat" or "meeting" or "meeting.deleted" or "connection" or "snapshot")
            RefreshChatPreview();
        SummaryButton.IsEnabled = Model.Connected && Model.Entries.Count > 0 && !Model.SummaryBusy;
        SummaryIcon.Glyph = Model.SummaryBusy ? "" : "";
        SummaryButtonLabel.Text = Model.SummaryBusy ? "总结中…" : "立即总结";
        SummaryProgress.IsActive = Model.SummaryBusy;
        SummaryProgress.Visibility = Model.SummaryBusy ? Visibility.Visible : Visibility.Collapsed;
        FolderButton.IsEnabled = Model.Directory.Length > 0;
        ExportBar.Message = Model.ExportError;
        ExportBar.IsOpen = Model.ExportError.Length > 0;
        ExportBar.Visibility = ExportBar.IsOpen ? Visibility.Visible : Visibility.Collapsed;
        RetryExportButton.IsEnabled = Model.Connected;
        ReconnectButton.Visibility = Model.Connected ? Visibility.Collapsed : Visibility.Visible;
        SettingsReconnectButton.IsEnabled = !Model.Connected;
        BackendSummary.Text = Model.Connected ? "已连接" : "已断开 · 点击“重新连接”重启本地后端";
        NotesTree.IsEnabled = NewNoteButton.IsEnabled = ManageNotesButton.IsEnabled = Model.Connected;
        if (kind is "configuration" or "notes.changed" or "connection")
            _ = RefreshSidebarNotes();
        // Reopen the error bar only for a new error, so closing it is not undone by the next event.
        if (Model.Error.Length > 0 && (kind == "error" || Model.Error != shownError))
        { ErrorBar.Message = Model.Error; ErrorBar.IsOpen = true; ErrorBar.Visibility = Visibility.Visible; }
        shownError = Model.Error;
        if (kind is "configuration" or "appearance")
        {
            Automatic.IsChecked = Model.Configuration.AutomaticSummary;
            Interval.Value = Model.Configuration.IntervalMinutes;
            ScheduleLabel.Text = Model.Configuration.AutomaticSummary
                ? $"自动总结 · 每 {Model.Configuration.IntervalMinutes} 分钟" : "自动总结 · 已关闭";
            AutomationProperties.SetName(ScheduleButton, ScheduleLabel.Text);
            var effort = ChatOptionsFlyout.EffortLabel(Model.Configuration.ChatReasoningEffort);
            var chatProfile = Model.Configuration.ApiProfiles.FirstOrDefault(p => p.Id ==
                (Model.Configuration.ChatApiProfile ?? Model.Configuration.SelectedApiProfile));
            var chatModel = string.IsNullOrWhiteSpace(Model.Configuration.ChatModel)
                ? chatProfile?.Model : Model.Configuration.ChatModel;
            if (string.IsNullOrWhiteSpace(chatModel) && chatProfile?.UsesLocalAgent == true)
                chatModel = $"{chatProfile.Name}（默认模型）";
            ChatEffortLabel.Text = string.IsNullOrWhiteSpace(chatModel) ? effort : $"{chatModel} · {effort}";
            ToolTipService.SetToolTip(ChatOptionsButton,
                $"模型：{(string.IsNullOrWhiteSpace(chatModel) ? "未选择" : chatModel)}\n思考强度：{effort}");
            ApplyTheme();
            if (Model.Connected) Services.ThemeCache.Write(themeCache, Model.Configuration.Appearance);
            RefreshSettings();
        }
        if (kind == "devices") PopulateDevicePickers();
        if ((kind is "meeting" or "meeting.loaded" or "meeting.renamed" or "meeting.deleted") && !renaming)
            TitleEdit.Text = Model.MeetingTitle;
        if (kind == "progress") { progressStarted = DateTime.Now; progressStatus = Model.Status; startupClock.Start(); }
        if (Model.State != "preparing") { startupClock.Stop(); progressStatus = ""; }
        UpdateWindowTitle();
        if (kind == "transcript" && transcriptFollowing && Model.Entries.Count > 0)
            TranscriptList.ScrollIntoView(Model.Entries[^1]);
        if (kind == "summary.summary" && summaryFollowing && Model.Summaries.Count > 0)
            SummaryList.ScrollIntoView(Model.Summaries[^1]);
        if (kind == "chat" && chatFollowing && Model.ChatPreviewText.Length == 0 && Model.ChatMessages.Count > 0)
            ChatList.ScrollIntoView(Model.ChatMessages[^1]);
        if (kind is "configuration" or "meeting" or "meeting.loaded" or "meeting.deleted" or "transcript"
            or "chat" or "chat.updated" or "summary.summary" or "state" or "snapshot" or "disconnected")
            ScheduleContextUsage();
        RebuildMeetingRows();
        UpdateRecordingClock();
        UpdateHeader();
        UpdateCards();
        updating = false;
    }

    // ── Header, cards and window title ──────────────────────────────────────

    private void UpdateWindowTitle()
    {
        var detail = view switch { "chat" => "会议问答", "settings" => "设置", "profiles" => "API 配置", _ => "会议转写" };
        if (Model.State == "preparing" && progressStatus.Length > 0)
        {
            var progress = $"{progressStatus} · {(DateTime.Now - progressStarted).TotalSeconds:F0}s";
            Title = $"言川 · {progress}";
            AppTitleDetail.Text = progress;
            return;
        }
        Title = $"言川 · {detail}";
        AppTitleDetail.Text = detail;
    }

    private bool Recording => Model.Active && Model.Mode == "meeting" && Model.State == "recording";

    private void UpdateHeader()
    {
        var meeting = view == "meeting";
        BackButton.Visibility = meeting ? Visibility.Collapsed : Visibility.Visible;
        ChatToggle.Visibility = ScheduleButton.Visibility = meeting ? Visibility.Visible : Visibility.Collapsed;
        RenameButton.Visibility = meeting && !renaming ? Visibility.Visible : Visibility.Collapsed;
        if (!renaming) TitleText.Visibility = Visibility.Visible;
        TitleText.Text = view switch
        {
            "chat" => "会议问答", "settings" => "设置", "profiles" => "API 配置",
            _ => Model.MeetingTitle.Length > 0 ? Model.MeetingTitle
                : pendingTitle.Length > 0 ? pendingTitle : "新会议",
        };
        RecordingPill.Visibility = meeting && Recording ? Visibility.Visible : Visibility.Collapsed;
        MetaText.Text = view switch
        {
            "chat" => Model.MeetingId.Length > 0 ? $"{Model.MeetingTitle} · 回答时按需查阅完整原文"
                : "打开或开始一场会议后即可提问",
            "settings" or "profiles" => "",
            _ => MeetingMeta(),
        };
        MetaText.Visibility = MetaText.Text.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        FitTitle();
    }

    private MeetingItem? CurrentListItem => Model.Directory.Length == 0 ? null : Model.Meetings.FirstOrDefault(item =>
        string.Equals(item.Directory, Model.Directory, StringComparison.OrdinalIgnoreCase));

    private string MeetingMeta()
    {
        if (!Model.Connected || Model.Directory.Length == 0) return Model.Status;
        var parts = new List<string>();
        if (MeetingTime.Parse(Model.MeetingStartedAt) is { } started)
            parts.Add(started.ToString(started.Year == DateTimeOffset.Now.Year ? "M 月 d 日 HH:mm" : "yyyy 年 M 月 d 日 HH:mm",
                CultureInfo.InvariantCulture));
        if (Recording) parts.Add("本地识别，自动保存");
        else if (Model.Active) parts.Add(Model.Status);
        else
        {
            if (MeetingTime.Duration(Model.MeetingStartedAt, Model.MeetingEndedAt ?? CurrentListItem?.EndedAt) is { } duration)
                parts.Add($"时长 {duration}");
            parts.Add(Model.State == "error" ? Model.Status : "本地识别，已自动保存");
        }
        return string.Join(" · ", parts);
    }

    private void TitleAreaResized(object sender, SizeChangedEventArgs args) => FitTitle();

    // Text in a horizontal StackPanel is measured unbounded, so cap it to keep long titles trimmed.
    private void FitTitle()
    {
        var width = TitleArea.ActualWidth;
        if (width <= 0) return;
        TitleText.MaxWidth = Math.Max(120, width - 44);
        TitleEdit.MaxWidth = Math.Max(200, width - 8);
        MetaText.MaxWidth = Math.Max(120, width - (RecordingPill.Visibility == Visibility.Visible
            ? RecordingPill.ActualWidth + 10 : 0));
    }

    private void UpdateRecordingClock()
    {
        if (!Recording) { recordClock.Stop(); recordingSince = null; return; }
        recordingSince ??= DateTimeOffset.Now;
        if (!recordClock.IsEnabled) recordClock.Start();
        var elapsed = DateTimeOffset.Now - (MeetingTime.Parse(Model.MeetingStartedAt) ?? recordingSince.Value);
        if (elapsed < TimeSpan.Zero) elapsed = TimeSpan.Zero;
        RecordingClock.Text = $"录制中 {(int)elapsed.TotalHours:00}:{elapsed.Minutes:00}:{elapsed.Seconds:00}";
    }

    private void UpdateCards()
    {
        var entries = Model.Entries.Count;
        var live = Model.Active && Model.Mode != "captions";
        TranscriptCount.Text = entries == 0 ? "" : live
            ? $"{entries} 段 · {(transcriptFollowing ? "跟随最新" : "已暂停跟随")}" : $"{entries} 段";
        TranscriptEmpty.Visibility = entries == 0 ? Visibility.Visible : Visibility.Collapsed;
        TranscriptEmptyTitle.Text = !live ? "还没有转写内容"
            : Model.State == "preparing" ? "正在准备识别…" : "正在聆听…";
        TranscriptEmptyHint.Text = live ? "识别出的句子会逐条出现在这里。"
            : "点击右上角「开始录制」，或从左侧导入录音。识别出的句子会逐条出现在这里。";
        LiveBadge.Visibility = live ? Visibility.Visible : Visibility.Collapsed;
        var duration = MeetingTime.Duration(Model.MeetingStartedAt, Model.MeetingEndedAt ?? CurrentListItem?.EndedAt);
        TranscriptFooterText.Text = live ? Model.LiveText
            : entries == 0 ? ""
            : duration is null ? $"共 {entries} 段" : $"本次转写已结束 · 共 {duration}";
        TranscriptFooter.Visibility = TranscriptFooterText.Text.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        var summaries = Model.Summaries.Count;
        SummaryCount.Text = summaries == 0 ? "" : $"{summaries} 次";
        SummaryEmpty.Visibility = summaries == 0 ? Visibility.Visible : Visibility.Collapsed;
        SummaryEmptyHint.Text = Model.Configuration.AutomaticSummary
            ? $"录制或导入后，每 {Model.Configuration.IntervalMinutes} 分钟自动总结新增内容；也可以点「立即总结」。"
            : "自动总结已关闭，有转写内容后可点「立即总结」。";
    }

    private void ShowNotice(string text)
    {
        CopyNotice.Text = text;
        CopyToast.Visibility = Visibility.Visible;
        copyClock.Stop(); copyClock.Start();
    }

    private void NoticeClosed(InfoBar sender, InfoBarClosedEventArgs args) => sender.Visibility = Visibility.Collapsed;

    // ── Views ───────────────────────────────────────────────────────────────

    private void ShowView(string next)
    {
        if (renaming) EndRename();
        if (view == "profiles" && next != "profiles") ProfilesPanel.Content = null;
        view = next;
        ChatPanel.Visibility = chatOpen ? Visibility.Visible : Visibility.Collapsed;
        SettingsPanel.Visibility = view == "settings" ? Visibility.Visible : Visibility.Collapsed;
        ProfilesPanel.Visibility = view == "profiles" ? Visibility.Visible : Visibility.Collapsed;
        SettingsSelected.Visibility = settingsOpen ? Visibility.Visible : Visibility.Collapsed;
        IconActions.Describe(BackButton, view == "profiles" ? "返回 AI 连接" : "返回会议记录");
        ArrangeWorkspace();
        UpdateHeader();
        UpdateWindowTitle();
        if (chatOpen) { ChatInput.Focus(FocusState.Programmatic); ScheduleContextUsage(); RefreshChatPreview(); }
        if (view == "settings") RefreshSettings();
    }

    private void ToggleChat(object sender, RoutedEventArgs args) => ShowView(chatOpen ? "meeting" : "chat");
    private void ToggleSettings(object sender, RoutedEventArgs args) => ShowView(settingsOpen ? "meeting" : "settings");
    private void GoBack(object sender, RoutedEventArgs args)
    {
        if (view == "profiles") FinishProfileEdit();
        else ShowView("meeting");
    }
    private void WorkspaceResized(object sender, SizeChangedEventArgs args) => ArrangeWorkspace();
    private void ContentTabChanged(object sender, SelectionChangedEventArgs args) => ArrangeWorkspace();

    private void ArrangeWorkspace()
    {
        if (ChatPanel is null || Workspace is null || ContentTabs is null || SettingsPanel is null) return;
        var meeting = view == "meeting";
        Workspace.Visibility = settingsOpen ? Visibility.Collapsed : Visibility.Visible;
        var compact = meeting && Workspace.ActualWidth < 900;
        TranscriptColumn.Width = chatOpen ? new GridLength(0) : new GridLength(1, GridUnitType.Star);
        ChatColumn.Width = chatOpen ? new GridLength(1, GridUnitType.Star) : new GridLength(0);
        SummaryColumn.Width = !meeting || compact ? new GridLength(0) : new GridLength(1, GridUnitType.Star);
        ContentTabs.Visibility = compact ? Visibility.Visible : Visibility.Collapsed;
        TranscriptPanel.Visibility = meeting && (!compact || ContentTabs.SelectedIndex == 0) ? Visibility.Visible : Visibility.Collapsed;
        SummaryPanel.Visibility = meeting && (!compact || ContentTabs.SelectedIndex == 1) ? Visibility.Visible : Visibility.Collapsed;
        Grid.SetColumn(SummaryPanel, compact ? 0 : 1);
        Workspace.ColumnSpacing = meeting ? 16 : 0;
        MeetingControls.Visibility = meeting ? Visibility.Visible : Visibility.Collapsed;
    }

    // ── Meeting title ───────────────────────────────────────────────────────

    private void BeginRename(object sender, RoutedEventArgs args) => BeginRename();

    private void BeginRename()
    {
        if (!Model.Connected || renaming) return;
        renaming = true;
        TitleEdit.Text = Model.Directory.Length > 0 ? Model.MeetingTitle : pendingTitle;
        TitleText.Visibility = RenameButton.Visibility = Visibility.Collapsed;
        TitleEdit.Visibility = Visibility.Visible;
        TitleEdit.Focus(FocusState.Programmatic);
        TitleEdit.SelectAll();
    }

    private async void TitleEditKeyDown(object sender, KeyRoutedEventArgs args)
    {
        if (args.Key == VirtualKey.Enter) { args.Handled = true; await CommitRename(); }
        else if (args.Key == VirtualKey.Escape) { args.Handled = true; EndRename(); }
    }

    private async void TitleEditLostFocus(object sender, RoutedEventArgs args) => await CommitRename();

    private async Task CommitRename()
    {
        if (!renaming || committingRename) return;
        committingRename = true;
        var title = TitleEdit.Text.Trim();
        try
        {
            if (Model.Directory.Length == 0) pendingTitle = title;
            else if (title.Length > 0 && title != Model.MeetingTitle) await Run(() => Model.RenameAsync(title));
        }
        finally { committingRename = false; EndRename(); }
    }

    private void EndRename()
    {
        renaming = false;
        TitleEdit.Visibility = Visibility.Collapsed;
        UpdateHeader();
    }

    // ── Sidebar history ─────────────────────────────────────────────────────

    private bool HistoryLocked => !Model.Connected || Model.Active || Model.SummaryBusy || deletingMeeting;

    private void ScheduleMeetingRows()
    {
        if (rowsQueued) return;
        rowsQueued = true;
        DispatcherQueue.TryEnqueue(() => { rowsQueued = false; RebuildMeetingRows(force: true); UpdateHeader(); UpdateCards(); });
    }

    private void RebuildMeetingRows(bool force = false)
    {
        var recording = Model.Active && Model.Mode == "meeting";
        var signature = $"{recording}|{HistoryLocked}|{Model.Directory}|{MeetingSearch.Text}|{DateTime.Now:yyyyMMdd}";
        if (!force && signature == meetingSignature) return;
        meetingSignature = signature;
        var groups = MeetingListProjection.Build(Model.Meetings, MeetingSearch.Text, Model.Directory,
            recording, HistoryLocked, DateTimeOffset.Now);
        meetingGroups.Source = groups;
        HistoryList.SelectedItem = groups.SelectMany(group => group).FirstOrDefault(row => row.Current);
        HistoryEmpty.Text = Model.Meetings.Count == 0 ? "还没有会议记录" : "没有匹配的会议";
        HistoryEmpty.Visibility = groups.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
    }

    private void MeetingSearchChanged(AutoSuggestBox sender, AutoSuggestBoxTextChangedEventArgs args) =>
        RebuildMeetingRows();

    private void RestoreHistorySelection() => DispatcherQueue.TryEnqueue(() =>
        HistoryList.SelectedItem = (meetingGroups.Source as List<MeetingGroup>)?
            .SelectMany(group => group).FirstOrDefault(row => row.Current));

    private async void OpenHistory(object sender, ItemClickEventArgs args)
    {
        if (args.ClickedItem is not MeetingRow row) return;
        if (view != "meeting") ShowView("meeting");
        if (row.Current) return;
        if (HistoryLocked)
        {
            RestoreHistorySelection();
            if (Model.Connected && !deletingMeeting) ShowNotice("录音或总结进行中，结束后再切换会议");
            return;
        }
        await Run(() => Model.OpenAsync(row.Item));
    }

    private void MeetingRowEntered(object sender, PointerRoutedEventArgs args) => SetMoreVisible(sender, true);
    private void MeetingRowExited(object sender, PointerRoutedEventArgs args) => SetMoreVisible(sender, false);
    private void MeetingMenuFocused(object sender, RoutedEventArgs args) => ((UIElement)sender).Opacity = 1;
    private void MeetingMenuUnfocused(object sender, RoutedEventArgs args) => ((UIElement)sender).Opacity = 0;

    private static void SetMoreVisible(object sender, bool visible)
    {
        if (sender is Grid grid && grid.Children.OfType<Button>().FirstOrDefault() is { } more
            && more.FocusState == FocusState.Unfocused)
            more.Opacity = visible ? 1 : 0;
    }

    private void ShowMeetingMenu(object sender, RoutedEventArgs args)
    {
        if (sender is FrameworkElement { Tag: MeetingRow row } element)
            MeetingMenu(row).ShowAt(element, new FlyoutShowOptions { Placement = FlyoutPlacementMode.BottomEdgeAlignedRight });
    }

    private void MeetingRowRightTapped(object sender, RightTappedRoutedEventArgs args)
    {
        if (sender is not FrameworkElement { DataContext: MeetingRow row } element) return;
        MeetingMenu(row).ShowAt(element, new FlyoutShowOptions { Position = args.GetPosition(element) });
        args.Handled = true;
    }

    private MenuFlyout MeetingMenu(MeetingRow row)
    {
        var menu = new MenuFlyout();
        var reachable = Model.Connected && (row.Current || !HistoryLocked);
        MenuFlyoutItem Item(string text, string glyph, Func<Task> action, bool enabled)
        {
            var item = new MenuFlyoutItem { Text = text, Icon = new FontIcon { Glyph = glyph }, IsEnabled = enabled };
            item.Click += async (_, _) => await Run(action);
            return item;
        }
        menu.Items.Add(Item("重命名", "", () => RenameFromMenu(row), reachable));
        menu.Items.Add(Item("打开记录目录", "", () => { OpenDirectory(row.Item.Directory); return Task.CompletedTask; },
            true));
        menu.Items.Add(Item("复制全部内容", "", () => CopyFromMenu(row), reachable));
        menu.Items.Add(new MenuFlyoutSeparator());
        var delete = Item("删除会议…", "", () => DeleteMeetingAsync(row.Item), Model.Connected && !HistoryLocked);
        delete.Foreground = new SolidColorBrush(Root.ActualTheme == ElementTheme.Dark
            ? Microsoft.UI.ColorHelper.FromArgb(255, 255, 153, 164) : Microsoft.UI.ColorHelper.FromArgb(255, 196, 43, 28));
        menu.Items.Add(delete);
        return menu;
    }

    // Rename and copy act on the open meeting, so a row from history is opened first.
    private async Task<bool> OpenRowAsync(MeetingRow row)
    {
        if (!row.Current)
        {
            if (HistoryLocked) return false;
            await Model.OpenAsync(row.Item);
        }
        return string.Equals(Model.Directory, row.Item.Directory, StringComparison.OrdinalIgnoreCase);
    }

    private async Task RenameFromMenu(MeetingRow row)
    {
        if (!await OpenRowAsync(row)) return;
        if (view != "meeting") ShowView("meeting");
        BeginRename();
    }

    private async Task CopyFromMenu(MeetingRow row)
    {
        if (await OpenRowAsync(row)) await CopyContentAsync("all");
    }

    private async Task DeleteMeetingAsync(MeetingItem item)
    {
        if (deletingMeeting || !Model.Connected) return;
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

    // ── Meeting actions ─────────────────────────────────────────────────────

    public async Task Run(Func<Task> action)
    { try { await action(); } catch (Exception error) { Model.ShowError(error); } }
    private async void RetryExport(object sender, RoutedEventArgs args) => await Run(Model.RetryExportAsync);
    private static string Tag(ComboBox box) => (string)((ComboBoxItem)box.SelectedItem).Tag;
    private async void SourceChanged(object sender, SelectionChangedEventArgs args)
    {
        if (updating || !Model.Connected || !Model.Active || Model.Mode != "meeting"
            || Model.State != "recording") return;
        await Run(() => Model.SetSourceAsync(Tag(SourcePicker)));
    }
    private async void ToggleMeeting(object sender, RoutedEventArgs args) => await Run(async () =>
    {
        if (Model.Active) await Model.Backend.CallAsync("session.stop");
        else
        {
            await Model.StartAsync("meeting", pendingTitle, Tag(SourcePicker), Tag(LanguagePicker), microphoneId, systemId);
            pendingTitle = "";
        }
    });
    private async void ImportFile(object sender, RoutedEventArgs args) => await Run(async () =>
    {
        var picker = new FileOpenPicker();
        WinRT.Interop.InitializeWithWindow.Initialize(picker, WinRT.Interop.WindowNative.GetWindowHandle(this));
        picker.FileTypeFilter.Add(".wav"); picker.FileTypeFilter.Add(".flac");
        var file = await picker.PickSingleFileAsync();
        if (file is not null)
        {
            if (view != "meeting") ShowView("meeting");
            await Model.StartAsync("file", Path.GetFileNameWithoutExtension(file.Name),
                Tag(SourcePicker), Tag(LanguagePicker), microphoneId, systemId, file.Path);
        }
    });
    private async void Summarize(object sender, RoutedEventArgs args) => await Run(Model.SummarizeAsync);

    private void SetClipboard(string text, string notice = "已复制到剪贴板")
    {
        if (string.IsNullOrWhiteSpace(text)) return;
        var data = new DataPackage(); data.SetText(text); Clipboard.SetContent(data); Clipboard.Flush();
        ShowNotice(notice);
    }
    private async void CopyContent(object sender, RoutedEventArgs args) => await Run(async () =>
        await CopyContentAsync((string)((Button)sender).Tag));
    private async Task CopyContentAsync(string kind)
    {
        var result = await Model.Backend.CallAsync("meetings.content", new { kind });
        SetClipboard(result.GetProperty("text").GetString() ?? "", kind switch
        { "transcript" => "已复制全部转写", "summaries" => "已复制全部总结", _ => "已复制会议全部内容" });
    }
    private void CopyChatMessage(object sender, RoutedEventArgs args) =>
        SetClipboard(LiveCaptions.Desktop.Rendering.MarkdownClipboard.Format(((Button)sender).Tag as string ?? ""));

    // ── Notes ───────────────────────────────────────────────────────────────

    private async Task RefreshSidebarNotes()
    {
        var revision = ++notesRevision;
        if (!Model.Connected || closing) return;
        try
        {
            var groups = await NotesViewModel.SidebarAsync(Model.Backend);
            if (revision != notesRevision || !Model.Connected || closing) return;
            var existing = NotesTree.RootNodes.ToDictionary(n => ((SidebarNote)n.Content).GroupId, n => n.IsExpanded);
            var selected = (NotesTree.SelectedNode?.Content as SidebarNote);
            NotesTree.RootNodes.Clear();
            foreach (var (group, notes) in groups)
            {
                var parent = new TreeViewNode
                {
                    Content = new SidebarNote(group.Id, null, group.Title),
                    IsExpanded = existing.TryGetValue(group.Id, out var expanded) ? expanded : true
                };
                foreach (var note in notes)
                    parent.Children.Add(new TreeViewNode { Content = new SidebarNote(group.Id, note.Id, note.Title) });
                NotesTree.RootNodes.Add(parent);
                if (selected?.GroupId == group.Id)
                    NotesTree.SelectedNode = selected.NoteId is null ? parent
                        : parent.Children.FirstOrDefault(n => ((SidebarNote)n.Content).NoteId == selected.NoteId);
            }
            NotesEmpty.Visibility = groups.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
        }
        catch (Exception error)
        {
            if (revision == notesRevision && Model.Connected && !closing) Model.ShowError(error);
        }
    }
    private async Task ShowNotebook(string? groupId = null, string? noteId = null, bool createNote = false)
    {
        if (notesDialogOpen || !Model.Connected) return;
        notesDialogOpen = true;
        try
        {
            await Run(() => NotesDialog.ShowAsync(Root.XamlRoot, Model.Backend, Model.MeetingId,
                groupId, noteId, createNote));
        }
        finally { notesDialogOpen = false; await RefreshSidebarNotes(); }
    }
    private async void OpenNotes(object sender, RoutedEventArgs args) => await ShowNotebook();
    private async void NewSidebarNote(object sender, RoutedEventArgs args) =>
        await ShowNotebook((NotesTree.SelectedNode?.Content as SidebarNote)?.GroupId, createNote: true);
    private async void OpenSidebarNote(TreeView sender, TreeViewItemInvokedEventArgs args)
    {
        if (args.InvokedItem is not TreeViewNode node || node.Content is not SidebarNote item) return;
        if (item.NoteId is null) { node.IsExpanded = !node.IsExpanded; return; }
        await ShowNotebook(item.GroupId, item.NoteId);
    }
    private async void AddAnswerToNotes(object sender, RoutedEventArgs args)
    {
        if (((Button)sender).Tag is not ChatMessage { CanAddToNotes: true } message) return;
        var meetingId = Model.MeetingId;
        await Run(async () =>
        {
            await NotesViewModel.AddAnswerAsync(Model.Backend, meetingId, message.RequestId);
            ShowNotice("已加入对应会议的笔记");
        });
    }

    // ── Meeting chat ────────────────────────────────────────────────────────

    private void RefreshChatPreview()
    {
        ChatPreview.Text = Model.ChatPreviewText;
        ChatPreview.Visibility = ChatPreview.Text.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        if (!chatOpen || !chatFollowing || ChatPreview.Visibility != Visibility.Visible) return;
        ChatList.UpdateLayout();
        FindChatScroll();
        chatScroll?.ChangeView(null, chatScroll.ScrollableHeight, null, disableAnimation: true);
    }

    private void FindChatScroll()
    {
        if (chatScroll is not null) return;
        chatScroll = FindScroll(ChatList);
        if (chatScroll is not null) chatScroll.ViewChanged += (_, _) =>
            chatFollowing = chatScroll.ScrollableHeight - chatScroll.VerticalOffset < 40;
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
    private async void ChatInputKeyDown(object sender, KeyRoutedEventArgs args)
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
            ChatContextPercent.Text = "";
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
            ChatContextPercent.Text = $"{usage.Percent:F0}%";
            ToolTipService.SetToolTip(ChatContextButton, usage.Description);
            AutomationProperties.SetName(ChatContextButton, usage.Description);
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
    private async void ChatCapacity(object sender, RoutedEventArgs args) => await Run(() =>
        ChatContextDialog.ShowAsync(Root.XamlRoot, Root.RequestedTheme, Model, ChatInput.Text));

    // ── Schedule, theme and settings ────────────────────────────────────────

    private async void ScheduleChanged(object sender, RoutedEventArgs args) => await SaveSchedule();
    private async void IntervalChanged(NumberBox sender, NumberBoxValueChangedEventArgs args) => await SaveSchedule();
    private async Task SaveSchedule()
    {
        if (updating || !Model.Connected || !double.IsFinite(Interval.Value)) return;
        await Run(async () => await Model.Backend.CallAsync("schedule.set",
            new { minutes = (int)Math.Round(Interval.Value), automatic = Automatic.IsChecked == true }));
    }

    private void ApplyTheme()
    {
        var dark = Model.Configuration.Appearance == "dark";
        Root.RequestedTheme = dark ? ElementTheme.Dark : ElementTheme.Light;
        ThemeIcon.Glyph = dark ? "\uE706" : "\uE708";
        IconActions.Describe(ThemeButton, dark ? "切换浅色模式" : "切换深色模式");
        Services.WindowChrome.ApplyCaptionButtons(AppWindow, Root.RequestedTheme);
        captions?.ApplyTheme(Root.RequestedTheme);
    }

    private async void ToggleTheme(object sender, RoutedEventArgs args) =>
        await SetAppearance(Model.Configuration.Appearance == "dark" ? "light" : "dark");

    private async void ThemeOptionClicked(object sender, RoutedEventArgs args)
    {
        var mode = (string)((FrameworkElement)sender).Tag;
        SyncThemeOptions(); // ToggleButtons flip themselves; the saved configuration decides which is on.
        if (mode != Model.Configuration.Appearance) await SetAppearance(mode);
    }

    private async Task SetAppearance(string mode)
    {
        if (changingTheme || !Model.Connected) return;
        changingTheme = true;
        ThemeButton.IsEnabled = ThemeLightOption.IsEnabled = ThemeDarkOption.IsEnabled = false;
        try { await Run(async () => await Model.Backend.CallAsync("appearance.set", new { mode })); }
        finally
        {
            changingTheme = false;
            ThemeButton.IsEnabled = ThemeLightOption.IsEnabled = ThemeDarkOption.IsEnabled = Model.Connected;
            SyncThemeOptions();
        }
    }

    private void SyncThemeOptions()
    {
        var dark = Model.Configuration.Appearance == "dark";
        ThemeLightOption.IsChecked = !dark;
        ThemeDarkOption.IsChecked = dark;
    }

    private void RefreshSettings()
    {
        SyncThemeOptions();
        var wasUpdating = updating;
        updating = true;
        TrayToggle.IsOn = Model.Configuration.CloseToTray;
        updating = wasUpdating;
        var profiles = Model.Configuration.ApiProfiles;
        var profile = profiles.FirstOrDefault(p => p.Id == Model.Configuration.SelectedApiProfile);
        ProfileSummary.Text = profile is null ? "尚未配置"
            : $"当前：{profile.Name}"
              + (profile.Model.Length > 0 ? $" · {profile.Model}" : profile.UsesLocalAgent ? " · 默认模型" : "")
              + $" · 共 {profiles.Count} 个配置";
        var templates = Model.Configuration.PromptTemplates;
        var template = templates.FirstOrDefault(t => t.Id == Model.Configuration.SelectedPromptTemplate);
        TemplateSummary.Text = template is null ? $"共 {templates.Count} 个模板"
            : $"当前：{template.Name} · 共 {templates.Count} 个模板";
    }

    private void SettingsCategoryChanged(object sender, SelectionChangedEventArgs args)
    {
        var category = (SettingsNav.SelectedItem as ListViewItem)?.Tag as string ?? "appearance";
        SettingsAppearance.Visibility = category == "appearance" ? Visibility.Visible : Visibility.Collapsed;
        SettingsAudio.Visibility = category == "audio" ? Visibility.Visible : Visibility.Collapsed;
        SettingsModels.Visibility = category == "models" ? Visibility.Visible : Visibility.Collapsed;
        SettingsAi.Visibility = category == "ai" ? Visibility.Visible : Visibility.Collapsed;
        SettingsTemplates.Visibility = category == "templates" ? Visibility.Visible : Visibility.Collapsed;
        SettingsDiagnostics.Visibility = category == "diagnostics" ? Visibility.Visible : Visibility.Collapsed;
        if (category == "audio" && Model.Connected) _ = Run(Model.RefreshDevicesAsync);
    }

    private async void TrayToggled(object sender, RoutedEventArgs args)
    {
        if (updating || !Model.Connected) return;
        var on = TrayToggle.IsOn;
        if (on == Model.Configuration.CloseToTray) return;
        await Run(async () =>
        {
            if (on) tray.Show();
            try { await Model.Backend.CallAsync("window.configure", new { close_to_tray = on }); }
            catch { if (!Model.Configuration.CloseToTray) tray.Dispose(); throw; }
            if (!on) tray.Dispose();
        });
        RefreshSettings();
    }

    private async void RefreshDevices(object sender, RoutedEventArgs args) => await Run(Model.RefreshDevicesAsync);

    private void PopulateDevicePickers()
    {
        updatingDevices = true;
        try
        {
            Fill(MicrophonePicker, "microphone", microphoneId);
            Fill(SystemPicker, "system", systemId);
            // A device that disappeared falls back to the system default, matching what the picker shows.
            microphoneId = DeviceId(MicrophonePicker); systemId = DeviceId(SystemPicker);
        }
        finally { updatingDevices = false; }

        void Fill(ComboBox box, string kind, string? selected)
        {
            var items = new List<DeviceInfo> { new("", "跟随系统默认设备", kind) };
            items.AddRange(Model.Devices.Where(device => device.Kind == kind));
            box.ItemsSource = items;
            box.SelectedItem = items.FirstOrDefault(device => device.Id == (selected ?? "")) ?? items[0];
        }
    }

    private static string? DeviceId(ComboBox box) => box.SelectedItem is DeviceInfo { Id.Length: > 0 } device ? device.Id : null;

    private void DeviceChanged(object sender, SelectionChangedEventArgs args)
    {
        if (updatingDevices) return;
        microphoneId = DeviceId(MicrophonePicker); systemId = DeviceId(SystemPicker);
    }

    private void EditProfiles(object sender, RoutedEventArgs args)
    {
        if (!Model.Connected) return;
        var editor = new ApiProfilesView(Model.Configuration, Model.Backend);
        editor.Finished += () =>
        {
            if (ReferenceEquals(ProfilesPanel.Content, editor)) FinishProfileEdit();
        };
        ProfilesPanel.Content = editor;
        ShowView("profiles");
    }

    private void FinishProfileEdit()
    {
        ShowView("settings");
        EditProfilesButton.Focus(FocusState.Programmatic);
    }

    private async void ManageModels(object sender, RoutedEventArgs args) =>
        await Run(() => ModelManagementDialog.ShowAsync(this, Root, Model));
    private async void EditTemplates(object sender, RoutedEventArgs args) =>
        await Run(() => ConfigurationDialogs.TemplatesAsync(Root.XamlRoot, Model.Configuration, Model.Backend));
    private void OpenCaptions(object sender, RoutedEventArgs args)
    {
        if (captions is null)
        {
            captions = new CaptionWindow(Model, () => (microphoneId, systemId));
            captions.Closed += (_, _) => captions = null;
        }
        captions.ShowControls(); captions.ApplyTheme(Root.RequestedTheme); captions.Activate();
    }
    private void OpenFolder(object sender, RoutedEventArgs args) => OpenDirectory(Model.Directory);
    private static void OpenDirectory(string directory)
    {
        if (System.IO.Directory.Exists(directory)) Process.Start(new ProcessStartInfo(directory) { UseShellExecute = true });
    }
    private void OpenBackendLog(object sender, RoutedEventArgs args)
    {
        var path = Services.ApplicationPaths.BackendLogPath(root);
        try
        {
            if (!File.Exists(path)) throw new IOException("启动本地后端后会生成诊断日志。");
            Process.Start(new ProcessStartInfo(path) { UseShellExecute = true });
        }
        catch (Exception error) { Model.ShowError(error); }
    }
    private async void Reconnect(object sender, RoutedEventArgs args) => await Run(Model.ConnectAsync);

    // ── Window lifetime ─────────────────────────────────────────────────────

    private void RestoreFromTray()
    {
        if (closed) return;
        AppWindow.Show();
        if (AppWindow.Presenter is OverlappedPresenter presenter
            && presenter.State == OverlappedPresenterState.Minimized) presenter.Restore();
        Activate();
        WinUIEx.WindowExtensions.SetForegroundWindow(this);
    }

    private void ExitFromTray()
    {
        if (closing || closed) return;
        forceExit = true;
        RestoreFromTray();
        Close();
    }

    private async void Closing(AppWindow sender, AppWindowClosingEventArgs args)
    {
        if (closed) return;
        args.Cancel = true;
        if (closing) return;
        try
        {
            await lifetime.CloseAsync(Model.Configuration.CloseToTray && !forceExit && verifyFile is null,
                () => { tray.Show(); tray.Update(Model.Status); AppWindow.Hide(); },
                async () =>
                {
                    contextClock.Stop(); recordClock.Stop(); contextRevision++;
                    StartButton.IsEnabled = false; ImportButton.IsEnabled = false;
                    Title = "言川 · 正在保存并退出…";
                    AppTitleDetail.Text = "正在保存并退出…";
                    await Model.Backend.DisposeAsync();
                },
                () => { tray.Dispose(); captions?.Close(); Close(); });
        }
        catch (Exception error) { forceExit = false; RestoreFromTray(); Model.ShowError(error); }
    }

    // ── Silent verification ─────────────────────────────────────────────────

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
