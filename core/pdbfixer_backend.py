"""Explicit, provenance-preserving heavy-atom reconstruction; no system preparation.

Inputs have already passed the caller's model/conformer/chemistry decisions.
Unknown components are retained as fixed steric context, never parameterized or
silently converted. PDBFixer's local coordinate relaxation is not production MD.
"""
from __future__ import annotations

from dataclasses import dataclass
import importlib.metadata
import importlib.util
from io import StringIO
import math
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Callable, Mapping


PROTEIN = frozenset("ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL".split())


@dataclass(frozen=True)
class BackendRepairResult:
    output_path: Path
    modeled_atoms: tuple[dict, ...]
    residue_mapping: tuple[dict, ...]
    added_residues: tuple[dict, ...]
    preserved_atom_count: int
    max_observed_displacement_angstrom: float
    backend_versions: dict[str, str]
    warnings: tuple[str, ...]


def backend_available() -> bool:
    """Check optional dependencies without importing OpenMM or accessing a network."""
    return all(importlib.util.find_spec(name) is not None for name in ("pdbfixer", "openmm"))


def _key(line: str) -> tuple[str, ...]:
    return (line[21:22].strip(), line[22:26].strip(), line[26:27].strip(),
            line[17:20].strip(), line[12:16].strip())


def _atom_key(atom) -> tuple[str, ...]:
    residue = atom.residue
    return (residue.chain.id.strip(), residue.id, residue.insertionCode.strip(), residue.name, atom.name)


def _identity(key: tuple[str, ...]) -> dict:
    fields = ("chain_id", "residue_id", "insertion_code", "residue_name", "atom_name")
    return dict(zip(fields, key))


def _coordinates(line: str) -> tuple[float, ...]:
    values = tuple(float(line[index:index + 8]) for index in (30, 38, 46))
    if not all(math.isfinite(value) for value in values):
        raise ValueError("PDB coordinates must be finite.")
    return values


def _read_source(path: Path) -> tuple[tuple[str, ...], dict]:
    lines = tuple(path.read_text(encoding="utf-8").splitlines())
    if sum(line.startswith("MODEL ") for line in lines) > 1:
        raise ValueError("Select one model before reconstruction.")
    atom_lines = tuple(line for line in lines if line.startswith(("ATOM  ", "HETATM")))
    if not atom_lines:
        raise ValueError("The input contains no PDB atoms.")
    if any(line[16:17].strip() for line in atom_lines):
        raise ValueError("Resolve alternate locations before reconstruction.")
    if len({_key(line) for line in atom_lines}) != len(atom_lines):
        raise ValueError("Duplicate atom identities must be resolved before reconstruction.")
    if len({int(line[6:11]) for line in atom_lines}) != len(atom_lines):
        raise ValueError("Duplicate atom serial numbers prevent preserving connectivity.")
    for line in atom_lines:
        _coordinates(line)
    return lines, {_key(line): line for line in atom_lines}


def _gap_plan(topology, gaps, polymer_keys) -> tuple[dict, tuple[tuple, ...]]:
    """Resolve approved sequence slots, never infer gaps from author numbering."""
    chains = tuple(topology.chains())
    protein_chains = tuple(c for c in chains if any(r.name in PROTEIN for r in c.residues()))
    ids = tuple(c.id.strip() for c in protein_chains)
    if gaps and len(set(ids)) != len(ids):
        raise ValueError("Repeated protein chain IDs make gap placement ambiguous.")
    lookup = {c.id.strip(): c for c in protein_chains}
    approved = {}
    added = []
    occupied = {(r.chain.id.strip(), r.id, r.insertionCode.strip()) for r in topology.residues()}
    for gap in gaps:
        chain = lookup.get(str(gap.get("chain_id", "")).strip())
        if chain is None:
            raise ValueError("An approved gap refers to an unknown protein chain.")
        residues = tuple(chain.residues())
        if any((r.chain.id.strip(), r.id, r.insertionCode.strip(), r.name) not in polymer_keys for r in residues):
            raise ValueError("Gap placement is ambiguous: non-polymer components share the protein segment. Preserve or review TER boundaries first.")
        index = gap.get("insert_before")
        names = tuple(gap.get("resnames", ()))
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index <= len(residues):
            raise ValueError("Gap insert_before must identify a retained-chain residue position.")
        if not names or any(name not in PROTEIN for name in names):
            raise ValueError("Only explicit standard amino-acid sequences can be reconstructed.")
        if (chain.index, index) in approved:
            raise ValueError("Multiple approved gaps target the same insertion position.")
        first = int(residues[index].id) - len(names) if index < len(residues) else int(residues[-1].id) + 1
        numbers = tuple(range(first, first + len(names)))
        if (index and first <= int(residues[index - 1].id)) or any(not 0 <= n <= 9999 for n in numbers):
            raise ValueError("No unambiguous residue numbering space for this gap; supply a reviewed renumbered input.")
        new_keys = tuple((chain.id.strip(), str(n), "", name) for n, name in zip(numbers, names))
        if any(key[:3] in occupied for key in new_keys):
            raise ValueError("New residue identity collides with retained author numbering.")
        if index and any(a.name == "OXT" for a in residues[index - 1].atoms()):
            raise ValueError("Gap extension would remove an observed OXT atom; review terminal chemistry first.")
        occupied = occupied.union(key[:3] for key in new_keys)
        approved = {**approved, (chain.index, index): list(names)}
        added.extend(new_keys)
    return approved, tuple(added)


