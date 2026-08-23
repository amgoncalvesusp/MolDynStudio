"""ACPYPE ligand parameterization wrapper.

Generates a GROMACS-compatible topology for a small molecule (MOL2/SDF/PDB)
by calling ``acpype`` inside the moldynstudio conda environment via
:mod:`core.wsl_bridge`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

try:
    from PyQt5.QtCore import QThread, pyqtSignal
except Exception:  # pragma: no cover - allow headless import
    class _Signal:
        def connect(self, *_a, **_k) -> None: return None
        def emit(self, *_a, **_k) -> None: return None

    def pyqtSignal(*_a, **_k):  # type: ignore[no-redef]
        return _Signal()

    class QThread:  # type: ignore[no-redef]
        def __init__(self, *a, **k): pass
        def start(self) -> None: self.run()

from core import wsl_bridge


CHARGE_METHODS: Mapping[str, str] = {
    "AM1-BCC": "bcc",
    "Gasteiger": "gas",
    "RESP": "resp",
    "Formal charge only": "user",
}

TWO_LETTER_ELEMENTS = frozenset(
    {
        "Ac", "Ag", "Al", "Am", "Ar", "As", "At", "Au",
        "Ba", "Be", "Bh", "Bi", "Bk", "Br",
        "Ca", "Cd", "Ce", "Cf", "Cl", "Cm", "Cn", "Co", "Cr", "Cs", "Cu",
        "Ds", "Dy", "Er", "Es", "Eu", "Fe", "Fl", "Fm", "Fr",
        "Ga", "Gd", "Ge", "Hf", "Hg", "Ho", "Hs",
        "In", "Ir", "Kr", "La", "Li", "Lr", "Lu", "Lv",
        "Mc", "Md", "Mg", "Mn", "Mo", "Mt",
        "Na", "Nb", "Nd", "Ne", "Nh", "Ni", "No", "Np",
        "Os", "Pa", "Pb", "Pd", "Pm", "Po", "Pr", "Pt", "Pu",
        "Ra", "Rb", "Re", "Rf", "Rg", "Rh", "Rn", "Ru",
        "Sb", "Sc", "Se", "Sg", "Si", "Sm", "Sn", "Sr",
        "Ta", "Tb", "Tc", "Te", "Th", "Ti", "Tl", "Tm", "Ts",
        "Xe", "Yb", "Zn", "Zr",
    }
)


class UnsupportedLigandChemistryError(ValueError):
    """Raised when ACPYPE/GAFF2 cannot represent the supplied chemistry."""


def _normalize_element(value: str) -> str:
    cleaned = "".join(character for character in value if character.isalpha())
    if not cleaned:
        return ""
    return cleaned[0].upper() + cleaned[1:].lower()


def _elements_from_pdb(lines: list[str]) -> frozenset[str]:
    elements: set[str] = set()
    for line in lines:
        if not line.startswith(("ATOM  ", "HETATM")):
            continue
        element = _normalize_element(line[76:78] if len(line) >= 78 else "")
        if not element:
            atom_field = line[12:16].ljust(4)
            if atom_field[0].isdigit():
                element = _normalize_element(atom_field[1])
            elif atom_field[0].isspace():
                element = _normalize_element(atom_field[1])
            else:
                candidate = _normalize_element(atom_field[:2])
                element = candidate if candidate in TWO_LETTER_ELEMENTS else candidate[:1]
        if element:
            elements.add(element)
    return frozenset(elements)


def _elements_from_v3000(lines: list[str]) -> frozenset[str]:
    elements: set[str] = set()
    in_atom_section = False
    for line in lines:
        stripped = line.strip()
        if stripped == "M  V30 BEGIN ATOM":
            in_atom_section = True
            continue
        if stripped == "M  V30 END ATOM":
            break
        if not in_atom_section:
            continue
        fields = stripped.split()
        if len(fields) < 4 or fields[0:2] != ["M", "V30"]:
            continue
        element = _normalize_element(fields[3])
        if element:
            elements.add(element)
    return frozenset(elements)


def _elements_from_mol(lines: list[str]) -> frozenset[str]:
    if len(lines) < 4:
        return frozenset()
    if "V3000" in lines[3]:
        return _elements_from_v3000(lines)
    try:
        atom_count = int(lines[3][0:3])
    except (ValueError, IndexError):
        return frozenset()

    elements: set[str] = set()
    for line in lines[4 : 4 + atom_count]:
        field = line[31:34].strip() if len(line) >= 34 else ""
        if not field:
            fields = line.split()
            field = fields[3] if len(fields) >= 4 else ""
        element = _normalize_element(field)
        if element:
            elements.add(element)
    return frozenset(elements)


def _elements_from_mol2(lines: list[str]) -> frozenset[str]:
    elements: set[str] = set()
    in_atom_section = False
    for line in lines:
        if line.startswith("@<TRIPOS>"):
            in_atom_section = line.strip() == "@<TRIPOS>ATOM"
            continue
        if not in_atom_section or not line.strip():
            continue
        fields = line.split()
        if len(fields) < 6:
            continue
        element = _normalize_element(fields[5].split(".", maxsplit=1)[0])
        if element:
            elements.add(element)
    return frozenset(elements)


def validate_acpype_input(ligand_path: str | Path) -> frozenset[str]:
    """Return ligand elements or fail early for unsupported ACPYPE chemistry."""

    path = Path(ligand_path)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    suffix = path.suffix.lower()
    if suffix == ".pdb":
        elements = _elements_from_pdb(lines)
    elif suffix in {".sdf", ".mol"}:
        elements = _elements_from_mol(lines)
    elif suffix == ".mol2":
        elements = _elements_from_mol2(lines)
    else:
        elements = frozenset()

    if "B" in elements:
        raise UnsupportedLigandChemistryError(
            "ACPYPE/GAFF2 is not suitable for this boron-containing ligand. "
            "Use validated CGenFF parameters instead. For a ligand bonded to "
            "the protein, use a dedicated covalent-ligand workflow."
        )
    return elements


@dataclass(frozen=True)
class LigandParams:
    ligand_path: str
    work_dir: str
    charge_method: str = "AM1-BCC"
    net_charge: int = 0
    base_name: str = "ligand"


class LigandParamWorker(QThread):
    """Run ACPYPE in a worker thread and stream its output."""

    log = pyqtSignal(str)
    done = pyqtSignal(bool, str)

    def __init__(self, params: LigandParams, parent=None):
        super().__init__(parent)
        self.p = params

    def run(self) -> None:
        p = self.p
        try:
            validate_acpype_input(p.ligand_path)
        except (OSError, UnsupportedLigandChemistryError) as exc:
            self.done.emit(False, str(exc))
            return

        wsl_lig = wsl_bridge.win_to_wsl(p.ligand_path)
        cm = CHARGE_METHODS.get(p.charge_method, "bcc")

        cmd = [
            "acpype",
            "-i", wsl_lig,
            "-c", cm,
            "-n", str(int(p.net_charge)),
            "-b", p.base_name,
            "-o", "gmx",
        ]
        self.log.emit("Running ACPYPE: " + " ".join(cmd))
        try:
            proc = wsl_bridge.popen(cmd, cwd=p.work_dir)
        except (OSError, FileNotFoundError) as exc:
            self.done.emit(False, f"Failed to launch ACPYPE: {exc}")
            return

        if proc.stdout is None:
            self.done.emit(False, "ACPYPE stdout not captured.")
            return
        try:
            for line in proc.stdout:
                self.log.emit(line.rstrip())
        finally:
            rc = proc.wait()
        if rc == 0:
            self.done.emit(True, "Ligand topology generated.")
        else:
            self.done.emit(False, f"ACPYPE exited with status {rc}. See log above.")
