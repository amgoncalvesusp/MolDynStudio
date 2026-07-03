# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(__file__).resolve().parent

hiddenimports = ['PyQt5.sip', 'PyQt5.QtWebEngineWidgets', 'matplotlib.backends.backend_qt5agg']
hiddenimports += collect_submodules('tabs')
hiddenimports += collect_submodules('windows')
hiddenimports += collect_submodules('core')
hiddenimports += collect_submodules('utils')


a = Analysis(
    [str(ROOT / 'main.py')],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(ROOT / 'environment.yml'), '.'), (str(ROOT / 'install_wsl.ps1'), '.'), (str(ROOT / 'assets'), 'assets')],
    hiddenimports=hiddenimports,
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
    name='MolDynStudio',
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
)
