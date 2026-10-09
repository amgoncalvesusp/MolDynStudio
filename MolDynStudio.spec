# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).resolve()

hiddenimports = ['PyQt5.sip', 'PyQt5.QtWebEngineWidgets', 'matplotlib.backends.backend_qt5agg']
hiddenimports += collect_submodules('tabs')
hiddenimports += collect_submodules('windows')
hiddenimports += collect_submodules('core')
hiddenimports += collect_submodules('utils')
hiddenimports += collect_submodules('analysis')
analysis_datas, analysis_binaries, analysis_imports = collect_all('MDAnalysis')
trajectory_datas, trajectory_binaries, trajectory_imports = collect_all('mdtraj')
repair_collections = [collect_all(package) for package in ('Bio', 'pdbfixer', 'openmm', 'parmed')]
repair_datas = [item for data, _, _ in repair_collections for item in data]
repair_binaries = [item for _, binaries, _ in repair_collections for item in binaries]
repair_imports = [item for _, _, imports in repair_collections for item in imports]
hiddenimports += analysis_imports + trajectory_imports
hiddenimports += repair_imports


a = Analysis(
    [str(ROOT / 'main.py')],
    pathex=[str(ROOT)],
    binaries=analysis_binaries + trajectory_binaries + repair_binaries,
    datas=[(str(ROOT / 'environment.yml'), '.'), (str(ROOT / 'install_wsl.ps1'), '.'), (str(ROOT / 'assets'), 'assets')] + analysis_datas + trajectory_datas + repair_datas,
    hiddenimports=hiddenimports,
    hookspath=[str(ROOT / 'build' / 'hooks')],
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
