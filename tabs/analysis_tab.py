"""Post-MD analysis tab."""

from __future__ import annotations

import json
from pathlib import Path

from PyQt5.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtCore import Qt, pyqtSignal

from analysis.cpptraj_builder import CpptrajBuilderWidget
from analysis.execution import ANALYSES, AnalysisInputs, AnalysisWorker
from analysis.qc_pipeline import QCGromacsWorker, QCPipelineInputs, build_qc_tasks, default_qc_output_dir
from core.settings import SettingsStore
from tabs.base import MolDynBasePage, PathSelector
from utils.tooltips import tooltip
from windows.mmpbsa_dialog import MMPBSADialog
from windows.trajectory_viewer import TrajectoryViewerWindow


class AnalysisTab(MolDynBasePage):
    request_analysis_files = pyqtSignal(object)

    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self.settings = settings if settings is not None else SettingsStore()
        self.operation_blocked = None
        self._backend_override = None
        self._analysis_worker = None
        self._qc_worker = None
        self._analysis_busy = False
        self._qc_busy = False
        self.last_manifest_path = ""
        outer = QVBoxLayout(self)
        title = QLabel("Analysis")
        title.setObjectName("PageTitle")
        outer.addWidget(title)

        tabs = QTabWidget()
        tabs.addTab(self._build_quick_tools(), "Analysis Tools")
        self.cpptraj = CpptrajBuilderWidget(settings=self.settings)
        self.cpptraj.operation_blocked = lambda: self.is_busy() or bool(self.operation_blocked and self.operation_blocked())
        tabs.addTab(self.cpptraj, "CPPTRAJ Script Builder")
        outer.addWidget(tabs, 1)

    def _build_quick_tools(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        splitter.addWidget(left)

        files = QGroupBox("Input Files")
        form = QFormLayout(files)
        self.fields["topology"] = PathSelector("Topology (.tpr/.pdb/.gro)", "Topology (*.tpr *.pdb *.gro *.top);;All Files (*)")
        form.addRow("Topology", self.fields["topology"])
        self.fields["trajectory"] = PathSelector("Trajectory (.xtc/.trr/.nc)", "Trajectory (*.xtc *.trr *.nc *.dcd);;All Files (*)")
        form.addRow("Trajectory", self.fields["trajectory"])
        self.fields["energy"] = PathSelector("Energy (.edr)", "Energy (*.edr);;All Files (*)")
        form.addRow("Energy", self.fields["energy"])
        self.fields["qc_output"] = PathSelector("Output folder", directory=True)
        form.addRow("Output folder", self.fields["qc_output"])
        self.fields["selection"] = QLineEdit("protein")
        form.addRow("Atom selection (MDAnalysis)", self.fields["selection"])
        self.fields["partner_selection"] = QLineEdit("resname LIG")
        form.addRow("H-bond partner selection", self.fields["partner_selection"])
        self.fields["qc_group"] = QLineEdit("Protein")
        form.addRow("GROMACS QC group", self.fields["qc_group"])
        self.fields["prepared"] = QCheckBox("Molecules are whole and PBC artifacts have been corrected")
        self.fields["prepared"].setToolTip("Required for RMSD, RMSF, PCA, and radius of gyration. This confirmation records your preparation choice; it does not validate coordinates.")
        form.addRow(self.fields["prepared"])
        for key in ("topology", "trajectory"):
            self.fields[key].line_edit.textChanged.connect(lambda _text: self.fields["prepared"].setChecked(False))
        left_layout.addWidget(files)

        tools = QGroupBox("Analysis Selection")
        grid = QGridLayout(tools)
        for index, label in enumerate(ANALYSES):
            checkbox = QCheckBox(label)
            checkbox.setChecked(index < 4)
            if label in {"MM-GBSA", "MM-PBSA"}:
                checkbox.setToolTip(tooltip("mm_gbsa" if label == "MM-GBSA" else "mm_pbsa"))
            self.fields[f"analysis_{index}"] = checkbox
            grid.addWidget(checkbox, index // 2, index % 2)
        left_layout.addWidget(tools)

        note = QLabel("Python analyses use MDAnalysis/MDTraj in this application's Python environment. Select complete protein residues for SASA, DSSP, and Ramachandran; PCA uses three components. CSV tables use ns and nm.")
        note.setWordWrap(True)
        left_layout.addWidget(note)
        buttons = QGridLayout()
        self.run_button = QPushButton("Run selected analyses")
        self.run_button.clicked.connect(self.run_selected)
        buttons.addWidget(self.run_button, 0, 0)
        mmpbsa = QPushButton("MM-PBSA / MM-GBSA setup")
        mmpbsa.clicked.connect(self.open_mmpbsa_dialog)
        buttons.addWidget(mmpbsa, 0, 1)
        viewer = QPushButton("Open Trajectory Viewer")
        viewer.clicked.connect(self.open_trajectory_viewer)
        buttons.addWidget(viewer, 1, 0)
        self.qc_button = QPushButton("Generate QC Dashboard")
        self.qc_button.clicked.connect(self.run_qc_pipeline)
        buttons.addWidget(self.qc_button, 1, 1)
        preview_qc = QPushButton("Preview QC Commands")
        preview_qc.clicked.connect(self.preview_qc_commands)
        buttons.addWidget(preview_qc, 2, 0)
        self.cancel_button = QPushButton("Stop after current analysis")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_analysis)
        buttons.addWidget(self.cancel_button, 2, 1)
        left_layout.addLayout(buttons)
        left_layout.addStretch(1)

        right = QGroupBox("Results")
        right_layout = QVBoxLayout(right)
        self.results = QTextEdit()
        self.results.setReadOnly(True)
        self.results.setPlainText("Analysis results and command previews will appear here.")
        right_layout.addWidget(self.results)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        return page

    def selected_analyses(self) -> list[str]:
        return [
            widget.text()
            for key, widget in self.fields.items()
            if key.startswith("analysis_") and isinstance(widget, QCheckBox) and widget.isChecked()
        ]

    def set_input_files(self, topology: str, trajectory: str, energy: str = "") -> None:
        if self.is_busy():
            self.request_log.emit("Analysis inputs kept unchanged while a job is active.")
            return
        for key, value in (("topology", topology), ("trajectory", trajectory), ("energy", energy)):
            self.fields[key].setText(value)
        self.fields["prepared"].setChecked(False)
        self._backend_override = None

    def set_backend(self, gromacs_binary: str, conda_env: str) -> None:
        self._backend_override = (gromacs_binary, conda_env)

    def reset_session(self) -> None:
        if self.is_busy():
            return
        self._backend_override = None
        self.last_manifest_path = ""
        self.results.clear()
        self.cpptraj.workdir.setText("")
        self.cpptraj.insert_template()
        self.cpptraj.output.clear()
        viewer = getattr(self, "_viewer", None)
        if viewer is not None:
            viewer.close()
            self._viewer = None
        dialog = getattr(self, "_mmpbsa_dialog", None)
        if dialog is not None:
            dialog.close()
            self._mmpbsa_dialog = None

    def is_busy(self) -> bool:
        workers = (self._analysis_worker, self._qc_worker)
        viewer = getattr(self, "_viewer", None)
        dialog = getattr(self, "_mmpbsa_dialog", None)
        return (self._analysis_busy or self._qc_busy or any(worker is not None and worker.isRunning() for worker in workers)
                or bool(viewer and hasattr(viewer, "is_busy") and viewer.is_busy())
                or bool(dialog and dialog.is_busy())
                or bool(hasattr(self.cpptraj, "is_busy") and self.cpptraj.is_busy()))

    def run_selected(self) -> None:
        if self.is_busy() or (self.operation_blocked is not None and self.operation_blocked()):
            self.results.append("Wait for the current job to finish.")
            return
        inputs = AnalysisInputs(
            self.fields["topology"].text(), self.fields["trajectory"].text(),
            self.fields["qc_output"].text() or default_qc_output_dir(self.fields["trajectory"].text()),
            tuple(self.selected_analyses()), self.fields["selection"].text(),
            self.fields["partner_selection"].text(), self.fields["prepared"].isChecked())
        try:
            inputs.validate()
        except ValueError as exc:
            QMessageBox.warning(self, "Analysis inputs", str(exc))
            return
        self.fields["qc_output"].setText(inputs.output_dir)
        self._analysis_worker = AnalysisWorker(inputs, self)
        self._analysis_worker.log.connect(self._append_qc_log)
        self._analysis_worker.completed.connect(self._on_analysis_finished)
        self._analysis_worker.finished.connect(self._analysis_thread_finished)
        self.run_button.setEnabled(False)
        self.qc_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self._analysis_busy = True
        self._analysis_worker.start()

    def _analysis_thread_finished(self):
        self._analysis_busy = False
        self._reset_buttons()

    def _qc_thread_finished(self):
        self._qc_busy = False
        self._reset_buttons()

    def run_selected_preview(self) -> None:
        """Compatibility entry point: now dispatches real selected analyses."""
        self.run_selected()

    def cancel_analysis(self) -> None:
        if self._analysis_worker is not None and self._analysis_worker.isRunning():
            self._analysis_worker.requestInterruption()
            self.results.append("Stop requested; the current analysis will finish and remaining steps will be skipped.")
            self.cancel_button.setEnabled(False)

    def _reset_buttons(self) -> None:
        self.run_button.setEnabled(True)
        self.qc_button.setEnabled(True)
        self.cancel_button.setEnabled(False)

    def _on_analysis_finished(self, ok: bool, manifest_path: str, message: str) -> None:
        self._append_qc_log(message)
        if not manifest_path:
            return
        self.last_manifest_path = manifest_path
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        paths = tuple(item["path"] for item in manifest["results"] if item["status"] == "complete")
        self.request_analysis_files.emit(paths)
        qc_names = {"RMSD", "RMSF", "Radius of gyration", "SASA"}
        qc = tuple(item["path"] for item in manifest["results"] if item["status"] == "complete" and item["name"] in qc_names)
        if qc and not manifest.get("source_changed", False):
            self.request_qc_files.emit(qc)

    def open_mmpbsa_dialog(self) -> None:
        if self.is_busy() or (self.operation_blocked is not None and self.operation_blocked()):
            self.results.append("Wait for the current job to finish.")
            return
        dialog = MMPBSADialog(self, settings=self.settings,
                              topology=self.fields["topology"].text(), trajectory=self.fields["trajectory"].text())
        self._mmpbsa_dialog = dialog
        dialog.exec_()

    def open_trajectory_viewer(self) -> None:
        opener = getattr(self, "viewer_opener", None)
        if opener is not None:
            opener(self.fields["topology"].text(), self.fields["trajectory"].text())
            return
        viewer = getattr(self, "_viewer", None)
        if viewer is not None and hasattr(viewer, "is_busy") and viewer.is_busy():
            viewer.raise_()
            viewer.activateWindow()
            return
        if viewer is not None:
            viewer.close()
        self._viewer = TrajectoryViewerWindow(self.fields["topology"].text(), self.fields["trajectory"].text(), self, settings=self.settings)
        self._viewer.show()

    def qc_inputs(self) -> QCPipelineInputs:
        trajectory = self.fields["trajectory"].text()
        output_dir = self.fields["qc_output"].text() or default_qc_output_dir(trajectory)
        self.fields["qc_output"].setText(output_dir)
        binary, env = self._backend_override or (self.settings.value("gromacs_binary"), self.settings.value("conda_environment"))
        return QCPipelineInputs(
            topology=self.fields["topology"].text(),
            trajectory=trajectory,
            energy=self.fields["energy"].text(),
            output_dir=output_dir,
            fit_group=self.fields["qc_group"].text(),
            analysis_group=self.fields["qc_group"].text(),
            gromacs_binary="gmx" if binary == "auto" else binary,
            conda_env=env,
        )

    def preview_qc_commands(self) -> None:
        try:
            tasks = build_qc_tasks(self.qc_inputs())
        except ValueError as exc:
            QMessageBox.warning(self, "Missing inputs", str(exc))
            return
        lines = ["QC commands to run:"]
        for task in tasks:
            lines.append(f"\n[{task.name}]\n{' '.join(task.command)}")
        self.results.append("\n".join(lines))
        self.request_log.emit("QC command preview prepared.")

    def run_qc_pipeline(self) -> None:
        if self.is_busy() or (self.operation_blocked is not None and self.operation_blocked()):
            self.results.append("Wait for the current job to finish.")
            return
        if not self.fields["prepared"].isChecked():
            QMessageBox.warning(self, "Trajectory preparation", "Confirm that molecules are whole and PBC artifacts have been corrected.")
            return
        try:
            inputs = self.qc_inputs()
            task_count = len(build_qc_tasks(inputs))
        except ValueError as exc:
            QMessageBox.warning(self, "Missing inputs", str(exc))
            return
        self.results.append(f"Starting QC pipeline with {task_count} commands. Output: {inputs.output_dir}")
        self._qc_worker = QCGromacsWorker(inputs, parent=self)
        self._qc_worker.log.connect(self._append_qc_log)
        self._qc_worker.completed.connect(self._on_qc_finished)
        self._qc_worker.finished.connect(self._qc_thread_finished)
        self.run_button.setEnabled(False)
        self.qc_button.setEnabled(False)
        self._qc_busy = True
        self._qc_worker.start()

    def _append_qc_log(self, line: str) -> None:
        if line:
            self.results.append(line)
            self.request_log.emit(line)

    def _on_qc_finished(self, ok: bool, files: object, message: str) -> None:
        prefix = "[OK]" if ok else "[FAIL]"
        self.results.append(f"{prefix} {message}")
        self.request_log.emit(f"{prefix} {message}")
        if ok:
            self.request_qc_files.emit(tuple(files))

    def preview_text(self) -> str:
        return (
            "[Analysis Preview]\n"
            f"Topology: {self.fields['topology'].text() or 'Not set'}\n"
            f"Trajectory: {self.fields['trajectory'].text() or 'Not set'}\n"
            f"QC output: {self.fields['qc_output'].text() or 'Auto'}\n"
            f"Selected: {', '.join(self.selected_analyses()) if self.selected_analyses() else 'None'}"
        )
