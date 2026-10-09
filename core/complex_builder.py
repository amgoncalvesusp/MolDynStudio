"""Coordination helpers for assembling protein--ligand project inputs."""

from __future__ import annotations

from pathlib import Path
import math
import re

from core.artifact_validation import validate_gro, validate_topology
from utils.topology_builder import (
    LigandParamWorker,
    LigandParams,
    LigandTopologyArtifacts,
    normalize_acpype_outputs,
    read_molecule_name_from_itp,
)


ENVIRONMENT_MOLECULES = frozenset({"SOL", "WAT", "HOH", "NA", "CL", "K", "CA", "MG", "ZN", "SOD", "CLA", "POT", "CAL", "MG2", "CES"})
PARAMETER_SECTIONS = frozenset({"atomtypes", "bondtypes", "pairtypes", "angletypes", "dihedraltypes", "constrainttypes", "nonbond_params", "cmaptypes"})


def _is_environment_molecule(name: str) -> bool:
    return name.upper() in ENVIRONMENT_MOLECULES or name.lower().startswith("ion_chain_")


def _gro_parts(path: str | Path) -> tuple[list[str], str]:
    """Read a validated GRO and return atom records plus its box line."""
    candidate = Path(path)
    result = validate_gro(candidate)
    if not result.ok:
        raise ValueError(result.message)
    lines = candidate.read_text(encoding="utf-8").splitlines()
    count = int(lines[1].strip())
    return lines[2 : 2 + count], lines[2 + count]


def combine_gro(
    protein_gro: str | Path,
    ligand_gro: str | Path,
    output_gro: str | Path,
) -> Path:
    """Insert all ligand poses before the existing water/ion tail."""
    protein_atoms, protein_box = _gro_parts(protein_gro)
    ligand_atoms, _ligand_box = _gro_parts(ligand_gro)
    insertion = next((i for i, row in enumerate(protein_atoms) if _is_environment_molecule(row[5:10].strip())), len(protein_atoms))
    if any(not _is_environment_molecule(row[5:10].strip()) for row in protein_atoms[insertion:]):
        raise ValueError("Protein coordinates contain interleaved solvent/ions; a contiguous molecular order is required.")
    combined = protein_atoms[:insertion] + ligand_atoms + protein_atoms[insertion:]
    output = Path(output_gro)
    output.parent.mkdir(parents=True, exist_ok=True)
    text = "Protein-ligand complex\n{}\n{}\n{}\n".format(
        len(protein_atoms) + len(ligand_atoms),
        "\n".join(combined),
        protein_box,
    )
    output.write_text(text, encoding="utf-8", newline="\n")
    validation = validate_gro(output)
    if not validation.ok:
        raise ValueError(f"Generated complex GRO is invalid: {validation.message}")
    return output


