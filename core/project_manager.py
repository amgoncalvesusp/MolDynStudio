"""MolDynStudio .mds project file persistence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


class ProjectFormatError(ValueError):
    """Raised when a project file cannot be loaded safely."""


@dataclass(frozen=True)
class ProjectManager:
    project_version: str = "1.0"

    def save(self, path: str | Path, state: Mapping[str, Any]) -> Path:
        target = Path(path).expanduser()
        data = {
            "version": self.project_version,
            "created": datetime.now().isoformat(timespec="seconds"),
            "state": dict(state),
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return target

    def load(self, path: str | Path) -> dict[str, Any]:
        source = Path(path).expanduser()
        data = json.loads(source.read_text(encoding="utf-8"))
        version = data.get("version")
        if version != self.project_version:
            raise ProjectFormatError(
                f"Incompatible project version {version!r}; expected {self.project_version!r}."
            )
        state = data.get("state")
        if not isinstance(state, dict):
            raise ProjectFormatError("Project state is missing or invalid.")
        return dict(state)

