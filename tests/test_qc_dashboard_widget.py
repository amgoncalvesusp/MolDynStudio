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

    def test_reimport_replaces_source_and_does_not_create_fake_replica(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rmsd.xvg"
            path.write_text('@ xaxis label "Time (ns)"\n0 0.1\n1 0.1\n2 0.1\n', encoding="utf-8")
            widget = QCDashboardWidget()
            self.addCleanup(widget.close)
            widget.load_files([str(path)])
            widget.load_files([str(path)])
            self.assertEqual(len(widget.series), 1)
            self.assertEqual(widget.summaries[0].x_unit, "ns")
            self.assertIn("heuristic", widget.summary.toPlainText().lower())

    def test_plot_keeps_rmsf_separate_from_time_and_flags_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for name, content in (("rmsf.xvg", '1 0.1\n2 0.2\n3 0.1\n'),
                                  ("rmsd.xvg", '@ xaxis label "Time (ps)"\n0 0.1\n1000 0.1\n1000 0.1\n')):
                path = Path(tmp) / name
                path.write_text(content, encoding="utf-8")
                paths.append(str(path))
            widget = QCDashboardWidget()
            self.addCleanup(widget.close)
            widget.load_files(paths)
            self.assertIn("Overall status: FAIL", widget.summary.toPlainText())
            if widget.figure is not None:
                self.assertEqual(len(widget.figure.axes), 2)
                self.assertIn("Residue", {ax.get_xlabel() for ax in widget.figure.axes})


if __name__ == "__main__":
    unittest.main()
