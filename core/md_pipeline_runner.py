"""Stage-aware execution of the real GROMACS molecular-dynamics pipeline.

The service in this module owns orchestration and manifest decisions.  It is
deliberately independent from Qt widgets; :class:`MDPipelineRunner` is only a
thin thread-and-signals adapter for the desktop UI.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import threading
from typing import Callable, Protocol, Sequence

try:
    from PyQt5.QtCore import QThread, pyqtSignal
except Exception:  # pragma: no cover - allow service use without Qt
    class _Signal:
        def __init__(self) -> None:
            self._callbacks: list[Callable[..., None]] = []

        def connect(self, callback: Callable[..., None], *_args, **_kwargs) -> None:
            self._callbacks.append(callback)

        def emit(self, *args, **kwargs) -> None:
            for callback in tuple(self._callbacks):
                callback(*args, **kwargs)

    class _SignalDescriptor:
        def __set_name__(self, _owner, name: str) -> None:
            self._name = f"__signal_{name}"

        def __get__(self, instance, _owner):
            if instance is None:
                return self
            signal = instance.__dict__.get(self._name)
            if signal is None:
                signal = _Signal()
                instance.__dict__[self._name] = signal
            return signal

    def pyqtSignal(*_args, **_kwargs):  # type: ignore[no-redef]
        return _SignalDescriptor()

    class QThread:  # type: ignore[no-redef]
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def start(self) -> None:
            self.run()


from core.artifact_validation import (
    ValidationResult,
    validate_checkpoint,
    validate_gro,
    validate_tpr,
)
from core.gromacs_capabilities import (
    GromacsCapabilities,
    build_mdrun_resource_args,
)
from core.gromacs_runner import CommandSpec, GROMACSRunner, GromacsCommandBuilder
from core.md_pipeline import PipelineCommand, PipelineStage
from core.run_manifest import (
    CommandRecord,
    RunManifest,
    StageName,
    StageRecord,
    StageStatus,
    load_manifest,
    save_manifest,
    utc_now_iso,
)


StageCallback = Callable[[StageName, StageStatus, str], None]
LogCallback = Callable[[str], None]
FinishedCallback = Callable[[bool, str], None]
Validator = Callable[[str | Path], ValidationResult]


class MDPipelineError(RuntimeError):
    """Base error for an MD pipeline action that cannot be performed."""


class PipelineResumeError(MDPipelineError):
    """Raised when a production checkpoint is unsafe to resume."""


@dataclass(frozen=True)
class CommandOutcome:
    """Result returned by an injectable command runner."""

    exit_code: int
    cancelled: bool = False

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.cancelled


@dataclass(frozen=True)
class ArtifactValidators:
    """Validation functions used at scientific stage boundaries."""

    validate_gro: Validator = validate_gro
    validate_tpr: Validator = validate_tpr
    validate_checkpoint: Validator = validate_checkpoint
    validate_file: Validator = lambda path: ValidationResult(
        Path(path).is_file(),
        f"File exists: {path}" if Path(path).is_file() else f"File is missing: {path}",
    )


class CommandRunnerService(Protocol):
    """Minimal execution boundary consumed by :class:`MDPipelineService`."""

    def execute(
        self,
        command: CommandSpec,
        on_output: LogCallback | None = None,
    ) -> CommandOutcome: ...

    def cancel_active(self) -> None: ...


class GromacsCommandRunnerService:
    """Run one command at a time with the shared cancellable GROMACS runner."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: GROMACSRunner | None = None

    def execute(
        self,
        command: CommandSpec,
        on_output: LogCallback | None = None,
    ) -> CommandOutcome:
        statuses: list[tuple[bool, int]] = []
        runner = GROMACSRunner((command,))
        if on_output is not None:
            runner.log_line.connect(on_output)
        runner.finished_with_status.connect(
            lambda success, code: statuses.append((success, code))
        )
        with self._lock:
            self._active = runner
        try:
            runner.run()
        finally:
            with self._lock:
                if self._active is runner:
                    self._active = None
        if not statuses:
            return CommandOutcome(1)
        success, exit_code = statuses[-1]
        return CommandOutcome(exit_code, cancelled=not success and exit_code == -1)

    def cancel_active(self) -> None:
        with self._lock:
            active = self._active
        if active is not None:
            active.cancel()


