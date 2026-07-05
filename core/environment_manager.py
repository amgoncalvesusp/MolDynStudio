"""Dependency detection and installation helpers for MolDynStudio."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from dataclasses import dataclass
from typing import Mapping

try:
    from PyQt5.QtCore import QThread, pyqtSignal
except Exception:  # pragma: no cover - keeps pure helpers importable without PyQt5
    class _Signal:
        def connect(self, *_args, **_kwargs) -> None:
            return None

        def emit(self, *_args, **_kwargs) -> None:
            return None

    def pyqtSignal(*_args, **_kwargs):  # type: ignore[no-redef]
        return _Signal()

    class QThread:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            pass

        def start(self) -> None:
            self.run()


REQUIRED_PACKAGES: Mapping[str, str] = {
    "gromacs": "conda-forge::gromacs",
    "ambertools": "conda-forge::ambertools=23",
    "gmx_mmpbsa": "conda-forge::gmx_mmpbsa",
    "mdanalysis": "conda-forge::mdanalysis",
    "mdtraj": "conda-forge::mdtraj",
    "prody": "conda-forge::prody",
    "matplotlib": "conda-forge::matplotlib",
    "py3dmol": "pip::py3dmol",
    "acpype": "pip::acpype",
    "numpy": "conda-forge::numpy",
    "pandas": "conda-forge::pandas",
    "scipy": "conda-forge::scipy",
    "seaborn": "conda-forge::seaborn",
    "openpyxl": "conda-forge::openpyxl",
}

IMPORT_NAMES: Mapping[str, str] = {
    "gmx_mmpbsa": "GMXMMPBSA",
    "mdanalysis": "MDAnalysis",
}


@dataclass(frozen=True)
class PackageStatus:
    package: str
    installed: bool
    detail: str


def package_import_name(package: str) -> str:
    return IMPORT_NAMES.get(package, package)


def is_python_package_installed(package: str) -> bool:
    return importlib.util.find_spec(package_import_name(package)) is not None


def build_conda_install_command(specs: list[str], env_name: str = "moldynstudio") -> list[str]:
    conda_specs = [spec.split("::", 1)[1] for spec in specs if not spec.startswith("pip::")]
    pip_specs = [spec.split("::", 1)[1] for spec in specs if spec.startswith("pip::")]
    if not conda_specs:
        return ["conda", "run", "-n", env_name, "pip", "install", *pip_specs]
    command = ["conda", "install", "-n", env_name, "-c", "conda-forge", *conda_specs, "-y"]
    if pip_specs:
        command.extend(["&&", "conda", "run", "-n", env_name, "pip", "install", *pip_specs])
    return command


class DependencyChecker(QThread):
    status_update = pyqtSignal(str, str)
    all_done = pyqtSignal(bool)
    command_preview = pyqtSignal(str)

    def __init__(self, env_name: str = "moldynstudio", auto_install: bool = False, parent=None):
        super().__init__(parent)
        self.env_name = env_name
        self.auto_install = auto_install

    def run(self) -> None:
        missing: list[str] = []
        for package, spec in REQUIRED_PACKAGES.items():
            installed = self.is_installed(package)
            self.status_update.emit(package, "OK" if installed else "MISSING")
            if not installed:
                missing.append(spec)
        if missing:
            command = build_conda_install_command(missing, self.env_name)
            self.command_preview.emit(" ".join(command))
            if self.auto_install:
                self.install_missing(missing)
        self.all_done.emit(not missing)

    def is_installed(self, package: str) -> bool:
        if package in {"gromacs", "ambertools", "acpype"}:
            executables = {
                "gromacs": "gmx",
                "ambertools": "cpptraj",
                "acpype": "acpype",
            }
            return shutil.which(executables[package]) is not None
        return is_python_package_installed(package)

    def install_missing(self, specs: list[str]) -> int:
        command = build_conda_install_command(specs, self.env_name)
        if "&&" in command:
            return subprocess.call(" ".join(command), shell=True)
        return subprocess.call(command)
