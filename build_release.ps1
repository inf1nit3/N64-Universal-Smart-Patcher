# Build the release executables for the Universal N64 Smart Patcher.
# Usage: powershell -ExecutionPolicy Bypass -File build_release.ps1
$ErrorActionPreference = "Stop"

Write-Host "==> Running test suite..." -ForegroundColor Cyan
python -m unittest test_n64_core
if ($LASTEXITCODE -ne 0) { throw "Tests failed - aborting release build." }

Write-Host "==> Building GUI executable..." -ForegroundColor Cyan
python -m PyInstaller --onefile --noconsole --name "N64_Smart_Patcher" --icon "app_icon.ico" `
  --add-data "N64noAAPatcher/additionals;N64noAAPatcher/additionals" `
  --add-data "N64noAAPatcher/hires_patches;N64noAAPatcher/hires_patches" `
  --add-data "app_icon.ico;." `
  N64_Smart_Patcher_GUI.py

Write-Host "==> Building CLI executable..." -ForegroundColor Cyan
python -m PyInstaller --onefile --console --name "N64_Smart_Patcher_CLI" `
  --add-data "N64noAAPatcher/additionals;N64noAAPatcher/additionals" `
  --add-data "N64noAAPatcher/hires_patches;N64noAAPatcher/hires_patches" `
  n64_patcher_cli.py

Write-Host "==> Done. Artifacts in dist\" -ForegroundColor Green
Get-ChildItem dist | Format-Table Name, Length
