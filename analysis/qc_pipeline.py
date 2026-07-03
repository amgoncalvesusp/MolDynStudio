"""GROMACS command pipeline for QC dashboard inputs."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from core import wsl_bridge

try:
    from PyQt5.QtCore import QThread, pyqtSignal
except Exception:  # pragma: no cover - keeps command builders importable
    class _Signal:
        def connect(self, *_args, **_kwargs) -> None:
            return None

        def emit(self, *_args, **_kwargs) -> None:
            return None

    def pyqtSignal(*_args, **_kwargs):  # type: ignore[no-redef]
        return _Signal()

    class QThread:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            pass

        def start(self) -> None:
            self.run()


@dataclass(frozen=True)
class QCPipelineInputs:
    topology: str
    trajectory: str
    output_dir: str
    energy: str = ""
    fit_group: str = "Protein"
    analysis_group: str = "Protein"


@dataclass(frozen=True)
class QCTask:
    name: str
    output_file: str
    command: tuple[str, ...]


def default_qc_output_dir(trajectory: str) -> str:
    if trajectory:
        return str(Path(trajectory).expanduser().parent / "qc_xvg")
    return str(Path.home() / "MolDynStudio" / "qc_xvg")


def expected_output_files(inputs: QCPipelineInputs) -> tuple[str, ...]:
    return tuple(str(Path(inputs.output_dir) / task.output_file) for task in build_qc_tasks(inputs))


def build_qc_tasks(inputs: QCPipelineInputs) -> tuple[QCTask, ...]:
    if not inputs.topology.strip():
        raise ValueError("Topology file is required for QC generation.")
    if not inputs.trajectory.strip():
        raise ValueError("Trajectory file is required for QC generation.")
    if not inputs.output_dir.strip():
        raise ValueError("Output directory is required for QC generation.")

    topology = wsl_bridge.win_to_wsl(inputs.topology)
    trajectory = wsl_bridge.win_to_wsl(inputs.trajectory)
    tasks = [
        QCTask(
            "RMSD",
            "rmsd.xvg",
            _pipe_to_gmx(
                [inputs.fit_group, inputs.analysis_group],
                ["rms", "-s", topology, "-f", trajectory, "-o", "rmsd.xvg", "-tu", "ns"],
            ),
        ),
        QCTask(
            "Radius of gyration",
            "gyrate.xvg",
            _pipe_to_gmx(
                [inputs.analysis_group],
                ["gyrate", "-s", topology, "-f", trajectory, "-o", "gyrate.xvg"],
            ),
        ),
        QCTask(
            "SASA",
            "sasa.xvg",
            (
                "gmx",
                "sasa",
                "-s",
                topology,
                "-f",
                trajectory,
                "-o",
                "sasa.xvg",
                "-surface",
                inputs.analysis_group,
                "-output",
                inputs.analysis_group,
            ),
        ),
        QCTask(
            "RMSF",
            "rmsf.xvg",
            _pipe_to_gmx(
                [inputs.analysis_group],
                ["rmsf", "-s", topology, "-f", trajectory, "-o", "rmsf.xvg", "-res"],
            ),
        ),
    ]

    if inputs.energy.strip():
        energy = wsl_bridge.win_to_wsl(inputs.energy)
        tasks.extend(_energy_tasks(energy))
    return tuple(tasks)


class QCGromacsWorker(QThread):
    log = pyqtSignal(str)
    finished = pyqtSignal(bool, object, str)

    def __init__(self, inputs: QCPipelineInputs, parent=None):
        super().__init__(parent)
        self.inputs = inputs

    def run(self) -> None:
        try:
            out_dir = Path(self.inputs.output_dir).expanduser()
            out_dir.mkdir(parents=True, exist_ok=True)
            tasks = build_qc_tasks(self.inputs)
        except (OSError, ValueError) as exc:
            self.finished.emit(False, (), str(exc))
            return

        for task in tasks:
            self.log.emit(f"[qc] running {task.name}: {' '.join(task.command)}")
            try:
                proc = wsl_bridge.popen(task.command, cwd=str(out_dir))
            except (OSError, FileNotFoundError) as exc:
                self.finished.emit(False, (), f"Failed to start {task.name}: {exc}")
                return
            if proc.stdout is None:
                self.finished.emit(False, (), f"No stdout captured for {task.name}.")
                return
            try:
                for line in proc.stdout:
                    self.log.emit(line.rstrip())
            finally:
                return_code = proc.wait()
            if return_code != 0:
                self.finished.emit(False, (), f"{task.name} failed with exit code {return_code}.")
                return

        outputs = tuple(path for path in expected_output_files(self.inputs) if Path(path).exists())
        if not outputs:
            self.finished.emit(False, (), "QC commands finished, but no XVG outputs were found.")
            return
        self.finished.emit(True, outputs, f"Generated {len(outputs)} QC files.")


def _energy_tasks(energy: str) -> list[QCTask]:
    terms = [
        ("Temperature", "temperature.xvg", "Temperature"),
        ("Pressure", "pressure.xvg", "Pressure"),
        ("Density", "density.xvg", "Density"),
        ("Potential energy", "potential.xvg", "Potential"),
        ("Total energy", "total_energy.xvg", "Total-Energy"),
    ]
    return [
        QCTask(
            name,
            output,
            _pipe_to_gmx([term, "0"], ["energy", "-f", energy, "-o", output]),
        )
        for name, output, term in terms
    ]


def _pipe_to_gmx(responses: Iterable[str], args: Iterable[str]) -> tuple[str, ...]:
    payload = "".join(f"{response}\n" for response in responses)
    quoted_args = " ".join(shlex.quote(str(arg)) for arg in args)
    script = f"printf {shlex.quote(payload)} | gmx {quoted_args}"
    return ("bash", "-lc", script)
