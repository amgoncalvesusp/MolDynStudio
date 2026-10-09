"""Export a compact, auditable record without changing simulation artifacts."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import tempfile
import zipfile

from core.run_manifest import MANIFEST_FILENAME, load_manifest


_TEXT_SUFFIXES = {".mdp", ".top", ".itp", ".pdb", ".gro", ".xvg", ".csv", ".log", ".ndx",
                  ".fasta", ".fa", ".faa"}
_BINARY_SUFFIXES = {".xtc", ".trr", ".tpr", ".edr", ".cpt", ".dcd", ".nc"}
_JSON_NAMES = {MANIFEST_FILENAME, "analysis_manifest.json", "qc_manifest.json",
               "qc_commands.json", "provenance.json", "docklens_launch.json",
               "docklens_result_frame.json", "mmpbsa_manifest.json",
               "launch.json", "result.json", "stage_qc.json", "audit.json", "plan.json",
               "repair_report.json"}
_METHOD_FILES = {"MMPBSA.in", "FINAL_RESULTS_MMPBSA.dat", "FINAL_DECOMP_MMPBSA.dat",
                 "FINAL_RESULTS.dat", "DECOMP_RESULTS.dat"}
_SKIP_DIRS = {".git", ".venv", "__pycache__", "dist", "build", ".worktrees"}
_COPY_LIMIT = 16 * 1024 * 1024


def _linked(path: Path) -> bool:
    info = path.lstat()
    return path.is_symlink() or bool(
        getattr(info, "st_reparse_tag", 0) & 0x20000000
    )


def _artifacts(root: Path):
    count = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(name for name in dirs if not name.startswith(".")
                         and name not in _SKIP_DIRS and not _linked(Path(current) / name))
        for name in sorted(files):
            path = Path(current) / name
            if name.startswith(".") or _linked(path) or not path.is_file():
                continue
            if path.suffix.lower() not in _TEXT_SUFFIXES | _BINARY_SUFFIXES and name not in _JSON_NAMES | _METHOD_FILES:
                continue
            path.resolve().relative_to(root)
            count += 1
            if count > 10000:
                raise ValueError("Evidence export exceeds 10,000 artifacts; select a single simulation project.")
            yield path


def _record(archive: zipfile.ZipFile, root: Path, path: Path) -> dict:
    before = path.stat()
    relative = path.relative_to(root).as_posix()
    included = path.suffix.lower() not in _BINARY_SUFFIXES and before.st_size <= _COPY_LIMIT
    digest = hashlib.sha256()
    if included:
        # Small evidence files use exactly the same bytes for the digest and ZIP.
        content = path.read_bytes()
        digest.update(content)
        archive.writestr(f"files/{relative}", content)
    else:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"Artifact changed during export: {relative}. Stop the running task and retry.")
    return {"path": relative, "size_bytes": before.st_size, "sha256": digest.hexdigest(),
            "included": included,
            "omission_reason": None if included else "Binary simulation data or file above 16 MiB; retain original."}


def _versions() -> dict[str, str]:
    result = {}
    for package in ("MDAnalysis", "mdtraj", "numpy", "PyQt5", "biopython", "pdbfixer", "openmm"):
        try:
            result[package] = version(package)
        except PackageNotFoundError:
            result[package] = "not installed"
    return result


def export_evidence_bundle(project_dir: str | Path, destination: str | Path) -> Path:
    """Create a new ZIP; binary trajectories are hashed, not copied.

    This is an evidence snapshot, not a portable simulation backup. Original
    manifests/logs may contain local paths. Run only when project jobs are idle.
    """
    supplied = Path(project_dir).expanduser()
    if not supplied.is_dir() or _linked(supplied):
        raise ValueError("Select a project directory containing a run manifest.")
    root = supplied.resolve()
    manifest_file = root / MANIFEST_FILENAME
    if not manifest_file.is_file() or _linked(manifest_file):
        raise ValueError("Project run manifest is missing or linked.")
    manifest = load_manifest(manifest_file)
    if any(stage.status == "running" for stage in manifest.stages.values()):
        raise ValueError("A simulation stage is running. Wait or stop it before exporting evidence.")
    target = Path(destination).expanduser().absolute()
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"Destination already exists: {target}. Choose a new filename.")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".moldyn-evidence-", suffix=".tmp", dir=target.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            records = [_record(archive, root, path) for path in _artifacts(root)]
            index = {
                "schema": "moldynstudio-evidence-v1", "application": "MolDynStudio",
                "run_schema": manifest.schema_version,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "stages": {name: stage.status for name, stage in manifest.stages.items()},
                "runtime_packages": _versions(), "files": records,
                "scope": "Evidence snapshot; retain original binary files and external source structures.",
            }
            archive.writestr("evidence_manifest.json", json.dumps(index, indent=2, ensure_ascii=False))
            archive.writestr("README.md", _README)
        # Atomic no-replace publication: unlike replace(), link() rejects a race.
        os.link(temporary, target)
        return target
    finally:
        temporary.unlink(missing_ok=True)


_README = """# MolDynStudio evidence snapshot

`evidence_manifest.json` lists relative artifact paths, byte sizes, SHA-256
digests and recorded stage states. `files/` preserves included artifacts.
Binary simulation data and files above 16 MiB are hashed but not copied.
Retain the original trajectories, checkpoints, force-field files and external
source structures for reproduction. This ZIP cannot resume a simulation alone.

This record is not proof of convergence, binding affinity or physical validity.
Preserve warnings, failed stages and unfavorable results when reporting work.
Review ensembles, independent replicas, parameterization, equilibration and
periodic-boundary processing before interpreting analyses. Runtime package
versions describe the export host; original execution details remain in logs
and manifests. Those files can contain local absolute paths and usernames;
review them before public deposition. The export does not anonymize content.
"""
