from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from tabs.qc_dashboard_tab import QCDashboardWidget


class QCDashboardWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_load_files_populates_table_and_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rep1_rmsd.xvg"
            path.write_text(
                "0 0.4\n1 0.35\n2 0.31\n3 0.30\n4 0.30\n5 0.31\n",
                encoding="utf-8",
            )
            widget = QCDashboardWidget()
            widget.load_files([str(path)])

        self.assertEqual(widget.table.rowCount(), 1)
        self.assertIn("Overall status", widget.summary.toPlainText())


if __name__ == "__main__":
    unittest.main()
