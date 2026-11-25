using LiveCaptions.Desktop.Services;

static class WindowLifetimeChecks
{
    public static async Task RunAsync()
    {
        var lifetime = new WindowLifetime();
        int hidden = 0, stopped = 0, closed = 0;
        var drain = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        Task Shutdown() { stopped++; return drain.Task; }
        await lifetime.CloseAsync(true, () => hidden++, Shutdown, () => closed++);
        if (hidden != 1 || stopped != 0 || closed != 0 || lifetime.Exiting)
            throw new Exception("Tray close stopped the backend");
        var exit = lifetime.CloseAsync(false, () => hidden++, Shutdown, () => closed++);
        await lifetime.CloseAsync(false, () => hidden++, Shutdown, () => closed++);
        if (stopped != 1 || closed != 0 || !lifetime.Exiting)
            throw new Exception("Duplicate exit or close before backend drain");
        drain.SetResult();
        await exit;
        if (!lifetime.Finished || closed != 1) throw new Exception("Exit did not finish");
        var failure = new WindowLifetime();
        try { await failure.CloseAsync(true, () => throw new IOException("tray"), Shutdown, () => closed++); }
        catch (IOException) { }
        if (failure.Exiting || failure.Finished || stopped != 1)
            throw new Exception("Tray failure stranded the window");
        try { await failure.CloseAsync(false, () => { }, () => Task.FromException(new IOException("shutdown")), () => { }); }
        catch (IOException) { }
        if (failure.Exiting || failure.Finished) throw new Exception("Exit failure prevented retry");
        await failure.CloseAsync(false, () => { }, () => Task.CompletedTask, () => { });
        if (!failure.Finished) throw new Exception("Exit retry failed");
        var callbackFailure = new WindowLifetime();
        int shutdowns = 0;
        Task StopOnce() { shutdowns++; return Task.CompletedTask; }
        try { await callbackFailure.CloseAsync(false, () => { }, StopOnce, () => throw new IOException("window")); }
        catch (IOException) { }
        await callbackFailure.CloseAsync(false, () => { }, StopOnce, () => { });
        if (!callbackFailure.Finished || callbackFailure.Exiting || shutdowns != 1)
            throw new Exception("Completed shutdown was repeated after window callback failed");
        Console.WriteLine("PASS: close-to-tray, explicit exit, duplicate close, backend drain and failure recovery");
    }
}
