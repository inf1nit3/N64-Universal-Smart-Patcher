# Build the release executables for the Universal N64 Smart Patcher.
# Usage: powershell -ExecutionPolicy Bypass -File build_release.ps1
$ErrorActionPreference = "Stop"

Write-Host "==> Installing package (editable) with dev extras..." -ForegroundColor Cyan
python -m pip install -e ".[dev]"
if ($LASTEXITCODE -ne 0) { throw "Install failed - aborting release build." }

Write-Host "==> Linting..." -ForegroundColor Cyan
python -m ruff check .
if ($LASTEXITCODE -ne 0) { throw "Lint failed - aborting release build." }

Write-Host "==> Type checking..." -ForegroundColor Cyan
python -m mypy
if ($LASTEXITCODE -ne 0) { throw "Type check failed - aborting release build." }

Write-Host "==> Running test suite..." -ForegroundColor Cyan
python -m pytest
if ($LASTEXITCODE -ne 0) { throw "Tests failed - aborting release build." }

Write-Host "==> Building GUI executable..." -ForegroundColor Cyan
python -m PyInstaller --noconfirm N64_Smart_Patcher.spec
if ($LASTEXITCODE -ne 0) { throw "GUI build failed." }

Write-Host "==> Building CLI executable..." -ForegroundColor Cyan
python -m PyInstaller --noconfirm N64_Smart_Patcher_CLI.spec
if ($LASTEXITCODE -ne 0) { throw "CLI build failed." }

Write-Host "==> Building wheel and sdist..." -ForegroundColor Cyan
python -m pip install -q build
python -m build
if ($LASTEXITCODE -ne 0) { throw "Wheel build failed." }

Write-Host "==> Done. Artifacts in dist\" -ForegroundColor Green
Get-ChildItem dist | Format-Table Name, Length
