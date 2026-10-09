"""Reproducible analysis dispatch, validated CSV tables, and Qt background job."""

from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import json
import math
import platform
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal

from analysis.base import AnalysisResult


ANALYSES = ("RMSD", "RMSF", "Radius of gyration", "SASA", "Hydrogen bonds", "PCA", "DSSP", "Contact map", "Ramachandran")


@dataclass(frozen=True)
class AnalysisInputs:
    topology: str
    trajectory: str
    output_dir: str
    analyses: tuple[str, ...]
    selection: str = "protein"
    partner_selection: str = "resname LIG"
    prepared: bool = False

    def validate(self) -> None:
        for label, filename in (("Topology", self.topology), ("Trajectory", self.trajectory)):
            if not filename.strip() or not Path(filename).is_file():
                raise ValueError(f"{label} file does not exist: {filename}")
        if not self.output_dir.strip():
            raise ValueError("Choose an output folder.")
        if not self.analyses or set(self.analyses) - set(ANALYSES):
            raise ValueError("Select at least one supported analysis.")
        if not self.selection.strip() or not self.partner_selection.strip():
            raise ValueError("Atom selections must not be empty.")
        if not self.prepared:
            raise ValueError("Confirm that molecules are whole and periodic boundary artifacts have been corrected before analysis.")


def fingerprint(path: str | Path) -> dict:
    source = Path(path).resolve()
    before = source.stat()
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"Input changed while hashing: {source}")
    return {"path": str(source), "size_bytes": after.st_size, "mtime_ns": after.st_mtime_ns, "sha256": digest.hexdigest()}


def run_backend(name: str, inputs: AnalysisInputs) -> AnalysisResult:
    from analysis.contact_map import ContactMapAnalysis
    from analysis.dihedral import RamachandranAnalysis
    from analysis.dssp import DSSPAnalysis
    from analysis.gyration import RadiusOfGyrationAnalysis
    from analysis.hbond import HydrogenBondAnalysis
    from analysis.pca import PCAAnalysis
    from analysis.rmsd import RMSDAnalysis
    from analysis.rmsf import RMSFAnalysis
    from analysis.sasa import SASAAnalysis

    backends = dict(zip(ANALYSES, (RMSDAnalysis, RMSFAnalysis, RadiusOfGyrationAnalysis, SASAAnalysis,
                                  HydrogenBondAnalysis, PCAAnalysis, DSSPAnalysis, ContactMapAnalysis, RamachandranAnalysis)))
    backend = backends[name]()
    if name == "Hydrogen bonds":
        return backend.run(inputs.topology, inputs.trajectory, selection1=inputs.selection, selection2=inputs.partner_selection)
    return backend.run(inputs.topology, inputs.trajectory, selection=inputs.selection)


def normalized_table(result: AnalysisResult) -> tuple[tuple[str, ...], list[tuple]]:
    columns = result.columns
    if not columns:
        raise ValueError("Analysis did not provide column names and units.")
    if result.name == "RMSF":
        indices = result.metadata["atom_indices_zero_based"]
        residues = result.metadata["residue_ids"]
        if len(indices) != len(result.data) or len(residues) != len(result.data):
            raise ValueError("RMSF atom mapping does not match output length.")
        source_rows = list(zip(indices, residues, result.data))
    else:
        source_rows = list(result.data)
    if not source_rows and result.name != "Hydrogen bonds":
        raise ValueError("Analysis returned no data rows.")
    # Time-series CSV files start with x and y so the existing QC reader can
    # consume them directly. Exact source frame identity remains a column.
    order = list(range(len(columns)))
    if "time_ps" in columns:
        time_index = columns.index("time_ps")
        values = [i for i, column in enumerate(columns) if column not in {"frame", "time_ps"}]
        order = [time_index, *values, columns.index("frame")]
    elif result.name == "RMSF":
        order = [1, 2, 0]
    names = tuple(columns[i].replace("_angstrom", "_nm").replace("time_ps", "time_ns") for i in order)
    rows = []
    for source in source_rows:
        if len(source) != len(columns):
            raise ValueError("Analysis result shape does not match its columns.")
        values = []
        for index in order:
            value = source[index]
            if not isinstance(value, str):
                number = float(value)
                if not math.isfinite(number):
                    raise ValueError("Analysis returned non-finite data.")
                if columns[index] == "time_ps":
                    value = number / 1000.0
                elif columns[index].endswith("_angstrom"):
                    value = number / 10.0
                elif hasattr(value, "item"):
                    value = value.item()
            values.append(value)
        rows.append(tuple(values))
    return names, rows


