"""Convert the supported single-residue MATCH topology through ParmEd.

This adapter constructs CHARMM AUTO angles/dihedrals from explicit RTF bonds.
ParmEd performs parameter matching and force-function/unit conversion. Advanced
RTF features are rejected rather than dropped from the generated potential.
"""

from __future__ import annotations

from copy import copy
from dataclasses import dataclass
import hashlib
from io import StringIO
from itertools import combinations
import math
from pathlib import Path
import re


@dataclass(frozen=True)
class CharmmConversion:
    ligand_itp: Path
    parameters_itp: Path
    ligand_gro: Path
    psf_path: Path
    atom_names: tuple[str, ...]
    net_charge: float
    term_counts: tuple[tuple[str, int], ...]


def _read_input(path: str | Path) -> str:
    source = Path(path)
    if source.stat().st_size > 5 * 1024 * 1024:
        raise ValueError(f"CHARMM ligand input exceeds the 5 MiB limit: {source.name}")
    return source.read_text(encoding="utf-8")


def _rtf_contract(text: str) -> tuple[str, float, tuple[tuple[str, ...], ...]]:
    records = tuple(line.split("!", 1)[0].strip().split() for line in text.splitlines()
                    if line.strip() and not line.lstrip().startswith("*"))
    allowed = {"MASS", "AUTO", "AUTOGENERATE", "RESI", "GROUP", "ATOM", "BOND",
               "DOUBLE", "IMPR", "IMPH", "PATCH", "PATCHING", "END"}
    for row in records:
        if not row or row[0].isdigit():
            continue
        keyword = row[0].upper()
        if keyword not in allowed:
            raise ValueError(f"Unsupported CHARMM RTF directive: {keyword}")
        if keyword in {"PATCH", "PATCHING"} and tuple(x.upper() for x in row[1:]) != ("FIRST", "NONE", "LAST", "NONE"):
            raise ValueError("CHARMM patches are unsupported for the isolated ligand workflow.")
        if keyword in {"ATOM", "BOND", "DOUBLE", "IMPR", "IMPH"} and any(x.startswith(("+", "-")) for x in row[1:2] if keyword == "ATOM"):
            raise ValueError("External residue references are unsupported.")
        if keyword in {"BOND", "DOUBLE", "IMPR", "IMPH"}:
            width = 2 if keyword in {"BOND", "DOUBLE"} else 4
            if (len(row) - 1) % width or any(x.startswith(("+", "-")) for x in row[1:]):
                raise ValueError(f"Invalid or external atom reference in {keyword}.")
    residues = tuple(row for row in records if row and row[0].upper() == "RESI")
    if len(residues) != 1 or len(residues[0]) != 3:
        raise ValueError("Exactly one isolated RESI definition is required.")
    auto = tuple(row for row in records if row and row[0].upper() in {"AUTO", "AUTOGENERATE"})
    if len(auto) != 1 or set(x[:4].upper() for x in auto[0][1:]) != {"ANGL", "DIHE"}:
        raise ValueError("The supported RTF must declare AUTO ANGLES DIHE.")
    atoms = tuple(row for row in records if row and row[0].upper() == "ATOM")
    if not atoms or len(atoms) > 1000 or any(len(row) != 4 for row in atoms):
        raise ValueError("RTF must contain 1–1000 standard non-polarizable ATOM records.")
    names = tuple(row[1].upper() for row in atoms)
    if len(set(names)) != len(names) or any(not re.fullmatch(r"[A-Z0-9][A-Z0-9_'*]{0,4}", name) for name in names):
        raise ValueError("RTF atom names must be unique and fit five-character GRO fields.")
    bonds = tuple(tuple(sorted(x.upper() for x in row[i:i + 2])) for row in records
                  if row and row[0].upper() in {"BOND", "DOUBLE"} for i in range(1, len(row), 2))
    if len(set(bonds)) != len(bonds) or any(a == b or not {a, b}.issubset(names) for a, b in bonds):
        raise ValueError("Duplicate, self-bond or unknown atom in RTF bond connectivity.")
    impropers = tuple(tuple(x.upper() for x in row[i:i + 4]) for row in records
                      if row and row[0].upper() in {"IMPR", "IMPH"}
                      for i in range(1, len(row), 4))
    if any(len(set(item)) != 4 or not set(item).issubset(names) for item in impropers):
        raise ValueError("Improper torsions must reference four distinct ligand atoms.")
    return residues[0][1].upper(), float(residues[0][2]), impropers


