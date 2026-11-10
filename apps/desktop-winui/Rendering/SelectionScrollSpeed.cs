namespace LiveCaptions.Desktop.Rendering;

public static class SelectionScrollSpeed
{
    // Logical pixels per second; a full viewport takes several seconds.
    public static double At(double y, double height)
    {
        if (height <= 0 || !double.IsFinite(y)) return 0;
        var edge = Math.Min(48, height / 4);
        if (y < edge) return -Math.Clamp((edge - y) / edge * 140, 25, 140);
        if (y > height - edge) return Math.Clamp((y - height + edge) / edge * 140, 25, 140);
        return 0;
    }
}
