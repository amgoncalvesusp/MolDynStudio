# MolDynStudio Windows launcher (PowerShell).
#
# Ensures Python GUI dependencies on the Windows side and launches main.py.
# GROMACS runs inside WSL2 via core/wsl_bridge.py.

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -Path $here
$env:PYTHONPATH = $here

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "python not found on PATH. Install Python 3.11+ from https://www.python.org/downloads/"
    Read-Host "Press Enter to exit"
    exit 1
}

$checkImports = @"
import PyQt5, PyQt5.QtWebEngineWidgets, matplotlib, numpy, pandas, scipy, seaborn, openpyxl
"@
python -c $checkImports
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing GUI dependencies from requirements.txt..."
    python -m pip install -r (Join-Path $here "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Dependency installation failed."
        Read-Host "Press Enter to exit"
        exit 1
    }
}

python (Join-Path $here "main.py")
exit $LASTEXITCODE
