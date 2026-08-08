# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the GUI build. Windows, macOS and Linux.

Everything platform-specific is decided here rather than in three separate
spec files, so a data file added for one platform cannot be forgotten on
another - the bug that shipped an EXE with an empty patch database twice.
"""
import sys

WINDOWS = sys.platform == "win32"
MACOS = sys.platform == "darwin"

# Bundled at these destination paths because n64_core resolves assets
# relative to sys._MEIPASS. Changing a destination here breaks the frozen
# build while leaving every unit test green, so scripts/smoke_test.py is
# run against the built binary in CI.
DATAS = [
    ('src/n64patcher/patches', 'patches'),
    ('src/n64patcher/N64noAAPatcher/additionals', 'N64noAAPatcher/additionals'),
    ('src/n64patcher/N64noAAPatcher/hires_patches', 'N64noAAPatcher/hires_patches'),
    ('app_icon.ico', '.'),
    ('app_icon.png', '.'),
]

if WINDOWS:
    ICON = 'app_icon.ico'
elif MACOS:
    # PyInstaller converts a PNG to .icns at build time (needs Pillow).
    ICON = 'app_icon.png'
else:
    # Linux binaries carry no embedded icon; the .desktop file supplies it.
    ICON = None

a = Analysis(
    ['launch_gui.py'],
    pathex=['src'],
    binaries=[],
    datas=DATAS,
    hiddenimports=['n64patcher', 'n64patcher.gui'],
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
    name='N64_Smart_Patcher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX is Windows-only here: it corrupts the signature of a macOS binary
    # and buys little on Linux.
    upx=WINDOWS,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
)

if MACOS:
    # Without this the build is a bare Unix executable that Finder will not
    # launch as an app and that cannot own a Dock icon or a menu bar.
    app = BUNDLE(
        exe,
        name='N64 Smart Patcher.app',
        icon=ICON,
        bundle_identifier='io.github.inf1nit3.n64patcher',
        info_plist={
            'NSHighResolutionCapable': True,
            'LSMinimumSystemVersion': '11.0',
            'CFBundleDocumentTypes': [{
                'CFBundleTypeName': 'Nintendo 64 ROM',
                'CFBundleTypeRole': 'Editor',
                'LSHandlerRank': 'Alternate',
                'CFBundleTypeExtensions': ['z64', 'n64', 'v64'],
            }],
        },
    )
