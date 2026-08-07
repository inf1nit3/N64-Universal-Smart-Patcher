#!/usr/bin/env bash
# Build the release executables for the Universal N64 Smart Patcher
# on macOS / Linux (Windows: use build_release.ps1).
# Usage: ./build_release.sh
set -euo pipefail

echo "==> Installing package (editable) with dev extras..."
python3 -m pip install -e ".[dev]"

echo "==> Linting..."
python3 -m ruff check .

echo "==> Type checking..."
python3 -m mypy

echo "==> Running test suite..."
python3 -m pytest || { echo "Tests failed - aborting release build."; exit 1; }

echo "==> Building GUI executable..."
python3 -m PyInstaller --noconfirm N64_Smart_Patcher.spec

echo "==> Building CLI executable..."
python3 -m PyInstaller --noconfirm N64_Smart_Patcher_CLI.spec

echo "==> Building wheel and sdist..."
python3 -m pip install -q build
python3 -m build

echo "==> Done. Artifacts in dist/"
ls -la dist/
