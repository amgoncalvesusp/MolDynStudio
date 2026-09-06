"""GROMACS system preparation pipeline.

Runs the canonical setup flow for a protein-only (or protein+ligand) system:

    1. ``gmx pdb2gmx``  -- generate topology from PDB
    2. ``gmx editconf`` -- define simulation box
    3. ``gmx solvate``  -- add water
    4. ``gmx grompp``   -- prepare for ion addition
    5. ``gmx genion``   -- neutralize and reach target ionic strength

All commands run inside the moldynstudio conda env via :mod:`core.wsl_bridge`,
so the same code path works on Windows (through WSL2) and on Linux/macOS.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

try:
    from PyQt5.QtCore import QThread, pyqtSignal
except Exception:  # pragma: no cover - allow headless import
    class _Signal:
        def connect(self, *_a, **_k) -> None: return None
        def emit(self, *_a, **_k) -> None: return None

    def pyqtSignal(*_a, **_k):  # type: ignore[no-redef]
        return _Signal()

    class QThread:  # type: ignore[no-redef]
        def __init__(self, *a, **k): pass
        def start(self) -> None: self.run()

from core import wsl_bridge
from core.forcefield_manager import (
    CHARMM36_BASENAME,
    stage_charmm36_force_field,
)


FORCE_FIELD_MAP: Mapping[str, str] = {
    "AMBER99SB-ILDN": "amber99sb-ildn",
    "AMBER14SB": "amber14sb",
    "CHARMM36m": CHARMM36_BASENAME,
    # Legacy project files used this label. Upgrade them to the current
    # CHARMM36m port because the February 2026 package rejects USE_OLD_C36.
    "CHARMM36": CHARMM36_BASENAME,
    "OPLS-AA": "oplsaa",
    "GROMOS96 54A7": "gromos54a7",
}

WATER_MODEL_MAP: Mapping[str, str] = {
    "TIP3P": "tip3p",
    "SPC/E": "spce",
    "SPC": "spc",
    "TIP4P-Ew": "tip4pew",
    "TIP4P": "tip4p",
}

def build_ions_mdp(force_field: str) -> str:
    if force_field.upper().startswith("CHARMM"):
        nonbonded = """cutoff-scheme = Verlet
nstlist       = 20
rlist         = 1.2
vdwtype       = cutoff
vdw-modifier  = force-switch
rvdw-switch   = 1.0
rvdw          = 1.2
coulombtype   = PME
rcoulomb      = 1.2
DispCorr      = no"""
    else:
        nonbonded = """cutoff-scheme = Verlet
nstlist       = 10
rcoulomb      = 1.0
rvdw          = 1.0
coulombtype   = PME"""
    return f"""; MolDynStudio - minimization preset for ion addition
integrator      = steep
emtol           = 1000.0
emstep          = 0.01
nsteps           = 50000
{nonbonded}
pbc             = xyz
"""


@dataclass(frozen=True)
class SystemPrepParams:
    """Immutable parameter bundle for the preparation pipeline."""

    pdb_path: str
    work_dir: str
    force_field: str = "AMBER99SB-ILDN"
    water_model: str = "TIP3P"
    box_type: str = "Dodecahedron"
    box_padding_nm: float = 1.2
    ion_concentration_m: float = 0.15
    ion_pos: str = "NA"
    ion_neg: str = "CL"
    gromacs_binary: str = "gmx"
    conda_environment: str = "moldynstudio"


def build_pdb2gmx_step(
    params: SystemPrepParams,
) -> tuple[str, str, list[str], str | None]:
    """Build the protein topology step without embedding an executable."""

    ff = FORCE_FIELD_MAP.get(params.force_field, params.force_field.lower())
    wm = WATER_MODEL_MAP.get(params.water_model, params.water_model.lower())
    return (
        "Running pdb2gmx (topology generation)...",
        "pdb2gmx",
        [
            "-f", wsl_bridge.win_to_wsl(params.pdb_path),
            "-o", "protein.gro",
            "-p", "topol.top",
            "-water", wm,
            "-ff", ff,
            "-ignh",
        ],
        None,
    )


def build_box_solvent_ion_steps(
    params: SystemPrepParams,
    coordinate_input: str = "protein.gro",
) -> list[tuple[str, str, list[str], str | None]]:
    """Build the setup tail shared by protein and complex workflows."""

    is_charmm = params.force_field.upper().startswith("CHARMM")
    positive_ion = "SOD" if is_charmm and params.ion_pos == "NA" else params.ion_pos
    negative_ion = "CLA" if is_charmm and params.ion_neg == "CL" else params.ion_neg
    return [
        (
            "Defining simulation box...", "editconf",
            ["-f", coordinate_input, "-o", "boxed.gro", "-c", "-d", f"{params.box_padding_nm:.3f}", "-bt", params.box_type.lower()],
            None,
        ),
        (
            "Adding solvent...", "solvate",
            ["-cp", "boxed.gro", "-cs", "spc216.gro", "-o", "solvated.gro", "-p", "topol.top"],
            None,
        ),
        (
            "Preparing ion addition (grompp)...", "grompp",
            ["-f", "ions.mdp", "-c", "solvated.gro", "-p", "topol.top", "-o", "ions.tpr"],
            None,
        ),
        (
            "Adding neutralizing ions...", "genion",
            ["-s", "ions.tpr", "-o", "system.gro", "-p", "topol.top", "-pname", positive_ion, "-nname", negative_ion, "-neutral", "-conc", f"{params.ion_concentration_m:.4f}"],
            "SOL\n",
        ),
    ]


class SystemPrepWorker(QThread):
    """Run the prep pipeline in a thread; emit log/progress/done signals."""

    log = pyqtSignal(str)
    done = pyqtSignal(bool, str)
    progress = pyqtSignal(int)

    def __init__(self, params: SystemPrepParams, parent=None):
        super().__init__(parent)
        self.p = params

    def _steps(self) -> list[tuple[str, list[str]]]:
        steps = [build_pdb2gmx_step(self.p), *build_box_solvent_ion_steps(self.p)]
        return [(description, [subcommand, *args]) for description, subcommand, args, _stdin in steps]

    def _ensure_force_field(self) -> None:
        if not self.p.force_field.upper().startswith("CHARMM36"):
            return
        self.log.emit(
            "Staging locally installed CHARMM36m February 2026 force field..."
        )
        stage_charmm36_force_field(Path(self.p.work_dir))

    def _ensure_ions_mdp(self) -> None:
        target = Path(self.p.work_dir) / "ions.mdp"
        target.write_text(
            build_ions_mdp(self.p.force_field),
            encoding="utf-8",
        )

    def run(self) -> None:  # QThread entry point
        # Compatibility wrapper. New UI code uses PreparationWorker directly.
        from core.preparation_orchestrator import (
            PreparationError,
            PreparationOrchestrator,
            PreparationRequest,
        )

        request = PreparationRequest(
            system=self.p,
            gromacs_binary=self.p.gromacs_binary,
            conda_environment=self.p.conda_environment,
        )
        try:
            service = PreparationOrchestrator(
                request, on_log=self.log.emit, on_progress=self.progress.emit
            )
            service.run()
        except (PreparationError, OSError, ValueError) as exc:
            self.done.emit(False, str(exc))
            return
        self.done.emit(True, "System preparation complete.")
