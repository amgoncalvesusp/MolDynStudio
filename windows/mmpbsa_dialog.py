"""MM-PBSA / MM-GBSA setup dialog."""

from __future__ import annotations

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QMessageBox,
    QLabel,
)

from analysis.mmpbsa import MMPBSAAnalysis, MMPBSAOptions, build_input_file
from core.settings import SettingsStore
from tabs.base import PathSelector
from utils.tooltips import tooltip


class MMPBSAWorker(QThread):
    completed = pyqtSignal(bool, str)

    def __init__(self, inputs: dict, parent=None):
        super().__init__(parent)
        self.inputs = inputs

    def run(self):
        try:
            result = MMPBSAAnalysis().run(**self.inputs)
            self.completed.emit(result.data["success"], f"{result.message}\n{result.data['output']}\nManifest: {result.data['manifest']}\n{result.data['stderr'][-3000:]}")
        except Exception as exc:
            self.completed.emit(False, str(exc))


class MMPBSADialog(QDialog):
    run_requested = pyqtSignal(object)

    def __init__(self, parent=None, settings=None, topology="", trajectory=""):
        super().__init__(parent)
        self.settings = settings if settings is not None else SettingsStore()
        self._worker = None
        self._busy = False
        self.setWindowTitle("MM-PBSA / MM-GBSA Setup")
        self.resize(680, 620)

        layout = QVBoxLayout(self)

        method_group = QGroupBox("Method")
        method_layout = QHBoxLayout(method_group)
        self.gbsa = QRadioButton("MM-GBSA")
        self.gbsa.setChecked(True)
        self.gbsa.setToolTip(tooltip("mm_gbsa"))
        self.pbsa = QRadioButton("MM-PBSA")
        self.pbsa.setToolTip(tooltip("mm_pbsa"))
        method_layout.addWidget(self.gbsa)
        method_layout.addWidget(self.pbsa)
        method_layout.addStretch(1)
        layout.addWidget(method_group)

        form_group = QGroupBox("Options")
        form = QFormLayout(form_group)
        self.topology = PathSelector("Matching TPR", "Topology (*.tpr);;All files (*)")
        self.topology.setText(topology)
        form.addRow("Complex structure (.tpr)", self.topology)
        self.trajectory = PathSelector("Prepared trajectory", "Trajectory (*.xtc *.trr *.nc *.dcd);;All files (*)")
        self.trajectory.setText(trajectory)
        form.addRow("Trajectory", self.trajectory)
        self.workdir = PathSelector("Folder containing topol.top and index.ndx", directory=True)
        form.addRow("Topology working folder", self.workdir)
        self.receptor = QSpinBox()
        self.receptor.setRange(0, 100000)
        self.receptor.setValue(1)
        form.addRow("Receptor index-group ID", self.receptor)
        self.ligand = QSpinBox()
        self.ligand.setRange(0, 100000)
        self.ligand.setValue(13)
        form.addRow("Ligand index-group ID", self.ligand)
        note = QLabel("Use numeric groups from index.ndx. Keep topol.top and its include files in the working folder. Energies are end-state estimates; no automatic affinity interpretation.")
        note.setWordWrap(True)
        form.addRow(note)
        self.igb = QComboBox()
        self.igb.addItems(["5 - OBC model", "2 - GB HCT", "8 - GBneck2"])
        form.addRow("IGB model", self.igb)

        self.salt = QDoubleSpinBox()
        self.salt.setRange(0.0, 5.0)
        self.salt.setDecimals(3)
        self.salt.setSingleStep(0.01)
        self.salt.setValue(0.150)
        form.addRow("Salt concentration (M)", self.salt)

        self.start = QSpinBox()
        self.start.setRange(1, 10_000_000)
        self.start.setValue(1)
        form.addRow("Start frame", self.start)

        self.end = QSpinBox()
        self.end.setRange(1, 10_000_000)
        self.end.setValue(9999)
        form.addRow("End frame", self.end)

        self.interval = QSpinBox()
        self.interval.setRange(1, 100_000)
        self.interval.setValue(1)
        form.addRow("Interval", self.interval)

        self.decomposition = QCheckBox("Per-residue decomposition")
        form.addRow("Decomposition", self.decomposition)

        self.entropy = QComboBox()
        self.entropy.addItems(["None", "NMODE"])
        form.addRow("Entropy correction", self.entropy)
        layout.addWidget(form_group)

        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMinimumHeight(180)
        layout.addWidget(self.preview)

        row = QHBoxLayout()
        preview_button = QPushButton("Preview Input File")
        preview_button.clicked.connect(self.refresh_preview)
        row.addWidget(preview_button)
        self.run_button = QPushButton("Run MM-PBSA / MM-GBSA")
        self.run_button.clicked.connect(self._emit_run)
        row.addWidget(self.run_button)
        row.addStretch(1)
        layout.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh_preview()

    def options(self) -> MMPBSAOptions:
        igb = int(self.igb.currentText().split(" ", 1)[0])
        return MMPBSAOptions(
            method="PBSA" if self.pbsa.isChecked() else "GBSA",
            igb=igb,
            saltcon=self.salt.value(),
            startframe=self.start.value(),
            endframe=self.end.value(),
            interval=self.interval.value(),
            decomposition=self.decomposition.isChecked(),
            entropy=self.entropy.currentText(),
        )

    def refresh_preview(self) -> None:
        try:
            self.preview.setPlainText(build_input_file(self.options()))
        except ValueError as exc:
            self.preview.setPlainText(f"Invalid options: {exc}")

    def _emit_run(self) -> None:
        if self.is_busy():
            return
        try:
            options = self.options()
            build_input_file(options)
        except ValueError as exc:
            QMessageBox.warning(self, "MM-PBSA inputs", str(exc))
            return
        self.refresh_preview()
        inputs = dict(topology=self.topology.text(), trajectory=self.trajectory.text(),
                      receptor_group=str(self.receptor.value()), ligand_group=str(self.ligand.value()),
                      options=options, workdir=self.workdir.text(),
                      conda_env=self.settings.value("conda_environment"))
        self._worker = MMPBSAWorker(inputs, self)
        self._worker.completed.connect(self._on_finished)
        self._worker.finished.connect(self._thread_finished)
        self.run_button.setEnabled(False)
        self._busy = True
        self.preview.append("\nRunning gmx_MMPBSA in the configured scientific environment...")
        self._worker.start()
        self.run_requested.emit(options)

    def is_busy(self):
        return self._busy or (self._worker is not None and self._worker.isRunning())

    def _thread_finished(self):
        self._busy = False
        self.run_button.setEnabled(True)

    def _on_finished(self, ok, message):
        self.preview.append(("\n[OK] " if ok else "\n[FAIL] ") + message)

    def reject(self):
        if self.is_busy():
            self.preview.append("Wait for MM-PBSA to finish before closing this dialog.")
            return
        super().reject()

    def closeEvent(self, event):
        if self.is_busy():
            event.ignore()
            return
        super().closeEvent(event)

