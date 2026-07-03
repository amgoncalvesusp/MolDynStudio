"""Input validation helpers for molecular dynamics files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    message: str


def validate_file(path: str, allowed_suffixes: Iterable[str], label: str) -> ValidationResult:
    if not path:
        return ValidationResult(False, f"{label} is required.")
    candidate = Path(path).expanduser()
    if not candidate.exists():
        return ValidationResult(False, f"{label} does not exist: {candidate}")
    if not candidate.is_file():
        return ValidationResult(False, f"{label} is not a file: {candidate}")
    suffixes = tuple(s.lower() for s in allowed_suffixes)
    if suffixes and candidate.suffix.lower() not in suffixes:
        expected = ", ".join(suffixes)
        return ValidationResult(False, f"{label} must use one of: {expected}")
    return ValidationResult(True, f"{label} OK.")


def validate_optional_file(path: str, allowed_suffixes: Iterable[str], label: str) -> ValidationResult:
    if not path:
        return ValidationResult(True, f"{label} not provided.")
    return validate_file(path, allowed_suffixes, label)


def validate_md_inputs(paths: Mapping[str, str]) -> Mapping[str, ValidationResult]:
    """Validate common MD input files without mutating the caller's mapping."""

    return {
        "protein": validate_file(paths.get("protein", ""), (".pdb", ".gro"), "Protein structure"),
        "ligand": validate_optional_file(paths.get("ligand", ""), (".mol2", ".sdf", ".pdb"), "Ligand file"),
        "topology": validate_optional_file(paths.get("topology", ""), (".tpr", ".top", ".gro", ".pdb"), "Topology"),
        "trajectory": validate_optional_file(paths.get("trajectory", ""), (".xtc", ".trr", ".nc", ".dcd"), "Trajectory"),
        "energy": validate_optional_file(paths.get("energy", ""), (".edr", ".xvg", ".csv"), "Energy data"),
    }

