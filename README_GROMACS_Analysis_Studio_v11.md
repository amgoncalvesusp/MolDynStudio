# MolDynStudio v1.0.1

Inventor: Adriano Marques Gonçalves (UNIARA)

MolDynStudio is a PyQt5 desktop prototype for molecular dynamics setup, execution monitoring, and post-MD analysis. It extends the previous GROMACS Analysis Studio prototype by adding:

- MD Setup page for system inputs, force-field settings, ligand parameterization choices, and `.mdp` generation
- MD Run page with pipeline controls, log output, and live monitoring previews
- Analysis page with RMSD/RMSF/SASA/PCA/MM-PBSA/MM-GBSA entry points and a CPPTRAJ script builder
- `.mds` project save/load with 5-minute autosave
- WSL2 installer script for Windows
- Conda environment specification for GROMACS, AmberTools, MDAnalysis, MDTraj, ProDy, PyTraj, and Qt

## Run

```bash
python main.py
```

The legacy entry file still works:

```bash
python gromacs_analysis_studio_v11.py
```

## Windows WSL2 Setup

Run PowerShell as Administrator:

```powershell
.\install_wsl.ps1
```

After WSL2 is ready, create the conda environment:

```bash
conda env create -f environment.yml
conda activate moldynstudio
```

## Minimal UI-Only Setup

For interface testing without the full MD stack:

```bash
pip install -r requirements.txt
python main.py
```

## Ubuntu Linux Prerequisites

The Linux installer creates a Python virtual environment for the GUI. On clean Ubuntu installs, Qt may also need XCB/OpenGL platform libraries:

```bash
sudo apt install -y \
  python3-venv \
  libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 \
  libxcb-xkb1 libxkbcommon-x11-0 libx11-xcb1 libxcb-randr0 \
  libxcb-shape0 libxcb-sync1 libxcb-xfixes0 libxcb-xinerama0 \
  libgl1
```

The generated Linux launcher defaults to `QT_QPA_PLATFORM=xcb` for predictable XWayland compatibility. Override it before launching if you want to test native Wayland:

```bash
QT_QPA_PLATFORM=wayland moldynstudio
```

## Dependency Check

```bash
python setup_environment.py
```

Use `--auto-install` only when you want the checker to run conda/pip installation commands.

## Tests

```bash
python -m unittest discover -s tests
```

The tests focus on pure logic that does not require launching Qt: project files, MDP generation, validators, and environment command construction.

## Release Builds

Windows executable:

```powershell
python build\create_installer.py
Compress-Archive -Path .\dist\MolDynStudio.exe -DestinationPath .\dist\MolDynStudio-windows-x86_64.zip -Force
```

Linux installer:

```bash
python build/create_linux_installer.py
chmod +x dist/MolDynStudio-linux-x86_64.run
./dist/MolDynStudio-linux-x86_64.run
```

The Linux installer creates a GUI virtual environment, installs a `moldynstudio` launcher in `~/.local/bin`, and registers a desktop entry. The scientific Conda environment is still created from `environment.yml` by the app launcher when needed.
