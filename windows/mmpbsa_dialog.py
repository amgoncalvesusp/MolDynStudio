"""MM-PBSA / MM-GBSA setup dialog."""

from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
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
)

from analysis.mmpbsa import MMPBSAOptions, build_input_file
from utils.tooltips import tooltip


class MMPBSADialog(QDialog):
    run_requested = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
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

        self.alanine_scanning = QCheckBox("Enable alanine scanning")
        form.addRow("Alanine scanning", self.alanine_scanning)

        self.entropy = QComboBox()
        self.entropy.addItems(["None", "NMODE", "Quasi-harmonic"])
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
        run_button = QPushButton("Run")
        run_button.clicked.connect(self._emit_run)
        row.addWidget(run_button)
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
        self.preview.setPlainText(build_input_file(self.options()))

    def _emit_run(self) -> None:
        self.refresh_preview()
        self.run_requested.emit(self.options())