_DYNAMICS_STAGES = (
    StageName.MINIMIZATION,
    StageName.NVT,
    StageName.NPT,
    StageName.PRODUCTION,
)

_STAGE_STEMS = {
    StageName.MINIMIZATION: "em",
    StageName.NVT: "nvt",
    StageName.NPT: "npt",
    StageName.PRODUCTION: "md",
}

_REQUIRED_MDRUN_OUTPUTS = {
    StageName.MINIMIZATION: (
        ("em.gro", "gro"),
        ("em.edr", "file"),
        ("em.log", "file"),
    ),
    StageName.NVT: (
        ("nvt.gro", "gro"),
        ("nvt.edr", "file"),
        ("nvt.log", "file"),
        ("nvt.cpt", "checkpoint"),
    ),
    StageName.NPT: (
        ("npt.gro", "gro"),
        ("npt.edr", "file"),
        ("npt.log", "file"),
        ("npt.cpt", "checkpoint"),
    ),
    StageName.PRODUCTION: (
        ("md.gro", "gro"),
        ("md.edr", "file"),
        ("md.log", "file"),
        ("md.cpt", "checkpoint"),
        ("md.xtc", "file"),
    ),
}


class MDPipelineService:
    """Execute stage specifications and persist every meaningful transition."""

    def __init__(
        self,
        manifest_file: str | Path,
        stages: Sequence[PipelineStage],
        capabilities: GromacsCapabilities,
        *,
        command_runner: CommandRunnerService | None = None,
        validators: ArtifactValidators | None = None,
        on_stage_status: StageCallback | None = None,
        on_log: LogCallback | None = None,
        on_finished: FinishedCallback | None = None,
    ) -> None:
        self.manifest_file = Path(manifest_file)
        self.stages = tuple(stages)
        self.capabilities = capabilities
        self.command_runner = command_runner or GromacsCommandRunnerService()
        self.validators = validators or ArtifactValidators()
        self.on_stage_status = on_stage_status or (lambda _name, _status, _message: None)
        self.on_log = on_log or (lambda _line: None)
        self.on_finished = on_finished or (lambda _success, _message: None)
        self._stop_requested = threading.Event()
        self._active_stage: StageName | None = None
        self._manifest_lock = threading.Lock()
        self._manifest: RunManifest | None = None

    def run(self) -> bool:
        """Run the supplied stage specs in order until completion or failure."""

        self._stop_requested.clear()
        self._manifest = load_manifest(self.manifest_file)
        for stage in self.stages:
            if stage.name == StageName.PREPARATION and not stage.commands:
                continue
            if self._stop_requested.is_set():
                return self._finish(False, "MD pipeline stopped.")
            if not self._run_stage(stage):
                message = (
                    "MD pipeline stopped."
                    if self._stop_requested.is_set()
                    else f"MD pipeline failed during {stage.name.value}."
                )
                return self._finish(False, message)
        return self._finish(True, "MD pipeline completed.")

    def stop(self) -> None:
        """Cancel the currently active external process, if any."""

        self._stop_requested.set()
        self.command_runner.cancel_active()

    def resume_production(self) -> bool:
        """Validate and resume the interrupted production checkpoint."""

        self._stop_requested.clear()
        self._manifest = load_manifest(self.manifest_file)
        root = self._project_dir
        resume_inputs = (
            ("md.tpr", root / "md.tpr", self.validators.validate_tpr),
            ("md.cpt", root / "md.cpt", self.validators.validate_checkpoint),
            ("md.log", root / "md.log", self.validators.validate_file),
            ("md.edr", root / "md.edr", self.validators.validate_file),
        )
        for label, path, validator in resume_inputs:
            result = self._validate(validator, path)
            if not result.ok:
                raise PipelineResumeError(
                    f"Cannot resume production: {label} validation failed: {result.message}"
                )

        production = next(
            (stage for stage in self.stages if stage.name == StageName.PRODUCTION),
            PipelineStage(StageName.PRODUCTION, ()),
        )
        resume_command = PipelineCommand(
            ("mdrun", "-deffnm", "md", "-cpi", "md.cpt", "-v"),
            production.commands[0].cwd if production.commands else str(root),
        )
        resumed_stage = PipelineStage(StageName.PRODUCTION, (resume_command,))
        if not self._run_stage(resumed_stage):
            message = (
                "Production resume stopped."
                if self._stop_requested.is_set()
                else "Production resume failed."
            )
            return self._finish(False, message)
        return self._finish(True, "MD pipeline completed.")

    @property
    def _project_dir(self) -> Path:
        assert self._manifest is not None
        return Path(self._manifest.project_dir)

    def _run_stage(self, stage: PipelineStage) -> bool:
        self._active_stage = stage.name
        self._transition(
            stage.name,
            StageStatus.RUNNING,
            f"{stage.name.value.title()} is running.",
            reset_commands=True,
        )
        for command in stage.commands:
            if self._stop_requested.is_set():
                self._stop_stage(stage.name)
                return False
            try:
                spec = self._command_spec(stage.name, command)
            except Exception as exc:
                self._fail_stage(
                    stage.name,
                    f"Could not configure {command.args[0]}: {exc}",
                )
                return False
            record_index = self._start_command(stage.name, spec)
            try:
                outcome = self.command_runner.execute(spec, self.on_log)
            except Exception as exc:
                outcome = CommandOutcome(1)
                self._finish_command(stage.name, record_index, outcome)
                self._fail_stage(
                    stage.name,
                    f"{spec.command_label} execution failed: {exc}",
                )
                return False
            self._finish_command(stage.name, record_index, outcome)
            if self._stop_requested.is_set() or outcome.cancelled:
                self._stop_stage(stage.name)
                return False
            if not outcome.success:
                self._fail_stage(
                    stage.name,
                    f"{spec.command_label} failed with exit code {outcome.exit_code}.",
                )
                return False
            validation = self._validate_after_command(stage.name, spec.args[0])
            if validation is not None and not validation.ok:
                self._fail_stage(stage.name, validation.message)
                return False

        outputs = self._stage_outputs(stage.name)
        self._transition(
            stage.name,
            StageStatus.COMPLETED,
            f"{stage.name.value.title()} completed.",
            outputs=outputs,
        )
        self._mark_next_ready(stage.name)
        self._active_stage = None
        return True

    def _command_spec(
        self,
        stage: StageName,
        command: PipelineCommand,
    ) -> CommandSpec:
        assert self._manifest is not None
        subcommand, *arguments = command.args
        if subcommand == "mdrun":
            arguments.extend(
                build_mdrun_resource_args(
                    self._manifest.gpu_mode,
                    self._manifest.requested_cores,
                    self.capabilities,
                )
            )
        return GromacsCommandBuilder(
            conda_env=self._manifest.conda_environment,
            executable=self._manifest.gromacs_binary,
        ).build(
            subcommand,
            arguments,
            command.cwd,
            stage=stage.value,
            command_name=subcommand,
        )

    def _validate_after_command(
        self,
        stage: StageName,
        subcommand: str,
    ) -> ValidationResult | None:
        stem = _STAGE_STEMS[stage]
        if subcommand == "grompp":
            result = self._validate(self.validators.validate_tpr, self._project_dir / f"{stem}.tpr")
            if not result.ok:
                return ValidationResult(False, f"{stem}.tpr validation failed: {result.message}")
            return result
        if subcommand != "mdrun":
            return None

        validators = {
            "gro": self.validators.validate_gro,
            "checkpoint": self.validators.validate_checkpoint,
            "file": self.validators.validate_file,
        }
        for filename, validator_name in _REQUIRED_MDRUN_OUTPUTS[stage]:
            validator = validators[validator_name]
            result = self._validate(validator, self._project_dir / filename)
            if not result.ok:
                return ValidationResult(
                    False,
                    f"{filename} validation failed: {result.message}",
                )
        return ValidationResult(True, f"{stage.value} outputs are valid.")

    @staticmethod
    def _validate(validator: Validator, path: Path) -> ValidationResult:
        try:
            result = validator(path)
        except Exception as exc:
            return ValidationResult(False, f"validator raised {exc}")
        if not isinstance(result, ValidationResult):
            return ValidationResult(False, "validator returned an invalid result")
        return result

    def _stage_outputs(self, stage: StageName) -> dict[str, str]:
        stem = _STAGE_STEMS[stage]
        filenames = [
            f"{stem}.tpr",
            *(filename for filename, _kind in _REQUIRED_MDRUN_OUTPUTS[stage]),
        ]
        return {name: str((self._project_dir / name).resolve()) for name in filenames}

    def _start_command(self, stage: StageName, spec: CommandSpec) -> int:
        record = CommandRecord(
            stage=stage.value,
            argv=[spec.executable, *spec.args],
            cwd=str(spec.cwd or self._project_dir),
            started_at=utc_now_iso(),
        )
        current = self._stage_record(stage)
        index = len(current.commands)
        self._replace_stage(stage, replace(current, commands=[*current.commands, record]))
        return index

    def _finish_command(
        self,
        stage: StageName,
        index: int,
        outcome: CommandOutcome,
    ) -> None:
        current = self._stage_record(stage)
        commands = list(current.commands)
        commands[index] = replace(
            commands[index],
            finished_at=utc_now_iso(),
            exit_code=outcome.exit_code,
            success=outcome.success,
        )
        self._replace_stage(stage, replace(current, commands=commands))

    def _transition(
        self,
        stage: StageName,
        status: StageStatus,
        message: str,
        *,
        reset_commands: bool = False,
        outputs: dict[str, str] | None = None,
        notify: bool = True,
    ) -> None:
        current = self._stage_record(stage)
        now = utc_now_iso()
        started_at = now if status == StageStatus.RUNNING else current.started_at
        finished_at = (
            now
            if status
            in (
                StageStatus.COMPLETED,
                StageStatus.FAILED,
                StageStatus.STOPPED,
                StageStatus.INTERRUPTED,
            )
            else None
        )
        replacement = replace(
            current,
            status=status.value,
            started_at=started_at,
            finished_at=finished_at,
            message=message,
            commands=[] if reset_commands else list(current.commands),
            outputs={} if status == StageStatus.RUNNING else (outputs or dict(current.outputs)),
        )
        self._replace_stage(stage, replacement)
        if notify:
            self.on_stage_status(stage, status, message)

    def _fail_stage(self, stage: StageName, message: str) -> None:
        self._transition(stage, StageStatus.FAILED, message)
        self._invalidate_downstream(stage, f"{stage.value.title()} failed; stage is not ready.")
        self._active_stage = None

    def _stop_stage(self, stage: StageName) -> None:
        self._transition(stage, StageStatus.STOPPED, f"{stage.value.title()} was stopped.")
        self._invalidate_downstream(stage, f"{stage.value.title()} stopped; stage is not ready.")
        self._active_stage = None

    def _invalidate_downstream(self, stage: StageName, message: str) -> None:
        try:
            start = _DYNAMICS_STAGES.index(stage) + 1
        except ValueError:
            return
        for downstream in _DYNAMICS_STAGES[start:]:
            current = self._stage_record(downstream)
            self._replace_stage(
                downstream,
                replace(
                    current,
                    status=StageStatus.NOT_READY.value,
                    started_at=None,
                    finished_at=None,
                    message=message,
                    commands=[],
                    inputs={},
                    outputs={},
                ),
            )
            self.on_stage_status(downstream, StageStatus.NOT_READY, message)

    def _mark_next_ready(self, stage: StageName) -> None:
        try:
            next_stage = _DYNAMICS_STAGES[_DYNAMICS_STAGES.index(stage) + 1]
        except (ValueError, IndexError):
            return
        current = self._stage_record(next_stage)
        ready = replace(
            current,
            status=StageStatus.READY.value,
            started_at=None,
            finished_at=None,
            message=f"{stage.value.title()} completed; {next_stage.value} is ready.",
            commands=[],
            inputs=self._stage_inputs(next_stage),
            outputs={},
        )
        self._replace_stage(next_stage, ready)

    def _stage_inputs(self, stage: StageName) -> dict[str, str]:
        filenames = {
            StageName.NVT: ("em.gro", "topol.top", "nvt.mdp"),
            StageName.NPT: ("nvt.gro", "nvt.cpt", "topol.top", "npt.mdp"),
            StageName.PRODUCTION: ("npt.gro", "npt.cpt", "topol.top", "md.mdp"),
        }.get(stage, ())
        return {name: str((self._project_dir / name).resolve()) for name in filenames}

    def _stage_record(self, stage: StageName) -> StageRecord:
        assert self._manifest is not None
        return self._manifest.stages[stage.value]

    def _replace_stage(self, stage: StageName, record: StageRecord) -> None:
        assert self._manifest is not None
        with self._manifest_lock:
            self._manifest = replace(
                self._manifest,
                stages={**self._manifest.stages, stage.value: record},
                updated_at=utc_now_iso(),
            )
            save_manifest(self._manifest, self.manifest_file)

    def _finish(self, success: bool, message: str) -> bool:
        self.on_finished(success, message)
        return success