def _validate_parameter_contract(text: str) -> None:
    for raw in text.splitlines():
        row = raw.split("!", 1)[0].split()
        if row and row[0].upper() in {"NBFIX", "CMAP", "THOLE", "HBOND", "DRUDE"}:
            raise ValueError(f"Unsupported CHARMM parameter section: {row[0].upper()}")
        if "E14FAC" in [item.upper() for item in row]:
            position = [item.upper() for item in row].index("E14FAC")
            if position + 1 >= len(row) or float(row[position + 1]) != 1.0:
                raise ValueError("Only CHARMM E14FAC 1.0 is supported.")


def _complete_graph(structure, impropers: tuple[tuple[str, ...], ...]) -> None:
    from parmed.topologyobjects import Angle, Dihedral, Improper

    edges = tuple(tuple(sorted((bond.atom1.idx, bond.atom2.idx))) for bond in structure.bonds)
    if len(set(edges)) != len(edges) or any(a == b for a, b in edges):
        raise ValueError("Duplicate or self-bond in CHARMM ligand connectivity.")
    neighbors = tuple(tuple(sorted(b if a == i else a for a, b in edges if i in (a, b)))
                      for i in range(len(structure.atoms)))
    reached, pending = set(), [0]
    while pending:
        current = pending.pop()
        if current not in reached:
            reached.add(current)
            pending.extend(neighbors[current])
    if len(reached) != len(structure.atoms):
        raise ValueError("Disconnected ligand fragments are unsupported.")
    for center, adjacent in enumerate(neighbors):
        structure.angles.extend(Angle(structure.atoms[a], structure.atoms[center], structure.atoms[c])
                                for a, c in combinations(adjacent, 2))
    torsions = tuple(sorted({min((a, b, c, d), (d, c, b, a)) for b, c in edges
                             for a in neighbors[b] for d in neighbors[c]
                             if len({a, b, c, d}) == 4}))
    structure.dihedrals.extend(Dihedral(*(structure.atoms[index] for index in term)) for term in torsions)
    by_name = {atom.name: atom for atom in structure.atoms}
    structure.impropers.extend(Improper(*(by_name[name] for name in term)) for term in impropers)


def _validate_bonded_parameters(structure) -> None:
    from parmed.topologyobjects import DihedralTypeList

    fields = ("k", "req", "theteq", "phi_k", "per", "phase", "psi_k", "psi_eq", "scee", "scnb")
    for collection in ("bonds", "angles", "dihedrals", "impropers", "urey_bradleys"):
        for term in getattr(structure, collection):
            types = tuple(term.type) if isinstance(term.type, DihedralTypeList) else (term.type,)
            if not types or any(value is None for value in types):
                raise ValueError(f"Missing bonded parameter in {collection}.")
            if any(not math.isfinite(float(getattr(value, field))) for value in types
                   for field in fields if hasattr(value, field)):
                raise ValueError(f"Nonfinite bonded parameter in {collection}.")


def load_charmm_ligand(rtf_path: str | Path, parameter_path: str | Path,
                      mol2_path: str | Path, *, net_charge: int):
    """Return a fully parameterized PSF-like structure, or reject lost chemistry."""
    if isinstance(net_charge, bool) or not isinstance(net_charge, int) or abs(net_charge) > 20:
        raise ValueError("Ligand net charge must be an integer between -20 and 20.")
    rtf, parameters, coordinates = tuple(_read_input(path) for path in (rtf_path, parameter_path, mol2_path))
    residue, declared_charge, impropers = _rtf_contract(rtf)
    _validate_parameter_contract(parameters)
    try:
        from parmed.charmm import CharmmParameterSet, CharmmPsfFile
        from parmed.formats import Mol2File
        from parmed.periodic_table import AtomicNum

        parameter_set = CharmmParameterSet(str(rtf_path), str(parameter_path))
        template = parameter_set.residues[residue]
        structure = CharmmPsfFile.from_structure(template.to_structure(), copy=True)
        molecule = Mol2File.parse(StringIO(coordinates), structure=True)
        source_names = tuple(atom.name.upper() for atom in molecule.atoms)
        names = tuple(atom.name for atom in structure.atoms)
        if len(molecule.residues) != 1 or len(set(source_names)) != len(source_names) or set(source_names) != set(names):
            raise ValueError("MATCH MOL2 atom identities do not match the RTF ligand.")
        by_name = {atom.name.upper(): atom for atom in molecule.atoms}
        def connectivity(item):
            return {tuple(sorted((bond.atom1.name.upper(), bond.atom2.name.upper()))) for bond in item.bonds}
        if connectivity(molecule) != connectivity(structure):
            raise ValueError("MATCH MOL2 and RTF ligand bond connectivity differ.")
        charge = sum(atom.charge for atom in structure.atoms)
        if (not math.isfinite(charge) or not math.isfinite(declared_charge)
                or abs(charge - net_charge) > 1e-4 or abs(declared_charge - net_charge) > 1e-4):
            raise ValueError(f"MATCH net charge changed: expected {net_charge}, RTF declares {declared_charge:g}, atom sum {charge:g}.")
        _complete_graph(structure, impropers)
        structure.load_parameters(parameter_set)
        if structure.combining_rule != "lorentz":
            raise ValueError("Ligand LJ mixing must match the parent CHARMM Lorentz-Berthelot rule.")
        _validate_bonded_parameters(structure)
        for atom in structure.atoms:
            atom.mass = atom.atom_type.mass
            source = by_name[atom.name]
            # ParmEd guesses some MOL2 elements from atom names; use the explicit
            # Tripos type instead (e.g. an atom named B can still be carbon).
            element = str(source.type).split(".", 1)[0]
            if AtomicNum.get(element, 0) != atom.atomic_number:
                raise ValueError(f"Element mismatch for MATCH atom {atom.name}.")
            if any(not math.isfinite(float(value)) for value in (atom.mass, atom.charge, atom.epsilon, atom.rmin,
                                                                 atom.epsilon_14, atom.rmin_14, source.xx, source.xy, source.xz)):
                raise ValueError("Nonfinite coordinates or force-field parameters are unsupported.")
            atom.xx, atom.xy, atom.xz = source.xx, source.xy, source.xz
        return structure
    except (OSError, ImportError):
        raise
    except Exception as exc:
        raise ValueError(f"CHARMM ligand validation failed: {exc}") from exc


