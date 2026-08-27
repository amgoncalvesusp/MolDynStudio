"""MolDynStudio .mds project file persistence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


class ProjectFormatError(ValueError):
    """Raised when a project file cannot be loaded safely."""


CURRENT_PROJECT_VERSION = "2.0"
LEGACY_PROJECT_VERSION = "1.0"


def _version_parts(value: object) -> tuple[int, ...] | None:
    if not isinstance(value, str):
        return None
    try:
        return tuple(int(part) for part in value.split("."))
    except ValueError:
        return None


def migrate_project_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Return a current project envelope without mutating the loaded payload."""

    if not isinstance(data, dict):
        raise ProjectFormatError("Project payload must be a JSON object.")

    version = data.get("version")
    if version == CURRENT_PROJECT_VERSION:
        return dict(data)
    if version == LEGACY_PROJECT_VERSION:
        return {**data, "version": CURRENT_PROJECT_VERSION}

    supplied = _version_parts(version)
    current = _version_parts(CURRENT_PROJECT_VERSION)
    if supplied is not None and current is not None and supplied > current:
        raise ProjectFormatError(
            f"Project version {version!r} is newer than the supported "
            f"schema {CURRENT_PROJECT_VERSION!r}. Update MolDynStudio to open it."
        )
    raise ProjectFormatError(
        f"Unsupported project version {version!r}; supported versions are "
        f"{LEGACY_PROJECT_VERSION!r} and {CURRENT_PROJECT_VERSION!r}."
    )


def _state_for_save(
    state: Mapping[str, Any],
    target: Path,
) -> dict[str, Any]:
    saved = dict(state)
    context = saved.get("project_context")
    if not isinstance(context, Mapping):
        return saved

    project_text = str(context.get("project_dir", "")).strip()
    manifest_text = str(context.get("manifest_path", "")).strip()
    if not project_text:
        return saved

    project_dir = Path(project_text).expanduser().resolve()
    manifest_file = (
        Path(manifest_text).expanduser()
        if manifest_text
        else project_dir / "moldynstudio_run.json"
    )
    if not manifest_file.is_absolute():
        manifest_file = project_dir / manifest_file
    manifest_file = manifest_file.resolve()

    try:
        project_reference = project_dir.relative_to(target.parent.resolve())
        project_value = project_reference.as_posix() if project_reference.parts else "."
    except ValueError:
        project_value = str(project_dir)

    try:
        manifest_value = manifest_file.relative_to(project_dir).as_posix()
    except ValueError:
        manifest_value = str(manifest_file)

    saved["project_context"] = {
        **dict(context),
        "project_dir": project_value,
        "manifest_path": manifest_value,
    }
    return saved


def _state_after_load(state: Mapping[str, Any], source: Path) -> dict[str, Any]:
    loaded = dict(state)
    context = loaded.get("project_context")
    if not isinstance(context, Mapping):
        return loaded

    project_text = str(context.get("project_dir", "")).strip()
    if not project_text:
        return loaded
    project_dir = Path(project_text).expanduser()
    if not project_dir.is_absolute():
        project_dir = source.parent / Path(project_text.replace("\\", "/"))
    project_dir = project_dir.resolve()

    manifest_text = str(context.get("manifest_path", "")).strip()
    manifest_reference = manifest_text or "moldynstudio_run.json"
    manifest_file = Path(manifest_reference).expanduser()
    if not manifest_file.is_absolute():
        manifest_file = project_dir / Path(manifest_reference.replace("\\", "/"))

    loaded["project_context"] = {
        **dict(context),
        "project_dir": str(project_dir),
        "manifest_path": str(manifest_file.resolve()),
    }
    return loaded


@dataclass(frozen=True)
class ProjectManager:
    project_version: str = CURRENT_PROJECT_VERSION

    def save(self, path: str | Path, state: Mapping[str, Any]) -> Path:
        target = Path(path).expanduser()
        data = {
            "version": self.project_version,
            "created": datetime.now().isoformat(timespec="seconds"),
            "state": _state_for_save(state, target),
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return target

    def load(self, path: str | Path) -> dict[str, Any]:
        source = Path(path).expanduser()
        raw_data = json.loads(source.read_text(encoding="utf-8"))
        data = migrate_project_payload(raw_data)
        state = data.get("state")
        if not isinstance(state, dict):
            raise ProjectFormatError("Project state is missing or invalid.")
        return _state_after_load(state, source)
