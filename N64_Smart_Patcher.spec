# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['launch_gui.py'],
    pathex=['src'],
    binaries=[],
    datas=[('src/n64patcher/N64noAAPatcher/additionals', 'n64patcher/N64noAAPatcher/additionals'), ('src/n64patcher/N64noAAPatcher/hires_patches', 'n64patcher/N64noAAPatcher/hires_patches'), ('app_icon.ico', '.')],
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
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['app_icon.ico'],
)
