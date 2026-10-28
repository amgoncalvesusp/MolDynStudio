"""Lazy frame inspection and auditable, same-frame DockLens handoff.

Export preserves coordinates without fitting, wrapping, chemical perception or
bond-order assignment. The caller must review periodic-boundary preprocessing.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import string
import subprocess
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class TrajectoryError(ValueError):
    """An input cannot be inspected or exported without losing its meaning."""


@dataclass(frozen=True)
class FrameSnapshot:
    frame_index: int
    n_frames: int
    time_ps: float | None
    receptor_count: int
    ligand_count: int
    receptor_coordinates: tuple[tuple[float, float, float], ...]
    ligand_coordinates: tuple[tuple[float, float, float], ...]
    display_reduced: bool
    receptor_bonds: tuple[tuple[int, int], ...] = ()
    ligand_bonds: tuple[tuple[int, int], ...] = ()
    structure_only: bool = False
    highlight_coordinates: tuple[tuple[float, float, float], ...] = ()
    highlight_label: str = ""


@dataclass(frozen=True)
class FrameExport:
    directory: Path
    manifest_path: Path
    provenance_path: Path
    frame_index: int


def _signature(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _attribute(atom, name: str, default=""):
    try:
        return getattr(atom, name)
    except (AttributeError, ValueError):
        return default


def _reader_path(path: Path) -> str:
    original = str(path)
    if os.name != "nt" or original.isascii() or path.suffix.lower() not in {".xtc", ".trr"}:
        return original
    import ctypes

    # shortcut: Windows XDR readers need ASCII aliases; remove when they support Unicode.
    message = (
        "The Windows trajectory reader needs an ASCII path, and a matching ASCII short path "
        "is unavailable. Select source files under ASCII-only folder and file names."
    )
    try:
        get_short_path = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
        get_short_path.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
        get_short_path.restype = ctypes.c_uint32
        buffer = ctypes.create_unicode_buffer(32768)
        length = get_short_path(original, buffer, len(buffer))
        alias = buffer.value
        if not 0 < length < len(buffer) or not alias.isascii() or not os.path.samefile(original, alias):
            raise TrajectoryError(message)
        return alias
    except OSError as exc:
        raise TrajectoryError(message) from exc


class TrajectorySession:
    """One disk-backed reader; callers serialize access to its current frame."""

    def __init__(self, universe, topology: Path, trajectory: Path, *, structure_only=False):
        self.universe = universe
        self.topology = topology
        self.trajectory = trajectory
        self.structure_only = structure_only
        self._source_signatures = (_signature(topology), _signature(trajectory))
        self._analysis_source_hashes = None

    @classmethod
    def open(cls, topology: str, trajectory: str = "") -> "TrajectorySession":
        try:
            if not isinstance(topology, (str, os.PathLike)) or not str(topology).strip():
                raise ValueError("Choose an existing structure or topology file.")
            if not isinstance(trajectory, (str, os.PathLike)):
                raise ValueError("The trajectory path must be text or a filesystem path.")
            structure_only = not str(trajectory).strip()
            sources = (topology, topology if structure_only else trajectory)
            paths = tuple(Path(value).expanduser().resolve() for value in sources)
            if not all(path.is_file() for path in paths):
                raise ValueError("Choose existing structure/topology and trajectory files.")
        except (OSError, TypeError, ValueError, RuntimeError) as exc:
            raise TrajectoryError(f"Cannot open the selected source files: {exc}") from exc
        try:
            import MDAnalysis as mda
        except ImportError as exc:
            raise TrajectoryError(
                "MDAnalysis is required in the Python environment running MolDynStudio. "
                "Install the GUI requirements and restart MolDynStudio."
            ) from exc
        try:
            # Do not silently guess elements, types or bonds for external chemistry.
            inputs = paths[:1] if structure_only else paths
            universe = mda.Universe(*(_reader_path(path) for path in inputs), to_guess=())
            if not len(universe.trajectory):
                universe.trajectory.close()
                raise TrajectoryError("The trajectory has no readable frames.")
            return cls(universe, *paths, structure_only=structure_only)
        except (OSError, ValueError, TypeError, EOFError) as exc:
            if paths[0].suffix.lower() == ".tpr" and "tpx version" in str(exc):
                raise TrajectoryError(
                    "This TPR version is not supported by the installed MDAnalysis reader. "
                    "The default GROMACS 2025.4 environment produces compatible TPR files. "
                    "For a newer existing run, explicitly convert its topology to a matching PDB "
                    "with GROMACS editconf -conect and check atom order and chemical typing. "
                    "PDB does not retain force-field charges or bond orders."
                ) from exc
            raise TrajectoryError(f"Cannot open the trajectory: {exc}") from exc

    def close(self) -> None:
        self.universe.trajectory.close()

    def _assert_sources_unchanged(self) -> None:
        if self._source_signatures != (_signature(self.topology), _signature(self.trajectory)):
            raise TrajectoryError("A source file changed after loading. Reload the trajectory first.")

    def _select(self, frame_index: int, receptor: str, ligand: str):
        from MDAnalysis.exceptions import SelectionError

        self._assert_sources_unchanged()
        n_frames = 1 if self.structure_only else len(self.universe.trajectory)
        if type(frame_index) is not int or not 0 <= frame_index < n_frames:
            raise TrajectoryError("Frame index is outside the trajectory (indices start at 0).")
        if not receptor.strip():
            raise TrajectoryError("Enter a receptor selection.")
        try:
            self.universe.trajectory[frame_index]
            groups = (
                self.universe.select_atoms(receptor),
                self.universe.select_atoms(ligand) if ligand.strip() else self.universe.atoms[[]],
            )
        except (ValueError, TypeError, IndexError, SelectionError) as exc:
            raise TrajectoryError(f"Cannot read the frame or selection: {exc}") from exc
        if set(groups[0].indices) & set(groups[1].indices):
            raise TrajectoryError("Receptor and ligand selections overlap. Choose disjoint atom groups.")
        return groups

    def read_frame(self, frame_index: int, receptor_selection="protein", ligand_selection="resname LIG") -> FrameSnapshot:
        self._assert_sources_unchanged()
        previous_frame = int(self.universe.trajectory.frame)
        try:
            groups = self._select(frame_index, receptor_selection, ligand_selection)
            limit = 6000
            step = max(1, math.ceil(sum(len(group) for group in groups) / limit))
            displayed = tuple(group[::step] for group in groups)
            coordinates = tuple(tuple(tuple(float(value) for value in xyz) for xyz in group.positions)
                                for group in displayed)
            bonds = self._bonds()
            displayed_bonds = tuple(self._display_bonds(group.indices, bonds) for group in displayed)
            raw_time = None if self.structure_only else float(self.universe.trajectory.ts.time)
            self._assert_sources_unchanged()
            return FrameSnapshot(
                frame_index, 1 if self.structure_only else len(self.universe.trajectory),
                raw_time if raw_time is not None and math.isfinite(raw_time) else None,
                len(groups[0]), len(groups[1]), *coordinates, step > 1,
                *displayed_bonds, self.structure_only,
            )
        except Exception as exc:
            self._restore_frame(previous_frame, exc)
            raise

    def _restore_frame(self, frame_index: int, error: Exception) -> None:
        if self.universe.trajectory.frame == frame_index:
            return
        try:
            self._assert_sources_unchanged()
            self.universe.trajectory[frame_index]
        except (OSError, TypeError, ValueError, IndexError, EOFError) as restore_error:
            raise TrajectoryError(
                f"Frame operation failed: {error}. Previous frame could not be restored: "
                f"{restore_error}. Reload the trajectory."
            ) from restore_error

    def read_analysis_point(self, source: str, row_index: int,
                            receptor_selection="protein", ligand_selection="resname LIG") -> FrameSnapshot:
        """Navigate one provenanced CSV row without changing export selections."""
        from analysis.result_links import load_result_rows

        previous_frame = None
        try:
            self._assert_sources_unchanged()
            if self.structure_only:
                raise ValueError("Analysis navigation requires an explicit trajectory.")
            previous_frame = int(self.universe.trajectory.frame)
            columns, rows, manifest, result = load_result_rows(source)
            if type(row_index) is not int or not 0 <= row_index < len(rows):
                raise ValueError("Analysis row index is outside the result (indices start at 0).")
            self._check_analysis_sources(manifest)
            row = dict(zip(columns, rows[row_index]))
            origin = f"{Path(source).name}, numeric row {row_index} (0-based)"
            if result["name"] != "RMSF":
                frame = row["frame"]
                if not frame.is_integer():
                    raise ValueError("Analysis frame index must be an integer.")
                snapshot = self.read_frame(int(frame), receptor_selection, ligand_selection)
                if snapshot.time_ps is None or not math.isclose(snapshot.time_ps, row["time_ns"] * 1000,
                                                                rel_tol=1e-6, abs_tol=1e-5):
                    raise ValueError("Analysis time does not match the exact source frame.")
                self._assert_sources_unchanged()
                return replace(snapshot, highlight_label=f"{origin}; exact frame {int(frame)}, {snapshot.time_ps:g} ps")
            snapshot = self._highlight_analysis_residue(row, row_index, rows, result["metadata"],
                                                         receptor_selection, ligand_selection)
            self._assert_sources_unchanged()
            return replace(snapshot, highlight_label=f"{origin}; {snapshot.highlight_label}. Per-atom RMSF; no residue averaging.")
        except (OSError, TypeError, ValueError, KeyError, IndexError) as exc:
            if previous_frame is not None:
                self._restore_frame(previous_frame, exc)
            raise TrajectoryError(f"Cannot inspect analysis point: {exc}") from exc

    def _check_analysis_sources(self, manifest):
        inputs = manifest.get("inputs")
        if not isinstance(inputs, dict):
            raise TypeError("Analysis manifest is missing source fingerprints.")
        expected = []
        for key, path in (("topology", self.topology), ("trajectory", self.trajectory)):
            item = inputs.get(key)
            if (not isinstance(item, dict) or not isinstance(item.get("path"), str)
                    or Path(item["path"]).resolve() != path or type(item.get("size_bytes")) is not int
                    or item["size_bytes"] != path.stat().st_size or not isinstance(item.get("sha256"), str)):
                raise ValueError(f"Analysis {key} does not match the open 3D source.")
            expected.append(item["sha256"])
        if self._analysis_source_hashes is None:
            hashes = tuple(_sha256(path) for path in (self.topology, self.trajectory))
            self._assert_sources_unchanged()
            self._analysis_source_hashes = hashes
        if tuple(expected) != self._analysis_source_hashes:
            raise ValueError("Analysis source hashes do not match the open 3D sources.")

    def _highlight_analysis_residue(self, row, row_index, rows, metadata, receptor, ligand):
        index = row["atom_index"]
        if not index.is_integer() or not 0 <= index < len(self.universe.atoms):
            raise ValueError("RMSF atom index is outside the source topology.")
        atom = self.universe.atoms[int(index)]
        identity = {
            "atom_indices_zero_based": int(atom.index), "residue_ids": int(atom.resid),
            "residue_indices_zero_based": int(atom.resindex), "segment_indices_zero_based": int(atom.segindex),
            "atom_names": str(atom.name),
        }
        for key, value in identity.items():
            values = metadata.get(key)
            if (not isinstance(values, list) or len(values) != len(rows)
                    or type(values[row_index]) is not type(value) or values[row_index] != value):
                raise ValueError("RMSF atom/residue identity does not match the source topology.")
        if row["residue_id"] != atom.resid:
            raise ValueError("RMSF CSV residue ID does not match its atom mapping.")
        snapshot = self.read_frame(int(self.universe.trajectory.frame), receptor, ligand)
        coordinates = tuple(tuple(map(float, xyz)) for xyz in atom.residue.atoms.positions)
        if not all(math.isfinite(value) for xyz in coordinates for value in xyz):
            raise ValueError("Highlighted residue contains non-finite coordinates.")
        label = (f"{atom.resname} {atom.resid}, resindex {atom.resindex}, segment {atom.segindex} "
                 f"({atom.segid}); RMSF atom {atom.index} {atom.name}")
        return replace(snapshot, highlight_coordinates=coordinates, highlight_label=label)

    @staticmethod
    def _display_bonds(indices, bonds) -> tuple[tuple[int, int], ...]:
        positions = {int(index): local for local, index in enumerate(indices)}
        return tuple((positions[left], positions[right]) for left, right in bonds
                     if left in positions and right in positions)

    def export_frame(self, output_dir, frame_index: int, receptor_selection: str,
                     ligand_selection: str, pbc_reviewed: bool, profile="plip") -> FrameExport:
        if self.structure_only:
            raise TrajectoryError("Open a trajectory before exporting a molecular-dynamics frame.")
        if not pbc_reviewed:
            raise TrajectoryError("Review periodic boundaries and keep receptor and ligand in the same image before export.")
        if profile not in {"plip", "luna", "dsv", "luna_dsv"}:
            raise TrajectoryError("Unsupported DockLens scientific profile.")
        groups = self._select(frame_index, receptor_selection, ligand_selection)
        if any(len(group) == 0 for group in groups):
            raise TrajectoryError("Export requires nonempty receptor and ligand selections.")
        bonds = self._bonds()
        left, right = (set(map(int, group.indices)) for group in groups)
        if any((a in left and b in right) or (b in left and a in right) for a, b in bonds):
            raise TrajectoryError("A covalent bond joins receptor and ligand. Separate-pair export is unsupported.")
        if any((a in right) != (b in right) for a, b in bonds):
            raise TrajectoryError("The selection cuts a bond. Select the complete ligand, including its bonded atoms.")
        chain_map = self._chain_mapping(groups)
        records = tuple(self._records(group, chain_map) for group in groups)
        if not str(output_dir).strip():
            raise TrajectoryError("Choose an output folder.")
        return self._publish(output_dir, frame_index, receptor_selection, ligand_selection, records, bonds, profile)

    def _bonds(self) -> tuple[tuple[int, int], ...]:
        try:
            return tuple(tuple(map(int, bond)) for bond in self.universe.bonds.indices)
        except (AttributeError, ValueError):
            return ()

    def _chain_mapping(self, groups) -> dict[tuple[int, str], str]:
        """Reserve source PDB chains, then map long/blank TPR chains uniquely.

        Segment index separates repeated source chain labels. One mapping covers
        both exported partners; allocation follows original atom order.
        """
        indices = sorted({int(index) for group in groups for index in group.indices})
        identities = tuple(dict.fromkeys(
            (int(atom.segindex), str(_attribute(atom, "chainID")))
            for atom in self.universe.atoms[indices]
        ))
        alphabet = string.ascii_uppercase + string.ascii_lowercase + string.digits
        mapping = {}
        for identity in identities:
            source_chain = identity[1].strip()
            if len(source_chain) == 1 and source_chain in alphabet and source_chain not in mapping.values():
                mapping = {**mapping, identity: source_chain}
        available = iter(symbol for symbol in alphabet if symbol not in mapping.values())
        for identity in identities:
            if identity in mapping:
                continue
            try:
                mapping = {**mapping, identity: next(available)}
            except StopIteration as exc:
                raise TrajectoryError("Too many selected chains for unambiguous PDB chain identifiers.") from exc
        return mapping

    def _records(self, group, chain_map=None) -> tuple[dict, ...]:
        if len(group) > 99999:
            raise TrajectoryError("PDB export is limited to 99,999 atoms per selection.")
        chain_map = self._chain_mapping((group,)) if chain_map is None else chain_map
        records = []
        for serial, atom in enumerate(group, 1):
            if str(_attribute(atom, "icode")).strip():
                raise TrajectoryError("DockLens PDB import cannot preserve residue insertion codes; use unambiguous residue numbering.")
            element = str(_attribute(atom, "element")).strip()
            if not element or len(element) > 2 or not element.isalpha():
                raise TrajectoryError("Explicit atom elements are required; use a matching topology with element identities.")
            segid = str(_attribute(atom, "segid"))
            source_chain = str(_attribute(atom, "chainID"))
            source_segindex = int(atom.segindex)
            chain = chain_map[(source_segindex, source_chain)]
            values = dict(
                serial=serial, source_index=int(atom.index), source_id=int(_attribute(atom, "id", atom.index)), name=str(atom.name),
                resname=str(atom.resname), resid=int(atom.resid), chain=chain,
                source_chain=source_chain, segid=segid, source_segindex=source_segindex,
                source_resindex=int(atom.resindex), element=element,
                formal_charge=int(_attribute(atom, "formalcharge", 0)),
                formal_charge_known=_attribute(atom, "formalcharge", None) is not None,
                xyz=tuple(map(float, atom.position)),
            )
            _validate_record(values)
            records.append(values)
        residue_ids = {(item["chain"], item["resid"], item["source_resindex"]) for item in records}
        if len({item[:2] for item in residue_ids}) != len(residue_ids):
            raise TrajectoryError("PDB residue identities collide. Use unique chain and residue identifiers.")
        return tuple(records)

    def _publish(self, output_dir, index, receptor, ligand, records, bonds, profile):
        root = Path(output_dir).expanduser().resolve()
        stage = None
        try:
            root.mkdir(parents=True, exist_ok=True)
            name = f"frame_{index:06d}_{uuid4().hex[:12]}"
            destination = root / name
            stage = Path(tempfile.mkdtemp(prefix=".frame-", dir=root)).resolve()
            for filename, atoms in zip(("receptor.pdb", "ligand.pdb"), records):
                _write_pdb(stage / filename, atoms, bonds)
            (stage / "reports").mkdir()
            sources = {
                key: {"path": str(path), "sha256": _sha256(path), "size_bytes": path.stat().st_size}
                for key, path in (("topology", self.topology), ("trajectory", self.trajectory))
            }
            self._assert_sources_unchanged()
            self._write_metadata(stage, destination, index, receptor, ligand, records, bonds, profile, sources)
            stage.rename(destination)
            stage = None
            return FrameExport(destination, destination / "docklens_launch.json", destination / "provenance.json", index)
        except (OSError, ValueError) as exc:
            raise TrajectoryError(f"Cannot export the frame: {exc}") from exc
        finally:
            # Only our unique, verified staging child can be recursively removed.
            if stage is not None and stage.parent == root and stage.name.startswith(".frame-") and not stage.is_symlink():
                shutil.rmtree(stage)

    def _write_metadata(self, stage, destination, index, receptor, ligand, records, bonds, profile, sources):
        from MDAnalysis import __version__ as reader_version

        manifest = {
            "schema": "docklens-launch-v1", "project_root": str(destination),
            "run_id": destination.name,
            "receptor": {"path": "receptor.pdb", "sha256": _sha256(stage / "receptor.pdb")},
            "poses": {"path": "ligand.pdb", "sha256": _sha256(stage / "ligand.pdb")},
            "options": {"hbond_preset": profile, "key_residues": []},
            "result": {"schema": "docklens-result-v1", "path": "reports/docklens_result_frame.json"},
        }
        raw_time = float(self.universe.trajectory.ts.time)
        provenance = {
            "schema": "moldynstudio-frame-v1", "created_at": datetime.now(timezone.utc).isoformat(),
            "generated_by": {"application": "MolDynStudio", "reader": "MDAnalysis", "reader_version": reader_version},
            "sources": sources, "frame_index": index, "frame_index_origin": 0,
            "time_ps": raw_time if math.isfinite(raw_time) else None,
            "time_basis": "MDAnalysis reader; verify source timestep and time origin",
            "coordinate_unit": "angstrom", "preprocessing": "none; source coordinates preserved",
            "periodic_boundaries_reviewed": True,
            "selections": {"receptor": receptor, "ligand": ligand},
            "receptor_atom_map": [{key: value for key, value in item.items() if key != "xyz"} for item in records[0]],
            "ligand_atom_map": [{key: value for key, value in item.items() if key != "xyz"} for item in records[1]],
            "bond_basis": "source topology connectivity" if bonds else "absent; DockLens may infer bonds by distance",
            "limitations": [
                "PDB does not preserve bond orders or force-field atom types; validate chemical typing in DockLens.",
                "Absent formal charges are not reconstructed from partial charges.",
                "One frame does not establish interaction occupancy, convergence or binding affinity.",
                "Choose Molecular dynamics frames in the DockLens Evidence control.",
            ],
        }
        _write_json(stage / "docklens_launch.json", manifest)
        _write_json(stage / "provenance.json", {**provenance, "launch_manifest_sha256": _sha256(stage / "docklens_launch.json")})


def _validate_record(item: dict) -> None:
    for key, maximum in (("name", 4), ("resname", 3), ("chain", 1)):
        value = item[key]
        if not value or len(value) > maximum or not value.isascii() or any(char.isspace() for char in value):
            raise TrajectoryError(f"Atom {key} cannot be represented faithfully in PDB format.")
    if not -999 <= item["resid"] <= 9999 or abs(item["formal_charge"]) > 9:
        raise TrajectoryError("Residue identity or formal charge exceeds the PDB field limits.")
    if any(not math.isfinite(value) or not -999.999 <= value <= 9999.999 for value in item["xyz"]):
        raise TrajectoryError("Coordinates are nonfinite or outside PDB field limits.")


def _write_pdb(path: Path, records: tuple[dict, ...], bonds) -> None:
    serials = {item["source_index"]: item["serial"] for item in records}
    adjacency = {serial: [] for serial in serials.values()}
    for a, b in bonds:
        if a in serials and b in serials:
            adjacency[serials[a]].append(serials[b])
            adjacency[serials[b]].append(serials[a])
    with path.open("w", encoding="ascii", newline="\n") as output:
        output.write("REMARK MolDynStudio same-frame export; see provenance.json\n")
        for item in records:
            x, y, z = item["xyz"]
            charge = item["formal_charge"]
            charge_text = f"{abs(charge)}{'+' if charge > 0 else '-'}" if charge else "  "
            output.write(
                f"ATOM  {item['serial']:5d} {item['name']:<4} {item['resname']:>3} "
                f"{item['chain']}{item['resid']:4d}    {x:8.3f}{y:8.3f}{z:8.3f}"
                f"{1:6.2f}{0:6.2f}          {item['element']:>2}{charge_text}\n"
            )
        for serial, neighbors in adjacency.items():
            for start in range(0, len(neighbors), 4):
                output.write(f"CONECT{serial:5d}" + "".join(f"{value:5d}" for value in sorted(neighbors)[start:start + 4]) + "\n")
        output.write("END\n")


def launch_docklens(binary: str, manifest_path: Path):
    """Launch an explicitly chosen installed executable without a shell."""
    if binary.strip().lower() == "auto":
        binary = shutil.which("docklens") or shutil.which("DockLens-windows-x86_64") or shutil.which("DockLens-linux-x86_64") or ""
    executable = Path(binary).expanduser().resolve()
    manifest = Path(manifest_path).resolve()
    if not binary.strip() or not executable.is_file():
        raise TrajectoryError("Choose an installed DockLens executable with --manifest support.")
    # Windows runs batch files through cmd.exe even with shell=False, which
    # would interpret shell metacharacters inside a user-selected export path.
    if os.name == "nt" and executable.suffix.lower() != ".exe":
        raise TrajectoryError("Choose the installed DockLens .exe file; script launchers are unsupported on Windows.")
    if not manifest.is_file():
        raise TrajectoryError("Export the current frame before opening DockLens.")
    options = {"shell": False, "cwd": str(manifest.parent)}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        options["start_new_session"] = True
    try:
        return subprocess.Popen([str(executable), "--manifest", str(manifest)], **options)
    except OSError as exc:
        raise TrajectoryError(f"Cannot start DockLens: {exc}") from exc
