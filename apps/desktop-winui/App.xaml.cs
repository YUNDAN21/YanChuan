using Microsoft.UI.Xaml;
using LiveCaptions.Desktop.Services;
using LiveCaptions.Desktop.ViewModels;
using System.Text.Json;

namespace LiveCaptions.Desktop;

public partial class App : Application
{
    private Window? window;
    private FileStream? instanceLock;
    public App() => InitializeComponent();

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        var arguments = Environment.GetCommandLineArgs();
        var root = Argument(arguments, "--root") ?? FindRoot();
        var dataRoot = ApplicationPaths.DataRoot(root,
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            Argument(arguments, "--data-root"));
        Directory.CreateDirectory(dataRoot);
        try
        {
            instanceLock = new FileStream(Path.Combine(dataRoot, ".desktop.instance.lock"),
                FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
        }
        catch (IOException) { Exit(); return; }
        var captionVerification = Argument(arguments, "--verify-captions");
        var client = new BackendClient(root, dataRoot, verificationFile: captionVerification,
            nativeLibrary: Argument(arguments, "--native-library") ?? BundledNativeLibrary(root),
            pythonExecutable: Argument(arguments, "--backend-python"));
        var model = new DesktopViewModel(client);
        window = new MainWindow(model, dataRoot, Argument(arguments, "--verify") ?? captionVerification,
                                Argument(arguments, "--report"), captionVerification is not null);
        window.Closed += (_, _) => { instanceLock?.Dispose(); instanceLock = null; };
        window.Activate();
    }

    private static string? Argument(string[] args, string name)
    {
        var index = Array.IndexOf(args, name);
        return index >= 0 && index + 1 < args.Length ? args[index + 1] : null;
    }

    private static string? BundledNativeLibrary(string root)
    {
        var marker = ApplicationPaths.FindMarker(root);
        if (marker is null) return null;
        using var document = JsonDocument.Parse(File.ReadAllText(marker));
        return document.RootElement.TryGetProperty("native_runtime", out var runtime)
            && runtime.GetString() is { Length: > 0 } path ? Path.Combine(root, path) : null;
    }

    private static string FindRoot()
    {
        if (ApplicationPaths.FindMarker(AppContext.BaseDirectory) is not null) return AppContext.BaseDirectory;
        for (var directory = new DirectoryInfo(AppContext.BaseDirectory); directory != null;
             directory = directory.Parent)
            if (File.Exists(Path.Combine(directory.FullName, "pyproject.toml")) &&
                Directory.Exists(Path.Combine(directory.FullName, "src", "livecaptions_asr")))
                return directory.FullName;
        throw new DirectoryNotFoundException("请通过项目启动脚本运行应用，或指定 --root。");
    }
}
