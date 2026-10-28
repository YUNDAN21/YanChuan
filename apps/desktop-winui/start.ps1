$ErrorActionPreference = 'Stop'
$desktopRoot = Split-Path -Parent $PSScriptRoot
$desktopRoot = Split-Path -Parent $desktopRoot
$backendRoot = Join-Path $desktopRoot 'apps\desktop-backend'
$backendPython = Join-Path $backendRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $backendPython)) {
    & uv sync --project $backendRoot --locked
    if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed.' }
}
$desktopExe = Join-Path $PSScriptRoot 'bin\x64\Debug\net10.0-windows10.0.26100.0\win-x64\YanChuan.exe'
$existingDesktop = Get-Process -Name 'YanChuan' -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $desktopExe }
if ($existingDesktop) {
    Write-Output 'YanChuan is already running.'
    return
}
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$desktopMSBuild = & $vswhere -latest -products '*' -requires Microsoft.Component.MSBuild -find 'MSBuild\**\Bin\MSBuild.exe' | Select-Object -First 1
if (-not $desktopMSBuild) { throw 'Visual Studio MSBuild is required.' }
& $desktopMSBuild (Join-Path $PSScriptRoot 'LiveCaptions.csproj') /restore /p:Configuration=Debug /p:Platform=x64 /verbosity:minimal /nologo
if ($LASTEXITCODE -ne 0) { throw 'WinUI build failed.' }
Start-Process -FilePath $desktopExe -ArgumentList @('--root', ('"' + $desktopRoot + '"')) -WorkingDirectory $desktopRoot
