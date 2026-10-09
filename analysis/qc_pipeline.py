"""GROMACS command pipeline for QC dashboard inputs."""

from __future__ import annotations

import shlex
import json
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path, PureWindowsPath
from typing import Iterable

from core import wsl_bridge
from analysis.qc import parse_numeric_series

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
    gromacs_binary: str = "gmx"
    conda_env: str = "moldynstudio"


@dataclass(frozen=True)
class QCTask:
    name: str
    output_file: str
    command: tuple[str, ...]
    optional: bool = False


def default_qc_output_dir(trajectory: str) -> str:
    if trajectory:
        return str(_host_path(trajectory).parent / "qc_xvg")
    return str(Path.home() / "MolDynStudio" / "qc_xvg")


def expected_output_files(inputs: QCPipelineInputs) -> tuple[str, ...]:
    output_dir = _host_path(inputs.output_dir)
    return tuple(str(output_dir / task.output_file) for task in build_qc_tasks(inputs))


def _host_path(path: str) -> Path | PureWindowsPath:
    """Preserve Windows path semantics even when inspected from Linux/WSL."""

    windows_path = PureWindowsPath(path)
    if windows_path.drive or path.startswith(("\\\\", "//")):
        return windows_path
    return Path(path).expanduser()


def build_qc_tasks(inputs: QCPipelineInputs) -> tuple[QCTask, ...]:
    if not inputs.topology.strip():
        raise ValueError("Topology file is required for QC generation.")
    if not inputs.trajectory.strip():
        raise ValueError("Trajectory file is required for QC generation.")
    if not inputs.output_dir.strip():
        raise ValueError("Output directory is required for QC generation.")
    if any("\n" in group or "\r" in group or not group.strip() for group in (inputs.fit_group, inputs.analysis_group)):
        raise ValueError("QC groups must be nonempty single-line group names.")

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
                ["gyrate", "-s", topology, "-f", trajectory, "-o", "gyrate.xvg", "-tu", "ns"],
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
                "-tu",
                "ns",
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
    binary = wsl_bridge.win_to_wsl(inputs.gromacs_binary or "gmx")
    return tuple(replace(task, command=_configured_command(task.command, binary)) for task in tasks)


def _configured_command(command: tuple[str, ...], binary: str) -> tuple[str, ...]:
    if command[0] == "gmx":
        return (binary, *command[1:])
    return (*command[:2], command[2].replace("| gmx ", f"| {shlex.quote(binary)} ", 1))


class QCGromacsWorker(QThread):
    log = pyqtSignal(str)
    completed = pyqtSignal(bool, object, str)

    def __init__(self, inputs: QCPipelineInputs, parent=None):
        super().__init__(parent)
        self.inputs = inputs

    def run(self) -> None:
        try:
            self._run_tasks()
        except Exception as exc:
            self.completed.emit(False, (), f"QC execution failed: {exc}")

    def _run_tasks(self) -> None:
        try:
            out_dir = Path(self.inputs.output_dir).expanduser()
            out_dir.mkdir(parents=True, exist_ok=True)
            for path in (self.inputs.topology, self.inputs.trajectory, self.inputs.energy):
                if path and not Path(path).is_file():
                    raise ValueError(f"QC input does not exist: {path}")
            if self.inputs.energy and Path(self.inputs.energy).suffix.lower() != ".edr":
                raise ValueError("QC energy generation requires .edr; load existing XVG/CSV directly in the dashboard.")
            out_dir = Path(tempfile.mkdtemp(prefix="qc_", dir=out_dir))
            inputs = replace(self.inputs, output_dir=str(out_dir),
                             topology=str(Path(self.inputs.topology).resolve()),
                             trajectory=str(Path(self.inputs.trajectory).resolve()),
                             energy=str(Path(self.inputs.energy).resolve()) if self.inputs.energy else "")
            tasks = build_qc_tasks(inputs)
        except (OSError, ValueError) as exc:
            self.completed.emit(False, (), str(exc))
            return

        completed_tasks = ()
        unavailable = ()
        for task in tasks:
            self.log.emit(f"[qc] running {task.name}: {' '.join(task.command)}")
            try:
                proc = wsl_bridge.popen(task.command, cwd=str(out_dir), env_name=inputs.conda_env)
            except (OSError, ValueError) as exc:
                self.completed.emit(False, (), f"Failed to start {task.name}: {exc}")
                return
            if proc.stdout is None:
                self.completed.emit(False, (), f"No stdout captured for {task.name}.")
                return
            recent_output = ()
            try:
                for line in proc.stdout:
                    self.log.emit(line.rstrip())
                    recent_output = (*recent_output[-99:], line.rstrip())
            finally:
                return_code = proc.wait()
            if return_code != 0:
                # GROMACS omits ensemble-dependent energy fields (for example
                # Density in constant-volume runs). Only this exact selection
                # error is optional; any other tool failure remains fatal.
                text = "\n".join(recent_output)
                term = {"Potential energy": "Potential", "Total energy": "Total-Energy"}.get(task.name, task.name)
                missing = f"String '{term}' does not match anything"
                if task.optional and missing in text and "No energy terms selected" in text:
                    message = f"{task.name} is unavailable in the supplied EDR; GROMACS reported no matching energy term."
                    unavailable = (*unavailable, {"name": task.name, "reason": message, "returncode": return_code})
                    self.log.emit(f"[qc] partial result: {message}")
                    continue
                self.completed.emit(False, (), f"{task.name} failed with exit code {return_code}.")
                return
            completed_tasks = (*completed_tasks, task)

        outputs = tuple(str(out_dir / task.output_file) for task in completed_tasks)
        try:
            for path in outputs:
                parse_numeric_series(path)
            (out_dir / "qc_commands.json").write_text(json.dumps({
                "commands": [list(task.command) for task in tasks],
                "conda_environment": inputs.conda_env,
                "outputs": list(outputs),
                "time_unit_on_import": "ns (from XVG axis metadata)",
                "status": "partial" if unavailable else "complete",
                "unavailable_energy_terms": unavailable,
            }, indent=2), encoding="utf-8")
        except (OSError, ValueError) as exc:
            self.completed.emit(False, (), f"QC output validation failed: {exc}")
            return
        detail = f" Partial energy coverage: {len(unavailable)} unavailable term(s); see qc_commands.json." if unavailable else ""
        self.completed.emit(True, outputs, f"Generated {len(outputs)} QC files.{detail}")


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
            optional=True,
        )
        for name, output, term in terms
    ]


def _pipe_to_gmx(responses: Iterable[str], args: Iterable[str]) -> tuple[str, ...]:
    payload = "".join(f"{response}\n" for response in responses)
    quoted_args = " ".join(shlex.quote(str(arg)) for arg in args)
    script = f"printf '%s' {shlex.quote(payload)} | gmx {quoted_args}"
    return ("bash", "-lc", script)
