"""Read-only review of prepared inputs and canonical MD Setup choices."""

from __future__ import annotations

import stat
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from math import isclose, isfinite
from pathlib import Path
from typing import TYPE_CHECKING, SupportsFloat, cast

from core.run_manifest import RunManifest

if TYPE_CHECKING:
    from core.preparation_orchestrator import PreparationRequest


_PATH_FIELDS = ("project_dir", "protein", "ligand", "structure_repair_report")
_TEXT_FIELDS = ("force_field", "water_model", "box_type", "structure_repair_report_sha256")
_NUMBER_FIELDS = ("box_padding", "ion_concentration", "duration", "timestep", "temperature",
                  "pressure", "save_every", "nvt_steps", "npt_steps")
_MDP_NAMES = ("em.mdp", "nvt.mdp", "npt.mdp", "md.mdp")
_MAX_MDP_BYTES = 1024 * 1024
_UNKNOWN = "Not recorded"


@dataclass(frozen=True)
class RunReview:
    rows: tuple[tuple[str, str], ...]
    mdp_files: tuple[tuple[str, str], ...]
    issues: tuple[str, ...]
    protocol_label: str


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(cast(SupportsFloat | str, value))
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if isfinite(parsed) else None


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _path(value: object) -> str:
    selected = _text(value)
    if not selected:
        return ""
    try:
        return str(Path(selected).expanduser().resolve())
    except (ValueError, OSError, RuntimeError):
        return selected


def setup_review_snapshot(state: Mapping) -> dict:
    """Keep scientific UI choices; transient consent never becomes saved approval."""
    if not isinstance(state, Mapping):
        raise TypeError("MD Setup review state must be a mapping.")
    paths = {key: _path(state.get(key)) for key in _PATH_FIELDS}
    text = {key: _text(state.get(key)) for key in _TEXT_FIELDS}
    numbers = {key: parsed if (parsed := _number(state.get(key))) is not None
               else _text(state.get(key)) for key in _NUMBER_FIELDS}
    result = {**paths, **text, **numbers}
    if not result["ligand"]:
        return result
    charge = _number(state.get("charge"))
    ligand = {"charge": charge if charge is not None else _text(state.get("charge"))}
    if not text["force_field"].upper().startswith("CHARMM"):
        return {**result, **ligand, "charge_method": _text(state.get("charge_method"))}
    from_complex = state.get("ligand_from_complex") is True
    exclusions = ",".join(dict.fromkeys(
        item.strip().upper() for item in _text(state.get("excluded_residue_names")).split(",")
        if item.strip()
    )) if from_complex else ""
    return {**result, **ligand, "ligand_from_complex": from_complex,
            "source_residue_name": _text(state.get("source_residue_name")).upper() if from_complex else "",
            "excluded_residue_names": exclusions}


def build_preparation_review(request: PreparationRequest) -> dict:
    """Record the submitted request after preparation has validated its MDPs."""
    system = request.system
    params = replace(request.md_parameters, force_field=system.force_field,
                     water_model=system.water_model).normalized()
    state = {
        "project_dir": system.work_dir, "protein": system.pdb_path,
        "ligand": request.ligand_path or "", "force_field": system.force_field,
        "water_model": system.water_model, "box_type": system.box_type,
        "box_padding": system.box_padding_nm, "ion_concentration": system.ion_concentration_m,
        "duration": params.duration_ns, "timestep": params.timestep_fs,
        "temperature": params.temperature_k, "pressure": params.pressure_bar,
        "save_every": params.save_every_ps, "nvt_steps": params.nvt_steps,
        "npt_steps": params.npt_steps, "charge": request.net_charge,
        "charge_method": request.charge_method,
        "ligand_from_complex": bool(request.source_residue_name),
        "source_residue_name": request.source_residue_name,
        "excluded_residue_names": ",".join(request.excluded_residue_names),
        "structure_repair_report": request.structure_repair_report,
        "structure_repair_report_sha256": request.structure_repair_report_sha256,
    }
    return {"schema_version": 1, "system": asdict(system), "md_parameters": asdict(params),
            "ligand_backend": request.ligand_backend if request.ligand_path else "",
            "charge_method": request.charge_method if request.ligand_path else "",
            "net_charge": request.net_charge if request.ligand_path else None,
            "setup_snapshot": setup_review_snapshot(state)}


def _read_mdp(root: Path, name: str) -> str:
    path = root / name
    info = path.stat(follow_symlinks=False)
    if path.is_symlink() or path.resolve().parent != root:
        raise ValueError("linked files are not read; use regular prepared project files")
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("expected a regular MDP file")
    if info.st_size > _MAX_MDP_BYTES:
        raise ValueError("MDP review is limited to 1 MiB per file")
    with path.open("rb") as handle:
        data = handle.read(_MAX_MDP_BYTES + 1)
    if len(data) > _MAX_MDP_BYTES:
        raise ValueError("MDP review is limited to 1 MiB per file")
    return data.decode("utf-8")


def _mdp_values(content: str) -> dict[str, str]:
    pairs = (line.partition(";")[0].partition("=") for line in content.splitlines())
    return {key.strip().lower().replace("_", "-"): value.strip()
            for key, separator, value in pairs if separator and key.strip()}


