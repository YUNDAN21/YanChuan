param([switch]$NoRestore)

$ErrorActionPreference = 'Stop'
$desktopRoot = Split-Path -Parent $PSScriptRoot
$desktopRoot = Split-Path -Parent $desktopRoot
$desktopExe = Join-Path $PSScriptRoot 'bin\x64\Debug\net10.0-windows10.0.26100.0\win-x64\YanChuan.exe'
$installedRuntimeOutput = Join-Path $PSScriptRoot 'bin\installed-runtime'
$installedRuntimeExe = Join-Path $installedRuntimeOutput 'YanChuan.exe'
$existingDesktop = Get-Process -Name 'YanChuan' -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -in @($desktopExe, $installedRuntimeExe)
}
if ($existingDesktop) {
    Write-Output 'YanChuan is already running.'
    return
}

$backendRoot = Join-Path $desktopRoot 'apps\desktop-backend'
$backendPython = Join-Path $backendRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $backendPython)) {
    if ($NoRestore) { throw 'The backend environment is required with -NoRestore.' }
    & uv sync --project $backendRoot --locked
    if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed.' }
}
$buildArgs = @((Join-Path $PSScriptRoot 'LiveCaptions.csproj'),
    '/p:Configuration=Debug', '/p:Platform=x64', '/verbosity:minimal', '/nologo')
if (-not $NoRestore) { $buildArgs += '/restore' }

# A framework-dependent build needs an installed runtime at least as new as the referenced SDK (2.7.0).
$installedRuntime = Get-AppxPackage -Name 'Microsoft.WindowsAppRuntime.2' -ErrorAction SilentlyContinue |
    Where-Object { $_.IsFramework -and $_.Architecture -eq 'X64' -and [version]$_.Version -ge [version]'2.7.0.0' } |
    Select-Object -First 1
if ($installedRuntime) {
    $buildArgs += @('/p:WindowsAppSDKSelfContained=false', "/p:OutDir=$installedRuntimeOutput")
    $desktopExe = $installedRuntimeExe
}
$vswhere = Join-Path ([Environment]::GetFolderPath('ProgramFilesX86')) 'Microsoft Visual Studio\Installer\vswhere.exe'
$desktopMSBuild = & $vswhere -latest -products '*' -requires Microsoft.Component.MSBuild -find 'MSBuild\**\Bin\MSBuild.exe' | Select-Object -First 1
if (-not $desktopMSBuild) { throw 'Visual Studio MSBuild is required.' }
& $desktopMSBuild @buildArgs
if ($LASTEXITCODE -ne 0) { throw 'WinUI build failed.' }
Start-Process -FilePath $desktopExe -ArgumentList @('--root', ('"' + $desktopRoot + '"')) -WorkingDirectory $desktopRoot
