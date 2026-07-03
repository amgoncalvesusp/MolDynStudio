"""Small QSettings wrapper with typed defaults."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from PyQt5.QtCore import QSettings
except Exception:  # pragma: no cover - used only when PyQt5 is unavailable
    QSettings = None  # type: ignore[assignment]


@dataclass(frozen=True)
class SettingsDefaults:
    conda_environment: str = "moldynstudio"
    default_output_folder: str = str(Path.home() / "MolDynStudio")
    gromacs_binary: str = "auto"
    cpptraj_binary: str = "auto"
    cores: int = 4
    gpu_mode: str = "Auto"
    theme: str = "Light"
    language: str = "English"


class SettingsStore:
    def __init__(self, organization: str = "MolDynStudio", application: str = "MolDynStudio"):
        self.defaults = SettingsDefaults()
        self._settings = QSettings(organization, application) if QSettings is not None else None
        self._fallback: dict[str, Any] = {}

    def value(self, key: str, default: Any = None) -> Any:
        fallback = getattr(self.defaults, key, default)
        if self._settings is None:
            return self._fallback.get(key, fallback)
        return self._settings.value(key, fallback)

    def set_value(self, key: str, value: Any) -> None:
        if self._settings is None:
            self._fallback = {**self._fallback, key: value}
            return
        self._settings.setValue(key, value)

