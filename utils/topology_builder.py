"""ACPYPE ligand parameterization wrapper.

Generates a GROMACS-compatible topology for a small molecule (MOL2/SDF/PDB)
by calling ``acpype`` inside the moldynstudio conda environment via
:mod:`core.wsl_bridge`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
import re
import shutil

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


class AcpypeOutputError(ValueError):
    """Raised when ACPYPE did not produce a usable ligand topology."""


@dataclass(frozen=True)
class LigandTopologyArtifacts:
    """Canonical ligand files produced by ACPYPE."""

    ligand_gro: Path
    ligand_itp: Path
    ligand_posre_itp: Path | None = None

    @property
    def gro(self) -> Path:
        return self.ligand_gro

    @property
    def itp(self) -> Path:
        return self.ligand_itp

    @property
    def posre(self) -> Path | None:
        return self.ligand_posre_itp


def read_molecule_name_from_itp(path: str | Path) -> str:
    """Read the sole molecule name declared in an ITP ``[ moleculetype ]``."""

    section = False
    names: list[str] = []
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower() == "moleculetype"
            continue
        if section:
            fields = line.split()
            # ACPYPE/GROMACS requires exactly ``name nrexcl`` here.  Being
            # strict prevents accidentally treating an arbitrary ITP as the
            # ligand topology.
            if len(fields) != 2 or not fields[0]:
                raise AcpypeOutputError(
                    f"Invalid [ moleculetype ] record in ITP: {path}"
                )
            try:
                nrexcl = int(fields[1])
            except ValueError as exc:
                raise AcpypeOutputError(
                    f"Invalid [ moleculetype ] nrexcl in ITP: {path}"
                ) from exc
            if nrexcl < 0:
                raise AcpypeOutputError(
                    f"Invalid [ moleculetype ] nrexcl in ITP: {path}"
                )
            names.append(fields[0])
            section = False
    unique = list(dict.fromkeys(names))
    if len(unique) != 1 or len(names) != 1:
        raise AcpypeOutputError(f"ITP must contain one clear [ moleculetype ]: {path}")
    return unique[0]


def _is_posre(path: Path) -> bool:
    text = path.read_text(encoding="utf-8", errors="replace")
    return bool(re.search(r"(?im)^\s*\[\s*position_restraints\s*\]", text))


def _is_gro(path: Path) -> bool:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return len(lines) >= 3 and bool(re.match(r"^\s*\d+\s*$", lines[1]))
    except OSError:
        return False


def normalize_acpype_outputs(acpype_dir: str | Path, project_dir: str | Path) -> LigandTopologyArtifacts:
    """Find ACPYPE outputs by content and copy them to ``project/ligand``."""

    source = Path(acpype_dir)
    if not source.is_dir():
        raise AcpypeOutputError(f"ACPYPE output directory does not exist: {source}")
    destination = Path(project_dir) / "ligand"
    destination_resolved = destination.resolve()

    def in_destination(path: Path) -> bool:
        try:
            path.resolve().relative_to(destination_resolved)
            return True
        except ValueError:
            return False

    itps: list[tuple[Path, str]] = []
    for candidate in source.rglob("*.itp"):
        if in_destination(candidate):
            continue
        if _is_posre(candidate):
            continue
        try:
            itps.append((candidate, read_molecule_name_from_itp(candidate)))
        except (OSError, AcpypeOutputError):
            continue
    if len(itps) != 1:
        raise AcpypeOutputError("Expected exactly one ligand topology ITP in ACPYPE outputs")
    itp = itps[0][0]
    gros = [p for p in source.rglob("*.gro") if not in_destination(p) and _is_gro(p)]
    if not gros:
        raise AcpypeOutputError("ACPYPE outputs do not contain a valid GRO structure")
    # ACPYPE's *_GMX.gro is preferred; otherwise a single valid GRO is required.
    preferred = [p for p in gros if "_gmx" in p.stem.lower()]
    if len(preferred) == 1:
        gro = preferred[0]
    elif len(gros) == 1:
        gro = gros[0]
    else:
        raise AcpypeOutputError("Could not identify one unambiguous ACPYPE GRO structure")
    posres = [p for p in source.rglob("*.itp") if not in_destination(p) and _is_posre(p)]
    if len(posres) > 1:
        raise AcpypeOutputError("Multiple position-restraint ITP files found")
    destination.mkdir(parents=True, exist_ok=True)
    out_gro = destination / "ligand.gro"
    out_itp = destination / "ligand.itp"
    shutil.copyfile(gro, out_gro)
    shutil.copyfile(itp, out_itp)
    out_posre = destination / "ligand_posre.itp" if posres else None
    if posres:
        shutil.copyfile(posres[0], out_posre)
    elif (destination / "ligand_posre.itp").exists():
        (destination / "ligand_posre.itp").unlink()
    return LigandTopologyArtifacts(out_gro, out_itp, out_posre)


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
    artifacts_ready = pyqtSignal(object)

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
            try:
                artifacts = normalize_acpype_outputs(p.work_dir, p.work_dir)
            except (OSError, AcpypeOutputError) as exc:
                self.done.emit(False, f"ACPYPE completed but normalization failed: {exc}")
                return
            self.artifacts_ready.emit(artifacts)
            self.done.emit(True, "Ligand topology generated.")
        else:
            self.done.emit(False, f"ACPYPE exited with status {rc}. See log above.")
