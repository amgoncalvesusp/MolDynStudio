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

import shlex
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


FORCE_FIELD_MAP: Mapping[str, str] = {
    "AMBER99SB-ILDN": "amber99sb-ildn",
    "AMBER14SB": "amber14sb",
    "CHARMM36m": "charmm36m",
    "CHARMM36": "charmm36",
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

IONS_MDP = """; MolDynStudio - minimization preset for ion addition
integrator      = steep
emtol           = 1000.0
emstep          = 0.01
nsteps           = 50000
cutoff-scheme   = Verlet
nstlist         = 10
rcoulomb        = 1.0
rvdw            = 1.0
coulombtype     = PME
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


class SystemPrepWorker(QThread):
    """Run the prep pipeline in a thread; emit log/progress/done signals."""

    log = pyqtSignal(str)
    done = pyqtSignal(bool, str)
    progress = pyqtSignal(int)

    def __init__(self, params: SystemPrepParams, parent=None):
        super().__init__(parent)
        self.p = params

    def _steps(self) -> list[tuple[str, list[str]]]:
        p = self.p
        ff = FORCE_FIELD_MAP.get(p.force_field, p.force_field.lower())
        wm = WATER_MODEL_MAP.get(p.water_model, p.water_model.lower())
        wsl_pdb = wsl_bridge.win_to_wsl(p.pdb_path)

        return [
            (
                "Running pdb2gmx (topology generation)...",
                [
                    "pdb2gmx",
                    "-f", wsl_pdb,
                    "-o", "processed.gro",
                    "-p", "topol.top",
                    "-water", wm,
                    "-ff", ff,
                    "-ignh",
                ],
            ),
            (
                "Defining simulation box...",
                [
                    "editconf",
                    "-f", "processed.gro",
                    "-o", "boxed.gro",
                    "-c",
                    "-d", f"{p.box_padding_nm:.3f}",
                    "-bt", p.box_type.lower(),
                ],
            ),
            (
                "Adding solvent...",
                [
                    "solvate",
                    "-cp", "boxed.gro",
                    "-cs", "spc216.gro",
                    "-o", "solvated.gro",
                    "-p", "topol.top",
                ],
            ),
            (
                "Preparing ion addition (grompp)...",
                [
                    "grompp",
                    "-f", "ions.mdp",
                    "-c", "solvated.gro",
                    "-p", "topol.top",
                    "-o", "ions.tpr",
                    "-maxwarn", "5",
                ],
            ),
            (
                "Adding neutralizing ions...",
                [
                    "genion",
                    "-s", "ions.tpr",
                    "-o", "system.gro",
                    "-p", "topol.top",
                    "-pname", p.ion_pos,
                    "-nname", p.ion_neg,
                    "-neutral",
                    "-conc", f"{p.ion_concentration_m:.4f}",
                ],
            ),
        ]

    def _ensure_ions_mdp(self) -> None:
        target = Path(self.p.work_dir) / "ions.mdp"
        if not target.exists():
            target.write_text(IONS_MDP, encoding="utf-8")

    def run(self) -> None:  # QThread entry point
        try:
            Path(self.p.work_dir).mkdir(parents=True, exist_ok=True)
            self._ensure_ions_mdp()
        except OSError as exc:
            self.done.emit(False, f"Could not prepare work dir: {exc}")
            return

        steps = self._steps()
        for index, (description, args) in enumerate(steps, start=1):
            self.log.emit(description)
            try:
                if args[0] == "genion":
                    # genion needs interactive stdin: which solvent group to
                    # replace with ions. Pipe "SOL" via printf so there is
                    # only one bash subshell, not two.
                    quoted = " ".join(shlex.quote(str(a)) for a in args)
                    proc = wsl_bridge.popen(
                        ["bash", "-c", f"printf 'SOL\\n' | gmx {quoted}"],
                        cwd=self.p.work_dir,
                    )
                else:
                    proc = wsl_bridge.gmx_popen(args, cwd=self.p.work_dir)
                if proc.stdout is None:
                    self.done.emit(False, f"No stdout for step '{description}'.")
                    return
                try:
                    for line in proc.stdout:
                        self.log.emit(line.rstrip())
                finally:
                    rc = proc.wait()
            except (OSError, FileNotFoundError) as exc:
                self.done.emit(False, f"Failed to launch step '{description}': {exc}")
                return
            if rc != 0:
                self.done.emit(False, f"Step failed (exit {rc}): {description}")
                return
            self.progress.emit(int(index / len(steps) * 100))

        self.done.emit(True, "System preparation complete.")
