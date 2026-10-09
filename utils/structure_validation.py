"""Structural checks for the currently supported non-covalent workflow."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path


AMINO_ACIDS = frozenset(
    {
        "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS",
        "ILE", "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP",
        "TYR", "VAL", "ASH", "CYM", "CYX", "GLH", "HID", "HIE", "HIP",
        "LYN", "MSE",
    }
)
IGNORED_HETERO_RESIDUES = frozenset(
    {
        "HOH", "WAT", "SOL", "TIP", "TIP3", "TIP4",
        "NA", "SOD", "CL", "CLA", "K", "POT", "CA", "MG", "ZN",
    }
)
BIOPOLYMER_HETERO_RESIDUES = frozenset(
    {
        # Common PDB carbohydrate residue names used in protein glycans.
        "NAG", "NDG", "BMA", "MAN", "FUC", "GAL", "GLC", "BGC", "SIA",
    }
)


class CovalentLigandError(ValueError):
    """Raised when a protein-ligand covalent bond is present."""


@dataclass(frozen=True)
class _AtomReference:
    record: str
    serial: int
    atom_name: str
    residue_name: str
    chain: str
    residue_number: str
    insertion_code: str

    @property
    def residue_identity(self) -> tuple[str, str, str, str]:
        return self.residue_name, self.chain, self.residue_number, self.insertion_code

    @property
    def residue_label(self) -> str:
        parts = [self.residue_name, self.chain, self.residue_number + self.insertion_code]
        return " ".join(part for part in parts if part)


@dataclass(frozen=True)
class CovalentLigandLink:
    protein_atom: str
    protein_residue: str
    ligand_atom: str
    ligand_residue: str
    source: str


def _pdb_atom(line: str) -> _AtomReference | None:
    try:
        serial = int(line[6:11])
    except (ValueError, IndexError):
        return None
    return _AtomReference(
        record=line[0:6].strip(),
        serial=serial,
        atom_name=line[12:16].strip(),
        residue_name=line[17:20].strip(),
        chain=line[21:22].strip(),
        residue_number=line[22:26].strip(),
        insertion_code=line[26:27].strip(),
    )


def _link_atom(
    line: str,
    *,
    atom_slice: slice,
    residue_slice: slice,
    chain_index: int,
    number_slice: slice,
) -> _AtomReference:
    return _AtomReference(
        record="LINK",
        serial=0,
        atom_name=line[atom_slice].strip(),
        residue_name=line[residue_slice].strip(),
        chain=line[chain_index : chain_index + 1].strip(),
        residue_number=line[number_slice].strip(),
        insertion_code=line[number_slice.stop : number_slice.stop + 1].strip(),
    )


def _as_protein_ligand_link(
    first: _AtomReference,
    second: _AtomReference,
    *,
    source: str,
    modified_residues: frozenset[tuple[str, str, str, str]],
) -> CovalentLigandLink | None:
    first_is_protein = first.residue_name in AMINO_ACIDS or first.residue_identity in modified_residues
    second_is_protein = second.residue_name in AMINO_ACIDS or second.residue_identity in modified_residues
    if first_is_protein == second_is_protein:
        return None
    if first_is_protein:
        protein, ligand = first, second
    else:
        protein, ligand = second, first
    if (
        ligand.residue_name in IGNORED_HETERO_RESIDUES
        or ligand.residue_name in BIOPOLYMER_HETERO_RESIDUES
        or not ligand.residue_name
    ):
        return None
    return CovalentLigandLink(
        protein_atom=protein.atom_name,
        protein_residue=protein.residue_label,
        ligand_atom=ligand.atom_name,
        ligand_residue=ligand.residue_label,
        source=source,
    )


def _modified_protein_residues(lines: Iterable[str]) -> frozenset[tuple[str, str, str, str]]:
    """Recognize exact MODRES sites without replacing their chemical identity."""
    return frozenset(
        (line[12:15].strip(), line[16:17].strip(), line[18:22].strip(), line[22:23].strip())
        for line in lines
        if line[:6].strip() == "MODRES" and line[24:27].strip() in AMINO_ACIDS
    )


def _model_records(lines: Iterable[str]) -> Iterator[tuple[int | None, str]]:
    """Keep MODEL blocks separate even if their declared identifiers repeat."""
    scope = None
    block = 0
    for line in lines:
        record = line[:6].strip()
        if record == "MODEL":
            block += 1
            scope = block
        elif record == "ENDMDL":
            scope = None
        yield scope, line


def _coordinate_models(
    records: Iterable[tuple[int | None, str]],
) -> dict[int | None, dict[int, tuple[_AtomReference, ...]]]:
    models: dict[int | None, dict[int, tuple[_AtomReference, ...]]] = {}
    for scope, line in records:
        if line[:6].strip() in {"ATOM", "HETATM"}:
            atom = _pdb_atom(line)
            if atom is not None:
                atoms = models.setdefault(scope, {})
                atoms[atom.serial] = (*atoms.get(atom.serial, ()), atom)
    return models


def _link_records(
    lines: Iterable[str], modified_residues: frozenset[tuple[str, str, str, str]],
) -> Iterator[CovalentLigandLink]:
    for line in lines:
        if line[:6].strip() != "LINK":
            continue
        first = _link_atom(line, atom_slice=slice(12, 16), residue_slice=slice(17, 20),
                           chain_index=21, number_slice=slice(22, 26))
        second = _link_atom(line, atom_slice=slice(42, 46), residue_slice=slice(47, 50),
                            chain_index=51, number_slice=slice(52, 56))
        link = _as_protein_ligand_link(first, second, source="LINK", modified_residues=modified_residues)
        if link is not None:
            yield link


def _conect_serials(line: str) -> tuple[int, ...]:
    # wwPDB columns 7–31 contain the source and up to four bonded atom serials.
    try:
        source = int(line[6:11])
        targets = tuple(int(line[start:start + 5]) for start in range(11, 31, 5)
                        if line[start:start + 5].strip())
    except ValueError:
        return ()
    return (source, *targets)


def _connected_atom_links(
    atoms: dict[int, tuple[_AtomReference, ...]], serials: tuple[int, ...],
    modified_residues: frozenset[tuple[str, str, str, str]],
) -> Iterator[CovalentLigandLink]:
    for first in atoms.get(serials[0], ()):
        for serial in serials[1:]:
            for second in atoms.get(serial, ()):
                link = _as_protein_ligand_link(
                    first, second, source="CONECT", modified_residues=modified_residues,
                )
                if link is not None:
                    yield link


def _conect_records(
    records: Iterable[tuple[int | None, str]],
    models: dict[int | None, dict[int, tuple[_AtomReference, ...]]],
    modified_residues: frozenset[tuple[str, str, str, str]],
) -> Iterator[CovalentLigandLink]:
    for scope, line in records:
        if line[:6].strip() != "CONECT":
            continue
        serials = _conect_serials(line)
        if len(serials) > 1:
            # Records outside MODEL blocks apply within each model independently.
            candidates = models.values() if scope is None else (models.get(scope, {}),)
            for atoms in candidates:
                yield from _connected_atom_links(atoms, serials, modified_residues)


def find_covalent_ligand_links(
    pdb_path: str | Path,
) -> tuple[CovalentLigandLink, ...]:
    """Find explicit protein–ligand LINK/CONECT records, respecting MODEL scope.

    MODRES annotations identify modified protein sites; they do not establish
    force-field support. This check does not infer missing bonds from geometry.
    """
    path = Path(pdb_path)
    if path.suffix.lower() != ".pdb":
        return ()
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    modified_residues = _modified_protein_residues(lines)
    records = tuple(_model_records(lines))
    models = _coordinate_models(records)
    links: dict[tuple[str, str, str, str], CovalentLigandLink] = {}
    for candidates in (
        _link_records(lines, modified_residues),
        _conect_records(records, models, modified_residues),
    ):
        for link in candidates:
            key = (link.protein_atom, link.protein_residue, link.ligand_atom, link.ligand_residue)
            links.setdefault(key, link)
    return tuple(links.values())


def ensure_noncovalent_complex(pdb_path: str | Path) -> None:
    links = find_covalent_ligand_links(pdb_path)
    if not links:
        return
    link = links[0]
    raise CovalentLigandError(
        "Covalent protein-ligand linkage detected: "
        f"{link.ligand_residue} ({link.ligand_atom}) - "
        f"{link.protein_residue} ({link.protein_atom}). "
        "Automatic preparation supports non-covalent ligands only. "
        "This complete complex requires a validated custom topology with "
        "parameters for the covalent adduct before MD; automatic ACPYPE/GAFF2 "
        "parameterization is not suitable."
    )
