@echo off
REM Build MolDynStudio.exe in one click.
REM
REM Produces dist\MolDynStudio.exe -- a single self-contained Windows
REM executable. End users only need this .exe; on first run it launches
REM the SetupWizard, which installs WSL2 (if missing) and creates the
REM moldynstudio conda env inside WSL (with GROMACS + analysis stack).

setlocal
pushd "%~dp0"

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: python not found on PATH.
    echo Install Python 3.10+ from https://www.python.org/downloads/
    pause
    popd
    exit /b 1
)

echo === Installing PyInstaller and runtime deps ===
python -m pip install --upgrade pip
python -m pip install pyinstaller
python -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo ERROR: pip install failed.
    pause
    popd
    exit /b 1
)

echo === Building MolDynStudio.exe ===
python build\create_installer.py
if %errorlevel% neq 0 (
    echo ERROR: PyInstaller build failed.
    pause
    popd
    exit /b 1
)

echo.
echo === Build complete ===
echo Your executable is at: %CD%\dist\MolDynStudio.exe
echo Double-click it to launch MolDynStudio.
echo.
pause

popd
endlocal
