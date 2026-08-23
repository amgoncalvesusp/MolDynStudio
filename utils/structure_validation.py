"""Structural checks for the currently supported non-covalent workflow."""

from __future__ import annotations

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

    @property
    def residue_label(self) -> str:
        parts = [self.residue_name, self.chain, self.residue_number]
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
    )


def _as_protein_ligand_link(
    first: _AtomReference,
    second: _AtomReference,
    *,
    source: str,
) -> CovalentLigandLink | None:
    if first.residue_name in AMINO_ACIDS:
        protein, ligand = first, second
    elif second.residue_name in AMINO_ACIDS:
        protein, ligand = second, first
    else:
        return None
    if (
        ligand.residue_name in AMINO_ACIDS
        or ligand.residue_name in IGNORED_HETERO_RESIDUES
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


def find_covalent_ligand_links(
    pdb_path: str | Path,
) -> tuple[CovalentLigandLink, ...]:
    """Find explicit LINK/CONECT bonds between protein and hetero ligands."""

    path = Path(pdb_path)
    if path.suffix.lower() != ".pdb":
        return ()
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    atoms: dict[int, _AtomReference] = {}
    links: list[CovalentLigandLink] = []
    seen: set[tuple[str, str, str, str]] = set()

    for line in lines:
        if line.startswith(("ATOM  ", "HETATM")):
            atom = _pdb_atom(line)
            if atom is not None:
                atoms[atom.serial] = atom
        elif line.startswith("LINK"):
            first = _link_atom(
                line,
                atom_slice=slice(12, 16),
                residue_slice=slice(17, 20),
                chain_index=21,
                number_slice=slice(22, 26),
            )
            second = _link_atom(
                line,
                atom_slice=slice(42, 46),
                residue_slice=slice(47, 50),
                chain_index=51,
                number_slice=slice(52, 56),
            )
            link = _as_protein_ligand_link(first, second, source="LINK")
            if link is not None:
                key = (
                    link.protein_atom,
                    link.protein_residue,
                    link.ligand_atom,
                    link.ligand_residue,
                )
                if key not in seen:
                    seen.add(key)
                    links.append(link)

    for line in lines:
        if not line.startswith("CONECT"):
            continue
        fields = line.split()
        if len(fields) < 3:
            continue
        try:
            source_serial = int(fields[1])
            bonded_serials = [int(value) for value in fields[2:]]
        except ValueError:
            continue
        first = atoms.get(source_serial)
        if first is None:
            continue
        for bonded_serial in bonded_serials:
            second = atoms.get(bonded_serial)
            if second is None or first.record == second.record:
                continue
            link = _as_protein_ligand_link(first, second, source="CONECT")
            if link is None:
                continue
            key = (
                link.protein_atom,
                link.protein_residue,
                link.ligand_atom,
                link.ligand_residue,
            )
            if key not in seen:
                seen.add(key)
                links.append(link)
    return tuple(links)


def ensure_noncovalent_complex(pdb_path: str | Path) -> None:
    links = find_covalent_ligand_links(pdb_path)
    if not links:
        return
    link = links[0]
    raise CovalentLigandError(
        "Covalent protein-ligand linkage detected: "
        f"{link.ligand_residue} ({link.ligand_atom}) - "
        f"{link.protein_residue} ({link.protein_atom}). "
        "MolDynStudio currently supports non-covalent ligands only."
    )
