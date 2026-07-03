@echo off
REM MolDynStudio Windows launcher.
REM Ensures Python GUI dependencies on Windows and starts main.py.
REM GROMACS itself runs inside WSL via core/wsl_bridge.py.

setlocal
pushd "%~dp0"

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: python not found on PATH.
    echo Install Python 3.11+ from https://www.python.org/downloads/
    pause
    popd
    exit /b 1
)

python -c "import PyQt5, PyQt5.QtWebEngineWidgets, matplotlib, numpy, pandas, scipy, seaborn, openpyxl" >nul 2>&1
if %errorlevel% neq 0 (
    echo Installing GUI dependencies from requirements.txt...
    python -m pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo ERROR: dependency installation failed.
        pause
        popd
        exit /b 1
    )
)

python main.py
set EXITCODE=%errorlevel%

popd
endlocal & exit /b %EXITCODE%