def _parameter_namespace(structure, token: str):
    from parmed.charmm import CharmmPsfFile

    result = CharmmPsfFile.from_structure(structure, copy=True)
    for atom in result.atoms:
        atom_type = copy(atom.atom_type)
        atom_type.name = f"MS{token}_{atom_type.name}"
        # CHARMM files encode attractive well depths as negative epsilon;
        # GROMACS atomtypes require positive well-depth magnitudes.
        atom_type.epsilon = abs(atom_type.epsilon)
        atom_type.epsilon_14 = abs(atom_type.epsilon_14)
        atom.atom_type = atom_type
        atom.type = atom_type.name
    return result


def convert_charmm_ligand(rtf_path: str | Path, parameter_path: str | Path,
                         mol2_path: str | Path, output_dir: str | Path, *, net_charge: int) -> CharmmConversion:
    """Write a new ligand-only ITP, parameter include, GRO and reference PSF."""
    structure = load_charmm_ligand(rtf_path, parameter_path, mol2_path, net_charge=net_charge)
    output = Path(output_dir)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite CHARMM conversion output: {output}")
    from parmed.gromacs import GromacsTopologyFile

    token = hashlib.sha256(Path(parameter_path).read_bytes()).hexdigest()[:8]
    namespaced = _parameter_namespace(structure, token)
    topology = GromacsTopologyFile.from_structure(namespaced, copy=True)
    topology.defaults.gen_pairs = "no"  # Preserve distinct CHARMM LJ14 values explicitly.
    ub_pairs = {frozenset((term.atom1.idx, term.atom2.idx)) for term in topology.urey_bradleys}
    for angle in topology.angles:
        # RTF AUTO produces generic angles; ParmEd's writer selects the CHARMM
        # Urey-Bradley representation from this function code, not the UB list.
        if frozenset((angle.atom1.idx, angle.atom3.idx)) in ub_pairs:
            angle.funct = 5
    stream = StringIO()
    topology.write(stream, parameters="inline", combine="all")
    text = stream.getvalue()
    atomtypes = text.index("[ atomtypes ]")
    molecule = text.index("[ moleculetype ]")
    system = text.index("[ system ]")
    output.mkdir(parents=True, exist_ok=False)
    parameters_itp, ligand_itp = output / "ligand_parameters.itp", output / "ligand.itp"
    parameters_itp.write_text("; MATCH CHARMM parameters converted by ParmEd.\n" + text[atomtypes:molecule], encoding="utf-8", newline="\n")
    ligand_itp.write_text("; MATCH ligand; CHARMM defaults must be supplied by the parent topology.\n" + text[molecule:system], encoding="utf-8", newline="\n")
    ligand_gro, psf_path = output / "ligand.gro", output / "ligand.psf"
    structure.save(str(ligand_gro), overwrite=False)
    structure.save(str(psf_path), overwrite=False)
    counts = tuple((name, len(getattr(structure, name))) for name in
                   ("atoms", "bonds", "angles", "dihedrals", "impropers", "urey_bradleys"))
    return CharmmConversion(ligand_itp, parameters_itp, ligand_gro, psf_path,
                            tuple(atom.name for atom in structure.atoms),
                            sum(atom.charge for atom in structure.atoms), counts)
