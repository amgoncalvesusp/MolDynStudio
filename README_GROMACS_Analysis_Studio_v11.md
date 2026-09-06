# MolDynStudio v1.1.1

Inventor: Adriano Marques Gonçalves (UNIARA)

MolDynStudio is a PyQt5 desktop application for system preparation, real
GROMACS molecular-dynamics execution, execution monitoring, and post-MD
analysis. Version 1.1.0 replaces the former MD Run preview/mock flow with a
stage-aware pipeline that launches the selected GROMACS executable. Analysis
modules that do not launch external tools remain clearly labeled as previews.

It provides:

- MD Setup page for system inputs, force-field settings, ligand parameterization choices, and `.mdp` generation
- MD Run page with pipeline controls, GROMACS log output, checkpoint restart, and live monitoring from parsed output
- Analysis page with RMSD/RMSF/SASA/PCA/MM-PBSA/MM-GBSA entry points and a CPPTRAJ script builder
- `.mds` project save/load with 5-minute autosave
- WSL2 installer script for Windows
- Conda environment specification for GROMACS, AmberTools, MDAnalysis, MDTraj, ProDy, PyTraj, and Qt

## Supported MD workflows

The real MD pipeline supports:

- **Protein-only** systems.
- **Protein + separate non-covalent ligand** systems using an AMBER-family
  protein force field and ACPYPE/GAFF2.

Covalent protein–ligand structures are unsupported. Arbitrary automatic CGenFF
ligand generation is also unsupported. CHARMM36m can be used for systems whose
residues are already available in the locally imported force-field package;
it does not provide automatic CGenFF ligand generation.

## Restart and output continuity

After an interruption, MD Run enables **Resume Production checkpoint** only
when the production checkpoint and required outputs validate. The resume path
uses a valid `md.cpt` checkpoint and GROMACS `-cpi`. Keep `md.tpr`, `md.log`,
`md.edr`, and the other production output files unchanged so GROMACS can
append to the existing run; replacing, truncating, or renaming those files
breaks append-mode continuity.

## GPU selection

The **GPU acceleration** setting follows the capabilities reported by the
selected GROMACS build. **Auto** uses that build's supported backend and can
fall back to its CPU path. A GPU driver by itself does not guarantee that the
selected GROMACS executable was built with GPU support. Use the setup
diagnostics to confirm the executable before relying on GPU execution.

## Prepared project artifacts

The project folder contains the manifest, system/topology inputs, MDP files,
and the outputs produced by each stage:

```text
project/
  moldynstudio_run.json
  system.gro
  topol.top
  em.mdp
  nvt.mdp
  npt.mdp
  md.mdp
  em.*
  nvt.*
  npt.*
  md.*
```

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
python -m pip install -r requirements-build.txt
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
