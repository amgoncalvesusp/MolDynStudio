"""Preserve observed crystal waters outside CHARMM's protein hydrogen builder."""
from __future__ import annotations

from dataclasses import dataclass
import json
import hashlib
import math
from pathlib import Path
import re
from uuid import uuid4

from core.artifact_validation import validate_gro, validate_topology
from core.structure_audit import audit_structure, file_sha256

WATER_NAMES = frozenset({"HOH", "WAT", "SOL"})
UNSUPPORTED_WATERS = frozenset({"DOD", "TIP", "TIP3", "TIP4", "SPC", "T3P", "T4P"})


@dataclass(frozen=True)
class CrystalWaterInputs:
    protein_path: Path
    water_pdb: Path
    water_gro: Path
    water_count: int
    provenance_path: Path


def prepare_crystal_waters(pdb_path, output_dir, *, hydrogen_builder) -> CrystalWaterInputs | None:
    """Keep complete waters; model only two missing H atoms on isolated neutral O."""
    source = Path(pdb_path).resolve()
    snapshot = source.read_bytes()
    lines = snapshot.decode("utf-8-sig").splitlines()
    names = {line[17:20].strip() for line in lines if line.startswith(("ATOM  ", "HETATM"))}
    if names & UNSUPPORTED_WATERS:
        raise ValueError("Unsupported water residue: review its water model before preparation.")
    if not names & WATER_NAMES:
        return None
    audit = audit_structure(source)
    if audit.source_sha256 != hashlib.sha256(snapshot).hexdigest():
        raise ValueError("Crystal-water source changed during inspection.")
    if len(audit.model_ids) != 1 or audit.blocked_reasons:
        raise ValueError("Crystal-water preparation requires one valid coordinate model.")
    water_atoms = tuple(atom for atom in audit.atoms if atom.identity.residue_name in WATER_NAMES)
    if len({atom.identity for atom in water_atoms}) != len(water_atoms):
        raise ValueError("Crystal water contains duplicate atom identities.")
    groups = {}
    for atom in water_atoms:
        groups.setdefault(atom.identity.residue_id, []).append(atom)
    waters = tuple(_validate_water(tuple(group)) for group in groups.values())
    if len(waters) > 9999:
        raise ValueError("Crystal-water PDB output supports at most 9999 water residues.")
    water_serials = {atom.serial for atom in water_atoms}
    _validate_water_links(lines, water_atoms)
    folder = Path(output_dir).resolve() / f"crystal_water_{uuid4().hex}"
    folder.mkdir(parents=True)
    (folder / "source.pdb").write_bytes(snapshot)
    protein = folder / "protein_without_crystal_waters.pdb"
    removed_lines = {atom.line_number for atom in water_atoms}
    retained = [line for number, line in enumerate(lines, 1) if number not in removed_lines
                and not _water_auxiliary_record(line, water_serials)]
    protein.write_text("\n".join(retained) + "\n", encoding="utf-8", newline="\n")
    missing = tuple((index, group[0]) for index, group in enumerate(waters) if len(group) == 1)
    generated = {}
    if missing:
        sdf, mol2 = folder / "missing_water_hydrogens.sdf", folder / "modeled_waters.mol2"
        sdf.write_text("".join(_water_sdf(index, atom.coordinates) for index, atom in missing), encoding="utf-8", newline="\n")
        hydrogen_builder(sdf, mol2)
        generated = _read_modeled_waters(mol2, missing)
    coordinates = tuple((group[0].coordinates, *(atom.coordinates for atom in group[1:]))
                        if len(group) == 3 else (group[0].coordinates, *generated[index])
                        for index, group in enumerate(waters))
    water_pdb, water_gro = folder / "crystal_waters.pdb", folder / "crystal_waters.gro"
    _write_coordinates(coordinates, water_pdb, water_gro)
    if source.read_bytes() != snapshot:
        raise ValueError("Crystal-water source changed during hydrogen preparation.")
    report = {
        "schema_version": "1.0", "method": "Preserve observed water atoms; Open Babel adds H only to isolated oxygen-only waters",
        "source_path": str(source), "source_sha256": audit.source_sha256,
        "water_count": len(waters), "observed_oxygen_count": len(waters),
        "observed_hydrogen_count": sum(len(group) - 1 for group in waters), "modeled_hydrogen_count": 2 * len(missing),
        "max_observed_displacement_angstrom": 0.0,
        "max_gro_rounding_angstrom": max(abs(round(value / 10., 3) * 10. - value) for water in coordinates for xyz in water for value in xyz),
        "waters": [{"source_residue_id": group[0].identity.residue_id, "source_oxygen_serial": group[0].serial,
                    "output_residue_number": index + 1, "hydrogens": "observed" if len(group) == 3 else "modeled"}
                   for index, group in enumerate(waters)],
        "output_sha256": {path.name: file_sha256(path) for path in (protein, water_pdb, water_gro)},
        "limitations": "Added hydrogen orientations are modeled and require energy minimization; GRO uses standard 0.001 nm precision.",
    }
    provenance = folder / "water_provenance.json"
    provenance.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    return CrystalWaterInputs(protein, water_pdb, water_gro, len(waters), provenance)


