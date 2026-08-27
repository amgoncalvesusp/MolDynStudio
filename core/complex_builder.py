"""Coordination helpers for assembling protein--ligand project inputs."""

from __future__ import annotations

from pathlib import Path
import re

from core.artifact_validation import validate_gro, validate_topology
from utils.topology_builder import (
    LigandParamWorker,
    LigandParams,
    LigandTopologyArtifacts,
    normalize_acpype_outputs,
    read_molecule_name_from_itp,
)


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
    """Assemble a noncovalent complex, keeping protein atoms before ligand atoms."""
    protein_atoms, protein_box = _gro_parts(protein_gro)
    ligand_atoms, _ligand_box = _gro_parts(ligand_gro)
    output = Path(output_gro)
    output.parent.mkdir(parents=True, exist_ok=True)
    text = "Protein-ligand complex\n{}\n{}\n{}\n".format(
        len(protein_atoms) + len(ligand_atoms),
        "\n".join(protein_atoms + ligand_atoms),
        protein_box,
    )
    output.write_text(text, encoding="utf-8")
    validation = validate_gro(output)
    if not validation.ok:
        raise ValueError(f"Generated complex GRO is invalid: {validation.message}")
    return output


def patch_topology_for_ligand(
    topology: str | Path,
    ligand_itp: str | Path,
    output_topology: str | Path | None = None,
    molecule_name: str | None = None,
) -> Path:
    """Insert ligand include and molecule record into a system topology.

    The operation is idempotent and patches the input topology in place when no
    output path is supplied. Supplying ``output_topology`` writes a separate
    patched file.
    """
    source = Path(topology)
    destination = Path(output_topology) if output_topology is not None else source
    name = molecule_name or read_molecule_name_from_itp(ligand_itp)
    lines = source.read_text(encoding="utf-8").splitlines()

    # GROMACS resolves this project-relative include from the topology folder.
    ligand_path = Path(ligand_itp)
    try:
        include_path = ligand_path.resolve().relative_to(destination.parent.resolve()).as_posix()
    except ValueError:
        include_path = ligand_path.name
    if ligand_path.parent.name.lower() != "ligand" and not include_path.lower().startswith("ligand/"):
        include_path = f"ligand/{include_path}"
    include_line = f'#include "{include_path}"'
    include_pattern = re.compile(r"^\s*#include\s+[\"<]([^\">]+)[\">]")
    include_names = [m.group(1).replace("\\", "/") for line in lines if (m := include_pattern.match(line))]
    if include_path not in include_names:
        forcefield_indices = [i for i, value in enumerate(include_names) if "forcefield" in value.lower()]
        if forcefield_indices:
            # Locate the source line corresponding to the last force-field include.
            seen = -1
            for i, line in enumerate(lines):
                match = include_pattern.match(line)
                if match:
                    seen += 1
                    if seen == forcefield_indices[-1]:
                        lines.insert(i + 1, include_line)
                        break
        else:
            first_include = next((i for i, line in enumerate(lines) if include_pattern.match(line)), -1)
            lines.insert(first_include + 1 if first_include >= 0 else 0, include_line)

    section_start = next((i for i, line in enumerate(lines) if re.match(r"^\s*\[\s*molecules\s*\]", line, re.I)), None)
    if section_start is None:
        raise ValueError("Topology is missing the [ molecules ] section")
    section_end = next((i for i in range(section_start + 1, len(lines)) if re.match(r"^\s*\[", lines[i])), len(lines))
    molecule_re = re.compile(rf"^\s*{re.escape(name)}\s+\d+(?:\s*;.*)?$", re.I)
    if not any(molecule_re.match(line) for line in lines[section_start + 1 : section_end]):
        solvent_re = re.compile(r"^\s*(?:SOL|WAT|NA|CL|K|CA|MG|ZN|SOD|CLA)\b", re.I)
        insert_at = next((i for i in range(section_start + 1, section_end) if solvent_re.match(lines[i])), section_end)
        lines.insert(insert_at, f"{name:<18} 1")

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    validation = validate_topology(destination, ligand_name=name)
    if not validation.ok:
        raise ValueError(f"Patched topology is invalid: {validation.message}")
    # Reparse the generated text to ensure sections remain structurally readable.
    reparsed = destination.read_text(encoding="utf-8")
    if not re.search(r"^\s*\[\s*system\s*\]", reparsed, re.I | re.M) or not re.search(r"^\s*\[\s*molecules\s*\]", reparsed, re.I | re.M):
        raise ValueError("Patched topology could not be reparsed")
    return destination


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
