"""ACPYPE ligand parameterization wrapper.

Generates a GROMACS-compatible topology for a small molecule (MOL2/SDF/PDB)
by calling ``acpype`` inside the moldynstudio conda environment via
:mod:`core.wsl_bridge`.
"""

from __future__ import annotations

from dataclasses import dataclass
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


CHARGE_METHODS: Mapping[str, str] = {
    "AM1-BCC": "bcc",
    "Gasteiger": "gas",
    "RESP": "resp",
    "Formal charge only": "user",
}


@dataclass(frozen=True)
class LigandParams:
    ligand_path: str
    work_dir: str
    charge_method: str = "AM1-BCC"
    net_charge: int = 0
    base_name: str = "ligand"


class LigandParamWorker(QThread):
    """Run ACPYPE in a worker thread and stream its output."""

    log = pyqtSignal(str)
    done = pyqtSignal(bool, str)

    def __init__(self, params: LigandParams, parent=None):
        super().__init__(parent)
        self.p = params

    def run(self) -> None:
        p = self.p
        wsl_lig = wsl_bridge.win_to_wsl(p.ligand_path)
        cm = CHARGE_METHODS.get(p.charge_method, "bcc")

        cmd = [
            "acpype",
            "-i", wsl_lig,
            "-c", cm,
            "-n", str(int(p.net_charge)),
            "-b", p.base_name,
            "-o", "gmx",
        ]
        self.log.emit("Running ACPYPE: " + " ".join(cmd))
        try:
            proc = wsl_bridge.popen(cmd, cwd=p.work_dir)
        except (OSError, FileNotFoundError) as exc:
            self.done.emit(False, f"Failed to launch ACPYPE: {exc}")
            return

        if proc.stdout is None:
            self.done.emit(False, "ACPYPE stdout not captured.")
            return
        try:
            for line in proc.stdout:
                self.log.emit(line.rstrip())
        finally:
            rc = proc.wait()
        if rc == 0:
            self.done.emit(True, "Ligand topology generated.")
        else:
            self.done.emit(False, f"ACPYPE exited with status {rc}. See log above.")
