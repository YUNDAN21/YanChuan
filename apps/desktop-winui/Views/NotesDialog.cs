using LiveCaptions.Desktop.Models;
using LiveCaptions.Desktop.Services;
using LiveCaptions.Desktop.ViewModels;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace LiveCaptions.Desktop.Views;

public static class NotesDialog
{
    public static async Task ShowAsync(XamlRoot root, IBackendClient backend, string meetingId,
        string? groupId = null, string? noteId = null, bool createNote = false)
    {
        var model = new NotesViewModel(backend);
        await model.LoadAsync(meetingId, groupId, noteId);
        if (createNote) await model.CreateNoteAsync();
        var groups = new ComboBox { DisplayMemberPath = "Title", HorizontalAlignment = HorizontalAlignment.Stretch,
            PlaceholderText = "选择分组", Header = "笔记分组" };
        var groupTitle = new TextBox { PlaceholderText = "分组名称" };
        var list = new ListView { DisplayMemberPath = "Title", SelectionMode = ListViewSelectionMode.Single };
        var title = new TextBox { PlaceholderText = "笔记标题" };
        var editor = new TextBox { AcceptsReturn = true, TextWrapping = TextWrapping.Wrap,
            PlaceholderText = "写下想法，支持 Markdown…", VerticalAlignment = VerticalAlignment.Stretch };
        ScrollViewer.SetVerticalScrollBarVisibility(editor, ScrollBarVisibility.Auto);
        var preview = new MarkdownView();
        var previewScroll = new ScrollViewer { Content = preview, Visibility = Visibility.Collapsed };
        var mode = new ToggleSwitch { Header = "预览", OffContent = "编辑", OnContent = "预览" };
        var status = new TextBlock { Text = "修改在切换笔记或关闭时保存", TextWrapping = TextWrapping.Wrap, FontSize = 12 };
        var error = new TextBlock { TextWrapping = TextWrapping.Wrap,
            Foreground = new SolidColorBrush(Microsoft.UI.Colors.OrangeRed) };
        var panel = new Grid { ColumnSpacing = 20, RowSpacing = 10,
            Width = Math.Max(500, Math.Min(1000, root.Size.Width - 120)),
            Height = Math.Max(300, Math.Min(650, root.Size.Height - 210)) };
        panel.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(210) });
        panel.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        panel.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        panel.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        var left = new Grid { RowSpacing = 10 };
        for (var i = 0; i < 4; i++) left.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        left.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        var groupActions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6 };
        var noteActions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6 };
        left.Children.Add(groups); Grid.SetRow(groupTitle, 1); left.Children.Add(groupTitle);
        Grid.SetRow(groupActions, 2); left.Children.Add(groupActions);
        Grid.SetRow(noteActions, 3); left.Children.Add(noteActions);
        Grid.SetRow(list, 4); left.Children.Add(list);
        var right = new Grid { RowSpacing = 10 };
        right.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        right.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        right.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        right.Children.Add(title);
        var editActions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 12 };
        editActions.Children.Add(mode);
        Grid.SetRow(editActions, 1); right.Children.Add(editActions);
        Grid.SetRow(editor, 2); right.Children.Add(editor);
        Grid.SetRow(previewScroll, 2); right.Children.Add(previewScroll);
        panel.Children.Add(left); Grid.SetColumn(right, 1); panel.Children.Add(right);
        var messages = new StackPanel { Spacing = 4 };
        messages.Children.Add(status); messages.Children.Add(error);
        Grid.SetRow(messages, 1); Grid.SetColumnSpan(messages, 2); panel.Children.Add(messages);
        var host = new UserControl { Content = panel };
        var dialog = new ContentDialog { Title = "笔记", Content = host, CloseButtonText = "关闭",
            XamlRoot = root, RequestedTheme = ((FrameworkElement)root.Content).ActualTheme };
        dialog.Resources["ContentDialogMaxWidth"] = 1200d;
        bool changing = false, busy = false;

        Task Save() => model.SaveAsync(title.Text, editor.Text);
        void Render()
        {
            changing = true;
            groups.ItemsSource = model.Groups; groups.SelectedItem = model.Group;
            groupTitle.Text = model.Group?.Title ?? "";
            groupTitle.IsEnabled = model.Group is not null;
            list.ItemsSource = null; list.ItemsSource = model.Notes; list.SelectedItem = model.Note;
            title.Text = model.Note?.Title ?? ""; editor.Text = model.Note?.Content ?? "";
            title.IsEnabled = editor.IsEnabled = model.Note is not null;
            preview.Text = editor.Text;
            changing = false;
        }
        async Task Run(Func<Task> action)
        {
            if (busy) return;
            busy = true; host.IsEnabled = false; error.Text = "";
            try { await action(); Render(); status.Text = "已保存 · 修改在切换笔记或关闭时保存"; }
            catch (Exception ex)
            {
                error.Text = ex.Message;
                changing = true; groups.SelectedItem = model.Group; list.SelectedItem = model.Note; changing = false;
            }
            finally { busy = false; host.IsEnabled = true; }
        }
        Button Action(string glyph, string label, Func<Task> action)
        {
            var button = IconActions.Create(glyph, label);
            button.Click += async (_, _) => await Run(action);
            return button;
        }
        Button Delete(string label, string warning, Func<Task> action)
        {
            var button = IconActions.Create("\uE74D", label);
            var confirm = new Button { Content = "确认删除", HorizontalAlignment = HorizontalAlignment.Right };
            var contents = new StackPanel { Spacing = 12, MaxWidth = 260 };
            contents.Children.Add(new TextBlock { Text = warning, TextWrapping = TextWrapping.Wrap });
            contents.Children.Add(confirm);
            var flyout = new Flyout { Content = contents };
            button.Flyout = flyout;
            confirm.Click += async (_, _) => { flyout.Hide(); await Run(action); };
            return button;
        }
        groupActions.Children.Add(Action("\uE710", "新建分组", async () => { await Save(); await model.CreateGroupAsync(); }));
        groupActions.Children.Add(Action("\uE74E", "保存分组名称", async () => { await Save(); await model.RenameGroupAsync(groupTitle.Text); }));
        groupActions.Children.Add(Delete("删除分组", "删除当前分组及其中全部笔记？会议原文和问答仍会保留。",
            model.DeleteGroupAsync));
        noteActions.Children.Add(Action("\uE710", "新建笔记", async () => { await Save(); await model.CreateNoteAsync(); }));
        noteActions.Children.Add(Delete("删除笔记", "删除当前笔记？此操作无法撤销。", model.DeleteNoteAsync));
        editActions.Children.Add(Action("\uE74E", "保存笔记", Save));
        mode.Toggled += (_, _) =>
        {
            preview.Text = editor.Text;
            previewScroll.Visibility = mode.IsOn ? Visibility.Visible : Visibility.Collapsed;
            editor.Visibility = mode.IsOn ? Visibility.Collapsed : Visibility.Visible;
        };
        groups.SelectionChanged += async (_, _) =>
        {
            if (changing || busy || groups.SelectedItem is not NoteGroup selected || selected.Id == model.Group?.Id) return;
            await Run(async () => { await Save(); await model.OpenGroupAsync(selected); });
        };
        list.SelectionChanged += async (_, _) =>
        {
            if (changing || busy || list.SelectedItem is not NoteEntry selected || selected.Id == model.Note?.Id) return;
            await Run(async () => { await Save(); model.SelectNote(selected); });
        };
        dialog.Closing += async (_, args) =>
        {
            if (busy) { args.Cancel = true; return; }
            var deferral = args.GetDeferral();
            busy = true; host.IsEnabled = false;
            try { await Save(); }
            catch (Exception ex) { error.Text = ex.Message; args.Cancel = true; }
            finally { busy = false; host.IsEnabled = true; deferral.Complete(); }
        };
        Render();
        await dialog.ShowAsync();
    }
}
