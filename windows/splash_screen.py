"""Startup dependency-check splash screen.

Two modes:

* Legacy mode: ``SplashScreen()`` exposes ``add_status``, ``finish`` and the
  ``status`` text widget -- consumed by ``setup_environment.py`` together with
  :class:`core.environment_manager.DependencyChecker`.

* Real-check mode: call :meth:`SplashScreen.run_real_checks` to probe WSL2,
  the moldynstudio conda env, ``gmx`` and key Python imports inline.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Callable

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from core import wsl_bridge


def _resource_root() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


def _import_check(module_name: str) -> tuple[bool, str]:
    spec = importlib.util.find_spec(module_name)
    if spec is None:
        return False, f"{module_name} not importable"
    return True, f"{module_name} OK"


def _gmx_mmpbsa_check() -> tuple[bool, str]:
    try:
        result = wsl_bridge.run(["gmx_MMPBSA", "--version"], timeout=60)
    except Exception as exc:  # pragma: no cover - defensive
        return False, f"gmx_MMPBSA probe failed: {exc}"
    if result.returncode == 0:
        return True, "gmx_MMPBSA OK"
    return False, f"gmx_MMPBSA not callable: {(result.stderr or result.stdout)[:120]}"


REAL_CHECKS: tuple[tuple[str, Callable[[], tuple[bool, str]]], ...] = (
    ("WSL2", wsl_bridge.check_wsl_available),
    ("conda env", wsl_bridge.check_conda_env),
    ("GROMACS (gmx)", wsl_bridge.check_gmx),
    ("MDAnalysis", lambda: _import_check("MDAnalysis")),
    ("MDTraj", lambda: _import_check("mdtraj")),
    ("ProDy", lambda: _import_check("prody")),
    ("gmx_MMPBSA", _gmx_mmpbsa_check),
)


class SplashScreen(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowStaysOnTopHint)
        self.setWindowTitle("MolDynStudio Startup Check")
        self.resize(560, 420)
        layout = QVBoxLayout(self)

        self.title = QLabel("Checking MolDynStudio dependencies...")
        layout.addWidget(self.title)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        layout.addWidget(self.progress)

        self.status = QTextEdit()
        self.status.setReadOnly(True)
        layout.addWidget(self.status)

        button_row = QHBoxLayout()
        self.fix_button = QPushButton("Fix Issues")
        self.fix_button.setEnabled(False)
        self.fix_button.clicked.connect(self._on_fix_clicked)
        button_row.addWidget(self.fix_button)
        button_row.addStretch(1)
        self.launch_button = QPushButton("Launch")
        self.launch_button.setEnabled(False)
        self.launch_button.clicked.connect(self.accept)
        button_row.addWidget(self.launch_button)
        layout.addLayout(button_row)

        self._all_ok = False

    # Legacy API used by setup_environment.py / DependencyChecker.
    def add_status(self, package: str, status: str) -> None:
        self.status.append(f"{package}: {status}")

    def finish(self, ok: bool) -> None:
        self._all_ok = ok
        self.progress.setRange(0, 100)
        self.progress.setValue(100 if ok else 0)
        self.title.setText(
            "All dependencies detected." if ok else "Some dependencies are missing."
        )
        self.launch_button.setEnabled(ok)
        self.fix_button.setEnabled(not ok)

    # Real-check mode used by main() when launching the GUI directly.
    def run_real_checks(self) -> bool:
        self.status.clear()
        self.progress.setRange(0, len(REAL_CHECKS))
        self.progress.setValue(0)
        all_ok = True
        for index, (name, check) in enumerate(REAL_CHECKS, start=1):
            try:
                ok, message = check()
            except Exception as exc:  # pragma: no cover - defensive
                ok, message = False, f"check raised: {exc}"
            mark = "[OK]" if ok else "[MISSING]"
            self.status.append(f"{mark} {name}: {message}")
            self.progress.setValue(index)
            if not ok:
                all_ok = False
        self.finish(all_ok)
        return all_ok

    def _on_fix_clicked(self) -> None:
        env_yml = _resource_root() / "environment.yml"
        env_arg = wsl_bridge.win_to_wsl(str(env_yml))
        self.status.append("")
        self.status.append(f"Attempting: bootstrap Conda and sync env from {env_arg}")
        try:
            result = wsl_bridge.run_raw_shell(
                wsl_bridge.build_conda_env_sync_script(env_arg),
                timeout=60 * 60,
            )
        except Exception as exc:
            self.status.append(f"Fix failed to start: {exc}")
            return
        if result.stdout:
            self.status.append(result.stdout)
        if result.stderr:
            self.status.append(result.stderr)
        if result.returncode == 0:
            self.status.append("Environment ready. Re-running checks...")
            self.run_real_checks()
        else:
            self.status.append(f"conda env create exited with {result.returncode}")
