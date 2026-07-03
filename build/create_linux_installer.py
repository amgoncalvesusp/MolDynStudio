"""Create a self-extracting Linux installer for MolDynStudio.

The generated ``.run`` file installs the source bundle under
``~/.local/share/moldynstudio``, creates a Python virtual environment for the
GUI dependencies, writes a ``moldynstudio`` launcher to ``~/.local/bin``, and
registers a desktop entry. The scientific Conda environment is still managed
by the app launcher from ``environment.yml``.
"""

from __future__ import annotations

import argparse
import base64
import io
import os
import stat
import tarfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = REPO_ROOT / "dist"
DEFAULT_OUTPUT = DIST_DIR / "MolDynStudio-linux-x86_64.run"

INCLUDE_ROOT_FILES = (
    "README.md",
    "main.py",
    "gromacs_analysis_studio_v11.py",
    "setup_wizard.py",
    "setup_environment.py",
    "settings_dialog.py",
    "environment.yml",
    "requirements.txt",
    "requirements-analysis-studio.txt",
    "launch_analysis_studio.sh",
    "README_GROMACS_Analysis_Studio_v11.md",
)
INCLUDE_DIRS = ("analysis", "assets", "core", "tabs", "utils", "windows")
EXCLUDED_PARTS = {
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".git",
    ".claude",
    "build",
    "dist",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}

INSTALLER_STUB = r"""#!/usr/bin/env bash
set -euo pipefail

APP_NAME="MolDynStudio"
APP_ID="moldynstudio"
INSTALL_DIR="${MOLDYNSTUDIO_INSTALL_DIR:-$HOME/.local/share/moldynstudio}"
BIN_DIR="${MOLDYNSTUDIO_BIN_DIR:-$HOME/.local/bin}"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
PYTHON_BIN="${PYTHON:-python3}"
VENV_DIR="$INSTALL_DIR/.venv"
LAUNCHER="$BIN_DIR/$APP_ID"
DESKTOP_FILE="$DESKTOP_DIR/$APP_ID.desktop"

payload_line="$(awk '/^__MOLDYNSTUDIO_PAYLOAD_BELOW__$/ {print NR + 1; exit 0;}' "$0")"
if [ -z "$payload_line" ]; then
  echo "ERROR: installer payload marker not found." >&2
  exit 1
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "ERROR: python3 not found. Install Python 3.10+ and re-run this installer." >&2
  exit 1
fi

tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

echo "Installing $APP_NAME to $INSTALL_DIR"
mkdir -p "$BIN_DIR" "$DESKTOP_DIR"
tail -n +"$payload_line" "$0" | base64 -d > "$tmp_dir/payload.tar.gz"
if [ -e "$INSTALL_DIR" ]; then
  if [ ! -f "$INSTALL_DIR/.moldynstudio-install" ]; then
    cat >&2 <<MSG
ERROR: $INSTALL_DIR already exists and was not created by this installer.
Set MOLDYNSTUDIO_INSTALL_DIR to another path or move the existing directory.
MSG
    exit 1
  fi
  rm -rf "$INSTALL_DIR"
fi
mkdir -p "$INSTALL_DIR"
tar -xzf "$tmp_dir/payload.tar.gz" -C "$INSTALL_DIR"
touch "$INSTALL_DIR/.moldynstudio-install"

if ! "$PYTHON_BIN" -m venv "$VENV_DIR" >/dev/null 2>&1; then
  cat >&2 <<'MSG'
ERROR: failed to create a Python virtual environment.
On Debian/Ubuntu, install the venv package and retry:
  sudo apt install python3-venv
MSG
  exit 1
fi

"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -r "$INSTALL_DIR/requirements.txt"

quoted_install_dir="$(printf '%q' "$INSTALL_DIR")"
cat > "$LAUNCHER" <<LAUNCHER
#!/usr/bin/env bash
set -euo pipefail
cd $quoted_install_dir
export PYTHONPATH=$quoted_install_dir
exec $quoted_install_dir/.venv/bin/python $quoted_install_dir/main.py "\$@"
LAUNCHER
chmod +x "$LAUNCHER"

cat > "$DESKTOP_FILE" <<DESKTOP
[Desktop Entry]
Type=Application
Name=MolDynStudio
Comment=Desktop GUI for molecular dynamics setup, execution, and analysis
Exec=$LAUNCHER
Icon=$INSTALL_DIR/assets/logo_512.png
Terminal=false
Categories=Science;Education;
DESKTOP

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
fi

cat <<MSG

Install complete.
Run from a terminal with:
  $LAUNCHER

If the PyQt window does not open on a minimal Linux install, add the platform
libraries required by PyQtWebEngine for your distribution and run again.
MSG
exit 0

__MOLDYNSTUDIO_PAYLOAD_BELOW__
"""


def should_include(path: Path, root: Path = REPO_ROOT) -> bool:
    rel = path.relative_to(root)
    if any(part in EXCLUDED_PARTS for part in rel.parts):
        return False
    if path.suffix in EXCLUDED_SUFFIXES:
        return False
    return path.is_file()


def iter_payload_files(root: Path = REPO_ROOT) -> list[Path]:
    files: list[Path] = []
    for name in INCLUDE_ROOT_FILES:
        candidate = root / name
        if should_include(candidate, root):
            files.append(candidate)
    for dirname in INCLUDE_DIRS:
        base = root / dirname
        if not base.is_dir():
            continue
        files.extend(path for path in base.rglob("*") if should_include(path, root))
    return sorted(set(files), key=lambda path: path.relative_to(root).as_posix())


def build_payload(files: list[Path], root: Path = REPO_ROOT) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path in files:
            arcname = path.relative_to(root).as_posix()
            info = tar.gettarinfo(str(path), arcname=arcname)
            if path.suffix == ".sh":
                info.mode |= stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
            with path.open("rb") as handle:
                tar.addfile(info, handle)
    return buffer.getvalue()


def create_installer(output: Path = DEFAULT_OUTPUT) -> Path:
    files = iter_payload_files()
    if not files:
        raise RuntimeError("no files selected for Linux installer payload")
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = base64.encodebytes(build_payload(files)).decode("ascii")
    output.write_text(INSTALLER_STUB + payload, encoding="ascii", newline="\n")
    output.chmod(output.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the MolDynStudio Linux installer.")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Installer path (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()
    output = create_installer(args.output)
    size_mb = output.stat().st_size / (1024 * 1024)
    print(f"Linux installer created: {output} ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
