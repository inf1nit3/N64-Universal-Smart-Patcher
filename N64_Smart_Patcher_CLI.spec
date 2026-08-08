# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the CLI build. Windows, macOS and Linux.

The data list must stay in step with N64_Smart_Patcher.spec: a recipe the
GUI can find and the CLI cannot is a bug users hit and maintainers do not.
"""
import sys

WINDOWS = sys.platform == "win32"

DATAS = [
    ('src/n64patcher/patches', 'patches'),
    ('src/n64patcher/N64noAAPatcher/additionals', 'N64noAAPatcher/additionals'),
    ('src/n64patcher/N64noAAPatcher/hires_patches', 'N64noAAPatcher/hires_patches'),
]

a = Analysis(
    ['launch_cli.py'],
    pathex=['src'],
    binaries=[],
    datas=DATAS,
    hiddenimports=['n64patcher', 'n64patcher.cli'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='N64_Smart_Patcher_CLI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=WINDOWS,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