def _expected_residues(topology, approved) -> tuple[tuple, ...]:
    expected = []
    for chain in topology.chains():
        residues = tuple(chain.residues())
        for index in range(len(residues) + 1):
            names = approved.get((chain.index, index), ())
            first = int(residues[index].id) - len(names) if index < len(residues) else int(residues[-1].id) + 1
            expected.extend((chain.id.strip(), str(first + offset), "", name) for offset, name in enumerate(names))
            if index < len(residues):
                r = residues[index]
                expected.append((chain.id.strip(), r.id, r.insertionCode.strip(), r.name))
    return tuple(expected)


def _validate_result(fixer, original, expected, added_residues) -> tuple[dict, float]:
    from openmm import unit

    residues = tuple((r.chain.id.strip(), r.id, r.insertionCode.strip(), r.name) for r in fixer.topology.residues())
    if residues != expected:
        raise ValueError("Reconstruction changed residue identity or order; output rejected.")
    positions = fixer.positions.value_in_unit(unit.angstrom)
    result = {_atom_key(atom): tuple(float(v) for v in positions[atom.index]) for atom in fixer.topology.atoms()}
    if len(result) != fixer.topology.getNumAtoms() or not set(original).issubset(result):
        raise ValueError("Reconstruction removed or renamed an observed atom; output rejected.")
    if not all(math.isfinite(value) for values in result.values() for value in values):
        raise ValueError("Reconstruction produced nonfinite coordinates.")
    displacement = max(math.dist(_coordinates(line), result[key]) for key, line in original.items())
    if displacement > 1e-6:
        raise ValueError("Reconstruction moved observed coordinates; output rejected.")
    for a, b in fixer.topology.bonds():
        keys = (_atom_key(a), _atom_key(b))
        if not all(key in original for key in keys):
            distance = math.dist(result[keys[0]], result[keys[1]])
            if not 0.8 <= distance <= 2.3:
                raise ValueError(f"Implausible modeled bond ({distance:.3f} A): {keys}.")
    for chain in fixer.topology.chains():
        chain_residues = tuple(chain.residues())
        for left, right in zip(chain_residues, chain_residues[1:]):
            pair = ((_atom_key(next(iter(left.atoms()))))[:4], (_atom_key(next(iter(right.atoms()))))[:4])
            if any(key in added_residues for key in pair):
                c_key, n_key = (*pair[0], "C"), (*pair[1], "N")
                if c_key not in result or n_key not in result or not 1.0 <= math.dist(result[c_key], result[n_key]) <= 1.8:
                    raise ValueError("A reconstructed peptide junction is missing or geometrically implausible.")
    return result, displacement


def _validate_source_links(lines, result_atoms) -> None:
    for line in lines:
        if line.startswith("SSBOND"):
            keys = ((line[15:16].strip(), line[17:21].strip(), line[21:22].strip(), "CYS", "SG"),
                    (line[29:30].strip(), line[31:35].strip(), line[35:36].strip(), "CYS", "SG"))
            if any(key not in result_atoms for key in keys):
                raise ValueError("A retained disulfide refers to a missing sulfur atom.")
            if not 1.8 <= math.dist(*(result_atoms[key] for key in keys)) <= 2.3:
                raise ValueError("A retained disulfide has implausible sulfur-sulfur geometry.")
        elif line.startswith("LINK  "):
            keys = (_key(line), (line[51:52].strip(), line[52:56].strip(), line[56:57].strip(),
                                 line[47:50].strip(), line[42:46].strip()))
            if any(key not in result_atoms for key in keys):
                raise ValueError("A retained LINK refers to an absent atom; review input chemistry.")


