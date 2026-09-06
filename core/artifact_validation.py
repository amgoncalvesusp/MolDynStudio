"""Validation of files produced by the molecular-dynamics pipeline.

The text formats are checked locally.  Binary GROMACS formats are validated by
``gmx dump`` through the WSL bridge, which keeps subprocess concerns out of the
parsers and makes the checks straightforward to test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
import re
from typing import Any, Mapping

from core import wsl_bridge


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    message: str
    details: Mapping[str, "ValidationResult"] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


def _read_text(path: str | Path, label: str) -> tuple[Path | None, str | None, ValidationResult | None]:
    candidate = Path(path).expanduser() if path else None
    if candidate is None:
        return None, None, ValidationResult(False, f"{label} is required.")
    if not candidate.exists():
        return candidate, None, ValidationResult(False, f"{label} does not exist: {candidate}")
    if not candidate.is_file():
        return candidate, None, ValidationResult(False, f"{label} is not a file: {candidate}")
    try:
        return candidate, candidate.read_text(encoding="utf-8"), None
    except (OSError, UnicodeError) as exc:
        return candidate, None, ValidationResult(False, f"Could not read {label}: {exc}")


def validate_gro(path: str | Path) -> ValidationResult:
    """Strictly validate a GROMACS ``.gro`` structure with nonzero atoms."""

    _candidate, text, error = _read_text(path, "GRO structure")
    if error:
        return error
    assert text is not None
    lines = text.splitlines()
    if len(lines) < 3 or not lines[0].strip():
        return ValidationResult(False, "GRO structure must contain a title, atom count, atoms, and box.")
    try:
        atom_count = int(lines[1].strip())
    except ValueError:
        return ValidationResult(False, "GRO atom-count line must be an integer.")
    if atom_count <= 0:
        return ValidationResult(False, "GRO structure must contain at least one atom.")
    expected_total = atom_count + 3
    if len(lines) < expected_total:
        return ValidationResult(False, f"GRO structure is truncated: expected {atom_count} atom lines.")
    if len(lines) > expected_total and any(line.strip() for line in lines[expected_total:]):
        return ValidationResult(False, "GRO structure contains unexpected trailing content.")
    atom_lines = lines[2 : 2 + atom_count]
    for index, atom_line in enumerate(atom_lines, start=1):
        if len(atom_line) < 44:
            return ValidationResult(False, f"GRO atom line {index} is truncated.")
        try:
            coordinates = [float(atom_line[start:start + 8]) for start in (20, 28, 36)]
            if not all(math.isfinite(value) for value in coordinates):
                raise ValueError("non-finite coordinate")
        except ValueError:
            return ValidationResult(False, f"GRO atom line {index} has invalid coordinates.")
    box = lines[2 + atom_count].split()
    if len(box) not in (3, 9):
        return ValidationResult(False, "GRO box must contain 3 or 9 numeric values.")
    try:
        values = [float(value) for value in box]
    except ValueError:
        return ValidationResult(False, "GRO box contains invalid numeric values.")
    if not all(math.isfinite(value) for value in values):
        return ValidationResult(False, "GRO box values must be finite.")
    if any(value <= 0 for value in values[:3]):
        return ValidationResult(False, "GRO box dimensions must be positive.")
    return ValidationResult(True, "GRO structure is valid.")


def validate_topology(path: str | Path, ligand_name: str | None = None) -> ValidationResult:
    """Validate required topology sections and optionally a ligand molecule."""

    _candidate, text, error = _read_text(path, "Topology")
    if error:
        return error
    assert text is not None
    sections = {match.group(1).strip().lower() for match in re.finditer(r"^\s*\[\s*([^]]+)\s*\]\s*$", text, re.MULTILINE)}
    if "system" not in sections:
        return ValidationResult(False, "Topology is missing the [ system ] section.")
    if "molecules" not in sections:
        return ValidationResult(False, "Topology is missing the [ molecules ] section.")
    def section_body(name: str) -> str:
        match = re.search(rf"^\s*\[\s*{re.escape(name)}\s*\]\s*$([\s\S]*?)(?=^\s*\[|\Z)", text, re.IGNORECASE | re.MULTILINE)
        return match.group(1) if match else ""

    system_lines = [line.split(";", 1)[0].strip() for line in section_body("system").splitlines() if line.split(";", 1)[0].strip() and not line.lstrip().startswith("#")]
    if not system_lines:
        return ValidationResult(False, "Topology [ system ] section must contain a system name.")
    molecule_lines = []
    for raw_line in section_body("molecules").splitlines():
        line = raw_line.split(";", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) != 2:
            return ValidationResult(False, "Topology contains an invalid [ molecules ] line.")
        try:
            count = int(fields[1])
        except ValueError:
            return ValidationResult(False, "Topology molecule count must be an integer.")
        if count <= 0:
            return ValidationResult(False, "Topology molecule count must be positive.")
        molecule_lines.append((fields[0], count))
    if not molecule_lines:
        return ValidationResult(False, "Topology [ molecules ] section must contain at least one molecule.")
    if ligand_name and ligand_name not in {name for name, _count in molecule_lines}:
        return ValidationResult(False, f"Topology does not contain ligand molecule '{ligand_name}'.")
    return ValidationResult(True, "Topology is valid.")


def validate_mdp(path: str | Path, *, require_dt: bool = True) -> ValidationResult:
    """Validate the time-defining fields of a stage MDP file.

    Molecular-dynamics stages require both a positive integration timestep and
    a positive step count. Energy minimization still requires ``nsteps``, but
    its steepest-descent integrator does not consume ``dt``.
    """

    _candidate, text, error = _read_text(path, "MDP parameters")
    if error:
        return error
    assert text is not None
    parameters: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.split(";", 1)[0].strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        parameters[name.strip().casefold()] = value.strip()

    raw_nsteps = parameters.get("nsteps")
    if raw_nsteps is None:
        return ValidationResult(False, "MDP parameters are missing nsteps.")
    try:
        nsteps = int(raw_nsteps)
    except ValueError:
        return ValidationResult(False, "MDP nsteps must be an integer.")
    if nsteps <= 0:
        return ValidationResult(False, "MDP nsteps must be positive.")

    raw_dt = parameters.get("dt")
    if raw_dt is None:
        if require_dt:
            return ValidationResult(False, "MDP parameters are missing dt.")
        return ValidationResult(True, "MDP parameters are valid.")
    try:
        dt = float(raw_dt)
    except ValueError:
        return ValidationResult(False, "MDP dt must be numeric.")
    if not math.isfinite(dt) or dt <= 0:
        return ValidationResult(False, "MDP dt must be positive and finite.")
    return ValidationResult(True, "MDP parameters are valid.")


def _validate_binary(path: str | Path, kind: str, args: list[str], bridge: Any) -> ValidationResult:
    candidate = Path(path).expanduser() if path else None
    if candidate is None:
        return ValidationResult(False, f"{kind} is required.")
    if not candidate.exists() or not candidate.is_file():
        return ValidationResult(False, f"{kind} does not exist or is not a file: {candidate}")
    try:
        completed = bridge.run(["gmx", "dump", *args, str(candidate)])
    except Exception as exc:  # bridge errors are useful diagnostics to callers
        return ValidationResult(False, f"{kind} validation failed to run: {exc}")
    diagnostic = (getattr(completed, "stderr", "") or getattr(completed, "stdout", "") or "").strip()
    if getattr(completed, "returncode", 1) == 0:
        return ValidationResult(True, f"{kind} is valid.")
    return ValidationResult(False, f"{kind} validation failed (exit {completed.returncode}): {diagnostic or 'no diagnostic output'}")


def validate_tpr(path: str | Path, bridge: Any = wsl_bridge) -> ValidationResult:
    return _validate_binary(path, "TPR", ["-s"], bridge)


def validate_checkpoint(path: str | Path, bridge: Any = wsl_bridge) -> ValidationResult:
    return _validate_binary(path, "Checkpoint", ["-cp"], bridge)


def validate_stage_outputs(
    outputs: Mapping[str, str | Path],
    ligand_name: str | None = None,
    bridge: Any = wsl_bridge,
) -> ValidationResult:
    """Validate all supplied stage outputs and summarize individual results."""

    validators = {
        "structure": validate_gro,
        "gro": validate_gro,
        "topology": lambda value: validate_topology(value, ligand_name),
        "top": lambda value: validate_topology(value, ligand_name),
        "tpr": lambda value: validate_tpr(value, bridge),
        "checkpoint": lambda value: validate_checkpoint(value, bridge),
        "cpt": lambda value: validate_checkpoint(value, bridge),
    }
    details = {name: validators[name](value) for name, value in outputs.items() if name in validators}
    if not details:
        return ValidationResult(False, "No recognized MD stage outputs were supplied.", details)
    failed = [name for name, result in details.items() if not result.ok]
    message = "Stage outputs are valid." if not failed else "Invalid stage outputs: " + ", ".join(failed)
    return ValidationResult(not failed, message, details)
