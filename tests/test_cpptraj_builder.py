from __future__ import annotations

import os
import subprocess
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from analysis.cpptraj_builder import CpptrajBuilderWidget


class CpptrajBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_run_cpptraj_streams_editor_script_to_standard_input(self):
        widget = CpptrajBuilderWidget()
        widget.editor.setPlainText("parm system.pdb\nrun\n")
        completed = subprocess.CompletedProcess(
            ["cpptraj"],
            0,
            stdout="CPPTRAJ completed",
            stderr="",
        )

        with patch("analysis.cpptraj_builder.wsl_bridge.run", return_value=completed) as run:
            widget.run_cpptraj()

        run.assert_called_once_with(
            ["cpptraj"],
            input="parm system.pdb\nrun\n",
            check=False,
        )
        self.assertIn("completed", widget.output.toPlainText())
        widget.close()


if __name__ == "__main__":
    unittest.main()
