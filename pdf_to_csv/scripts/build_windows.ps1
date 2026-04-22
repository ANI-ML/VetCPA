# Build VetCPA for Windows using PyInstaller.
#
# Usage (from pdf_to_csv/):
#     powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1
#
# Or with bundled models (requires an already-populated %USERPROFILE%\.cache\docling):
#     $env:VETCPA_BUNDLE_MODELS=1; powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1
#
# Requirements: a .venv directory prepared with `py -3.11 -m venv .venv`
# and `.\.venv\Scripts\pip.exe install -e ".[dev,bundle]"`.

$ErrorActionPreference = "Stop"

Push-Location (Join-Path $PSScriptRoot "..")

try {
    $venv = if ($env:VENV) { $env:VENV } else { ".venv" }
    $python = Join-Path $venv "Scripts\python.exe"
    $pip = Join-Path $venv "Scripts\pip.exe"
    $pyinstaller = Join-Path $venv "Scripts\pyinstaller.exe"

    if (-not (Test-Path $python)) {
        Write-Error "Expected $python not found. Run: py -3.11 -m venv $venv ; .\$venv\Scripts\pip install -e `".[dev,bundle]`""
        exit 1
    }

    # Install PyInstaller on demand if it isn't there yet.
    & $python -c "import PyInstaller" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Installing PyInstaller into $venv ..."
        & $pip install --quiet "pyinstaller>=6.8"
    }

    Write-Host "Cleaning previous build output..."
    if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
    if (Test-Path "dist")  { Remove-Item -Recurse -Force "dist" }

    if ($env:VETCPA_BUNDLE_MODELS -eq "1") {
        Write-Host "VETCPA_BUNDLE_MODELS=1 - bundling %USERPROFILE%\.cache\docling into the build."
        $cache = Join-Path $env:USERPROFILE ".cache\docling"
        if (-not (Test-Path $cache)) {
            Write-Error "Expected $cache to exist. Run the app once with internet (so Docling can download its models) and re-run."
            exit 1
        }
    }

    Write-Host "Building VetCPA via PyInstaller..."
    & $pyinstaller VetCPA.spec --clean --noconfirm
    if ($LASTEXITCODE -ne 0) {
        Write-Error "PyInstaller build failed."
        exit $LASTEXITCODE
    }

    $exe = "dist\VetCPA\VetCPA.exe"
    if (Test-Path $exe) {
        $size = "{0:N1} MB" -f ((Get-ChildItem -Recurse "dist\VetCPA" | Measure-Object -Property Length -Sum).Sum / 1MB)
        Write-Host ""
        Write-Host "  Built dist\VetCPA\ ($size)" -ForegroundColor Green
        Write-Host "  Launch:  dist\VetCPA\VetCPA.exe"
        Write-Host "  Zip it and hand off the whole dist\VetCPA\ folder to the user."
        if ($env:VETCPA_BUNDLE_MODELS -ne "1") {
            Write-Host "  Note: first launch downloads ~2 GB of Docling models from HuggingFace." -ForegroundColor Yellow
            Write-Host "        Subsequent launches are fully offline."
        }
    } else {
        Write-Error "Build finished but $exe is missing - see PyInstaller output above."
        exit 1
    }
}
finally {
    Pop-Location
}