def patch_topology_for_ligand(
    topology: str | Path,
    ligand_itp: str | Path,
    output_topology: str | Path | None = None,
    molecule_name: str | None = None,
    *,
    molecule_count: int = 1,
    parameter_includes: tuple[Path, ...] = (),
) -> Path:
    """Insert ligand include and molecule record into a system topology.

    The operation is idempotent and patches the input topology in place when no
    output path is supplied. Supplying ``output_topology`` writes a separate
    patched file.
    """
    source = Path(topology)
    destination = Path(output_topology) if output_topology is not None else source
    name = molecule_name or read_molecule_name_from_itp(ligand_itp)
    if type(molecule_count) is not int or molecule_count < 1:
        raise ValueError("Ligand molecule count must be a positive integer.")
    for path in parameter_includes:
        text = Path(path).read_text(encoding="utf-8")
        sections = set(re.findall(r"(?im)^\s*\[\s*([^]]+?)\s*\]", text))
        if not sections or not sections.issubset(PARAMETER_SECTIONS) or re.search(r"(?im)^\s*#include", text):
            raise ValueError("Ligand parameter includes must contain GROMACS parameter sections only.")
    lines = source.read_text(encoding="utf-8").splitlines()

    # GROMACS resolves this project-relative include from the topology folder.
    ligand_path = Path(ligand_itp)
    try:
        include_path = ligand_path.resolve().relative_to(destination.parent.resolve()).as_posix()
    except ValueError:
        include_path = ligand_path.name
    if ligand_path.parent.name.lower() != "ligand" and not include_path.lower().startswith("ligand/"):
        include_path = f"ligand/{include_path}"
    include_paths = []
    for path in parameter_includes:
        try:
            relative = Path(path).resolve().relative_to(destination.parent.resolve()).as_posix()
        except ValueError as exc:
            raise ValueError("Ligand parameter includes must be staged within the project.") from exc
        include_paths.append(relative)
    include_paths.append(include_path)
    include_pattern = re.compile(r"^\s*#include\s+[\"<]([^\">]+)[\">]")
    lines = [line for line in lines if not ((match := include_pattern.match(line)) and match.group(1).replace("\\", "/") in include_paths)]
    forcefield_lines = [i for i, line in enumerate(lines) if (match := include_pattern.match(line)) and Path(match.group(1)).name.lower() == "forcefield.itp"]
    insertion = forcefield_lines[-1] + 1 if forcefield_lines else 0
    if any(re.match(r"^\s*\[\s*moleculetype\s*\]", line, re.I) for line in lines[:insertion]):
        raise ValueError("Force-field parameters must precede every molecule definition.")
    lines[insertion:insertion] = [f'#include "{path}"' for path in include_paths]

    section_start = next((i for i, line in enumerate(lines) if re.match(r"^\s*\[\s*molecules\s*\]", line, re.I)), None)
    if section_start is None:
        raise ValueError("Topology is missing the [ molecules ] section")
    section_end = next((i for i in range(section_start + 1, len(lines)) if re.match(r"^\s*\[", lines[i])), len(lines))
    molecule_re = re.compile(rf"^\s*{re.escape(name)}\s+\d+(?:\s*;.*)?$", re.I)
    existing = [line for line in lines[section_start + 1:section_end] if molecule_re.match(line)]
    if existing and (len(existing) != 1 or int(existing[0].split()[1]) != molecule_count):
        raise ValueError("Existing ligand molecule count conflicts with the supplied coordinate copies.")
    if not existing:
        insert_at = next((i for i in range(section_start + 1, section_end)
                          if lines[i].split() and _is_environment_molecule(lines[i].split()[0])), section_end)
        lines.insert(insert_at, f"{name:<18} {molecule_count}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    validation = validate_topology(destination, ligand_name=name)
    if not validation.ok:
        raise ValueError(f"Patched topology is invalid: {validation.message}")
    # Reparse the generated text to ensure sections remain structurally readable.
    reparsed = destination.read_text(encoding="utf-8")
    if not re.search(r"^\s*\[\s*system\s*\]", reparsed, re.I | re.M) or not re.search(r"^\s*\[\s*molecules\s*\]", reparsed, re.I | re.M):
        raise ValueError("Patched topology could not be reparsed")
    return destination


def validate_ligand_artifacts(artifacts: LigandTopologyArtifacts, *, expected_copies: int, net_charge: int) -> None:
    """Validate the topology atom order, molecular charge and all coordinate copies."""
    if type(artifacts.molecule_count) is not int or artifacts.molecule_count != expected_copies:
        raise ValueError("Ligand molecule count does not match the requested copies.")
    atoms, _box = _gro_parts(artifacts.ligand_gro)
    section, rows = "", []
    for raw in artifacts.ligand_itp.read_text(encoding="utf-8").splitlines():
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        if line.startswith("["):
            section = line.strip("[] ").lower()
        elif section == "atoms" and not line.startswith("#"):
            fields = line.split()
            if len(fields) < 8:
                raise ValueError("Ligand ITP contains incomplete atom records.")
            rows.append(fields)
    if not rows or len(atoms) != len(rows) * expected_copies:
        raise ValueError("Ligand coordinate atom count does not match its topology and copies.")
    if [int(row[0]) for row in rows] != list(range(1, len(rows) + 1)):
        raise ValueError("Ligand topology atom numbering must be consecutive.")
    charges = [float(row[6]) for row in rows]
    if not all(math.isfinite(value) for value in charges) or not math.isclose(sum(charges), net_charge, abs_tol=1e-4):
        raise ValueError("Ligand topology charge differs from the reviewed net charge.")
    expected_names = [(row[3], row[4]) for row in rows] * expected_copies
    if [(line[5:10].strip(), line[10:15].strip()) for line in atoms] != expected_names:
        raise ValueError("Ligand coordinate atom order/names differ from the topology.")


def normalize_ligand_topology(
    acpype_dir: str | Path, project_dir: str | Path
) -> LigandTopologyArtifacts:
    """Normalize ACPYPE output for consumption by the complex pipeline."""

    return normalize_acpype_outputs(acpype_dir, project_dir)


class LigandTopologyCoordinator:
    """Small integration boundary exposing structured worker results."""

    def __init__(self, params: LigandParams, parent=None):
        self.worker = LigandParamWorker(params, parent=parent)
        self.artifacts: LigandTopologyArtifacts | None = None
        self.worker.artifacts_ready.connect(self._set_artifacts)

    def _set_artifacts(self, artifacts: object) -> None:
        if isinstance(artifacts, LigandTopologyArtifacts):
            self.artifacts = artifacts

    def start(self) -> LigandParamWorker:
        self.worker.start()
        return self.worker
