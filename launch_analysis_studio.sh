#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
PYTHON_BIN="${PYTHON:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python"
fi
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "ERROR: Python 3.11+ not found. Install python3 and retry." >&2
  exit 1
fi
if ! "$PYTHON_BIN" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
  echo "ERROR: Python 3.11+ is required for trajectory analysis. Set PYTHON to a supported interpreter." >&2
  exit 1
fi
VENV_DIR="${MOLDYNSTUDIO_VENV_DIR:-$SCRIPT_DIR/.venv}"
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"
if [ ! -x "$VENV_DIR/bin/python" ]; then
  if ! "$PYTHON_BIN" -m venv "$VENV_DIR" >/dev/null 2>&1; then
    echo "ERROR: failed to create $VENV_DIR. On Debian/Ubuntu install python3-venv and retry." >&2
    exit 1
  fi
fi
if ! "$VENV_DIR/bin/python" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
  echo "ERROR: existing GUI environment uses Python below 3.11. Set MOLDYNSTUDIO_VENV_DIR to a new directory." >&2
  exit 1
fi
if ! "$VENV_DIR/bin/python" -c "import PyQt5, PyQt5.QtWebEngineWidgets, matplotlib, numpy, pandas, scipy, seaborn, openpyxl, MDAnalysis, mdtraj, Bio, pdbfixer, openmm" >/dev/null 2>&1; then
  "$VENV_DIR/bin/python" -m pip install --upgrade pip
  "$VENV_DIR/bin/python" -m pip install -r requirements.txt
fi
exec "$VENV_DIR/bin/python" main.py
