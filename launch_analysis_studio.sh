#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"
if ! python -c "import PyQt5, PyQt5.QtWebEngineWidgets, matplotlib, numpy, pandas, scipy, seaborn, openpyxl" >/dev/null 2>&1; then
  python -m pip install -r requirements.txt
fi
python main.py