def _duration(values: Mapping[str, str]) -> float | None:
    steps, dt = _number(values.get("nsteps")), _number(values.get("dt"))
    if steps is None or dt is None or steps <= 0 or dt <= 0:
        return None
    duration = steps * dt
    return duration if isfinite(duration) else None


def _protocol_stage(values: Mapping[str, str]) -> str:
    duration = _duration(values)
    if duration is None:
        return _UNKNOWN
    return f"{duration:g} ps ({values['nsteps']} steps; dt {float(values['dt']) * 1000:g} fs)"


def _output_interval(values: Mapping[str, str]) -> str:
    stride, dt = _number(values.get("nstxout-compressed")), _number(values.get("dt"))
    if stride is None or dt is None or stride <= 0 or dt <= 0 or not isfinite(stride * dt):
        return _UNKNOWN
    return f"{stride * dt:g} ps"


def _recorded_system(metadata: Mapping) -> Mapping:
    review = metadata.get("preparation_review")
    return review.get("system", {}) if isinstance(review, Mapping) and isinstance(review.get("system"), Mapping) else {}


def _system_rows(system: Mapping, metadata: Mapping) -> tuple[tuple[str, str], ...]:
    padding, salt = _number(system.get("box_padding_nm")), _number(system.get("ion_concentration_m"))
    box = _text(system.get("box_type"))
    water_evidence = metadata.get("crystal_waters", {})
    count = water_evidence.get("water_count") if isinstance(water_evidence, Mapping) else None
    waters = f"{count} retained" if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else _UNKNOWN
    return (("Box", f"{box}; padding {padding:g} nm" if box and padding is not None else _UNKNOWN),
            ("Salt concentration", f"{salt:g} M" if salt is not None else _UNKNOWN),
            ("Ion species", f"{system['ion_pos']} / {system['ion_neg']}" if system.get("ion_pos") and system.get("ion_neg") else _UNKNOWN),
            ("Crystal waters", waters))


def build_run_review(manifest: RunManifest, project_dir: str | Path) -> RunReview:
    """Review recorded preparation and exact MDP text without executing GROMACS."""
    root = Path(project_dir).expanduser().resolve()
    if root != Path(manifest.project_dir).expanduser().resolve():
        raise ValueError("Review project directory does not match the run manifest project.")
    files: list[tuple[str, str]] = []
    issues: list[str] = []
    for name in _MDP_NAMES:
        try:
            files.append((name, _read_mdp(root, name)))
        except (OSError, UnicodeError, ValueError) as exc:
            issues.append(f"Cannot review {name}: {exc}. Review prepared files in MD Setup.")
    system = _recorded_system(manifest.metadata)
    if not system:
        issues.append("Legacy project: preparation choices were not recorded. Existing MDP files remain reviewable.")
    mdps = {name: _mdp_values(content) for name, content in files}
    durations = tuple(_duration(mdps.get(name, {})) for name in ("nvt.mdp", "npt.mdp", "md.mdp"))
    short_test = all(value is not None and isclose(value, 2.0, rel_tol=0, abs_tol=1e-9) for value in durations)
    protocol_label = ("Short execution test: 2 ps per MD stage; not an equilibration protocol."
                      if short_test else "Configured MD protocol; review equilibration and sampling requirements.")
    review = manifest.metadata.get("preparation_review", {})
    review = review if isinstance(review, Mapping) else {}
    ligand = manifest.metadata.get("ligand_parameterization", {})
    ligand = ligand if isinstance(ligand, Mapping) else {}
    rows = (
        ("Project", str(root)), ("System", manifest.system_kind),
        ("Protein source", manifest.protein_source),
        ("Ligand source", manifest.ligand_source or "No separate ligand"),
        ("Force field", manifest.force_field), ("Water model", manifest.water_model),
        *_system_rows(system, manifest.metadata),
        ("Ligand parameterization", _text(ligand.get("backend") or review.get("ligand_backend")) or ("Not applicable" if not manifest.ligand_source else _UNKNOWN)),
        ("Ligand expected net charge", str(review["net_charge"]) if review.get("net_charge") is not None else ("Not applicable" if not manifest.ligand_source else _UNKNOWN)),
        ("GROMACS executable", manifest.gromacs_binary),
        ("Conda environment", manifest.conda_environment),
        ("Requested CPU cores", str(manifest.requested_cores)),
        ("Requested GPU mode", manifest.gpu_mode),
        ("Minimization", f"Maximum {mdps['em.mdp']['nsteps']} steps" if mdps.get("em.mdp", {}).get("nsteps") else _UNKNOWN),
        *((label, _protocol_stage(mdps.get(name, {}))) for label, name in
          (("NVT", "nvt.mdp"), ("NPT", "npt.mdp"), ("Production", "md.mdp"))),
        ("Production temperature (K)", mdps.get("md.mdp", {}).get("ref-t") or _UNKNOWN),
        ("Production pressure (bar)", mdps.get("md.mdp", {}).get("ref-p") or _UNKNOWN),
        ("Trajectory output interval", _output_interval(mdps.get("md.mdp", {}))),
    )
    return RunReview(rows, tuple(files), tuple(issues), protocol_label)