def _render_pdb(fixer, lines, original) -> str:
    """Keep observed metadata and chemical links; remap only coordinate serials."""
    from openmm.app import PDBFile

    buffer = StringIO()
    PDBFile.writeFile(fixer.topology, fixer.positions, buffer, keepIds=True)
    rendered = tuple(buffer.getvalue().splitlines())
    output_atoms = tuple(line for line in rendered if line.startswith(("ATOM  ", "HETATM")))
    if len(output_atoms) >= 100000 or any(_key(line) not in original and line[76:78].strip() in ("H", "D") for line in output_atoms):
        raise ValueError("Output exceeds PDB atom limits or unexpectedly adds hydrogen atoms.")
    serials = {_key(line): int(line[6:11]) for line in output_atoms}
    source_serials = {int(line[6:11]): serials[key] for key, line in original.items()}
    header = tuple(line for line in lines if not line.startswith(("ATOM  ", "HETATM", "ANISOU", "CONECT", "TER", "END", "MODEL", "MASTER")))
    coordinates = tuple(
        (original[_key(line)][:6] + line[6:11] + original[_key(line)][11:])
        if line.startswith(("ATOM  ", "HETATM")) and _key(line) in original else line
        for line in rendered if line.startswith(("ATOM  ", "HETATM", "TER"))
    )
    connectivity = []
    for line in lines:
        if not line.startswith("CONECT"):
            continue
        numbers = tuple(int(line[start:start + 5]) for start in range(6, len(line), 5) if line[start:start + 5].strip())
        if any(number not in source_serials for number in numbers):
            raise ValueError("A CONECT record refers to an absent atom; review input connectivity.")
        connectivity.append("CONECT" + "".join(f"{source_serials[number]:5d}" for number in numbers))
    return "\n".join((*header, "REMARK 950 MODELED ATOMS ARE INFERRED; SEE REPAIR PROVENANCE.", *coordinates, *connectivity, "END", ""))


def _build_missing_structure_local(
    input_path: str | Path, output_path: str | Path, *,
    missing_residues: tuple[Mapping, ...] = (), add_heavy_atoms: bool = True,
    seed: int = 2026, on_progress: Callable[[str], None] | None = None,
) -> BackendRepairResult:
    """Build only approved gaps/standard heavy atoms and publish after validation.

    Each gap has chain_id, insert_before (retained chain index), and resnames.
    Stable author IDs must have free integer space; ambiguous renumbering fails.
    Unknown components are preserved, but their chemistry is not repaired.
    """
    source, destination = Path(input_path).resolve(), Path(output_path).resolve()
    if source == destination or destination.exists():
        raise ValueError("Output must be a different, new file; it already exists or matches the source.")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**31:
        raise ValueError("The reconstruction seed must be an integer in [0, 2**31).")
    lines, original = _read_source(source)
    if not backend_available():
        raise RuntimeError("Install PDBFixer and OpenMM in the application's Python environment to reconstruct atoms.")
    from openmm import Platform
    from pdbfixer import PDBFixer

    notify = on_progress or (lambda message: None)
    notify("Inspecting retained atoms and explicitly approved sequence gaps.")
    platforms = tuple(Platform.getPlatform(i).getName() for i in range(Platform.getNumPlatforms()))
    platform_name = "CPU" if "CPU" in platforms else "Reference"
    fixer = PDBFixer(filename=str(source), platform=Platform.getPlatformByName(platform_name))
    if {_atom_key(atom) for atom in fixer.topology.atoms()} != set(original):
        raise ValueError("The PDB reader normalized or discarded atom identities; review input atom naming.")
    modified = {(line[16:17].strip(), line[18:22].strip(), line[22:23].strip(), line[12:15].strip())
                for line in lines if line.startswith("MODRES") and line[24:27].strip() in PROTEIN}
    polymer_keys = frozenset(key[:4] for key, line in original.items()
                             if line.startswith("ATOM  ") or key[3] in PROTEIN or key[:4] in modified)
    approved, added_residues = _gap_plan(fixer.topology, missing_residues, polymer_keys)
    expected = _expected_residues(fixer.topology, approved)
    fixer.missingResidues = approved
    fixer.findMissingAtoms()
    terminals = frozenset((*_atom_key(next(iter(r.atoms())))[:4], name) for r, names in fixer.missingTerminals.items() for name in names)
    if not add_heavy_atoms:
        fixer.missingAtoms, fixer.missingTerminals = {}, {}
    else:
        fixer.missingAtoms = {r: values for r, values in fixer.missingAtoms.items() if r.name in PROTEIN}
        fixer.missingTerminals = {r: values for r, values in fixer.missingTerminals.items() if r.name in PROTEIN}
    unknown = tuple(sorted({r.name for r in fixer.topology.residues() if r.name not in PROTEIN and r.name != "HOH"}))
    if approved or fixer.missingAtoms or fixer.missingTerminals:
        notify("Reconstructing approved heavy atoms with observed coordinates fixed.")
        fixer.addMissingAtoms(seed=seed)
    result_atoms, displacement = _validate_result(fixer, original, expected, added_residues)
    _validate_source_links(lines, result_atoms)
    modeled = tuple({**_identity(key), "reason": "missing_residue" if key[:4] in added_residues else "terminal_atom" if key in terminals else "missing_heavy_atom"}
                    for key in result_atoms if key not in original)
    mapping = tuple({**_identity(key), "origin": "inferred" if key in added_residues else "observed"} for key in expected)
    warnings = ("New coordinates are inferred by local template reconstruction, not experimentally observed or production-MD validated.",)
    if unknown:
        warnings += ("Retained unparameterized components as fixed context: " + ", ".join(unknown) + ". Their chemical completeness is not certified.",)
    output = _render_pdb(fixer, lines, original)
    handle = destination.open("x", encoding="utf-8", newline="\n")
    try:
        with handle:
            handle.write(output)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    notify("Reconstruction finished; atom identities, observed coordinates, and source connectivity preserved.")
    return BackendRepairResult(destination, modeled, mapping, tuple(_identity(key) for key in added_residues),
                               len(original), 0.0 if displacement < 1e-6 else displacement,
                               {"pdbfixer": importlib.metadata.version("pdbfixer"), "openmm": importlib.metadata.version("openmm"), "platform": platform_name, "seed": str(seed)}, warnings)


