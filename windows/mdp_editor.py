"""Simple MDP editor dialog with save support."""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtWidgets import QDialog, QDialogButtonBox, QFileDialog, QPushButton, QTextEdit, QVBoxLayout


class MDPEditor(QDialog):
    def __init__(self, title: str, content: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 640)
        layout = QVBoxLayout(self)
        self.editor = QTextEdit()
        self.editor.setPlainText(content)
        layout.addWidget(self.editor)
        save = QPushButton("Save")
        save.clicked.connect(self.save)
        layout.addWidget(save)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save MDP", str(Path.home() / "md.mdp"), "MDP files (*.mdp)")
        if path:
            Path(path).write_text(self.editor.toPlainText(), encoding="utf-8")

