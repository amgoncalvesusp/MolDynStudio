"""Extract all bound copies using a reviewed MOL2 chemical template.

Only Open Babel creates hydrogen coordinates. Observed heavy coordinates and
the template's named graph/protonation are invariants, checked after conversion.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import math
from pathlib import Path
import re
from uuid import uuid4

from core import wsl_bridge
from core.structure_audit import audit_structure, file_sha256
from utils.structure_validation import AMINO_ACIDS, ensure_noncovalent_complex

WATERS = frozenset({"HOH", "WAT", "SOL", "TIP", "TIP3", "TIP4"})
ELEMENTS = frozenset({"H", "B", "C", "N", "O", "F", "P", "S", "Cl", "Br", "I", "Si", "Se"})


@dataclass(frozen=True)
class Mol2Atom:
    number: int
    name: str
    coordinates: tuple[float, float, float]
    atom_type: str
    charge: float
    formal_charge: int = 0

    @property
    def element(self) -> str:
        return self.atom_type.split(".")[0].capitalize()


@dataclass(frozen=True)
class Mol2Molecule:
    atoms: tuple[Mol2Atom, ...]
    bonds: tuple[tuple[int, int, str], ...]


@dataclass(frozen=True)
class ComplexLigandInputs:
    protein_path: Path
    ligand_path: Path
    ligand_copies: tuple[Path, ...]
    provenance_path: Path


def _sections(text: str) -> dict[str, tuple[str, ...]]:
    chunks = text.split("@<TRIPOS>")[1:]
    labels = tuple(chunk.splitlines()[0].strip() for chunk in chunks)
    if len(labels) != len(set(labels)) or not {"MOLECULE", "ATOM", "BOND"} <= set(labels):
        raise ValueError("MOL2 must contain one molecule with atom and bond sections.")
    return {label: tuple(line.strip() for line in chunk.splitlines()[1:]
                        if line.strip() and not line.lstrip().startswith("#"))
            for label, chunk in zip(labels, chunks)}


def _formal_charges(lines: tuple[str, ...]) -> dict[int, int]:
    if len(lines) % 2:
        raise ValueError("Unsupported MOL2 atom attributes; retain explicit formal charges only.")
    entries = tuple((lines[i].split(), lines[i + 1].split()) for i in range(0, len(lines), 2))
    if any(len(atom) != 2 or atom[1] != "1" or len(attr) != 2 or attr[0] != "charge"
           for atom, attr in entries):
        raise ValueError("Unsupported MOL2 atom attributes; retain explicit formal charges only.")
    charges = {int(atom[0]): int(attr[1]) for atom, attr in entries}
    if len(charges) != len(entries):
        raise ValueError("Duplicate MOL2 formal charge attributes.")
    return charges


def _parse_atom(line: str, charges: dict[int, int]) -> Mol2Atom:
    fields = line.split()
    if len(fields) != 9 or fields[6] != "1":
        raise ValueError("MOL2 requires explicit charges and one ligand residue.")
    result = Mol2Atom(int(fields[0]), fields[1], tuple(map(float, fields[2:5])),
                      fields[5], float(fields[8]), charges.get(int(fields[0]), 0))
    if (result.number < 1 or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_+\-]{0,15}", result.name)
            or result.element not in ELEMENTS
            or not all(math.isfinite(v) for v in (*result.coordinates, result.charge))):
        raise ValueError("MOL2 contains invalid atom names, elements or nonfinite numeric values.")
    return result


def _validate_graph(molecule: Mol2Molecule) -> None:
    numbers = {atom.number for atom in molecule.atoms}
    edges = tuple(frozenset((a, b)) for a, b, _ in molecule.bonds)
    if len(set(edges)) != len(edges) or any(len(edge) != 2 or not edge <= numbers for edge in edges):
        raise ValueError("MOL2 has duplicate, self or dangling bonds.")
    reached = frozenset((molecule.atoms[0].number,))
    for _ in molecule.atoms:
        expanded = reached | frozenset(value for edge in edges if edge & reached for value in edge)
        if expanded == reached:
            break
        reached = expanded
    if reached != numbers:
        raise ValueError("MOL2 must describe one connected ligand molecule.")
    _hydrogen_groups(molecule)


def read_mol2(path: str | Path, *, allow_unnamed_hydrogens=False) -> Mol2Molecule:
    """Read the narrow explicit, single-residue MOL2 contract used by this adapter."""
    source = Path(path)
    if source.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("MOL2 exceeds the 4 MiB ligand input limit.")
    sections = _sections(source.read_text(encoding="utf-8-sig"))
    charges = _formal_charges(sections.get("UNITY_ATOM_ATTR", ()))
    parsed = tuple(_parse_atom(line, charges) for line in sections["ATOM"])
    # Open Babel names newly added hydrogens "H". Their atom IDs and parent
    # bonds remain unique; only this generated intermediate gets local names.
    atoms = tuple(replace(a, name=f"generated_H{a.number}") if allow_unnamed_hydrogens and a.element == "H"
                  else a for a in parsed)
    raw_bonds = tuple(line.split() for line in sections["BOND"])
    if not atoms or any(len(line) != 4 or line[3] not in {"1", "2", "3", "ar", "am"} for line in raw_bonds):
        raise ValueError("MOL2 has no atoms or unsupported bond records.")
    if len(sections["MOLECULE"]) < 4:
        raise ValueError("MOL2 molecule header is incomplete.")
    counts = sections["MOLECULE"][1].split()
    if len(counts) < 2 or (int(counts[0]), int(counts[1])) != (len(atoms), len(raw_bonds)):
        raise ValueError("MOL2 declared atom/bond counts do not match its records.")
    if (len({a.number for a in atoms}) != len(atoms) or len({a.name for a in atoms}) != len(atoms)
            or len({line[0] for line in raw_bonds}) != len(raw_bonds)
            or not set(charges) <= {a.number for a in atoms}):
        raise ValueError("MOL2 has duplicate identities or dangling formal charges.")
    molecule = Mol2Molecule(atoms, tuple((int(v[1]), int(v[2]), v[3]) for v in raw_bonds))
    _validate_graph(molecule)
    return molecule


def _hydrogen_groups(molecule: Mol2Molecule) -> dict[str, tuple[Mol2Atom, ...]]:
    atoms = {atom.number: atom for atom in molecule.atoms}
    hydrogens = tuple(atom for atom in molecule.atoms if atom.element == "H")
    parents = {atom.number: tuple(b if a == atom.number else a for a, b, _ in molecule.bonds
                                   if atom.number in (a, b)) for atom in hydrogens}
    if any(len(neighbors) != 1 or atoms[neighbors[0]].element == "H" for neighbors in parents.values()):
        raise ValueError("Each MOL2 hydrogen must have exactly one heavy-atom bond.")
    return {heavy.name: tuple(h for h in hydrogens if parents[h.number][0] == heavy.number)
            for heavy in molecule.atoms if heavy.element != "H"}


def _write_mol2(path: Path, molecule: Mol2Molecule) -> None:
    numbers = {atom.number: i for i, atom in enumerate(molecule.atoms, 1)}
    header = ["@<TRIPOS>MOLECULE", "BOUND_LIGAND", f"{len(numbers)} {len(molecule.bonds)} 1 0 0",
              "SMALL", "USER_CHARGES", "@<TRIPOS>ATOM"]
    atoms = [f"{numbers[a.number]:7d} {a.name:<8} {a.coordinates[0]:12.5f} {a.coordinates[1]:12.5f} "
             f"{a.coordinates[2]:12.5f} {a.atom_type:<8} 1 LIG {a.charge:.8f}" for a in molecule.atoms]
    formal = [line for a in molecule.atoms if a.formal_charge
              for line in (f"{numbers[a.number]} 1", f"charge {a.formal_charge}")]
    bonds = [f"{i} {numbers[a]} {numbers[b]} {order}" for i, (a, b, order) in enumerate(molecule.bonds, 1)]
    attributes = ["@<TRIPOS>UNITY_ATOM_ATTR", *formal] if formal else []
    path.write_text("\n".join([*header, *atoms, *attributes, "@<TRIPOS>BOND", *bonds,
                               "@<TRIPOS>SUBSTRUCTURE", "1 LIG 1"]) + "\n", encoding="ascii")


def _source_contract(source: Path, residue_name: str, excluded: tuple[str, ...]):
    ensure_noncovalent_complex(source)
    audit = audit_structure(source)
    if audit.covalent_links:
        raise ValueError("A covalent polymer/heterogen connection requires a validated complete topology; do not split it.")
    if audit.blocked_reasons or len(audit.model_ids) != 1 or audit.altloc_groups or audit.exact_duplicate_lines:
        raise ValueError("Resolve coordinate models, alternate conformers and duplicate atoms before extracting ligands.")
    if len({a.serial for a in audit.atoms}) != len(audit.atoms):
        raise ValueError("Duplicate PDB atom serials make connectivity ambiguous.")
    if len({a.identity for a in audit.atoms}) != len(audit.atoms):
        raise ValueError("Duplicate PDB atom identities make residue membership and LINK endpoints ambiguous.")
    names = {a.identity.residue_name for a in audit.atoms}
    polymer_names = AMINO_ACIDS | {r.identity.residue_name for r in audit.residues if r.polymer}
    if (residue_name not in names or residue_name in polymer_names | WATERS
            or set(excluded) - names or set(excluded) & (polymer_names | WATERS | {residue_name})):
        raise ValueError("Choose an existing ligand residue; exclusions cannot remove protein, water or the selected ligand.")
    unsupported = names - AMINO_ACIDS - WATERS - {residue_name} - set(excluded)
    if unsupported:
        raise ValueError("Explicit exclusions or separate validated parameters are required for: " + ", ".join(sorted(unsupported)))
    if not names & AMINO_ACIDS:
        raise ValueError("The complex contains no supported protein residues.")
    identities = tuple(dict.fromkeys(a.identity.residue_id for a in audit.atoms if a.identity.residue_name == residue_name))
    copies = tuple(tuple(a for a in audit.atoms if a.identity.residue_id == identity) for identity in identities)
    return audit, copies


def _pdb_edges(lines: tuple[str, ...]) -> frozenset[frozenset[int]]:
    records = tuple(tuple(int(line[i:i + 5]) for i in range(6, len(line), 5) if line[i:i + 5].strip())
                    for line in lines if line.startswith("CONECT"))
    atom_serials = {(line[12:16].strip(), line[17:20], line[21:27]): int(line[6:11]) for line in lines
                    if line.startswith(("ATOM  ", "HETATM"))}
    links = tuple(((line[12:16].strip(), line[17:20], line[21:27]),
                   (line[42:46].strip(), line[47:50], line[51:57])) for line in lines if line.startswith("LINK  "))
    if any(endpoint not in atom_serials for endpoints in links for endpoint in endpoints):
        raise ValueError("A PDB LINK endpoint is missing; review source connectivity before extraction.")
    return (frozenset(frozenset((record[0], target)) for record in records for target in record[1:])
            | frozenset(frozenset(atom_serials[endpoint] for endpoint in endpoints) for endpoints in links))


def _copy_contract(copy, template: Mol2Molecule, edges) -> None:
    heavy = {atom.name: atom for atom in template.atoms if atom.element != "H"}
    observed = {atom.identity.atom_name: atom for atom in copy}
    if len(observed) != len(copy) or set(observed) != set(heavy):
        raise ValueError("Ligand atom names must exactly match template heavy atoms; review missing, extra or observed hydrogen records.")
    if any(observed[name].element.capitalize() != atom.element for name, atom in heavy.items()):
        raise ValueError("Ligand/template atom elements disagree.")
    by_number = {atom.number: atom for atom in template.atoms}
    heavy_bonds = tuple((by_number[a].name, by_number[b].name) for a, b, _ in template.bonds
                        if by_number[a].element != "H" and by_number[b].element != "H")
    for a, b in heavy_bonds:
        distance = math.dist(observed[a].coordinates, observed[b].coordinates)
        if not 0.5 < distance < 3.0:
            raise ValueError("Template bonded-atom distance is implausible in the bound pose; check atom mapping/geometry.")
    serials = {atom.serial for atom in copy}
    involved = frozenset(edge for edge in edges if edge & serials)
    if any(not edge <= serials for edge in involved):
        raise ValueError("A selected ligand has an inter-residue covalent bond; splitting would lose connectivity.")
    expected = frozenset(frozenset((observed[a].serial, observed[b].serial)) for a, b in heavy_bonds)
    if involved and involved != expected:
        raise ValueError("PDB ligand connectivity disagrees with the reviewed template graph.")


def _protein_lines(lines, retained_serials):
    retained_keys = {(line[12:16].strip(), line[17:20], line[21:27]) for line in lines
                     if line.startswith(("ATOM  ", "HETATM")) and int(line[6:11]) in retained_serials}
    kept = []
    last_atom_kept = False
    for line in lines:
        record = line[:6].strip()
        if record in {"ATOM", "HETATM"}:
            last_atom_kept = int(line[6:11]) in retained_serials
            if last_atom_kept:
                kept.append(line)
        elif record == "TER":
            if last_atom_kept:
                kept.append(line)
            last_atom_kept = False
        elif record == "CONECT":
            ids = tuple(int(line[i:i + 5]) for i in range(6, len(line), 5) if line[i:i + 5].strip())
            targets = tuple(v for v in ids[1:] if v in retained_serials)
            if ids and ids[0] in retained_serials and targets:
                kept.append("CONECT" + "".join(f"{v:5d}" for v in (ids[0], *targets)))
        elif record == "LINK":
            endpoints = ((line[12:16].strip(), line[17:20], line[21:27]),
                         (line[42:46].strip(), line[47:50], line[51:57]))
            if all(key in retained_keys for key in endpoints):
                kept.append(line)
        elif record == "ANISOU" and int(line[6:11]) in retained_serials:
            kept.append(line)
        elif record in {"CRYST1", "SSBOND", "ORIGX1", "ORIGX2", "ORIGX3", "SCALE1", "SCALE2", "SCALE3"}:
            kept.append(line)
    return "\n".join(["REMARK 900 DERIVED PROTEIN/WATER INPUT; SEE COMPLEX INPUT PROVENANCE.", *kept, "END"]) + "\n"


def _generated_coordinates(generated: Mol2Molecule, template: Mol2Molecule, observed):
    heavy = {a.name: a for a in generated.atoms if a.element != "H"}
    if set(heavy) != set(observed):
        raise ValueError("Open Babel changed the heavy-atom names/count.")
    if any(a.element != observed[name].element.capitalize() for name, a in heavy.items()):
        raise ValueError("Open Babel changed a heavy-atom element.")
    if any(math.dist(a.coordinates, observed[name].coordinates) > 0.0001 for name, a in heavy.items()):
        raise ValueError("Open Babel moved an observed heavy-atom coordinate.")
    old_h, new_h = _hydrogen_groups(template), _hydrogen_groups(generated)
    if {k: len(v) for k, v in old_h.items()} != {k: len(v) for k, v in new_h.items()}:
        raise ValueError("Open Babel hydrogen/protonation pattern differs from the reviewed template.")
    by_number = {a.number: a for a in generated.atoms}
    expected = {a.number: a for a in template.atoms}
    def heavy_edges(molecule, lookup):
        return frozenset(frozenset((lookup[a].name, lookup[b].name)) for a, b, _ in molecule.bonds
                         if lookup[a].element != "H" and lookup[b].element != "H")
    if heavy_edges(generated, by_number) != heavy_edges(template, expected):
        raise ValueError("Open Babel changed the ligand heavy-atom graph.")
    hydrogens = {a.name: b.coordinates for parent in old_h for a, b in zip(old_h[parent], new_h[parent])}
    return {**{name: a.coordinates for name, a in observed.items()}, **hydrogens}


def _build_pose(copy, template, directory, index, conda_env, on_log):
    observed = {a.identity.atom_name: a for a in copy}
    heavy_atoms = tuple(replace(a, coordinates=observed[a.name].coordinates)
                        for a in template.atoms if a.element != "H")
    numbers = {a.number for a in heavy_atoms}
    heavy = Mol2Molecule(heavy_atoms, tuple(b for b in template.bonds if {b[0], b[1]} <= numbers))
    input_path = directory / f"ligand_{index:03d}_heavy.mol2"
    generated_path = directory / f"ligand_{index:03d}_obabel.mol2"
    output = directory / f"ligand_{index:03d}.mol2"
    _write_mol2(input_path, heavy)
    command = ["obabel", "-imol2", wsl_bridge.win_to_wsl(str(input_path)), "-omol2", "-O",
               wsl_bridge.win_to_wsl(str(generated_path)), "-h"]
    if on_log:
        on_log(f"Generating hydrogens for bound ligand copy {index}; keeping observed heavy coordinates.")
    try:
        completed = wsl_bridge.run(command, cwd=str(directory), env_name=conda_env, timeout=120)
    except (OSError, TimeoutError) as exc:
        raise ValueError(f"Open Babel hydrogen generation could not run: {exc}") from exc
    (directory / f"ligand_{index:03d}_obabel.log").write_text(
        (completed.stdout or "") + (completed.stderr or ""), encoding="utf-8")
    if completed.returncode or not generated_path.is_file():
        raise ValueError("Open Babel hydrogen generation failed; inspect the retained conversion log.")
    coordinates = _generated_coordinates(read_mol2(generated_path, allow_unnamed_hydrogens=True), template, observed)
    final = Mol2Molecule(tuple(replace(a, coordinates=coordinates[a.name]) for a in template.atoms), template.bonds)
    _write_mol2(output, final)
    return output


def prepare_complex_inputs(complex_pdb, template_mol2, output_dir, residue_name,
                           excluded_residue_names=(), *, conda_env="moldynstudio", on_log=None) -> ComplexLigandInputs:
    """Preserve all selected ligand copies and waters, with explicit heterogen exclusions.

    The caller must first validate the original coordinate-repair provenance.
    This adapter never modifies that report or substitutes a derived source hash.
    The reviewed MOL2 supplies chemical identity; CONECT verifies its heavy graph
    when available. PDB coordinates alone cannot establish bond orders/protonation.
    """
    if not isinstance(residue_name, str) or not re.fullmatch(r"[A-Za-z0-9]{1,3}", residue_name):
        raise ValueError("Select one explicit PDB ligand residue name.")
    if isinstance(excluded_residue_names, str):
        raise ValueError("Excluded residue names must be an explicit collection.")
    excluded = tuple(excluded_residue_names)
    if any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9]{1,4}", name) for name in excluded):
        raise ValueError("Excluded residue names must be explicit valid PDB identifiers.")
    source, template_path = Path(complex_pdb).resolve(), Path(template_mol2).resolve()
    source_hash, template_hash = file_sha256(source), file_sha256(template_path)
    audit, copies = _source_contract(source, residue_name, excluded)
    template = read_mol2(template_path)
    charge = sum(a.charge for a in template.atoms)
    if abs(charge - round(charge)) > 0.002:
        raise ValueError("Reviewed MOL2 charges do not sum to an integer molecular charge.")
    lines = tuple(source.read_text(encoding="utf-8-sig").splitlines())
    edges = _pdb_edges(lines)
    for copy in copies:
        _copy_contract(copy, template, edges)
    directory = Path(output_dir).resolve() / f"complex_inputs_{uuid4().hex}"
    directory.mkdir(parents=True)
    poses = tuple(_build_pose(copy, template, directory, index, conda_env, on_log)
                  for index, copy in enumerate(copies, 1))
    retained = tuple(a for a in audit.atoms if a.identity.residue_name not in {residue_name, *excluded})
    protein = directory / "protein_and_waters.pdb"
    protein.write_text(_protein_lines(lines, {a.serial for a in retained}), encoding="ascii")
    if file_sha256(source) != source_hash or file_sha256(template_path) != template_hash:
        raise ValueError("Source complex or reviewed ligand template changed during extraction.")
    report = {"schema_version": 1, "source_path": str(source), "source_sha256": source_hash,
              "template_path": str(template_path), "template_sha256": template_hash,
              "residue_name": residue_name, "excluded_residue_names": excluded,
              "excluded_residues": tuple(r.residue_id for r in audit.residues if r.identity.residue_name in excluded),
              "ligand_molecule_count": len(copies), "ligand_residue_ids": tuple(c[0].identity.residue_id for c in copies),
              "source_atom_mapping": tuple({"source_serial": a.serial, "atom_name": a.identity.atom_name,
                                              "residue_id": a.identity.residue_id} for c in copies for a in c),
              "retained_protein_water_atoms": len(retained), "template_net_charge": charge,
              "max_heavy_displacement_angstrom": 0.0, "conda_environment": conda_env,
              "hydrogen_backend": "Open Babel -h (no minimization)",
              "graph_evidence": tuple("PDB CONECT and reviewed MOL2" if any(e & {a.serial for a in c} for e in edges)
                                       else "Reviewed MOL2 only; no independent PDB CONECT" for c in copies),
              "artifacts": {p.name: file_sha256(p) for p in (protein, *poses)},
              "limitations": ["Extraction does not validate force-field parameters or physiological protonation.",
                              "Hydrogen coordinates are generated, not observed; template charge and named graph are retained."]}
    provenance = directory / "complex_input_provenance.json"
    provenance.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return ComplexLigandInputs(protein, poses[0], poses[1:], provenance)
