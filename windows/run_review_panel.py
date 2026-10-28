"""Prepared-input review without changing the recorded scientific workflow."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from pathlib import Path

from PyQt5.QtCore import Qt, QThread, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
)

from core import wsl_bridge
from core.gromacs_capabilities import GromacsCapabilities, build_mdrun_resource_args
from core.run_manifest import RunManifest, StageStatus
from core.run_review import RunReview, build_run_review


class EnvironmentProbeWorker(QThread):
    """Keep the existing GROMACS capability probe off the GUI thread."""

    result = pyqtSignal(object, str)

    def __init__(self, probe, executable: str, environment: str, parent=None):
        super().__init__(parent)
        self.probe, self.executable, self.environment = probe, executable, environment

    def run(self):
        try:
            self.result.emit(self.probe(self.executable, self.environment), "")
        except Exception as exc:  # noqa: BLE001 - deliver worker failures to the GUI.
            self.result.emit(None, str(exc))


class RunReviewDialog(QDialog):
    def __init__(self, review: RunReview, environment: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Review prepared MD run")
        self.resize(820, 620)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        summary = "\n".join(f"{label}: {value}" for label, value in review.rows)
        summary += "\n\n" + review.protocol_label + "\n\n" + environment
        if review.issues:
            summary += "\n\nReview notes:\n" + "\n".join(review.issues)
        for title, content in (("Prepared run", summary), *review.mdp_files):
            text = QPlainTextEdit()
            text.setReadOnly(True)
            text.setPlainText(content)
            text.setAccessibleName(f"Prepared run {title}")
            tabs.addTab(text, title)
        layout.addWidget(tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class RunReviewPanel(QGroupBox):
    """Show prepared settings separately from the editable Setup draft."""

    state_changed = pyqtSignal()
    setup_requested = pyqtSignal()
    settings_requested = pyqtSignal()

    def __init__(self, capability_probe: Callable, parent=None, *, operation_blocked=lambda: False):
        super().__init__("Prepared run review", parent)
        self._capability_probe = capability_probe
        self.operation_blocked = operation_blocked
        self._manifest: RunManifest | None = None
        self._project_dir: Path | None = None
        self._draft: dict | None = None
        self._worker: EnvironmentProbeWorker | None = None
        self._pending = False
        self._environment_context: tuple[object, ...] | None = None
        self.review: RunReview | None = None
        layout = QVBoxLayout(self)
        self.summary = QLabel("Prepare a system in MD Setup to review its inputs.")
        self.setup_status = QLabel("No prepared inputs loaded.")
        self.environment_status = QLabel("Execution environment: not checked.")
        self.failure_status = QLabel()
        for label in (self.summary, self.setup_status, self.environment_status, self.failure_status):
            label.setWordWrap(True)
            label.setTextFormat(Qt.PlainText)
            layout.addWidget(label)
        self.failure_status.hide()
        row = QHBoxLayout()
        self.review_button = QPushButton("Review run / MDP files")
        self.review_button.clicked.connect(self.open_review)
        self.environment_button = QPushButton("Check environment")
        self.environment_button.clicked.connect(self.check_environment)
        self.folder_button = QPushButton("Open project / logs")
        self.folder_button.clicked.connect(self.open_project_folder)
        setup = QPushButton("MD Setup")
        setup.clicked.connect(self.setup_requested)
        settings = QPushButton("Settings")
        settings.clicked.connect(self.settings_requested)
        for button in (self.review_button, self.environment_button, self.folder_button, setup, settings):
            row.addWidget(button)
        layout.addLayout(row)
        self.set_project(None, None)

    def is_busy(self) -> bool:
        return self._worker is not None

    @property
    def pending(self) -> bool:
        return self._pending

    def set_setup_state(self, snapshot: Mapping) -> None:
        self._draft = deepcopy(dict(snapshot))
        self._refresh_setup_status()

    def set_project(self, manifest: RunManifest | None, project_dir: Path | None) -> None:
        changed = self._project_dir != project_dir
        context = (project_dir, manifest.gromacs_binary, manifest.conda_environment,
                   manifest.requested_cores, manifest.gpu_mode,
                   manifest.stages["preparation"].finished_at) if manifest else None
        if self._environment_context != context:
            self.environment_status.setText("Execution environment: not checked.")
            self.clear_failure()
        self._environment_context = context
        self._manifest, self._project_dir = manifest, project_dir
        if changed:
            self.environment_status.setText("Execution environment: not checked.")
            self.failure_status.clear()
            self.failure_status.hide()
        self.review = build_run_review(manifest, project_dir) if manifest and project_dir else None
        enabled = self.review is not None
        self.review_button.setEnabled(enabled)
        self.folder_button.setEnabled(project_dir is not None)
        self.environment_button.setEnabled(enabled and not self.is_busy())
        if manifest:
            route = "Windows → WSL Linux" if wsl_bridge.IS_WINDOWS else "Linux native"
            self.summary.setText(
                f"{manifest.system_kind} · {manifest.force_field} · {manifest.water_model}\n"
                f"{route} · {manifest.gromacs_binary} · {manifest.requested_cores} requested CPU threads"
                f" · GPU mode: {manifest.gpu_mode}"
            )
            failures = tuple(stage.message for stage in manifest.stages.values()
                             if stage.status == StageStatus.FAILED.value and stage.message)
            if failures:
                self.show_failure(failures[-1])
        else:
            self.summary.setText("Prepare a system in MD Setup to review its inputs.")
        self._refresh_setup_status()

    def _refresh_setup_status(self) -> None:
        baseline = self._manifest.metadata.get("preparation_review", {}) if self._manifest else {}
        baseline = baseline.get("setup_snapshot") if isinstance(baseline, dict) else None
        draft = self._draft
        pending = False
        if isinstance(baseline, dict) and draft is not None:
            pending = baseline != draft
        if pending and isinstance(baseline, dict) and draft is not None:
            changed_fields = sorted(key for key in baseline.keys() | draft.keys()
                                    if baseline.get(key) != draft.get(key))
            names = ", ".join(key.replace("_", " ") for key in changed_fields[:4])
            self.setup_status.setText(
                f"Pending MD Setup changes: {names}. Prepare again or undo those changes before running. "
                "Prepared files and previous results remain available below."
            )
        elif self._manifest and baseline is None:
            self.setup_status.setText(
                "Setup comparison not recorded in this older project. Review the prepared inputs and MDP files."
            )
        else:
            self.setup_status.setText("Prepared settings match MD Setup." if self._manifest else
                                      "No prepared inputs loaded.")
        state_changed = self._pending != pending
        self._pending = pending
        if state_changed:
            self.state_changed.emit()

    def open_review(self) -> None:
        if self._manifest is None or self._project_dir is None:
            return
        self.review = build_run_review(self._manifest, self._project_dir)
        dialog = RunReviewDialog(self.review, self.environment_status.text(), self)
        dialog.exec_()

    def check_environment(self) -> None:
        if self.is_busy() or self._manifest is None:
            return
        if self.operation_blocked():
            self.environment_status.setText("Wait for the current project operation before checking the environment.")
            return
        self.environment_status.setText("Checking selected GROMACS build…")
        self.environment_button.setEnabled(False)
        worker = EnvironmentProbeWorker(self._capability_probe, self._manifest.gromacs_binary,
                                        self._manifest.conda_environment, self)
        worker.result.connect(self._environment_result)
        worker.finished.connect(self._environment_finished)
        self._worker = worker
        try:
            worker.start()
        except Exception as exc:  # noqa: BLE001 - restore controls if Qt cannot start the worker.
            self._worker = None
            self._environment_result(None, str(exc))
            self.environment_button.setEnabled(True)
            worker.deleteLater()

    def _environment_result(self, capabilities, error: str) -> None:
        if error or not isinstance(capabilities, GromacsCapabilities):
            self.environment_status.setText(f"Environment check failed: {error or 'invalid probe result'}. "
                                            "Review GROMACS and environment in Settings.")
            return
        self.show_capabilities(capabilities)

    def show_capabilities(self, capabilities: GromacsCapabilities) -> None:
        if not capabilities.executable_ok:
            self.environment_status.setText(f"GROMACS unavailable: {capabilities.version_text.strip()}. "
                                            "Review executable and environment in Settings.")
            return
        assert self._manifest is not None
        backend = capabilities.gpu_support_text or "not reported by this build"
        try:
            args = build_mdrun_resource_args(self._manifest.gpu_mode,
                                            self._manifest.requested_cores, capabilities)
            resources = " ".join(args)
        except ValueError as exc:
            resources = f"Resource selection unavailable: {exc} Review Settings and prepare again."
        self.environment_status.setText(
            f"GROMACS {capabilities.version or 'version not reported'} · GPU backend: {backend}. "
            f"Execution resources: {resources}. GPU use is determined by mdrun at execution."
        )

    def _environment_finished(self) -> None:
        worker, self._worker = self._worker, None
        self.environment_button.setEnabled(self._manifest is not None)
        if worker is not None:
            worker.deleteLater()

    def show_failure(self, message: str) -> None:
        self.failure_status.setText(f"{message}\nReview MD Setup for input problems, Settings for execution "
                                    "resources, or open the project folder to inspect stage logs.")
        self.failure_status.show()

    def clear_failure(self) -> None:
        self.failure_status.clear()
        self.failure_status.hide()

    def open_project_folder(self) -> None:
        if self._project_dir is not None and not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._project_dir))):
            self.show_failure(f"Could not open project folder: {self._project_dir}")
