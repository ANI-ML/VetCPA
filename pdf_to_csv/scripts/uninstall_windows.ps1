# VetCPA uninstaller for Windows.
#
# Removes the cached OCR model weights (~2 GB) and the feedback database.
# Does not touch %USERPROFILE%\.cache\huggingface (that cache may be
# shared with other HuggingFace-using apps).
#
# IMPORTANT: this doesn't know where you extracted the VetCPA folder. You
# need to delete that yourself (or pass its path as a positional argument;
# see below).
#
# One-liner install from the repo:
#   iwr https://raw.githubusercontent.com/ANI-ML/VetCPA/main/pdf_to_csv/scripts/uninstall_windows.ps1 -OutFile uninstall.ps1 ; powershell -ExecutionPolicy Bypass -File .\uninstall.ps1
#
# Or with extras:
#   .\uninstall.ps1 -Yes                         # skip the confirmation prompt
#   .\uninstall.ps1 -AppPath 'C:\Tools\VetCPA'   # also delete your extracted app folder

param(
    [switch] $Yes,
    [string] $AppPath = ""
)

$ErrorActionPreference = "Stop"

$paths = @(
  (Join-Path $env:LOCALAPPDATA "VetCPA"),
  (Join-Path $env:USERPROFILE ".cache\docling")
)
if ($AppPath -ne "") { $paths += $AppPath }

Write-Host ""
Write-Host "VetCPA uninstaller - Windows" -ForegroundColor Cyan
Write-Host ""
Write-Host "This will permanently remove:"

$toRemove = @()
foreach ($p in $paths) {
    if (Test-Path $p) {
        $size = (Get-ChildItem -Recurse -File -ErrorAction SilentlyContinue $p | Measure-Object -Property Length -Sum).Sum
        $sizeMB = if ($size) { "{0:N1} MB" -f ($size / 1MB) } else { "" }
        Write-Host ("  * {0,-60} {1}" -f $p, $sizeMB)
        $toRemove += $p
    }
}

if ($toRemove.Count -eq 0) {
    Write-Host "  (nothing - VetCPA doesn't appear to be installed)"
    exit 0
}

Write-Host ""
Write-Host "It will NOT remove:"
Write-Host "  * %USERPROFILE%\.cache\huggingface  (shared with other HF apps)"
Write-Host "  * The .zip you originally downloaded"
if ($AppPath -eq "") {
    Write-Host "  * Your extracted VetCPA folder (pass -AppPath '<path>' to include it)"
}
Write-Host ""

# --- Confirm -------------------------------------------------------------
if (-not $Yes) {
    $reply = Read-Host "Continue? (y/N)"
    if ($reply -notmatch "^[Yy]") {
        Write-Host "Cancelled."
        exit 0
    }
}

# --- Stop any running instance ------------------------------------------
Get-Process -Name "VetCPA" -ErrorAction SilentlyContinue |
  ForEach-Object {
    Write-Host "Stopping running VetCPA (pid $($_.Id))..."
    $_ | Stop-Process -Force -ErrorAction SilentlyContinue
  }

# --- Remove --------------------------------------------------------------
foreach ($p in $toRemove) {
    Write-Host "Removing $p"
    try {
        Remove-Item -Recurse -Force -LiteralPath $p
    } catch {
        Write-Warning "Could not fully remove ${p}: $($_.Exception.Message)"
    }
}

Write-Host ""
Write-Host "  VetCPA uninstalled." -ForegroundColor Green
Write-Host "Reinstall any time from:"
Write-Host "  https://github.com/ANI-ML/VetCPA/releases/latest"