def build_missing_structure(
    input_path: str | Path, output_path: str | Path, *,
    missing_residues: tuple[Mapping, ...] = (), add_heavy_atoms: bool = True,
    seed: int = 2026, on_progress: Callable[[str], None] | None = None,
) -> BackendRepairResult:
    """Run the native modeling engine in a crash-isolated, offline process.

    Request/result JSON and engine logs remain alongside the output, including
    after a native crash. No OpenMM Context is created in the application's GUI.
    """
    source, destination = Path(input_path).resolve(), Path(output_path).resolve()
    if source == destination or destination.exists():
        raise ValueError("Output must be a different, new file; it already exists or matches the source.")
    _read_source(source)
    if not backend_available():
        raise RuntimeError("Install PDBFixer and OpenMM in the application's Python environment to reconstruct atoms.")
    notify = on_progress or (lambda message: None)
    directory = Path(tempfile.mkdtemp(prefix="reconstruction_", dir=destination.parent))
    request_path = directory / "request.json"
    response_path = directory / "result.json"
    payload = {"input_path": str(source), "output_path": str(destination),
               "missing_residues": missing_residues, "add_heavy_atoms": add_heavy_atoms,
               "seed": seed, "result_path": str(response_path)}
    request_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    route = ["--structure-repair-worker"] if getattr(sys, "frozen", False) else ["-m", "core.repair_worker"]
    command = [sys.executable, *route, str(request_path)]
    notify("Reconstructing approved atoms in an isolated process; observed atoms remain fixed.")
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    with (directory / "engine.log").open("w", encoding="utf-8") as log:
        try:
            completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=600,
                                       cwd=Path(__file__).resolve().parent.parent,
                                       env={**os.environ, "OPENMM_CPU_THREADS": "1"}, **options)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError(f"Reconstruction process failed: {error}. Evidence: {directory}") from error
    if completed.returncode != 0 or not response_path.is_file():
        failure = json.loads(response_path.read_text(encoding="utf-8")) if response_path.is_file() else {}
        message = failure.get("error", f"Native engine exited with code {completed.returncode}")
        exception = ValueError if failure.get("error_type") == "ValueError" else RuntimeError
        raise exception(f"{message}. Evidence: {directory}")
    response = json.loads(response_path.read_text(encoding="utf-8"))
    result = response["result"]
    notify("Reconstruction completed; identity, coordinate, and connectivity checks passed.")
    return BackendRepairResult(Path(result["output_path"]), tuple(result["modeled_atoms"]),
                               tuple(result["residue_mapping"]), tuple(result["added_residues"]),
                               result["preserved_atom_count"], result["max_observed_displacement_angstrom"],
                               result["backend_versions"], tuple(result["warnings"]))
