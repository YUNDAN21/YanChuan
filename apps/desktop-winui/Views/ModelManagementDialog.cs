using LiveCaptions.Desktop.Models;
using LiveCaptions.Desktop.ViewModels;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Storage.Pickers;

namespace LiveCaptions.Desktop.Views;

public static class ModelManagementDialog
{
    public static async Task ShowAsync(Window owner, FrameworkElement root, DesktopViewModel model)
    {
        var backend = model.Backend;
        var inventory = WireJson.Read<ModelInventory>(await backend.CallAsync("models.list"));
        var picker = new ComboBox { Header = "识别模型", HorizontalAlignment = HorizontalAlignment.Stretch };
        picker.ItemsSource = inventory.Assets.Where(a => a.Kind == "asr").ToList();
        picker.SelectedItem = ((IEnumerable<ModelAsset>)picker.ItemsSource).First(a => a.Id == inventory.Settings.SelectedModel);
        var source = new ComboBox { Header = "下载源", HorizontalAlignment = HorizontalAlignment.Stretch };
        foreach (var name in inventory.Sources ?? ["huggingface"])
            source.Items.Add(new ComboBoxItem {
                Content = name == "modelscope" ? "ModelScope" : "Hugging Face", Tag = name });
        source.SelectedItem = source.Items.Cast<ComboBoxItem>().FirstOrDefault(
            item => (string)item.Tag == inventory.Settings.Source) ?? source.Items[0];
        var directory = new TextBox { Header = "模型存放目录", Text = inventory.Directory, MinWidth = 440 };
        var browse = IconActions.Create("\uED25", "选择模型文件夹");
        var native = inventory.Runtime.StartsWith("crispasr");
        var modelFile = new TextBox { Header = "已有 GGUF 文件（留空则从模型目录查找）",
            Text = inventory.Settings.ModelFile ?? "", Visibility = native ? Visibility.Visible : Visibility.Collapsed };
        var browseModel = IconActions.Create("\uE8E5", "选择 GGUF 文件");
        browseModel.Visibility = native ? Visibility.Visible : Visibility.Collapsed;
        var download = IconActions.Create("\uE896", "下载所选模型和分段模型");
        var cancel = IconActions.Create("\uE711", "取消下载");
        var progress = new ProgressBar { IsIndeterminate = inventory.Download.State == "downloading" };
        var status = new TextBlock { TextWrapping = TextWrapping.Wrap, MaxWidth = 520 };
        var assets = new TextBlock { TextWrapping = TextWrapping.Wrap, MaxWidth = 520, Opacity = 0.7 };
        var error = new InfoBar { Severity = InfoBarSeverity.Error, IsClosable = true };
        var actions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        actions.Children.Add(download); actions.Children.Add(cancel);
        var content = new StackPanel { Spacing = 12, MaxWidth = 560 };
        var existingModelRow = IconActions.WithField(modelFile, browseModel);
        existingModelRow.Visibility = native ? Visibility.Visible : Visibility.Collapsed;
        foreach (var element in new UIElement[] { picker, source, IconActions.WithField(directory, browse), existingModelRow,
            new TextBlock { Text = native
                ? "使用 CrispASR 兼容 Q8 GGUF。旧 safetensors 会保留，需要另行下载 GGUF；分段模型继续复用。"
                : "已有 models 文件夹可直接复用。Qwen 下载源可切换；分段模型使用固定上游版本。", TextWrapping = TextWrapping.Wrap, MaxWidth = 520 },
            assets, actions, progress, status, error }) content.Children.Add(element);
        var dialog = new ContentDialog { Title = "模型管理", XamlRoot = root.XamlRoot,
            Content = new ScrollViewer { Content = content, VerticalScrollBarVisibility = ScrollBarVisibility.Auto },
            PrimaryButtonText = "保存并使用", CloseButtonText = "关闭", RequestedTheme = root.RequestedTheme };
        var disposed = false;

        void ShowError(Exception exception) { error.Message = exception.Message; error.IsOpen = true; }
        void Update(ModelDownload state)
        {
            var busy = state.State == "downloading";
            status.Text = state.Message.Length > 0 ? state.Message : "选择模型和目录后，可下载或直接使用已有模型";
            progress.IsIndeterminate = busy && state.Total <= 0;
            progress.Value = state.Total > 0 ? Math.Clamp(100 * state.Current / state.Total, 0, 100) : state.State == "completed" ? 100 : 0;
            picker.IsEnabled = source.IsEnabled = directory.IsEnabled = browse.IsEnabled = !busy && !model.Active;
            modelFile.IsEnabled = browseModel.IsEnabled = !busy && !model.Active;
            download.IsEnabled = !busy && !model.Active;
            cancel.IsEnabled = busy;
            dialog.IsPrimaryButtonEnabled = !busy && !model.Active;
        }
        void ShowAssets(ModelInventory value) => assets.Text = string.Join("\n", value.Assets.Select(a =>
            (a.Ready ? "✓ " : "○ ") + a.Name + (a.Ready ? " · 已下载" : " · 未下载")));
        async Task Save()
        {
            inventory = WireJson.Read<ModelInventory>(await backend.CallAsync("models.configure", new {
                selected_model = ((ModelAsset)picker.SelectedItem).Id,
                source = (string)((ComboBoxItem)source.SelectedItem).Tag, directory = directory.Text.Trim(),
                model_file = native && modelFile.Text.Trim().Length > 0 ? modelFile.Text.Trim() : null }));
            ShowAssets(inventory);
        }
        void Observe(BackendEvent value)
        {
            if (value.Name != "models.download") return;
            root.DispatcherQueue.TryEnqueue(async () => {
                if (disposed) return;
                var state = WireJson.Read<ModelDownload>(value.Payload);
                Update(state);
                if (state.State is "completed" or "failed" or "cancelled")
                {
                    try { inventory = WireJson.Read<ModelInventory>(await backend.CallAsync("models.list")); ShowAssets(inventory); }
                    catch (Exception exception) { if (!disposed) ShowError(exception); }
                }
            });
        }
        browse.Click += async (_, _) => {
            try {
                var folderPicker = new FolderPicker(); folderPicker.FileTypeFilter.Add("*");
                WinRT.Interop.InitializeWithWindow.Initialize(folderPicker, WinRT.Interop.WindowNative.GetWindowHandle(owner));
                var folder = await folderPicker.PickSingleFolderAsync();
                if (folder is not null) directory.Text = folder.Path;
            } catch (Exception exception) { ShowError(exception); }
        };
        download.Click += async (_, _) => {
            download.IsEnabled = false;
            try { await Save(); Update(WireJson.Read<ModelDownload>(await backend.CallAsync("models.download"))); }
            catch (Exception exception) { ShowError(exception); download.IsEnabled = true; }
        };
        browseModel.Click += async (_, _) => {
            try {
                var filePicker = new FileOpenPicker(); filePicker.FileTypeFilter.Add(".gguf");
                WinRT.Interop.InitializeWithWindow.Initialize(filePicker, WinRT.Interop.WindowNative.GetWindowHandle(owner));
                var file = await filePicker.PickSingleFileAsync();
                if (file is not null) modelFile.Text = file.Path;
            } catch (Exception exception) { ShowError(exception); }
        };
        cancel.Click += async (_, _) => { try { await backend.CallAsync("models.cancel"); } catch (Exception exception) { ShowError(exception); } };
        dialog.PrimaryButtonClick += async (_, args) => {
            var deferral = args.GetDeferral();
            try { await Save(); }
            catch (Exception exception) { args.Cancel = true; ShowError(exception); }
            finally { deferral.Complete(); }
        };
        ShowAssets(inventory); Update(inventory.Download);
        backend.EventReceived += Observe;
        try { await dialog.ShowAsync(); }
        finally { disposed = true; backend.EventReceived -= Observe; }
    }
}
