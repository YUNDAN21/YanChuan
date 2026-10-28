namespace LiveCaptions.Desktop.ViewModels;

public sealed class CaptionChromeState
{
    public bool PointerInside { get; set; }
    public bool Expanded { get; set; }
    public int OpenInteractions { get; set; }
    public bool Visible => PointerInside || Expanded || OpenInteractions > 0;
}