def _validate_water(atoms):
    oxygens = tuple(atom for atom in atoms if atom.element.upper() == "O")
    hydrogens = tuple(atom for atom in atoms if atom.element.upper() == "H")
    if len(oxygens) != 1 or len(hydrogens) not in (0, 2) or len(atoms) != 1 + len(hydrogens):
        raise ValueError("Each crystal water must contain one oxygen and zero or two hydrogens.")
    if any(atom.identity.altloc for atom in atoms):
        raise ValueError("Resolve water alternate conformations before preparation.")
    if any(atom.source_line[78:80].strip() not in {'','0','00'} for atom in atoms):
        raise ValueError("Crystal waters must be neutral; explicit charged waters require a custom topology.")
    if hydrogens:
        _validate_geometry(oxygens[0].coordinates, tuple(atom.coordinates for atom in hydrogens))
    return (oxygens[0], *hydrogens)


def _validate_geometry(oxygen, hydrogens):
    vectors = tuple(tuple(h[i] - oxygen[i] for i in range(3)) for h in hydrogens)
    lengths = tuple(math.sqrt(sum(v*v for v in vector)) for vector in vectors)
    if any(not .5 < length < 1.5 for length in lengths):
        raise ValueError("Water hydrogen geometry has invalid O-H distances.")
    cosine = sum(a*b for a,b in zip(*vectors)) / (lengths[0] * lengths[1])
    angle = math.degrees(math.acos(max(-1.,min(1.,cosine))))
    if not 80. < angle < 130.:
        raise ValueError("Water hydrogen geometry must form a nondegenerate TIP3P-compatible triangle.")


def _validate_water_links(lines, atoms):
    serials = {atom.serial: atom.identity.residue_id for atom in atoms}
    keys = {(a.identity.chain_id, a.identity.residue_number, a.identity.insertion_code, a.identity.residue_name) for a in atoms}
    for line in lines:
        if line.startswith("LINK"):
            ends = ((line[21:22].strip(), line[22:26].strip(), line[26:27].strip(), line[17:20].strip()),
                    (line[51:52].strip(), line[52:56].strip(), line[56:57].strip(), line[47:50].strip()))
            if any(end in keys for end in ends):
                raise ValueError("Explicit crystal-water LINK requires a validated coordination topology.")
        if line.startswith("CONECT"):
            ids = [int(line[start:start+5]) for start in range(6,len(line),5) if line[start:start+5].strip()]
            if set(ids) & serials.keys() and (not set(ids) <= serials.keys() or len({serials[value] for value in ids}) != 1):
                raise ValueError("Crystal-water CONECT crosses molecular boundaries; custom topology required.")


def _water_auxiliary_record(line, serials):
    if line.startswith(("ANISOU", "SIGATM", "SIGUIJ", "CONECT")):
        return int(line[6:11]) in serials
    return False


def _water_sdf(index, xyz):
    return (f"WATER_{index}\nMolDynStudio\n\n  1  0  0  0  0  0            999 V2000\n"
            f"{xyz[0]:10.4f}{xyz[1]:10.4f}{xyz[2]:10.4f} O   0  0  0  0  0  0  0  0  0  0  0  0\nM  END\n$$$$\n")


def _read_modeled_waters(path, missing):
    blocks = path.read_text(encoding="utf-8").split("@<TRIPOS>MOLECULE")[1:]
    if len(blocks) != len(missing):
        raise ValueError("Water hydrogen builder returned the wrong molecule count.")
    result = {}
    for block, (index, oxygen) in zip(blocks, missing):
        match = re.search(r"@<TRIPOS>ATOM\s*\n(.*?)(?=@<TRIPOS>|\Z)",block,re.S)
        records = [line.split() for line in match.group(1).splitlines() if line.strip()] if match else []
        if len(records) != 3 or any(len(row) < 6 for row in records):
            raise ValueError("Water hydrogen builder must return exactly three atoms per water.")
        atoms = [(int(row[0]), row[5].split('.')[0].upper(), tuple(map(float,row[2:5]))) for row in records]
        oxygens, hydrogens = [row for row in atoms if row[1] == 'O'], [row for row in atoms if row[1] == 'H']
        if len(oxygens) != 1 or len(hydrogens) != 2 or any(not math.isfinite(v) for row in atoms for v in row[2]):
            raise ValueError("Water hydrogen builder returned invalid elements or coordinates.")
        if math.dist(oxygens[0][2],oxygen.coordinates) > 1e-4:
            raise ValueError("Water hydrogen builder moved an observed oxygen.")
        _validate_geometry(oxygen.coordinates, tuple(row[2] for row in hydrogens))
        result[index] = tuple(row[2] for row in hydrogens)
    return result


