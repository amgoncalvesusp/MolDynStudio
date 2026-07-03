"""Build a self-contained Windows distribution for MolDynStudio.

Bundled (Windows side):
    * Python + PyQt5 + matplotlib + pure-Python analysis libs
    * install_wsl.ps1     -- launched on first run if WSL is missing
    * environment.yml     -- used to set up the WSL conda env
    * launch_windows.bat / .ps1

Installed in WSL on first run (NOT bundled, too large):
    * GROMACS, AmberTools, gmx_MMPBSA, MDAnalysis, MDTraj, ProDy

Usage:
    pip install pyinstaller
    python build/create_installer.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRY = REPO_ROOT / "main.py"


def ensure_pyinstaller() -> None:
    probe = subprocess.run(
        [sys.executable, "-c", "import PyInstaller"],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        print("Installing PyInstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])


def build() -> int:
    ensure_pyinstaller()
    sep = ";" if sys.platform == "win32" else ":"
    icon = REPO_ROOT / "assets" / "logo.ico"
    work_path = Path(tempfile.mkdtemp(prefix="moldynstudio-pyinstaller-"))
    spec_path = work_path
    dist_path = REPO_ROOT / "dist"

    required = [REPO_ROOT / "environment.yml", REPO_ROOT / "install_wsl.ps1"]
    missing = [str(r) for r in required if not r.exists()]
    if missing:
        print("ERROR: required resources missing:", file=sys.stderr)
        for path in missing:
            print(f"  - {path}", file=sys.stderr)
        return 1

    args: list[str] = [
        sys.executable, "-m", "PyInstaller",
        "--name", "MolDynStudio",
        "--windowed",
        "--onefile",
        "--noconfirm",
        "--clean",
        "--workpath", str(work_path),
        "--specpath", str(spec_path),
        "--distpath", str(dist_path),
        "--add-data", f"{REPO_ROOT / 'environment.yml'}{sep}.",
        "--add-data", f"{REPO_ROOT / 'install_wsl.ps1'}{sep}.",
        "--hidden-import", "PyQt5.sip",
        "--hidden-import", "PyQt5.QtWebEngineWidgets",
        "--hidden-import", "matplotlib.backends.backend_qt5agg",
        "--collect-submodules", "tabs",
        "--collect-submodules", "windows",
        "--collect-submodules", "core",
        "--collect-submodules", "utils",
    ]
    if (REPO_ROOT / "assets").is_dir():
        args.extend(["--add-data", f"{REPO_ROOT / 'assets'}{sep}assets"])
    if icon.exists():
        args.extend(["--icon", str(icon)])
    args.append(str(ENTRY))
    print("Running:", " ".join(args))
    result = subprocess.call(args, cwd=str(REPO_ROOT))
    if result == 0:
        shutil.rmtree(work_path, ignore_errors=True)
    return result


if __name__ == "__main__":
    raise SystemExit(build())
