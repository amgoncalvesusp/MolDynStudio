"""Read-only PDB audit with full atom identities and conservative diagnostics."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path

from core.sequence_mapping import FastaRecord, SequenceGap, SequenceMapping, THREE_TO_ONE, align_sequence, parse_fasta_text


@dataclass(frozen=True)
class ResidueIdentity:
    model_id: int
    chain_id: str
    residue_number: str
    insertion_code: str
    residue_name: str

    @property
    def residue_id(self) -> str:
        return json.dumps((self.model_id, self.chain_id, self.residue_number,
                           self.insertion_code, self.residue_name), separators=(",", ":"))


@dataclass(frozen=True)
class AtomIdentity(ResidueIdentity):
    atom_name: str
    altloc: str


@dataclass(frozen=True)
class AtomRecord:
    line_number: int
    identity: AtomIdentity
    record_name: str
    serial: int
    coordinates: tuple[float, float, float]
    occupancy: float
    element: str
    source_line: str


@dataclass(frozen=True)
class ResidueRecord:
    identity: ResidueIdentity
    polymer: bool
    parent_residue_name: str
    atom_line_numbers: tuple[int, ...]

    @property
    def residue_id(self) -> str:
        return self.identity.residue_id


@dataclass(frozen=True)
class AltlocGroup:
    residue_id: str
    labels: tuple[str, ...]
    recommended: str
    scores: tuple[tuple[str, int, float], ...]
    line_numbers: tuple[int, ...]


@dataclass(frozen=True)
class ModifiedResidue:
    residue_id: str
    residue_name: str
    parent_residue_name: str
    removable_atom_names: tuple[str, ...]


@dataclass(frozen=True)
class AtomCollision:
    first_line: int
    second_line: int
    distance_angstrom: float


@dataclass(frozen=True)
class MissingHeavyAtoms:
    residue_id: str
    atom_names: tuple[str, ...]


@dataclass(frozen=True)
class CovalentConnection:
    polymer_atom: AtomIdentity
    heterogen_atom: AtomIdentity
    source: str


@dataclass(frozen=True)
class StructureAudit:
    source_path: Path
    source_sha256: str
    fasta_path: Path | None
    fasta_sha256: str | None
    atoms: tuple[AtomRecord, ...]
    residues: tuple[ResidueRecord, ...]
    model_ids: tuple[int, ...]
    chain_ids: tuple[str, ...]
    fasta_records: tuple[FastaRecord, ...]
    altloc_groups: tuple[AltlocGroup, ...]
    exact_duplicate_lines: tuple[int, ...]
    collisions: tuple[AtomCollision, ...]
    modified_residues: tuple[ModifiedResidue, ...]
    missing_heavy_atoms: tuple[MissingHeavyAtoms, ...]
    sequence_mappings: tuple[SequenceMapping, ...]
    sequence_gaps: tuple[SequenceGap, ...]
    covalent_links: tuple[CovalentConnection, ...]
    warnings: tuple[str, ...]
    blocked_reasons: tuple[str, ...]


HEAVY_ATOMS = {
    name: frozenset(("N CA C O " + sidechain).split())
    for name, sidechain in {
        "ALA": "CB", "ARG": "CB CG CD NE CZ NH1 NH2", "ASN": "CB CG OD1 ND2",
        "ASP": "CB CG OD1 OD2", "CYS": "CB SG", "GLN": "CB CG CD OE1 NE2",
        "GLU": "CB CG CD OE1 OE2", "GLY": "", "HIS": "CB CG ND1 CD2 CE1 NE2",
        "ILE": "CB CG1 CG2 CD1", "LEU": "CB CG CD1 CD2", "LYS": "CB CG CD CE NZ",
        "MET": "CB CG SD CE", "PHE": "CB CG CD1 CD2 CE1 CE2 CZ", "PRO": "CB CG CD",
        "SER": "CB OG", "THR": "CB OG1 CG2", "TRP": "CB CG CD1 CD2 NE1 CE2 CE3 CZ2 CZ3 CH2",
        "TYR": "CB CG CD1 CD2 CE1 CE2 CZ OH", "VAL": "CB CG1 CG2",
    }.items()
}


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def audit_structure(pdb_path: str | Path, fasta_path: str | Path | None = None,
                    chain_id: str | None = None, fasta_record_id: str | None = None) -> StructureAudit:
    source = Path(pdb_path).expanduser().resolve()
    source_bytes = source.read_bytes()
    raw = source_bytes.decode("utf-8-sig").splitlines()
    atoms, duplicates, blocks = _parse_atoms(raw)
    modres = _modres_records(raw)
    grouped: dict[str, list[AtomRecord]] = {}
    for atom in atoms:
        grouped.setdefault(atom.identity.residue_id, []).append(atom)
    residues, modifications, alternate, missing = [], [], [], []
    for key, group in grouped.items():
        identity = group[0].identity
        parent = modres.get((identity.chain_id, identity.residue_number, identity.insertion_code, identity.residue_name), identity.residue_name)
        polymer = parent in THREE_TO_ONE or any(atom.record_name == "ATOM" for atom in group)
        residue_identity = ResidueIdentity(identity.model_id, identity.chain_id, identity.residue_number,
                                           identity.insertion_code, identity.residue_name)
        residues.append(ResidueRecord(residue_identity, polymer, parent, tuple(atom.line_number for atom in group)))
        if parent != identity.residue_name:
            extras = tuple(sorted({atom.identity.atom_name for atom in group} - HEAVY_ATOMS.get(parent, frozenset()) - {"OXT"}))
            modifications.append(ModifiedResidue(key, identity.residue_name, parent, extras))
        labels = tuple(sorted({atom.identity.altloc for atom in group if atom.identity.altloc}))
        selected = ""
        if labels:
            scores = tuple((label, len({atom.identity.atom_name for atom in group if atom.identity.altloc in ("", label)}),
                            sum(atom.occupancy for atom in group if atom.identity.altloc == label)) for label in labels)
            selected = sorted(scores, key=lambda item: (-item[1], -item[2], item[0]))[0][0]
            alternate.append(AltlocGroup(key, labels, selected, scores, tuple(atom.line_number for atom in group)))
        names = {atom.identity.atom_name for atom in group if atom.identity.altloc in ("", selected)}
        absent = HEAVY_ATOMS.get(parent, frozenset()) - names
        if polymer and absent:
            missing.append(MissingHeavyAtoms(key, tuple(sorted(absent))))
    if not atoms:
        blocks.append("PDB contains no valid coordinate records.")
    models = tuple(dict.fromkeys(atom.identity.model_id for atom in atoms))
    chains = tuple(dict.fromkeys(residue.identity.chain_id for residue in residues if residue.polymer))
    fasta = Path(fasta_path).expanduser().resolve() if fasta_path else None
    fasta_bytes = fasta.read_bytes() if fasta else None
    records = parse_fasta_text(fasta_bytes.decode("utf-8-sig")) if fasta_bytes is not None else ()
    mappings, sequence_warnings = _sequence_maps(tuple(residues), raw, records, chain_id, fasta_record_id)
    warnings = list(sequence_warnings)
    if len(models) > 1:
        warnings.append("Multiple coordinate models: choose one explicitly; models are never collapsed together.")
    if modifications:
        warnings.append("MODRES residues remain polymer members; parent conversion requires explicit selection.")
    covalent = _covalent_connections(raw, atoms, residues)
    if covalent:
        warnings.append(f"{len(covalent)} explicit polymer–heterogen covalent connection(s): preserve the complete complex; MD requires a validated custom topology.")
    return StructureAudit(source, hashlib.sha256(source_bytes).hexdigest(), fasta,
                          hashlib.sha256(fasta_bytes).hexdigest() if fasta_bytes is not None else None,
                          tuple(atoms), tuple(residues), models, chains, records, tuple(alternate),
                          tuple(duplicates), _collisions(atoms), tuple(modifications), tuple(missing),
                          mappings, tuple(gap for mapping in mappings for gap in mapping.gaps), covalent,
                          tuple(warnings), tuple(blocks))


def _parse_atoms(lines: list[str]) -> tuple[list[AtomRecord], list[int], list[str]]:
    atoms, duplicate_lines, blocks = [], [], []
    model = 1
    model_labels = set()
    seen: set[tuple[int, str]] = set()
    for number, line in enumerate(lines, 1):
        record = line[:6].strip()
        if record == "MODEL":
            try:
                model = int(line[10:14])
                if model in model_labels:
                    blocks.append(f"Repeated MODEL identifier {model}; model identity is ambiguous.")
                model_labels.add(model)
            except ValueError:
                blocks.append(f"Invalid MODEL identifier at line {number}.")
            continue
        if record not in {"ATOM", "HETATM"}:
            continue
        try:
            serial = int(line[6:11])
            coordinates = tuple(float(line[start:start + 8]) for start in (30, 38, 46))
            occupancy = float(line[54:60].strip() or "1")
            if serial <= 0 or not line[22:26].strip() or not all(math.isfinite(value) for value in (*coordinates, occupancy)):
                raise ValueError("invalid coordinate values")
            identity = AtomIdentity(model, line[21:22].strip(), line[22:26].strip(), line[26:27].strip(),
                                    line[17:20].strip(), line[12:16].strip(), line[16:17].strip())
            atom = AtomRecord(number, identity, record, serial, coordinates, occupancy,
                              line[76:78].strip(), line)
        except (ValueError, IndexError):
            blocks.append(f"Invalid atom coordinate record at line {number}.")
            continue
        if (model, line) in seen:
            duplicate_lines.append(number)
        seen.add((model, line))
        atoms.append(atom)
    return atoms, duplicate_lines, blocks


def _modres_records(lines: list[str]) -> dict[tuple[str, str, str, str], str]:
    return {(line[16:17].strip(), line[18:22].strip(), line[22:23].strip(), line[12:15].strip()): line[24:27].strip()
            for line in lines if line.startswith("MODRES")}


def _collisions(atoms: list[AtomRecord]) -> tuple[AtomCollision, ...]:
    # Spatial cells avoid quadratic work for solvated coordinate files.
    cells: dict[tuple[int, int, int, int], list[AtomRecord]] = {}
    collisions = []
    for atom in atoms:
        xyz = tuple(math.floor(value / .5) for value in atom.coordinates)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for other in cells.get((atom.identity.model_id, xyz[0] + dx, xyz[1] + dy, xyz[2] + dz), ()):
                        if atom.source_line == other.source_line:
                            continue
                        if (atom.identity.residue_id == other.identity.residue_id and atom.identity.altloc
                                and other.identity.altloc and atom.identity.altloc != other.identity.altloc):
                            continue
                        distance = math.dist(atom.coordinates, other.coordinates)
                        if distance < .5:
                            collisions.append(AtomCollision(other.line_number, atom.line_number, distance))
        cells.setdefault((atom.identity.model_id, *xyz), []).append(atom)
    return tuple(collisions)


def _covalent_connections(lines: list[str], atoms: list[AtomRecord], residues: list[ResidueRecord]) -> tuple[CovalentConnection, ...]:
    polymers = {residue.residue_id for residue in residues if residue.polymer}
    by_serial: dict[int, list[AtomRecord]] = {}
    by_link: dict[tuple[str, ...], list[AtomRecord]] = {}
    for atom in atoms:
        by_serial.setdefault(atom.serial, []).append(atom)
        i = atom.identity
        by_link.setdefault((i.chain_id, i.residue_number, i.insertion_code, i.residue_name, i.atom_name, i.altloc), []).append(atom)
    links: dict[tuple[AtomIdentity, AtomIdentity], CovalentConnection] = {}
    active_model = None
    for line in lines:
        if line.startswith("MODEL "):
            try:
                active_model = int(line[10:14])
            except ValueError:
                active_model = None
            continue
        if line.startswith("ENDMDL"):
            active_model = None
            continue
        pairs = []
        if line.startswith("CONECT"):
            try:
                serials = [int(line[start:start + 5]) for start in range(6, len(line), 5) if line[start:start + 5].strip()]
            except ValueError:
                continue
            if serials:
                pairs.extend((first, second) for first in by_serial.get(serials[0], ())
                             for value in serials[1:] for second in by_serial.get(value, ()))
        elif line.startswith("LINK"):
            keys = ((line[21:22].strip(), line[22:26].strip(), line[26:27].strip(), line[17:20].strip(), line[12:16].strip(), line[16:17].strip()),
                    (line[51:52].strip(), line[52:56].strip(), line[56:57].strip(), line[47:50].strip(), line[42:46].strip(), line[46:47].strip()))
            def matches(key):
                if key[5]:
                    return by_link.get(key, ())
                return tuple(atom for identity, members in by_link.items() if identity[:5] == key[:5] for atom in members)
            pairs.extend((first, second) for first in matches(keys[0]) for second in matches(keys[1]))
        for first, second in pairs:
            if first.identity.model_id != second.identity.model_id:
                continue
            if active_model is not None and first.identity.model_id != active_model:
                continue
            first_polymer, second_polymer = first.identity.residue_id in polymers, second.identity.residue_id in polymers
            if first_polymer == second_polymer:
                continue
            polymer, ligand = (first, second) if first_polymer else (second, first)
            key = (polymer.identity, ligand.identity)
            links.setdefault(key, CovalentConnection(*key, line[:6].strip()))
    return tuple(links.values())


def _sequence_maps(residues: tuple[ResidueRecord, ...], lines: list[str], records: tuple[FastaRecord, ...],
                   selected_chain: str | None, record_id: str | None) -> tuple[tuple[SequenceMapping, ...], tuple[str, ...]]:
    groups: dict[tuple[int, str], list[ResidueRecord]] = {}
    for residue in residues:
        if residue.polymer:
            groups.setdefault((residue.identity.model_id, residue.identity.chain_id), []).append(residue)
    seqres: dict[str, list[str]] = {}
    for line in lines:
        if line.startswith("SEQRES"):
            seqres.setdefault(line[11:12].strip(), []).extend(line[19:70].split())
    parents = {(key[0], key[3]): parent for key, parent in _modres_records(lines).items()}
    warnings, mappings = [], []
    selected_record = next((record for record in records if record.record_id == record_id), None)
    if records and record_id and selected_record is None:
        raise ValueError(f"FASTA record {record_id!r} is absent.")
    if records and selected_record is None and len(records) == 1:
        selected_record = records[0]
    if records and selected_record is None:
        warnings.append("Choose one FASTA record explicitly before sequence mapping.")
    if selected_chain is not None and not any(key[1] == selected_chain for key in groups):
        raise ValueError(f"Polymer chain {selected_chain!r} is absent.")
    for (model, chain), members in groups.items():
        observed = "".join(THREE_TO_ONE.get(residue.parent_residue_name, "X") for residue in members)
        identities = tuple(residue.residue_id for residue in members)
        if chain in seqres:
            reference = "".join(THREE_TO_ONE.get(parents.get((chain, name), name), "X") for name in seqres[chain])
            mappings.append(align_sequence(reference, observed, identities, chain_id=chain,
                                           model_id=model, source="SEQRES", reference_id=chain or "blank"))
        if selected_record is not None and (chain == selected_chain or selected_chain is None and len(groups) == 1):
            mappings.append(align_sequence(selected_record.sequence, observed, identities, chain_id=chain,
                                           model_id=model, source="FASTA", reference_id=selected_record.record_id))
    if selected_record is not None and selected_chain is None and len(groups) > 1:
        warnings.append("Choose the coordinate chain explicitly for this FASTA record.")
    return tuple(mappings), tuple(warnings)