def _write_coordinates(waters, pdb_path, gro_path):
    pdb, gro = [], []
    for residue, water in enumerate(waters, 1):
        for name, xyz in zip(("OW", "HW1", "HW2"),water):
            serial = len(pdb) + 1
            pdb.append(f"HETATM{serial:5d} {name:>4} HOH W{residue:4d}    {xyz[0]:8.3f}{xyz[1]:8.3f}{xyz[2]:8.3f}  1.00  0.00          {name[0]:>2}  ")
            gro.append(f"{residue%100000:5d}{'SOL':<5}{name:>5}{serial%100000:5d}{xyz[0]/10.:8.3f}{xyz[1]/10.:8.3f}{xyz[2]/10.:8.3f}")
    pdb_path.write_text('\n'.join((*pdb,'END'))+'\n',encoding='utf-8',newline='\n')
    gro_path.write_text(f"Preserved crystal waters\n{len(gro)}\n"+'\n'.join(gro)+'\n1 1 1\n',encoding='utf-8',newline='\n')


def append_crystal_waters(protein_gro, topology, waters: CrystalWaterInputs) -> None:
    """Append each preserved water once, with matching SOL topology count."""
    protein_gro, topology = Path(protein_gro), Path(topology)
    for path in (protein_gro, waters.water_gro):
        validation = validate_gro(path)
        if not validation.ok:
            raise ValueError(validation.message)
    top_lines = topology.read_text(encoding='utf-8').splitlines()
    section = next((i for i,line in enumerate(top_lines) if re.match(r'^\s*\[\s*molecules\s*\]',line,re.I)),None)
    if section is None:
        raise ValueError('Topology lacks a molecules section for crystal waters.')
    if any(re.match(r'^\s*SOL\s+\d+',line) for line in top_lines[section+1:]):
        raise ValueError('Crystal water is already present in the generated topology.')
    if not any('tip3p.itp' in line.lower() and line.lstrip().startswith('#include') for line in top_lines):
        raise ValueError('CHARMM TIP3P water topology include is required.')
    protein_lines = protein_gro.read_text(encoding='utf-8').splitlines()
    water_lines = waters.water_gro.read_text(encoding='utf-8').splitlines()
    if int(water_lines[1]) != waters.water_count * 3:
        raise ValueError('Crystal-water coordinate count differs from its provenance.')
    atom_lines = protein_lines[2:-1] + water_lines[2:-1]
    protein_gro.write_text(f'{protein_lines[0]}\n{len(atom_lines)}\n'+'\n'.join(atom_lines)+'\n'+protein_lines[-1]+'\n',encoding='utf-8',newline='\n')
    top_lines.append(f"{'SOL':<18} {waters.water_count}")
    topology.write_text('\n'.join(top_lines)+'\n',encoding='utf-8',newline='\n')
    validation = validate_topology(topology)
    if not validation.ok:
        raise ValueError(validation.message)


def consolidate_water_molecules(topology: str | Path) -> int:
    """Combine consecutive SOL rows after solvate, without moving any molecule.

    genion operates on one solvent molecule block. Crystal waters followed by
    newly added solvent share that block only when no other molecule intervenes.
    """
    path = Path(topology)
    snapshot = path.read_bytes()
    lines = snapshot.decode("utf-8").splitlines()
    sections = [index for index, line in enumerate(lines)
                if re.fullmatch(r"\s*\[\s*molecules\s*\]\s*", line.split(';', 1)[0], re.I)]
    if len(sections) != 1:
        raise ValueError("Expected exactly one molecules section when validating solvent counts.")
    rows, conditional = [], False
    for index in range(sections[0] + 1, len(lines)):
        content = lines[index].split(';', 1)[0].strip()
        if content.startswith('['):
            break
        if not content:
            continue
        if content.startswith('#'):
            conditional = True
            continue
        fields = content.split()
        if len(fields) != 2:
            raise ValueError("Topology molecule rows require a name and integer count.")
        try:
            count = int(fields[1])
        except ValueError as exc:
            raise ValueError("Topology molecule count must be an integer.") from exc
        if count <= 0:
            raise ValueError("Topology molecule count must be positive.")
        rows.append((index, fields[0], count))
    waters = [(position, row) for position, row in enumerate(rows) if row[1] == 'SOL']
    if not waters:
        return 0
    if conditional:
        raise ValueError("Cannot consolidate conditional solvent molecule rows safely.")
    if waters[-1][0] - waters[0][0] + 1 != len(waters):
        raise ValueError("SOL molecule rows must be contiguous; merging separated blocks would reorder coordinates.")
    total = sum(row[2] for _position, row in waters)
    if total > 2_147_483_647:
        raise ValueError("Solvent molecule count exceeds the supported integer range.")
    if len(waters) == 1 and b'\r' not in snapshot:
        return total
    first = waters[0][1][0]
    comment = lines[first].partition(';')[2]
    if len(waters) > 1:
        lines[first] = f"{'SOL':<18} {total}" + (f" ;{comment}" if comment else '')
    for _position, (index, _name, _count) in waters[1:]:
        comment = lines[index].partition(';')[2]
        lines[index] = f";{comment}" if comment else ''
    staged = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        # genion trims spaces/tabs, but leaves CR at the end of section lines.
        staged.write_text('\n'.join(lines) + '\n', encoding='utf-8', newline='\n')
        staged.replace(path)
    finally:
        staged.unlink(missing_ok=True)
    return total
