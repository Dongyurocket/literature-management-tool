param(
    [string]$Version = "1.1.1"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Resolve-InnoSetupCompiler {
    $candidates = @(
        $env:ISCC_PATH,
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    ) | Where-Object { $_ }

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return (Resolve-Path $candidate).Path
        }
    }

    $command = Get-Command iscc -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    throw "Inno Setup compiler not found. Install JRSoftware.InnoSetup or set ISCC_PATH."
}

Write-Host "Cleaning old build artifacts..."
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

Write-Host "Building Windows executable with PyInstaller..."
python -m PyInstaller --noconfirm .\LiteratureManagementTool.spec

$distDir = Join-Path $root "dist\Literature management tool"
if (-not (Test-Path $distDir)) {
    throw "Build output not found: $distDir"
}

Copy-Item .\README.md $distDir -Force
Copy-Item .\LICENSE $distDir -Force

$setup = Join-Path $root ("dist\Literature-management-tool-v{0}-Setup.exe" -f $Version)
if (Test-Path $setup) {
    Remove-Item $setup -Force
}

$iscc = Resolve-InnoSetupCompiler
Write-Host "Building Windows installer with Inno Setup..."
& $iscc `
    "/DMyAppVersion=$Version" `
    "/DSourceDir=$distDir" `
    "/DOutputDir=$(Join-Path $root 'dist')" `
    ".\installer\LiteratureManagementTool.iss"

if (-not (Test-Path $setup)) {
    throw "Installer output not found: $setup"
}

Write-Host "Done. Installer created at: $setup"
