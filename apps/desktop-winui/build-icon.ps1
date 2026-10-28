param([string]$Source = (Join-Path $PSScriptRoot 'Assets\AppIcon.png'))
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$iconSizes = @(16, 24, 32, 48, 64, 128, 256)
$iconSource = [System.Drawing.Image]::FromFile($Source)
$iconImages = @()
try {
    foreach ($iconSize in $iconSizes) {
        $iconBitmap = New-Object System.Drawing.Bitmap($iconSize, $iconSize)
        $iconGraphics = [System.Drawing.Graphics]::FromImage($iconBitmap)
        $iconStream = New-Object System.IO.MemoryStream
        try {
            $iconGraphics.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceCopy
            $iconGraphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $iconGraphics.DrawImage($iconSource, 0, 0, $iconSize, $iconSize)
            $iconBitmap.Save($iconStream, [System.Drawing.Imaging.ImageFormat]::Png)
            $iconImages += ,$iconStream.ToArray()
        } finally { $iconStream.Dispose(); $iconGraphics.Dispose(); $iconBitmap.Dispose() }
    }
    $iconOutput = [System.IO.File]::Create((Join-Path $PSScriptRoot 'Assets\AppIcon.ico'))
    $iconWriter = New-Object System.IO.BinaryWriter($iconOutput)
    try {
        $iconWriter.Write([uint16]0); $iconWriter.Write([uint16]1); $iconWriter.Write([uint16]$iconSizes.Count)
        $iconOffset = 6 + 16 * $iconSizes.Count
        for ($iconIndex = 0; $iconIndex -lt $iconSizes.Count; $iconIndex++) {
            $iconDimension = if ($iconSizes[$iconIndex] -eq 256) { 0 } else { $iconSizes[$iconIndex] }
            $iconWriter.Write([byte]$iconDimension); $iconWriter.Write([byte]$iconDimension)
            $iconWriter.Write([byte]0); $iconWriter.Write([byte]0)
            $iconWriter.Write([uint16]1); $iconWriter.Write([uint16]32)
            $iconWriter.Write([uint32]$iconImages[$iconIndex].Length)
            $iconWriter.Write([uint32]$iconOffset)
            $iconOffset += $iconImages[$iconIndex].Length
        }
        foreach ($iconImage in $iconImages) { $iconWriter.Write([byte[]]$iconImage) }
    } finally { $iconWriter.Dispose(); $iconOutput.Dispose() }
} finally { $iconSource.Dispose() }
