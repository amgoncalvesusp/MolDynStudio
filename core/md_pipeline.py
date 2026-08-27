"""Deterministic, tokenized command definitions for the MD pipeline.

The preparation screen creates ``system.gro`` and ``topol.top``.  The stages
below therefore describe only the reproducible equilibration/production
chain; the preparation stage is retained as an explicit checkpoint with no
commands of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import tempfile
from typing import Callable, Sequence

from core.artifact_validation import (
    ValidationResult,
    validate_gro,
    validate_mdp,
    validate_topology,
)
from core.gromacs_capabilities import (
    GromacsCapabilities,
    build_mdrun_resource_args,
    probe_gromacs,
)
from core.run_manifest import RunManifest, StageName, StageStatus
from utils.topology_builder import read_molecule_name_from_itp


DEFAULT_FREE_SPACE_WARNING_BYTES = 2 * 1024**3

_STAGE_PREREQUISITES = {
    StageName.MINIMIZATION: StageName.PREPARATION,
    StageName.NVT: StageName.MINIMIZATION,
    StageName.NPT: StageName.NVT,
    StageName.PRODUCTION: StageName.NPT,
}

_STAGE_INPUTS = {
    StageName.MINIMIZATION: (
        ("system.gro", "gro"),
        ("topol.top", "topology"),
        ("em.mdp", "minimization_mdp"),
    ),
    StageName.NVT: (
        ("em.gro", "gro"),
        ("topol.top", "topology"),
        ("nvt.mdp", "dynamics_mdp"),
    ),
    StageName.NPT: (
        ("nvt.gro", "gro"),
        ("nvt.cpt", "file"),
        ("topol.top", "topology"),
        ("npt.mdp", "dynamics_mdp"),
    ),
    StageName.PRODUCTION: (
        ("npt.gro", "gro"),
        ("npt.cpt", "file"),
        ("topol.top", "topology"),
        ("md.mdp", "dynamics_mdp"),
    ),
}

GroValidator = Callable[[str | Path], ValidationResult]
TopologyValidator = Callable[[str | Path, str | None], ValidationResult]
MdpValidator = Callable[..., ValidationResult]
CapabilityProbe = Callable[[str, str], GromacsCapabilities]
WritableProbe = Callable[[Path], bool]
DiskUsage = Callable[[Path], object]


@dataclass(frozen=True)
class PipelineCommand:
    """One executable invocation, represented as tokens rather than a shell string."""

    args: tuple[str, ...]
    cwd: str


@dataclass(frozen=True)
class PipelineStage:
    """A named stage and the commands executed sequentially within it."""

    name: StageName
    commands: tuple[PipelineCommand, ...]


def _project_is_writable(project_dir: Path) -> bool:
    """Test actual file creation because permission bits alone are unreliable."""

    if not project_dir.is_dir():
        return False
    probe_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=project_dir,
            prefix=".moldynstudio-preflight-",
            delete=False,
        ) as handle:
            probe_path = Path(handle.name)
        return True
    except OSError:
        return False
    finally:
        if probe_path is not None:
            try:
                probe_path.unlink(missing_ok=True)
            except OSError:
                pass


def _required_file(path: Path) -> ValidationResult:
    if not path.is_file():
        return ValidationResult(False, f"Required input is missing or not a file: {path.name}")
    try:
        if path.stat().st_size <= 0:
            return ValidationResult(False, f"Required input is empty: {path.name}")
    except OSError as exc:
        return ValidationResult(False, f"Could not inspect required input {path.name}: {exc}")
    return ValidationResult(True, f"Required input is present: {path.name}")


def _checked_validation(
    validator: Callable[..., ValidationResult],
    *args: object,
    **kwargs: object,
) -> ValidationResult:
    try:
        result = validator(*args, **kwargs)
    except Exception as exc:
        return ValidationResult(False, f"Validation raised an exception: {exc}")
    if not isinstance(result, ValidationResult):
        return ValidationResult(False, "Validator returned an invalid result.")
    return result


def _nvt_restraint_validation(stage: PipelineStage) -> ValidationResult:
    command = next(
        (command for command in stage.commands if command.args and command.args[0] == "grompp"),
        None,
    )
    if command is None:
        return ValidationResult(False, "NVT is missing its grompp command.")
    try:
        reference = command.args[command.args.index("-r") + 1]
    except (ValueError, IndexError):
        return ValidationResult(False, "NVT grompp must specify restraint reference em.gro.")
    if reference != "em.gro":
        return ValidationResult(
            False,
            f"NVT restraint reference must be exactly em.gro, not {reference!r}.",
        )
    return ValidationResult(True, "NVT restraint reference is em.gro.")


def _ligand_name(manifest: RunManifest) -> tuple[str | None, ValidationResult]:
    has_ligand = bool(manifest.ligand_source)
    declares_complex = manifest.system_kind == "protein_ligand"
    if has_ligand != declares_complex:
        return None, ValidationResult(
            False,
            "Ligand manifest fields disagree: system_kind and ligand_source are inconsistent.",
        )
    if not has_ligand:
        return None, ValidationResult(True, "Manifest describes a protein-only system.")

    preparation = manifest.stages.get(StageName.PREPARATION.value)
    ligand_itp_value = (
        preparation.outputs.get("ligand/ligand.itp") if preparation is not None else None
    )
    if not ligand_itp_value:
        return None, ValidationResult(
            False,
            "Ligand manifest is missing the prepared ligand/ligand.itp artifact.",
        )
    ligand_itp = Path(ligand_itp_value)
    file_result = _required_file(ligand_itp)
    if not file_result.ok:
        return None, ValidationResult(
            False,
            f"Ligand topology artifact is invalid: {file_result.message}",
        )
    try:
        return read_molecule_name_from_itp(ligand_itp), ValidationResult(
            True,
            "Ligand manifest contains a readable molecule topology.",
        )
    except (OSError, UnicodeError, ValueError) as exc:
        return None, ValidationResult(False, f"Ligand topology artifact is invalid: {exc}")


def preflight_stage(
    stage: PipelineStage,
    manifest: RunManifest,
    env_name: str,
    gmx: str,
    *,
    capabilities: GromacsCapabilities | None = None,
    free_space_warning_bytes: int = DEFAULT_FREE_SPACE_WARNING_BYTES,
    writable_probe: WritableProbe = _project_is_writable,
    disk_usage: DiskUsage = shutil.disk_usage,
    capability_probe: CapabilityProbe = probe_gromacs,
    gro_validator: GroValidator = validate_gro,
    topology_validator: TopologyValidator = validate_topology,
    mdp_validator: MdpValidator = validate_mdp,
) -> ValidationResult:
    """Fail closed before launching a scientifically meaningful MD stage.

    Fatal blockers are returned through ``ok=False`` and named detail results.
    Low disk space and inability to measure it are warnings, because the exact
    storage required depends on output cadence and simulation duration.
    """

    details: dict[str, ValidationResult] = {}
    warnings: list[str] = []
    root = Path(manifest.project_dir)

    details["project_directory"] = ValidationResult(
        root.is_dir(),
        f"Project directory exists: {root}"
        if root.is_dir()
        else f"Project directory does not exist: {root}",
    )
    try:
        project_writable = bool(writable_probe(root))
        writable_message = (
            f"Project directory is writable: {root}"
            if project_writable
            else f"Project directory is not writable: {root}"
        )
    except Exception as exc:
        project_writable = False
        writable_message = f"Could not verify that project directory is writable: {exc}"
    details["project_writable"] = ValidationResult(
        project_writable,
        writable_message,
    )

    threshold = max(0, int(free_space_warning_bytes))
    try:
        free_bytes = int(getattr(disk_usage(root), "free"))
        if free_bytes < threshold:
            warnings.append(
                "Project free space is below the configured warning threshold "
                f"({free_bytes} available; {threshold} recommended)."
            )
    except (OSError, TypeError, ValueError) as exc:
        warnings.append(f"Could not determine project free space: {exc}")

    details["core_count"] = ValidationResult(
        isinstance(manifest.requested_cores, int)
        and not isinstance(manifest.requested_cores, bool)
        and manifest.requested_cores >= 1,
        f"Requested core count is {manifest.requested_cores}."
        if isinstance(manifest.requested_cores, int) and manifest.requested_cores >= 1
        else "Requested CPU core count must be at least 1.",
    )

    try:
        active_capabilities = capabilities or capability_probe(gmx, env_name)
    except Exception as exc:
        active_capabilities = None
        details["gromacs"] = ValidationResult(
            False, f"GROMACS capability probe failed: {exc}"
        )
    if active_capabilities is not None:
        details["gromacs"] = ValidationResult(
            active_capabilities.executable_ok,
            "The selected GROMACS executable is callable."
            if active_capabilities.executable_ok
            else "The selected GROMACS executable is not callable.",
        )
        if details["core_count"].ok and active_capabilities.executable_ok:
            try:
                build_mdrun_resource_args(
                    manifest.gpu_mode,
                    manifest.requested_cores,
                    active_capabilities,
                )
                details["gpu_mode"] = ValidationResult(
                    True, f"GPU mode {manifest.gpu_mode!r} is supported."
                )
            except Exception as exc:
                details["gpu_mode"] = ValidationResult(False, str(exc))
        else:
            details["gpu_mode"] = ValidationResult(
                False, "GPU mode cannot be validated until GROMACS and CPU cores are valid."
            )

    predecessor = _STAGE_PREREQUISITES.get(stage.name)
    if predecessor is not None:
        predecessor_record = manifest.stages.get(predecessor.value)
        completed = (
            predecessor_record is not None
            and predecessor_record.status == StageStatus.COMPLETED.value
        )
        details["stage_prerequisite"] = ValidationResult(
            completed,
            f"Stage prerequisite {predecessor.value} is completed."
            if completed
            else f"Stage prerequisite {predecessor.value} is not completed.",
        )

    ligand_name, ligand_result = _ligand_name(manifest)
    details["ligand_manifest"] = ligand_result
    validators: dict[str, Callable[[Path], ValidationResult]] = {
        "gro": lambda path: _checked_validation(gro_validator, path),
        "file": _required_file,
        "minimization_mdp": lambda path: _checked_validation(
            mdp_validator, path, require_dt=False
        ),
        "dynamics_mdp": lambda path: _checked_validation(
            mdp_validator, path, require_dt=True
        ),
        "topology": lambda path: _checked_validation(
            topology_validator, path, ligand_name
        ),
    }
    for filename, kind in _STAGE_INPUTS.get(stage.name, ()):
        details[f"input:{filename}"] = validators[kind](root / filename)

    if stage.name == StageName.NVT:
        details["nvt_restraint"] = _nvt_restraint_validation(stage)

    failures = [
        f"{name}: {result.message}" for name, result in details.items() if not result.ok
    ]
    if failures:
        return ValidationResult(
            False,
            "Preflight blockers: " + "; ".join(failures),
            details,
            tuple(warnings),
        )
    return ValidationResult(
        True,
        f"{stage.name.value.title()} preflight passed.",
        details,
        tuple(warnings),
    )


def _command(project_dir: str | Path, *args: str) -> PipelineCommand:
    return PipelineCommand(tuple(args), str(project_dir))


def build_prepare_stage(project_dir: str | Path) -> PipelineStage:
    """Return the preparation checkpoint.

    Structure/topology preparation is performed by ``SystemPrepWorker``;
    keeping this stage command-free prevents the MD runner from repeating
    interactive ion generation.
    """

    return PipelineStage(StageName.PREPARATION, ())


def build_preparation_stage(project_dir: str | Path) -> PipelineStage:
    """Descriptive alias for :func:`build_prepare_stage`."""

    return build_prepare_stage(project_dir)


def build_minimize_stage(project_dir: str | Path) -> PipelineStage:
    return PipelineStage(
        StageName.MINIMIZATION,
        (
            _command(project_dir, "grompp", "-f", "em.mdp", "-c", "system.gro",
                     "-p", "topol.top", "-o", "em.tpr"),
            _command(project_dir, "mdrun", "-deffnm", "em"),
        ),
    )


def build_minimization_stage(project_dir: str | Path) -> PipelineStage:
    return build_minimize_stage(project_dir)


def build_nvt_stage(project_dir: str | Path) -> PipelineStage:
    return PipelineStage(
        StageName.NVT,
        (
            _command(project_dir, "grompp", "-f", "nvt.mdp", "-c", "em.gro",
                     "-r", "em.gro", "-p", "topol.top", "-o", "nvt.tpr"),
            _command(project_dir, "mdrun", "-deffnm", "nvt"),
        ),
    )


def build_npt_stage(project_dir: str | Path) -> PipelineStage:
    return PipelineStage(
        StageName.NPT,
        (
            _command(project_dir, "grompp", "-f", "npt.mdp", "-c", "nvt.gro",
                     "-r", "nvt.gro", "-t", "nvt.cpt", "-p", "topol.top",
                     "-o", "npt.tpr"),
            _command(project_dir, "mdrun", "-deffnm", "npt"),
        ),
    )


def build_production_stage(project_dir: str | Path) -> PipelineStage:
    return PipelineStage(
        StageName.PRODUCTION,
        (
            _command(project_dir, "grompp", "-f", "md.mdp", "-c", "npt.gro",
                     "-t", "npt.cpt", "-p", "topol.top", "-o", "md.tpr"),
            _command(project_dir, "mdrun", "-deffnm", "md"),
        ),
    )


_BUILDERS = {
    StageName.PREPARATION: build_prepare_stage,
    StageName.MINIMIZATION: build_minimize_stage,
    StageName.NVT: build_nvt_stage,
    StageName.NPT: build_npt_stage,
    StageName.PRODUCTION: build_production_stage,
}


def build_pipeline(
    project_dir: str | Path,
    stages: Sequence[StageName] | None = None,
    *,
    resume_from: StageName | None = None,
    resume_from_stage: StageName | None = None,
) -> tuple[PipelineStage, ...]:
    """Build stages in canonical dependency order.

    ``stages`` is a selection (not an ordering instruction).  When resuming,
    the canonical list is sliced at the requested checkpoint before applying
    that selection, so a production run can never precede NPT.
    """

    if resume_from is not None and resume_from_stage is not None:
        raise ValueError("specify only one resume stage")
    selected = set(StageName if stages is None else stages)
    unknown = selected - set(StageName)
    if unknown:
        raise ValueError(f"unknown pipeline stage(s): {sorted(unknown)}")
    start = resume_from if resume_from is not None else resume_from_stage
    canonical = tuple(StageName)
    if start is not None:
        try:
            canonical = canonical[canonical.index(StageName(start)):]
        except (ValueError, TypeError) as exc:
            raise ValueError(f"unknown resume stage: {start!r}") from exc
    return tuple(_BUILDERS[name](project_dir) for name in canonical if name in selected)


__all__ = [
    "DEFAULT_FREE_SPACE_WARNING_BYTES", "PipelineCommand", "PipelineStage",
    "build_prepare_stage", "build_preparation_stage", "build_minimize_stage",
    "build_minimization_stage", "build_nvt_stage", "build_npt_stage",
    "build_production_stage", "build_pipeline", "preflight_stage",
]
