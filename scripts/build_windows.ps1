param(
    [string]$Version = "0.2.1"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "Cleaning old build artifacts..."
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

Write-Host "Building Windows executable with PyInstaller..."
python -m PyInstaller --noconfirm .\LiteratureManagementTool.spec

$distDir = Join-Path $root "dist\Literature management tool"
if (-not (Test-Path $distDir)) {
    throw "Build output not found: $distDir"
}

Copy-Item .\README.md $distDir -Force

$archive = Join-Path $root ("dist\Literature-management-tool-v{0}-windows-x64.zip" -f $Version)
if (Test-Path $archive) {
    Remove-Item $archive -Force
}

Write-Host "Creating release archive: $archive"
Compress-Archive -Path "$distDir\*" -DestinationPath $archive -CompressionLevel Optimal
Write-Host "Done. Archive created at: $archive"
