namespace LiveCaptions.Desktop.Services;

/// <summary>Close-to-tray never calls shutdown; explicit exit always waits for graceful shutdown.</summary>
public sealed class WindowLifetime
{
    private enum State { Open, Exiting, Finished }
    private State state;
    public bool Exiting => state == State.Exiting;
    public bool Finished => state == State.Finished;
    public async Task CloseAsync(bool keepInTray, Action hide, Func<Task> shutdown, Action finish)
    {
        if (state != State.Open) return;
        if (keepInTray) { hide(); return; }
        state = State.Exiting;
        try
        {
            await shutdown();
        }
        catch { state = State.Open; throw; }
        state = State.Finished;
        finish();
    }
}
