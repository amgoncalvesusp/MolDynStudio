"""CPPTRAJ script editor widget."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QTextCharFormat, QSyntaxHighlighter
from PyQt5.QtWidgets import QComboBox, QHBoxLayout, QPushButton, QTextEdit, QVBoxLayout, QWidget

from core import wsl_bridge


TEMPLATES: Mapping[str, str] = {
    "RMSD": "parm topology.parm7\ntrajin trajectory.nc\nrmsd :1-999@CA out rmsd.dat\nrun\n",
    "RMSF": "parm topology.parm7\ntrajin trajectory.nc\natomicfluct :1-999@CA out rmsf.dat byres\nrun\n",
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


class CpptrajBuilderWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        self.templates = QComboBox()
        self.templates.addItems(TEMPLATES.keys())
        top.addWidget(self.templates)
        insert = QPushButton("Insert")
        insert.clicked.connect(self.insert_template)
        top.addWidget(insert)
        run = QPushButton("Run CPPTRAJ")
        run.clicked.connect(self.run_cpptraj)
        top.addWidget(run)
        layout.addLayout(top)

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
        script = self.editor.toPlainText()
        try:
            completed = wsl_bridge.run(
                ["cpptraj", "-i", "-"],
                input=script,
                check=False,
            )
            self.output.setPlainText(completed.stdout + completed.stderr)
        except Exception as exc:
            self.output.setPlainText(f"CPPTRAJ execution failed: {exc}")
