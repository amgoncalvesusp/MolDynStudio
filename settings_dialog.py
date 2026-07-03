"""Application preferences dialog."""

from __future__ import annotations

import os
from pathlib import Path

from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from core.settings import SettingsStore


class SettingsDialog(QDialog):
    def __init__(self, settings: SettingsStore, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Preferences")
        self.resize(640, 420)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)

        self.gromacs = self._path_row(form, "GROMACS binary path", "gromacs_binary")
        self.cpptraj = self._path_row(form, "CPPTRAJ binary", "cpptraj_binary")

        self.conda_env = QLineEdit(str(settings.value("conda_environment")))
        form.addRow("Conda environment", self.conda_env)

        self.output = self._path_row(form, "Default output folder", "default_output_folder", directory=True)

        self.cores = QSpinBox()
        self.cores.setRange(1, max(1, os.cpu_count() or 1))
        self.cores.setValue(int(settings.value("cores", 4)))
        form.addRow(f"Cores for MD (detected: {os.cpu_count() or 1})", self.cores)

        self.gpu = QComboBox()
        self.gpu.addItems(["Auto", "CPU only", "CUDA", "OpenCL"])
        self.gpu.setCurrentText(str(settings.value("gpu_mode", "Auto")))
        form.addRow("GPU acceleration", self.gpu)

        self.theme = QComboBox()
        self.theme.addItems(["Light", "Dark"])
        self.theme.setCurrentText(str(settings.value("theme", "Light")))
        form.addRow("Theme", self.theme)

        self.language = QComboBox()
        self.language.addItems(["English", "Portuguese"])
        self.language.setCurrentText(str(settings.value("language", "English")))
        form.addRow("Language", self.language)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _path_row(self, form: QFormLayout, label: str, key: str, directory: bool = False) -> QLineEdit:
        row = QHBoxLayout()
        edit = QLineEdit(str(self.settings.value(key)))
        row.addWidget(edit)
        browse = QPushButton("Browse")
        browse.clicked.connect(lambda: self._browse(edit, directory))
        row.addWidget(browse)
        auto = QPushButton("Auto-detect")
        auto.clicked.connect(lambda: edit.setText("auto"))
        row.addWidget(auto)
        form.addRow(label, row)
        return edit

    def _browse(self, edit: QLineEdit, directory: bool) -> None:
        if directory:
            path = QFileDialog.getExistingDirectory(self, "Select folder", str(Path.home()))
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Select executable", str(Path.home()), "All files (*)")
        if path:
            edit.setText(path)

    def save(self) -> None:
        self.settings.set_value("gromacs_binary", self.gromacs.text())
        self.settings.set_value("cpptraj_binary", self.cpptraj.text())
        self.settings.set_value("conda_environment", self.conda_env.text())
        self.settings.set_value("default_output_folder", self.output.text())
        self.settings.set_value("cores", self.cores.value())
        self.settings.set_value("gpu_mode", self.gpu.currentText())
        self.settings.set_value("theme", self.theme.currentText())
        self.settings.set_value("language", self.language.currentText())
        self.accept()

