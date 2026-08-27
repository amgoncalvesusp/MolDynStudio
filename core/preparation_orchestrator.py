"""Deterministic, Qt-independent orchestration of MD system preparation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import subprocess
from typing import Callable, Iterable, Protocol

try:
    from PyQt5.QtCore import QThread, pyqtSignal
except Exception:  # pragma: no cover - allow service use without Qt
    class _Signal:
        def connect(self, *_args, **_kwargs) -> None: return None
        def emit(self, *_args, **_kwargs) -> None: return None

    def pyqtSignal(*_args, **_kwargs):  # type: ignore[no-redef]
        return _Signal()

    class QThread:  # type: ignore[no-redef]
        def __init__(self, *_args, **_kwargs): pass
        def start(self) -> None: self.run()

from core import wsl_bridge
from core.artifact_validation import validate_gro, validate_topology
from core.complex_builder import combine_gro, normalize_ligand_topology, patch_topology_for_ligand
from core.forcefield_manager import stage_charmm36_force_field
from core.run_manifest import (
    CommandRecord,
    RunManifest,
    StageName,
    StageRecord,
    StageStatus,
    load_manifest,
    manifest_path,
    new_manifest,
    save_manifest,
    utc_now_iso,
)
from core.system_prep import (
    SystemPrepParams,
    build_box_solvent_ion_steps,
    build_ions_mdp,
    build_pdb2gmx_step,
)
from utils.mdp_generator import MDParameters, generate_all_mdp
from utils.structure_validation import ensure_noncovalent_complex
from utils.topology_builder import (
    CHARGE_METHODS,
    LigandTopologyArtifacts,
    read_molecule_name_from_itp,
    validate_acpype_input,
)


@dataclass(frozen=True)
class PreparationCommand:
    """One fully resolved external command in the preparation plan."""

    description: str
    argv: tuple[str, ...]
    cwd: str
    conda_environment: str
    stdin_text: str | None = None


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""


class CommandRunner(Protocol):
    def run(
        self,
        command: PreparationCommand,
        on_output: Callable[[str], None] | None = None,
    ) -> CommandResult: ...


class SubprocessCommandRunner:
    """Execute resolved commands through the shared Conda/WSL bridge."""

    def run(
        self,
        command: PreparationCommand,
        on_output: Callable[[str], None] | None = None,
    ) -> CommandResult:
        kwargs = {"stdin": subprocess.PIPE} if command.stdin_text is not None else {}
        process = wsl_bridge.popen(
            command.argv,
            cwd=command.cwd,
            env_name=command.conda_environment,
            **kwargs,
        )
        lines: list[str] = []
        if command.stdin_text is not None:
            stdout, _unused = process.communicate(command.stdin_text)
            lines = (stdout or "").splitlines()
            exit_code = process.returncode
        else:
            if process.stdout is None:
                return CommandResult(1, "", "Command output was not captured.")
            for raw_line in process.stdout:
                lines.append(raw_line.rstrip())
            exit_code = process.wait()
        if on_output:
            for line in lines:
                on_output(line)
        return CommandResult(int(exit_code or 0), "\n".join(lines), "")


class PreparationCommandBuilder:
    """Build immutable commands using the selected executable and environment."""

    def __init__(self, gromacs_binary: str, conda_environment: str):
        selected = str(gromacs_binary).strip()
        self.gromacs_binary = "gmx" if not selected or selected.lower() == "auto" else selected
        self.conda_environment = str(conda_environment).strip() or "moldynstudio"

    def gromacs(
        self,
        subcommand: str,
        args: Iterable[str],
        cwd: str | Path,
        description: str = "",
        stdin_text: str | None = None,
    ) -> PreparationCommand:
        return PreparationCommand(
            description or f"Running GROMACS {subcommand}...",
            (self.gromacs_binary, subcommand, *(str(arg) for arg in args)),
            str(cwd),
            self.conda_environment,
            stdin_text,
        )

    def external(
        self,
        executable: str,
        args: Iterable[str],
        cwd: str | Path,
        description: str,
    ) -> PreparationCommand:
        return PreparationCommand(
            description,
            (executable, *(str(arg) for arg in args)),
            str(cwd),
            self.conda_environment,
        )


@dataclass(frozen=True)
class PreparationRequest:
    system: SystemPrepParams
    ligand_path: str | None = None
    charge_method: str = "AM1-BCC"
    net_charge: int = 0
    md_parameters: MDParameters = MDParameters()
    gromacs_binary: str = "gmx"
    conda_environment: str = "moldynstudio"
    requested_cores: int = 4
    gpu_mode: str = "Auto"


@dataclass(frozen=True)
class PreparationResult:
    system_gro: Path
    topology: Path
    mdp_files: dict[str, Path]
    ligand_artifacts: LigandTopologyArtifacts | None
    manifest: Path


class PreparationError(RuntimeError):
    """Raised after a preparation failure has been persisted to the manifest."""


class PreparationOrchestrator:
    """Sequence protein-only or protein-ligand setup without Qt dependencies."""

    def __init__(
        self,
        request: PreparationRequest,
        *,
        runner: CommandRunner | None = None,
        command_builder: PreparationCommandBuilder | None = None,
        structure_validator: Callable[[str], object] | None = None,
        ligand_normalizer: Callable[[str | Path, str | Path], LigandTopologyArtifacts] = normalize_ligand_topology,
        gro_combiner: Callable[[str | Path, str | Path, str | Path], Path] = combine_gro,
        topology_patcher: Callable[..., Path] = patch_topology_for_ligand,
        on_log: Callable[[str], None] | None = None,
        on_progress: Callable[[int], None] | None = None,
    ):
        self.request = request
        self.runner = runner or SubprocessCommandRunner()
        self.commands = command_builder or PreparationCommandBuilder(
            request.gromacs_binary, request.conda_environment
        )
        self.structure_validator = structure_validator or ensure_noncovalent_complex
        self.ligand_normalizer = ligand_normalizer
        self.gro_combiner = gro_combiner
        self.topology_patcher = topology_patcher
        self.on_log = on_log or (lambda _line: None)
        self.on_progress = on_progress or (lambda _value: None)
        self._manifest: RunManifest | None = None
        self._manifest_file = manifest_path(request.system.work_dir)

    def run(self) -> PreparationResult:
        root = Path(self.request.system.work_dir).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        self._start_manifest(root)
        try:
            self._validate_inputs()
            self._prepare_force_field(root)
            (root / "ions.mdp").write_text(
                build_ions_mdp(self.request.system.force_field), encoding="utf-8"
            )
            ligand_artifacts, coordinate_input = self._prepare_coordinates(root)
            trailing_steps = build_box_solvent_ion_steps(
                self.request.system, coordinate_input
            )
            total_commands = 1 + (1 if self.request.ligand_path else 0) + len(trailing_steps)
            completed = 1 + (1 if self.request.ligand_path else 0)
            for description, subcommand, args, stdin_text in trailing_steps:
                self._execute(
                    self.commands.gromacs(
                        subcommand, args, root, description, stdin_text
                    )
                )
                completed += 1
                self.on_progress(int(completed / total_commands * 100))
            result = self._validate_and_finalize(root, ligand_artifacts)
            self.on_progress(100)
            return result
        except Exception as exc:
            self._fail(str(exc))
            if isinstance(exc, PreparationError):
                raise
            raise PreparationError(str(exc)) from exc

    def _validate_inputs(self) -> None:
        protein = self.request.system.pdb_path
        if protein.startswith(("\\\\", "//")):
            raise PreparationError(
                "UNC/network paths are not supported. Copy the input structure to a local drive first."
            )
        wsl_bridge.win_to_wsl(protein)
        self.structure_validator(protein)
        if self.request.ligand_path:
            validate_acpype_input(self.request.ligand_path)

    def _prepare_force_field(self, root: Path) -> None:
        if self.request.system.force_field.upper().startswith("CHARMM36"):
            self.on_log("Staging locally installed CHARMM36m force field...")
            stage_charmm36_force_field(root)

    def _prepare_coordinates(
        self, root: Path
    ) -> tuple[LigandTopologyArtifacts | None, str]:
        description, subcommand, args, _stdin = build_pdb2gmx_step(self.request.system)
        self._execute(self.commands.gromacs(subcommand, args, root, description))
        self.on_progress(16 if self.request.ligand_path else 20)
        if not self.request.ligand_path:
            return None, "protein.gro"

        ligand_arg = wsl_bridge.win_to_wsl(self.request.ligand_path)
        charge = CHARGE_METHODS.get(self.request.charge_method, "bcc")
        acpype = self.commands.external(
            "acpype",
            ["-i", ligand_arg, "-c", charge, "-n", str(int(self.request.net_charge)), "-b", "ligand", "-o", "gmx"],
            root,
            "Running ACPYPE ligand parameterization...",
        )
        self._execute(acpype)
        artifacts = self.ligand_normalizer(root, root)
        self.gro_combiner(root / "protein.gro", artifacts.ligand_gro, root / "complex.gro")
        self.topology_patcher(root / "topol.top", artifacts.ligand_itp)
        self.on_progress(33)
        return artifacts, "complex.gro"

    def _execute(self, command: PreparationCommand) -> None:
        self.on_log(command.description)
        started = utc_now_iso()
        record = CommandRecord(
            StageName.PREPARATION.value,
            list(command.argv),
            str(Path(command.cwd).resolve()),
            started,
        )
        self._replace_stage(
            StageName.PREPARATION,
            lambda stage: replace(stage, commands=[*stage.commands, record]),
        )
        self._save()
        try:
            result = self.runner.run(command, self.on_log)
        except Exception as exc:
            self._finish_command(record, 1, False)
            raise PreparationError(f"Failed to launch {command.argv[0]}: {exc}") from exc
        self._finish_command(record, result.exit_code, result.exit_code == 0)
        if result.exit_code != 0:
            detail = (result.stderr or result.stdout).strip()
            message = f"Command failed (exit {result.exit_code}): {command.description}"
            if detail:
                message += f"\n{detail}"
            raise PreparationError(message)

    def _finish_command(self, original: CommandRecord, exit_code: int, success: bool) -> None:
        finished = replace(
            original,
            finished_at=utc_now_iso(),
            exit_code=exit_code,
            success=success,
        )
        self._replace_stage(
            StageName.PREPARATION,
            lambda stage: replace(
                stage,
                commands=[finished if item is original else item for item in stage.commands],
            ),
        )
        self._save()

    def _validate_and_finalize(
        self, root: Path, ligand: LigandTopologyArtifacts | None
    ) -> PreparationResult:
        system_gro = root / "system.gro"
        topology = root / "topol.top"
        gro_result = validate_gro(system_gro)
        if not gro_result.ok:
            raise PreparationError(f"system.gro validation failed: {gro_result.message}")
        ligand_name: str | None = None
        if ligand is not None:
            ligand_gro_result = validate_gro(ligand.ligand_gro)
            if not ligand_gro_result.ok:
                raise PreparationError(
                    f"Normalized ligand GRO validation failed: {ligand_gro_result.message}"
                )
            ligand_name = read_molecule_name_from_itp(ligand.ligand_itp)
            if ligand.ligand_posre_itp is not None and not ligand.ligand_posre_itp.is_file():
                raise PreparationError(
                    f"Normalized ligand restraint file is missing: {ligand.ligand_posre_itp}"
                )
        topology_result = validate_topology(topology, ligand_name)
        if not topology_result.ok:
            raise PreparationError(f"topol.top validation failed: {topology_result.message}")

        generated = generate_all_mdp(self.request.md_parameters)
        mdp_paths: dict[str, Path] = {}
        for filename, content in generated.items():
            target = root / filename
            target.write_text(content, encoding="utf-8")
            if not target.is_file() or target.stat().st_size == 0:
                raise PreparationError(f"Generated MDP is missing or empty: {filename}")
            mdp_paths[filename] = target.resolve()

        outputs = {
            "system.gro": str(system_gro.resolve()),
            "topol.top": str(topology.resolve()),
            **{name: str(path) for name, path in mdp_paths.items()},
        }
        if ligand is not None:
            outputs["ligand/ligand.gro"] = str(ligand.ligand_gro.resolve())
            outputs["ligand/ligand.itp"] = str(ligand.ligand_itp.resolve())
            if ligand.ligand_posre_itp is not None:
                outputs["ligand/ligand_posre.itp"] = str(ligand.ligand_posre_itp.resolve())
        now = utc_now_iso()
        self._replace_stage(
            StageName.PREPARATION,
            lambda stage: replace(
                stage,
                status=StageStatus.COMPLETED.value,
                finished_at=now,
                message="System preparation complete.",
                outputs=outputs,
            ),
        )
        self._replace_stage(
            StageName.MINIMIZATION,
            lambda stage: replace(
                stage,
                status=StageStatus.READY.value,
                message="Preparation completed; minimization is ready.",
                inputs={
                    "system.gro": str(system_gro.resolve()),
                    "topol.top": str(topology.resolve()),
                    "em.mdp": str(mdp_paths["em.mdp"]),
                },
            ),
        )
        self._save()
        return PreparationResult(
            system_gro.resolve(),
            topology.resolve(),
            mdp_paths,
            ligand,
            self._manifest_file.resolve(),
        )

    def _start_manifest(self, root: Path) -> None:
        if self._manifest_file.is_file():
            manifest = load_manifest(self._manifest_file)
            manifest = replace(
                manifest,
                project_dir=str(root),
                system_kind="protein_ligand" if self.request.ligand_path else "protein",
                protein_source=self.request.system.pdb_path,
                ligand_source=self.request.ligand_path,
                force_field=self.request.system.force_field,
                water_model=self.request.system.water_model,
                gromacs_binary=self.commands.gromacs_binary,
                conda_environment=self.commands.conda_environment,
                requested_cores=max(1, int(self.request.requested_cores)),
                gpu_mode=self.request.gpu_mode,
            )
        else:
            manifest = new_manifest(
                root,
                "protein_ligand" if self.request.ligand_path else "protein",
                self.request.system.pdb_path,
                self.request.ligand_path,
                self.request.system.force_field,
                self.request.system.water_model,
                self.commands.gromacs_binary,
                self.commands.conda_environment,
                max(1, int(self.request.requested_cores)),
                self.request.gpu_mode,
            )
        now = utc_now_iso()
        inputs = {"protein": str(Path(self.request.system.pdb_path).expanduser().resolve())}
        if self.request.ligand_path:
            inputs["ligand"] = str(Path(self.request.ligand_path).expanduser().resolve())
        prep = replace(
            manifest.stages[StageName.PREPARATION.value],
            status=StageStatus.RUNNING.value,
            started_at=now,
            finished_at=None,
            message="System preparation is running.",
            commands=[],
            inputs=inputs,
            outputs={},
        )
        minimization = replace(
            manifest.stages[StageName.MINIMIZATION.value],
            status=StageStatus.NOT_READY.value,
            message="Waiting for preparation to complete.",
        )
        self._manifest = replace(
            manifest,
            stages={
                **manifest.stages,
                StageName.PREPARATION.value: prep,
                StageName.MINIMIZATION.value: minimization,
            },
            updated_at=now,
        )
        self._save()

    def _fail(self, message: str) -> None:
        if self._manifest is None:
            return
        now = utc_now_iso()
        self._replace_stage(
            StageName.PREPARATION,
            lambda stage: replace(
                stage,
                status=StageStatus.FAILED.value,
                finished_at=now,
                message=message,
            ),
        )
        self._replace_stage(
            StageName.MINIMIZATION,
            lambda stage: replace(
                stage,
                status=StageStatus.NOT_READY.value,
                message="Preparation failed.",
            ),
        )
        self._save()

    def _replace_stage(
        self,
        name: StageName,
        transform: Callable[[StageRecord], StageRecord],
    ) -> None:
        assert self._manifest is not None
        key = name.value
        self._manifest = replace(
            self._manifest,
            stages={**self._manifest.stages, key: transform(self._manifest.stages[key])},
            updated_at=utc_now_iso(),
        )

    def _save(self) -> None:
        assert self._manifest is not None
        save_manifest(self._manifest, self._manifest_file)


class PreparationWorker(QThread):
    """Thin Qt wrapper around :class:`PreparationOrchestrator`."""

    log = pyqtSignal(str)
    done = pyqtSignal(bool, str)
    progress = pyqtSignal(int)

    def __init__(self, request: PreparationRequest, parent=None, **service_options):
        super().__init__(parent)
        self.request = request
        self.service_options = dict(service_options)

    def run(self) -> None:
        service = PreparationOrchestrator(
            self.request,
            on_log=self.log.emit,
            on_progress=self.progress.emit,
            **self.service_options,
        )
        try:
            service.run()
        except PreparationError as exc:
            self.done.emit(False, str(exc))
            return
        self.done.emit(True, "System preparation complete.")