def _versions() -> dict:
    versions = {"python": platform.python_version()}
    for package in ("MDAnalysis", "mdtraj", "numpy"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not installed"
    return versions


def _write_manifest(path: Path, content: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(content, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def execute_analyses(inputs: AnalysisInputs, log=None, interrupted=None) -> str:
    inputs.validate()
    log = log or (lambda _message: None)
    interrupted = interrupted or (lambda: False)
    root = Path(inputs.output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    destination = Path(tempfile.mkdtemp(prefix="analysis_", dir=root))
    path = destination / "analysis_manifest.json"
    started = time.monotonic()
    sources = {"topology": fingerprint(inputs.topology), "trajectory": fingerprint(inputs.trajectory)}
    content = {"schema_version": 1, "started_utc": datetime.now(timezone.utc).isoformat(), "status": "running",
               "inputs": sources, "parameters": asdict(inputs), "software": _versions(), "results": [],
               "limitations": "PBC preparation is user-confirmed, not verified. Numeric results do not establish binding affinity or convergence."}
    _write_manifest(path, content)
    results = []
    for name in inputs.analyses:
        if interrupted():
            break
        log(f"Running {name}...")
        step_started = time.monotonic()
        try:
            result = run_backend(name, inputs)
            columns, rows = normalized_table(result)
            json.dumps(result.metadata, allow_nan=False)
            output = destination / (name.lower().replace(" ", "_") + ".csv")
            with output.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(columns)
                writer.writerows(rows)
            entry = {"name": name, "status": "complete", **fingerprint(output), "columns": columns,
                     "rows": len(rows), "message": result.message, "metadata": result.metadata}
            log(f"Saved {name}: {output}")
        except Exception as exc:
            # Per-analysis errors are explicit in both the log and manifest;
            # independent selected analyses may still finish successfully.
            entry = {"name": name, "status": "failed", "error": str(exc), "error_type": type(exc).__name__}
            log(f"{name} failed: {exc}")
        results = [*results, {**entry, "elapsed_seconds": time.monotonic() - step_started}]
        content = {**content, "results": results}
        _write_manifest(path, content)
    try:
        changed = any((Path(item["path"]).stat().st_size, Path(item["path"]).stat().st_mtime_ns)
                      != (item["size_bytes"], item["mtime_ns"]) for item in sources.values())
    except OSError:
        changed = True
    complete = sum(item["status"] == "complete" for item in results)
    status = "complete" if complete == len(inputs.analyses) else "partial" if complete else "failed"
    if changed:
        status = "failed"
    if interrupted():
        status = "cancelled"
    content = {**content, "status": status, "source_changed": changed, "elapsed_seconds": time.monotonic() - started,
               "finished_utc": datetime.now(timezone.utc).isoformat()}
    _write_manifest(path, content)
    return str(path)


class AnalysisWorker(QThread):
    log = pyqtSignal(str)
    completed = pyqtSignal(bool, str, str)

    def __init__(self, inputs: AnalysisInputs, parent=None):
        super().__init__(parent)
        self.inputs = inputs

    def run(self) -> None:
        try:
            path = execute_analyses(self.inputs, self.log.emit, self.isInterruptionRequested)
            manifest = json.loads(Path(path).read_text(encoding="utf-8"))
            self.completed.emit(manifest["status"] == "complete", path, f"Analysis {manifest['status']}: {path}")
        except Exception as exc:
            self.completed.emit(False, "", f"Analysis failed: {exc}")
