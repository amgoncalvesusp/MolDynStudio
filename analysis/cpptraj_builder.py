"""CPPTRAJ script editor widget."""

from __future__ import annotations

from typing import Mapping

from PyQt5.QtGui import QColor, QTextCharFormat, QSyntaxHighlighter
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import QComboBox, QHBoxLayout, QPushButton, QTextEdit, QVBoxLayout, QWidget

from core import wsl_bridge
from core.settings import SettingsStore
from tabs.base import PathSelector


TEMPLATES: Mapping[str, str] = {
    "RMSD": "parm topology.parm7\ntrajin trajectory.nc\nrmsd :1-999@CA out rmsd.dat\nrun\n",
    "RMSF": "parm topology.parm7\ntrajin trajectory.nc\nrms first :1-999@CA\natomicfluct :1-999@CA out rmsf.dat byres\nrun\n",
    "H-bonds": "parm topology.parm7\ntrajin trajectory.nc\nhbond HB out hbonds.dat avgout hbonds_avg.dat\nrun\n",
    "Strip water": "parm topology.parm7\ntrajin trajectory.nc\nstrip :WAT,Na+,Cl-\ntrajout stripped.nc\nrun\n",
    "Clustering": "parm topology.parm7\ntrajin trajectory.nc\ncluster hieragglo epsilon 2.0 clusters 5 out clusters.dat summary summary.dat\nrun\n",
}


class CpptrajHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        keyword_format = QTextCharFormat()
        keyword_format.setForeground(QColor("#E87722"))
        keyword_format.setFontWeight(700)
        self.keyword_format = keyword_format
        self.keywords = {"parm", "trajin", "trajout", "rmsd", "atomicfluct", "hbond", "strip", "cluster", "run"}

    def highlightBlock(self, text: str) -> None:
        for word in self.keywords:
            start = text.find(word)
            while start >= 0:
                self.setFormat(start, len(word), self.keyword_format)
                start = text.find(word, start + len(word))


class CpptrajWorker(QThread):
    completed = pyqtSignal(str)

    def __init__(self, script, binary, conda_env, workdir, parent=None):
        super().__init__(parent)
        self.inputs = (script, binary, conda_env, workdir)

    def run(self):
        script, binary, conda_env, workdir = self.inputs
        try:
            completed = wsl_bridge.run([binary], input=script, env_name=conda_env, cwd=workdir or None, check=False)
            prefix = "[OK] " if completed.returncode == 0 else f"[FAIL] Exit code {completed.returncode}\n"
            self.completed.emit(prefix + completed.stdout + completed.stderr)
        except Exception as exc:
            self.completed.emit(f"CPPTRAJ execution failed: {exc}")


class CpptrajBuilderWidget(QWidget):
    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self.settings = settings if settings is not None else SettingsStore()
        self._worker = None
        self._busy = False
        self.operation_blocked = None
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        self.templates = QComboBox()
        self.templates.addItems(TEMPLATES.keys())
        top.addWidget(self.templates)
        insert = QPushButton("Insert")
        insert.clicked.connect(self.insert_template)
        top.addWidget(insert)
        self.run_button = QPushButton("Run CPPTRAJ")
        self.run_button.clicked.connect(self.run_cpptraj)
        top.addWidget(self.run_button)
        layout.addLayout(top)
        self.workdir = PathSelector("Working folder for relative script paths", directory=True)
        layout.addWidget(self.workdir)

        self.editor = QTextEdit()
        self.editor.setPlainText(TEMPLATES["RMSD"])
        self.highlighter = CpptrajHighlighter(self.editor.document())
        layout.addWidget(self.editor, 2)

        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setMinimumHeight(120)
        layout.addWidget(self.output, 1)

    def insert_template(self) -> None:
        template = TEMPLATES[self.templates.currentText()]
        self.editor.setPlainText(template)

    def run_cpptraj(self) -> None:
        if self.is_busy() or (self.operation_blocked and self.operation_blocked()):
            self.output.setPlainText("Wait for the active job to finish.")
            return
        script = self.editor.toPlainText()
        if not script.strip():
            self.output.setPlainText("Enter a CPPTRAJ script first.")
            return
        binary = self.settings.value("cpptraj_binary")
        binary = "cpptraj" if binary == "auto" else wsl_bridge.win_to_wsl(binary)
        self._worker = CpptrajWorker(script, binary, self.settings.value("conda_environment"), self.workdir.text(), self)
        self._worker.completed.connect(self.output.setPlainText)
        self._worker.finished.connect(self._on_finished)
        self._busy = True
        self.run_button.setEnabled(False)
        self.output.setPlainText("Running CPPTRAJ...")
        self._worker.start()

    def _on_finished(self):
        self._busy = False
        self.run_button.setEnabled(True)

    def is_busy(self):
        return self._busy or (self._worker is not None and self._worker.isRunning())
