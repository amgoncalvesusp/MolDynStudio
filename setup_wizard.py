"""Single-page launcher dialog for MolDynStudio.

Shows the status of every required dependency (WSL2, moldynstudio conda env,
gmx) with inline Install/Create buttons next to each missing item. When all
checks are green the ``Begin`` button is enabled and clicking it closes the
dialog and launches the GUI.

The dialog runs every time the app starts, so the user always sees a
diagnostic dashboard before the GUI opens.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core import wsl_bridge


def _resource_root() -> Path:
    """Return the directory holding bundled resources.

    Under PyInstaller ``--onefile``, ``environment.yml`` and ``install_wsl.ps1``
    are extracted to ``sys._MEIPASS``. In dev mode (running from source), they
    live next to this file.
    """

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent


REPO_ROOT = _resource_root()


def _check_virtualization() -> tuple[bool, str]:
    """Detect CPU virtualization (VT-x / AMD-V) state via Get-ComputerInfo.

    WSL2 cannot start any distro when virtualization is disabled in BIOS/UEFI,
    even if all Windows features are enabled and the OS has been rebooted.
    """

    if sys.platform != "win32":
        return True, "n/a (non-Windows)"
    try:
        r = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-ComputerInfo -Property HyperVRequirementVirtualizationFirmwareEnabled)"
                ".HyperVRequirementVirtualizationFirmwareEnabled",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return True, f"virtualization probe failed ({exc}); assuming OK"
    out = (r.stdout or "").strip().lower()
    if out == "false":
        return (
            False,
            "CPU virtualization DISABLED in BIOS/UEFI. Enable VT-x (Intel) "
            "or SVM Mode (AMD) in firmware, then reboot.",
        )
    return True, "virtualization enabled"


# ---------------------------------------------------------------------------
# Background worker for long-running install/create steps
# ---------------------------------------------------------------------------


class _CommandWorker(QThread):
    """Runs an arbitrary command and streams its stdout to the dialog."""

    line = pyqtSignal(str)
    done = pyqtSignal(bool)

    def __init__(self, build_proc: Callable[[], subprocess.Popen], parent=None):
        super().__init__(parent)
        self._build_proc = build_proc

    def run(self) -> None:  # noqa: D401 - QThread API
        try:
            proc = self._build_proc()
        except Exception as exc:  # noqa: BLE001 - surface anything to the UI
            self.line.emit(f"[error] failed to start: {exc}")
            self.done.emit(False)
            return
        try:
            if proc.stdout is not None:
                for raw in proc.stdout:
                    self.line.emit(raw.rstrip())
        finally:
            proc.wait()
        self.done.emit(proc.returncode == 0)


# ---------------------------------------------------------------------------
# Status row widget: one per dependency
# ---------------------------------------------------------------------------


class _StatusRow(QWidget):
    """One dependency: label + status text + optional Install button."""

    fixed = pyqtSignal()

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self.name = name
        self._ok: bool = False  # explicit state — Begin button reads this
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.icon_label = QLabel("…")
        self.icon_label.setFixedWidth(28)
        self.icon_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.icon_label)

        self.name_label = QLabel(f"<b>{name}</b>")
        self.name_label.setFixedWidth(160)
        layout.addWidget(self.name_label)

        self.detail_label = QLabel("checking…")
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.detail_label, 1)

        self.action_button = QPushButton("")
        self.action_button.setVisible(False)
        layout.addWidget(self.action_button)

    @property
    def is_ok(self) -> bool:
        return self._ok

    # ------- visual state -------

    def set_ok(self, message: str) -> None:
        self._ok = True
        self.icon_label.setText("✅")
        self.detail_label.setText(message)
        self.detail_label.setStyleSheet("color: #1b8a4a;")
        self.action_button.setVisible(False)

    def set_missing(self, message: str, action_label: str, on_click) -> None:
        self._ok = False
        self.icon_label.setText("❌")
        self.detail_label.setText(message)
        self.detail_label.setStyleSheet("color: #b03030;")
        try:
            self.action_button.clicked.disconnect()
        except TypeError:
            pass
        self.action_button.setText(action_label)
        self.action_button.clicked.connect(on_click)
        self.action_button.setVisible(True)

    def set_busy(self, message: str) -> None:
        self._ok = False
        self.icon_label.setText("⏳")
        self.detail_label.setText(message)
        self.detail_label.setStyleSheet("color: #888;")
        self.action_button.setVisible(False)


# ---------------------------------------------------------------------------
# Main launcher dialog
# ---------------------------------------------------------------------------


class SetupWizard(QDialog):
    """Single-page launcher: status dashboard + Begin button."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("MolDynStudio - Launcher")
        self.setMinimumSize(720, 520)

        self._worker: Optional[_CommandWorker] = None
        self._env_ok_cached: bool = False

        outer = QVBoxLayout(self)

        title = QLabel("<h2>MolDynStudio</h2>")
        title.setAlignment(Qt.AlignCenter)
        outer.addWidget(title)

        sub = QLabel(
            "Status of required components. Missing items can be installed "
            "directly from this screen."
        )
        sub.setAlignment(Qt.AlignCenter)
        sub.setWordWrap(True)
        outer.addWidget(sub)

        outer.addSpacing(12)

        self.row_wsl = _StatusRow("WSL2")
        self.row_env = _StatusRow("Conda env")
        self.row_gmx = _StatusRow("GROMACS")
        outer.addWidget(self.row_wsl)
        outer.addWidget(self.row_env)
        outer.addWidget(self.row_gmx)

        outer.addSpacing(8)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        outer.addWidget(self.progress)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Install/setup output will appear here…")
        outer.addWidget(self.log, 1)

        bottom = QHBoxLayout()
        self.refresh_button = QPushButton("Re-check")
        self.refresh_button.clicked.connect(self.refresh_all)
        bottom.addWidget(self.refresh_button)
        bottom.addStretch(1)

        self.cancel_button = QPushButton("Quit")
        self.cancel_button.clicked.connect(self.reject)
        bottom.addWidget(self.cancel_button)

        self.begin_button = QPushButton("Begin")
        self.begin_button.setDefault(True)
        self.begin_button.setMinimumWidth(140)
        self.begin_button.setStyleSheet(
            "QPushButton { font-weight: bold; padding: 8px 16px; }"
        )
        self.begin_button.clicked.connect(self.accept)
        bottom.addWidget(self.begin_button)
        outer.addLayout(bottom)

        self.refresh_all()

    # ----- public API ---------------------------------------------------

    def refresh_all(self) -> None:
        self.log.append("[check] running diagnostics…")
        self._refresh_wsl()
        self._refresh_env()
        self._refresh_gmx()
        self._update_begin_state()

    # ----- per-row checks ----------------------------------------------

    def _refresh_wsl(self) -> None:
        # Hard blocker: virtualization off in BIOS. WSL2 cannot run.
        virt_ok, virt_msg = _check_virtualization()
        if not virt_ok:
            self.row_wsl.set_missing(
                virt_msg, "BIOS guide", self._show_bios_help
            )
            return
        ok, msg = wsl_bridge.check_wsl_available()
        if ok:
            self.row_wsl.set_ok(msg)
        else:
            self.row_wsl.set_missing(
                msg, "Install WSL2", self._install_wsl
            )

    def _show_bios_help(self) -> None:
        self.log.append(
            "[bios] CPU virtualization is OFF in firmware. Steps:\n"
            "  1. Reboot, enter BIOS/UEFI (Del / F2 / F10 / F12 / Esc at boot).\n"
            "  2. Enable: Intel VT-x / VT-d  OR  AMD SVM Mode.\n"
            "     Usually under Advanced -> CPU Configuration, or Security.\n"
            "  3. Save & Exit, then re-launch MolDynStudio.\n"
        )

    def _refresh_env(self) -> None:
        ok, msg = wsl_bridge.check_conda_env()
        self._env_ok_cached = ok  # consumed by _refresh_gmx, avoids 2nd probe
        if ok:
            self.row_env.set_ok(msg)
        else:
            self.row_env.set_missing(
                msg, "Create env", self._create_env
            )

    def _refresh_gmx(self) -> None:
        # Only meaningful once the env exists; show a soft "pending" otherwise.
        if not getattr(self, "_env_ok_cached", False):
            self.row_gmx.set_busy("waiting for conda env")
            return
        ok, msg = wsl_bridge.check_gmx()
        if ok:
            self.row_gmx.set_ok(msg)
        else:
            self.row_gmx.set_missing(
                msg, "Reinstall GROMACS", self._create_env
            )

    def _update_begin_state(self) -> None:
        all_ok = (
            self.row_wsl.is_ok and self.row_env.is_ok and self.row_gmx.is_ok
        )
        self.begin_button.setEnabled(all_ok)
        self.begin_button.setText("Begin →" if all_ok else "Begin")

    # ----- install actions ---------------------------------------------

    def _install_wsl(self) -> None:
        script = REPO_ROOT / "install_wsl.ps1"
        self.log.append(f"[wsl] launching elevated installer: {script}")
        # Escape single quotes in path (e.g. user folder with apostrophe) so
        # PowerShell single-quoted string stays valid.
        script_str = str(script).replace("'", "''")
        try:
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Start-Process powershell "
                    f"-ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','{script_str}' "
                    "-Verb RunAs",
                ],
                check=False,
            )
        except OSError as exc:
            self.log.append(f"[wsl] failed to launch installer: {exc}")
            return
        self.log.append(
            "[wsl] installer launched. A reboot may be required. "
            "Click 'Re-check' after WSL is up."
        )

    def _create_env(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self.log.append("[env] a setup task is already running.")
            return
        env_yml = REPO_ROOT / "environment.yml"
        # conda runs inside WSL — must receive a WSL path, not a Windows one.
        try:
            env_yml_for_wsl = wsl_bridge.win_to_wsl(str(env_yml))
        except ValueError as exc:
            self.log.append(f"[env] cannot translate path: {exc}")
            return
        self.log.append(f"[env] ensuring Conda/Miniforge and syncing {env_yml_for_wsl}")
        self.row_env.set_busy("creating env (10-20 min)…")
        self.row_gmx.set_busy("waiting for conda env")
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)  # busy spinner
        self.refresh_button.setEnabled(False)
        self.begin_button.setEnabled(False)

        def build_proc() -> subprocess.Popen:
            return wsl_bridge.popen_raw_shell(
                wsl_bridge.build_conda_env_sync_script(env_yml_for_wsl)
            )

        self._worker = _CommandWorker(build_proc, parent=self)
        self._worker.line.connect(lambda line: self.log.append(line))
        self._worker.done.connect(self._on_create_env_done)
        self._worker.start()

    def _on_create_env_done(self, success: bool) -> None:
        self.progress.setVisible(False)
        self.progress.setRange(0, 100)
        self.refresh_button.setEnabled(True)
        if success:
            self.log.append("[env] Conda environment is ready.")
        else:
            self.log.append("[env] conda env creation FAILED. See log above.")
        self.refresh_all()


__all__ = ["SetupWizard"]
