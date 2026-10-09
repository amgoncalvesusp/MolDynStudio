"""Deterministic, Qt-independent orchestration of MD system preparation."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
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
from core.complex_builder import combine_gro, normalize_ligand_topology, patch_topology_for_ligand, validate_ligand_artifacts
from core.crystal_water import prepare_crystal_waters, append_crystal_waters, consolidate_water_molecules
from core.forcefield_manager import stage_charmm36_force_field
from core.gromacs_capabilities import resolve_gromacs_binary
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
from core.structure_audit import file_sha256
from utils.topology_builder import (
    acpype_charge_method,
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
        self.gromacs_binary = resolve_gromacs_binary(gromacs_binary)
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
    structure_repair_report: str = ""
    structure_repair_report_sha256: str = ""
    ligand_backend: str = "acpype_gaff2"
    allow_ligand_upload: bool = False
    ligand_protonation_confirmed: bool = False
    ligand_copies: tuple[str, ...] = ()
    source_residue_name: str = ""
    excluded_residue_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class PreparationResult:
    system_gro: Path
    topology: Path
    mdp_files: dict[str, Path]
    ligand_artifacts: LigandTopologyArtifacts | None
    manifest: Path


class PreparationError(RuntimeError):
    """Raised after a preparation failure has been persisted to the manifest."""


def _verify_repair_report(report_path: str, candidate_path: str) -> dict:
    from core.structure_repair import verify_repair_report

    return verify_repair_report(report_path, candidate_path)


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
        charmm_ligand_preparer: Callable[..., LigandTopologyArtifacts] | None = None,
        complex_input_preparer: Callable | None = None,
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
        self.charmm_ligand_preparer = charmm_ligand_preparer
        self.complex_input_preparer = complex_input_preparer
        self.on_log = on_log or (lambda _line: None)
        self.on_progress = on_progress or (lambda _value: None)
        self._manifest: RunManifest | None = None
        self._manifest_file = manifest_path(request.system.work_dir)
        self._ligand_source_hashes: dict[str, str] = {}

    def run(self) -> PreparationResult:
        root = Path(self.request.system.work_dir).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        self._start_manifest(root)
        try:
            self._validate_inputs()
            self._prepare_force_field(root)
            (root / "ions.mdp").write_text(
                build_ions_mdp(self.request.system.force_field), encoding="utf-8", newline="\n"
            )
            ligand_artifacts, coordinate_input = self._prepare_coordinates(root)
            trailing_steps = build_box_solvent_ion_steps(
                self.request.system, coordinate_input
            )
            total_commands = 1 + (1 if self.request.ligand_path else 0) + len(trailing_steps)
            completed = 1 + (1 if self.request.ligand_path else 0)
            for description, subcommand, args, stdin_text in trailing_steps:
                self._verify_ligand_evidence()
                self._execute(
                    self.commands.gromacs(
                        subcommand, args, root, description, stdin_text
                    )
                )
                if subcommand == "solvate":
                    water_count = consolidate_water_molecules(root / "topol.top")
                    self.on_log(f"Validated one contiguous solvent block before ion preparation: SOL {water_count}.")
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
        if self.request.structure_repair_report:
            if not self.request.structure_repair_report_sha256:
                raise PreparationError("Inspect and adopt the repaired candidate again to record its approved report digest.")
            metadata = _verify_repair_report(self.request.structure_repair_report, protein)
            if metadata.get("report_sha256") != self.request.structure_repair_report_sha256:
                raise PreparationError("Repair report changed after adoption; inspect and adopt the candidate again.")
            assert self._manifest is not None
            self._manifest = replace(
                self._manifest,
                metadata={**self._manifest.metadata, "structure_repair": metadata},
            )
            self._save()
        self.structure_validator(protein)
        self._ligand_source_hashes = {str(Path(protein).resolve()): file_sha256(protein)}
        if not self.request.ligand_path and (self.request.ligand_copies or self.request.source_residue_name):
            raise PreparationError("Ligand copies and complex extraction require a reviewed ligand template.")
        if self.request.excluded_residue_names and not self.request.source_residue_name:
            raise PreparationError("Residue exclusions require explicit complex ligand extraction.")
        if self.request.ligand_path:
            self._validate_ligand_request()
            self._ligand_source_hashes = {str(Path(path).resolve()): file_sha256(path)
                                          for path in (protein, self.request.ligand_path, *self.request.ligand_copies)}

    def _validate_ligand_request(self) -> None:
        force_field = self.request.system.force_field.upper()
        if self.request.ligand_backend == "acpype_gaff2":
            acpype_charge_method(self.request.charge_method)
            if not force_field.startswith("AMBER"):
                raise PreparationError("Automatic ACPYPE/GAFF2 requires an AMBER-family protein force field.")
            if self.request.ligand_copies or self.request.source_residue_name:
                raise PreparationError("Multiple ligand poses and complex extraction require the explicit CHARMM ligand workflow.")
            validate_acpype_input(self.request.ligand_path)
        elif self.request.ligand_backend == "swissparam_match":
            if not force_field.startswith("CHARMM36"):
                raise PreparationError("SwissParam MATCH requires the CHARMM36-family protein force field.")
            if not self.request.ligand_protonation_confirmed:
                raise PreparationError("Confirm the ligand protonation state before CHARMM parameterization.")
            if self.request.source_residue_name and self.request.ligand_copies:
                raise PreparationError("Choose complex extraction or additional ligand poses, not both.")
            for path in (self.request.ligand_path, *self.request.ligand_copies):
                if not path or Path(path).suffix.lower() != ".mol2" or not Path(path).is_file():
                    raise PreparationError("CHARMM ligand parameterization requires existing, reviewed MOL2 files with explicit hydrogens.")
        else:
            raise PreparationError(f"Unknown ligand parameterization backend: {self.request.ligand_backend}.")

    def _prepare_force_field(self, root: Path) -> None:
        if self.request.system.force_field.upper().startswith("CHARMM36"):
            self.on_log("Staging locally installed CHARMM36m force field...")
            stage_charmm36_force_field(root)

    def _prepare_coordinates(
        self, root: Path
    ) -> tuple[LigandTopologyArtifacts | None, str]:
        system, ligand_path, copies = self._coordinate_inputs(root)
        system, crystal_waters = self._prepare_crystal_waters(system, root)
        artifacts = None
        if ligand_path and self.request.ligand_backend == "swissparam_match":
            artifacts = self._prepare_charmm_ligand(root, ligand_path, copies)
            self._record_ligand_evidence(artifacts)
        self._verify_structure_repair_evidence()
        description, subcommand, args, _stdin = build_pdb2gmx_step(system)
        self._execute(self.commands.gromacs(subcommand, args, root, description))
        if crystal_waters:
            self._verify_ligand_sources()
            append_crystal_waters(root / "protein.gro", root / "topol.top", crystal_waters)
        self.on_progress(16 if self.request.ligand_path else 20)
        if not ligand_path:
            return None, "protein.gro"
        if artifacts is None:
            ligand_arg = wsl_bridge.win_to_wsl(ligand_path)
            charge = acpype_charge_method(self.request.charge_method)
            acpype = self.commands.external(
                "acpype",
                ["-i", ligand_arg, "-c", charge, "-a", "gaff2", "-n", str(int(self.request.net_charge)), "-b", "ligand", "-o", "gmx"],
                root, "Running ACPYPE ligand parameterization...",
            )
            self._execute(acpype)
            artifacts = self.ligand_normalizer(root, root)
            self._record_ligand_evidence(artifacts)
        self._verify_ligand_evidence()
        self.gro_combiner(root / "protein.gro", artifacts.ligand_gro, root / "complex.gro")
        if artifacts.parameter_includes or artifacts.molecule_count != 1:
            self.topology_patcher(root / "topol.top", artifacts.ligand_itp,
                                  molecule_count=artifacts.molecule_count, parameter_includes=artifacts.parameter_includes)
        else:
            self.topology_patcher(root / "topol.top", artifacts.ligand_itp)
        self.on_progress(33)
        return artifacts, "complex.gro"

    def _prepare_crystal_waters(self, system: SystemPrepParams, root: Path):
        if not system.force_field.upper().startswith("CHARMM36"):
            return system, None
        self._verify_ligand_sources()
        def build_hydrogens(source: Path, output: Path) -> None:
            command = self.commands.external("obabel", ["-isdf", wsl_bridge.win_to_wsl(str(source)),
                "-omol2", "-O", wsl_bridge.win_to_wsl(str(output)), "-h"], source.parent,
                "Adding hydrogens to isolated crystal waters; preserving observed oxygens...")
            self._execute(command)
        waters = prepare_crystal_waters(system.pdb_path, root / "crystal_waters", hydrogen_builder=build_hydrogens)
        self._verify_ligand_sources()
        if waters is None:
            return system, None
        if system.water_model != "TIP3P":
            raise PreparationError("Preserved CHARMM crystal waters require the TIP3P water model.")
        paths = (system.pdb_path, waters.protein_path, waters.water_pdb, waters.water_gro, waters.provenance_path)
        self._ligand_source_hashes = {**self._ligand_source_hashes,
            **{str(Path(path).resolve()): file_sha256(path) for path in paths}}
        assert self._manifest is not None
        self._manifest = replace(self._manifest, metadata={**self._manifest.metadata, "crystal_waters": {
            "water_count": waters.water_count, "provenance_path": str(waters.provenance_path.resolve()),
            "provenance_sha256": file_sha256(waters.provenance_path),
            "water_gro": str(waters.water_gro.resolve()), "water_pdb": str(waters.water_pdb.resolve()),
        }})
        self._save()
        self.on_log(f"Preserving {waters.water_count} crystal waters outside pdb2gmx; hydrogen provenance saved.")
        return replace(system, pdb_path=str(waters.protein_path)), waters

    def _verify_structure_repair_evidence(self) -> None:
        if self.request.structure_repair_report:
            current = _verify_repair_report(self.request.structure_repair_report, self.request.system.pdb_path)
            assert self._manifest is not None
            if current != self._manifest.metadata.get("structure_repair"):
                raise PreparationError("Repair evidence changed after validation; inspect and adopt the candidate again.")

    def _coordinate_inputs(self, root: Path):
        if not self.request.source_residue_name:
            return self.request.system, self.request.ligand_path, self.request.ligand_copies
        prepare = self.complex_input_preparer
        if prepare is None:
            from core.complex_ligand_input import prepare_complex_inputs
            prepare = prepare_complex_inputs
        result = prepare(self.request.system.pdb_path, self.request.ligand_path, root / "complex_inputs",
                         self.request.source_residue_name, self.request.excluded_residue_names,
                         conda_env=self.commands.conda_environment, on_log=self.on_log)
        self._verify_ligand_sources()
        paths = (result.protein_path, result.provenance_path, result.ligand_path, *result.ligand_copies)
        self._ligand_source_hashes = {**self._ligand_source_hashes,
                                      **{str(Path(path).resolve()): file_sha256(path) for path in paths}}
        assert self._manifest is not None
        self._manifest = replace(self._manifest, metadata={**self._manifest.metadata, "complex_inputs": {
            "provenance_path": str(result.provenance_path.resolve()),
            "provenance_sha256": file_sha256(result.provenance_path),
            "protein_path": str(result.protein_path.resolve()), "protein_sha256": file_sha256(result.protein_path),
        }})
        self._save()
        return replace(self.request.system, pdb_path=str(result.protein_path)), str(result.ligand_path), tuple(map(str, result.ligand_copies))

    def _prepare_charmm_ligand(self, root: Path, ligand_path: str, copies: tuple[str, ...]):
        prepare = self.charmm_ligand_preparer
        if prepare is None:
            from core.charmm_ligand import prepare_charmm_ligand
            prepare = prepare_charmm_ligand
        artifacts = prepare(ligand_path, root, net_charge=self.request.net_charge,
                            allow_upload=self.request.allow_ligand_upload, on_log=self.on_log, ligand_copies=copies)
        validate_ligand_artifacts(artifacts, expected_copies=1 + len(copies), net_charge=self.request.net_charge)
        if artifacts.provenance_path is None:
            raise PreparationError("CHARMM ligand parameterization did not return its provenance report.")
        self._verify_ligand_sources()
        return artifacts

    def _verify_ligand_sources(self) -> None:
        if any(file_sha256(path) != digest for path, digest in self._ligand_source_hashes.items()):
            raise PreparationError("A ligand source changed during preparation; review its inputs again.")

    def _record_ligand_evidence(self, artifacts: LigandTopologyArtifacts) -> None:
        self._verify_ligand_sources()
        outputs = (artifacts.ligand_gro, artifacts.ligand_itp, *artifacts.parameter_includes)
        if artifacts.ligand_posre_itp:
            outputs += (artifacts.ligand_posre_itp,)
        metadata = {"backend": self.request.ligand_backend, "molecule_count": artifacts.molecule_count,
                    "net_charge": self.request.net_charge, "input_sha256": self._ligand_source_hashes,
                    "output_sha256": {str(Path(path).resolve()): file_sha256(path) for path in outputs}}
        if artifacts.provenance_path:
            content = json.loads(artifacts.provenance_path.read_bytes())
            if not isinstance(content, dict):
                raise PreparationError("Ligand provenance must contain a JSON object.")
            metadata = {**metadata, "provenance_path": str(artifacts.provenance_path.resolve()),
                        "provenance_sha256": file_sha256(artifacts.provenance_path)}
        assert self._manifest is not None
        self._manifest = replace(self._manifest, metadata={**self._manifest.metadata, "ligand_parameterization": metadata})
        self._save()

    def _verify_ligand_evidence(self) -> None:
        self._verify_ligand_sources()
        assert self._manifest is not None
        evidence = self._manifest.metadata.get("ligand_parameterization", {})
        hashes = dict(evidence.get("output_sha256", {}))
        if evidence.get("provenance_path"):
            hashes[evidence["provenance_path"]] = evidence["provenance_sha256"]
        if any(file_sha256(path) != digest for path, digest in hashes.items()):
            raise PreparationError("Ligand parameterization files changed during preparation.")

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
        self._verify_ligand_evidence()
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

        generated = generate_all_mdp(replace(
            self.request.md_parameters,
            force_field=self.request.system.force_field,
            water_model=self.request.system.water_model,
        ))
        mdp_paths: dict[str, Path] = {}
        for filename, content in generated.items():
            target = root / filename
            target.write_text(content, encoding="utf-8", newline="\n")
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
            for index, path in enumerate(ligand.parameter_includes):
                outputs[f"ligand/parameters_{index}"] = str(path.resolve())
            if ligand.provenance_path:
                outputs["ligand/provenance.json"] = str(ligand.provenance_path.resolve())
        assert self._manifest is not None
        if water_evidence := self._manifest.metadata.get("crystal_waters"):
            outputs["crystal_waters/provenance.json"] = water_evidence["provenance_path"]
            outputs["crystal_waters/waters.gro"] = water_evidence["water_gro"]
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
        self._ligand_source_hashes = {}
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
        reset_stages = self._reset_downstream_stages(
            manifest.stages,
            "Waiting for the current preparation to complete.",
        )
        self._manifest = replace(
            manifest,
            metadata={key: value for key, value in manifest.metadata.items()
                      if key not in {"stage_qc", "structure_repair", "ligand_parameterization", "complex_inputs", "crystal_waters"}},
            stages={
                **reset_stages,
                StageName.PREPARATION.value: prep,
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
        assert self._manifest is not None
        self._manifest = replace(
            self._manifest,
            stages=self._reset_downstream_stages(
                self._manifest.stages,
                "Preparation failed; this stage is not ready.",
            ),
            updated_at=utc_now_iso(),
        )
        self._save()

    @staticmethod
    def _reset_downstream_stages(
        stages: dict[str, StageRecord],
        message: str,
    ) -> dict[str, StageRecord]:
        """Return stages with every artifact-dependent stage invalidated."""

        reset = dict(stages)
        for stage_name in (
            StageName.MINIMIZATION,
            StageName.NVT,
            StageName.NPT,
            StageName.PRODUCTION,
        ):
            reset[stage_name.value] = replace(
                stages[stage_name.value],
                status=StageStatus.NOT_READY.value,
                started_at=None,
                finished_at=None,
                message=message,
                commands=[],
                inputs={},
                outputs={},
            )
        return reset

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
        try:
            service = PreparationOrchestrator(
                self.request,
                on_log=self.log.emit,
                on_progress=self.progress.emit,
                **self.service_options,
            )
            service.run()
        except (PreparationError, OSError, ValueError) as exc:
            self.done.emit(False, str(exc))
            return
        self.done.emit(True, "System preparation complete.")
