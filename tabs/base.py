"""Shared Qt helpers for MolDynStudio pages."""

from __future__ import annotations

from typing import Any, Dict

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QWidget,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QSpinBox,
)


class PathSelector(QWidget):
    def __init__(self, placeholder: str, file_filter: str = "All Files (*)", directory: bool = False, parent=None):
        super().__init__(parent)
        self.file_filter = file_filter
        self.directory = directory
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.line_edit = QLineEdit()
        self.line_edit.setPlaceholderText(placeholder)
        layout.addWidget(self.line_edit, 1)
        button = QPushButton("Browse")
        button.clicked.connect(self.browse)
        layout.addWidget(button)

    def browse(self) -> None:
        if self.directory:
            path = QFileDialog.getExistingDirectory(self, "Select folder")
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Select file", "", self.file_filter)
        if path:
            self.line_edit.setText(path)

    def text(self) -> str:
        return self.line_edit.text().strip()

    def setText(self, value: str) -> None:
        self.line_edit.setText(value)


class MolDynBasePage(QWidget):
    request_log = pyqtSignal(str)
    request_preview = pyqtSignal(str)
    request_qc_files = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.fields: Dict[str, QWidget] = {}

    def get_state(self) -> Dict[str, Any]:
        state: Dict[str, Any] = {}
        for key, widget in self.fields.items():
            if isinstance(widget, QLineEdit):
                state[key] = widget.text()
            elif isinstance(widget, PathSelector):
                state[key] = widget.text()
            elif isinstance(widget, QCheckBox):
                state[key] = widget.isChecked()
            elif isinstance(widget, QComboBox):
                state[key] = widget.currentText()
            elif isinstance(widget, QSpinBox):
                state[key] = widget.value()
            elif isinstance(widget, QDoubleSpinBox):
                state[key] = widget.value()
        return state

    def set_state(self, state: Dict[str, Any]) -> None:
        for key, value in state.items():
            widget = self.fields.get(key)
            if widget is None:
                continue
            if isinstance(widget, QLineEdit):
                widget.setText(str(value))
            elif isinstance(widget, PathSelector):
                widget.setText(str(value))
            elif isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QComboBox):
                idx = widget.findText(str(value))
                if idx >= 0:
                    widget.setCurrentIndex(idx)
            elif isinstance(widget, QSpinBox):
                widget.setValue(int(value))
            elif isinstance(widget, QDoubleSpinBox):
                widget.setValue(float(value))

    def preview_text(self) -> str:
        return "No preview available."
