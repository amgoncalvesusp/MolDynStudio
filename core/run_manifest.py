"""Serializable, atomically persisted state for a GROMACS run."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
import os
from pathlib import Path
import tempfile
from typing import Any

class RunManifestError(ValueError):
    """Raised when a run manifest is invalid or cannot be read/written."""

class StageName(str, Enum):
    PREPARATION = "preparation"
    MINIMIZATION = "minimization"
    NVT = "nvt"
    NPT = "npt"
    PRODUCTION = "production"

class StageStatus(str, Enum):
    NOT_READY = "not_ready"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"
    INTERRUPTED = "interrupted"

@dataclass
class CommandRecord:
    stage: str
    argv: list[str]
    cwd: str
    started_at: str
    finished_at: str | None = None
    exit_code: int | None = None
    success: bool | None = None

@dataclass
class StageRecord:
    name: str
    status: str = StageStatus.NOT_READY.value
    started_at: str | None = None
    finished_at: str | None = None
    message: str = ""
    commands: list[CommandRecord] = field(default_factory=list)
    inputs: dict[str, str] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)

@dataclass
class RunManifest:
    schema_version: str
    project_dir: str
    system_kind: str
    protein_source: str
    ligand_source: str | None
    force_field: str
    water_model: str
    gromacs_binary: str
    conda_environment: str
    requested_cores: int
    gpu_mode: str
    created_at: str
    updated_at: str
    stages: dict[str, StageRecord]
    metadata: dict[str, Any] = field(default_factory=dict)

MANIFEST_FILENAME = "moldynstudio_run.json"
MANIFEST_SCHEMA_VERSION = "1.0"

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def new_manifest(project_dir: str | Path, system_kind: str, protein_source: str,
                 ligand_source: str | None, force_field: str, water_model: str,
                 gromacs_binary: str, conda_environment: str, requested_cores: int,
                 gpu_mode: str) -> RunManifest:
    now = utc_now_iso()
    stages = {name.value: StageRecord(name=name.value) for name in StageName}
    return RunManifest(MANIFEST_SCHEMA_VERSION, str(Path(project_dir)), system_kind,
                       protein_source, ligand_source, force_field, water_model,
                       gromacs_binary, conda_environment, requested_cores, gpu_mode,
                       now, now, stages)

def manifest_path(project_dir: str | Path) -> Path:
    return Path(project_dir) / MANIFEST_FILENAME

def save_manifest(manifest: RunManifest, path: str | Path) -> Path:
    if not isinstance(manifest, RunManifest):
        raise RunManifestError("manifest must be a RunManifest")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False) as handle:
            temp_path = Path(handle.name)
            json.dump(asdict(manifest), handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
        temp_path = None
        return target
    except (OSError, TypeError, ValueError) as exc:
        raise RunManifestError(f"Unable to save run manifest: {exc}") from exc
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

_REQUIRED = {"schema_version", "project_dir", "system_kind", "protein_source", "ligand_source", "force_field", "water_model", "gromacs_binary", "conda_environment", "requested_cores", "gpu_mode", "created_at", "updated_at", "stages", "metadata"}

def load_manifest(path: str | Path) -> RunManifest:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise RunManifestError("manifest root must be an object")
        missing = _REQUIRED - data.keys()
        if missing:
            raise RunManifestError(f"manifest missing required keys: {sorted(missing)}")
        if data["schema_version"] != MANIFEST_SCHEMA_VERSION:
            raise RunManifestError(f"unsupported schema version: {data['schema_version']!r}")
        stages_data = data["stages"]
        known = {name.value for name in StageName}
        if not isinstance(stages_data, dict) or set(stages_data) != known:
            raise RunManifestError("manifest stages must contain all five known stage names")
        stages = {key: _stage_record(key, value) for key, value in stages_data.items()}
        values = {key: data[key] for key in _REQUIRED - {"stages"}}
        if not isinstance(values["metadata"], dict) or not isinstance(data["requested_cores"], int) or isinstance(data["requested_cores"], bool):
            raise RunManifestError("manifest metadata or requested_cores is invalid")
        return RunManifest(stages=stages, **values)
    except RunManifestError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise RunManifestError(f"Unable to load run manifest: {exc}") from exc

def _stage_record(key: str, value: Any) -> StageRecord:
    if not isinstance(value, dict) or value.get("name") != key:
        raise RunManifestError(f"invalid stage record for {key!r}")
    try:
        StageName(key)
        StageStatus(value.get("status", StageStatus.NOT_READY.value))
        commands = value.get("commands", [])
        if not isinstance(commands, list):
            raise ValueError("commands must be a list")
        parsed = [CommandRecord(**command) for command in commands]
        return StageRecord(**{**value, "commands": parsed})
    except (TypeError, ValueError) as exc:
        raise RunManifestError(f"invalid stage record for {key!r}") from exc