class MDPipelineRunner(QThread):
    """Qt thread that forwards service callbacks as UI-safe signals."""

    stage_changed = pyqtSignal(str, str, str)
    log_line = pyqtSignal(str)
    finished_with_status = pyqtSignal(bool, str)

    def __init__(
        self,
        manifest_file: str | Path | None = None,
        stages: Sequence[PipelineStage] = (),
        capabilities: GromacsCapabilities | None = None,
        *,
        service: MDPipelineService | None = None,
        command_runner: CommandRunnerService | None = None,
        validators: ArtifactValidators | None = None,
        resume: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        if service is None:
            if manifest_file is None or capabilities is None:
                raise ValueError(
                    "manifest_file and capabilities are required when service is omitted"
                )
            service = MDPipelineService(
                manifest_file,
                stages,
                capabilities,
                command_runner=command_runner,
                validators=validators,
            )
        self.service = service
        self.resume = resume
        self.service.on_stage_status = self._emit_stage
        self.service.on_log = self.log_line.emit
        self.service.on_finished = self.finished_with_status.emit

    def run(self) -> None:
        try:
            if self.resume:
                self.service.resume_production()
            else:
                self.service.run()
        except MDPipelineError as exc:
            self.finished_with_status.emit(False, str(exc))
        except Exception as exc:  # preserve a useful UI failure instead of losing the thread
            self.finished_with_status.emit(False, f"MD pipeline execution failed: {exc}")

    def stop(self) -> None:
        self.service.stop()

    def _emit_stage(
        self,
        stage: StageName,
        status: StageStatus,
        message: str,
    ) -> None:
        self.stage_changed.emit(stage.value, status.value, message)


__all__ = [
    "ArtifactValidators",
    "CommandOutcome",
    "CommandRunnerService",
    "GromacsCommandRunnerService",
    "MDPipelineError",
    "MDPipelineRunner",
    "MDPipelineService",
    "PipelineResumeError",
]
