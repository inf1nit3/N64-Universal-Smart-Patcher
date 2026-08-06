#!/usr/bin/env bash
# Build the release executables for the Universal N64 Smart Patcher
# on macOS / Linux (Windows: use build_release.ps1).
# Usage: ./build_release.sh
set -euo pipefail

echo "==> Running test suite..."
python3 -m unittest discover -s . -p 'test_*.py' || { echo "Tests failed - aborting release build."; exit 1; }

echo "==> Building GUI executable..."
python3 -m PyInstaller --onefile --noconsole --name "N64_Smart_Patcher" \
  --add-data "N64noAAPatcher/additionals:N64noAAPatcher/additionals" \
  --add-data "N64noAAPatcher/hires_patches:N64noAAPatcher/hires_patches" \
  --add-data "app_icon.png:." \
  N64_Smart_Patcher_GUI.py

echo "==> Building CLI executable..."
python3 -m PyInstaller --onefile --console --name "N64_Smart_Patcher_CLI" \
  --add-data "N64noAAPatcher/additionals:N64noAAPatcher/additionals" \
  --add-data "N64noAAPatcher/hires_patches:N64noAAPatcher/hires_patches" \
  n64_patcher_cli.py

echo "==> Done. Artifacts in dist/"
ls -la dist/
