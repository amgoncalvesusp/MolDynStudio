from __future__ import annotations

import os
from pathlib import Path
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

from PyQt5.QtWidgets import QApplication

from tabs.md_run_tab import MDRunTab


class MDRunTabRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_run_all_does_not_use_mock_timer(self):
        tab = MDRunTab()
        self.addCleanup(tab.deleteLater)
        assert not hasattr(tab, "start_mock_run")

    def test_md_run_source_contains_no_synthetic_execution_markers(self):
        source_path = Path(__file__).resolve().parents[1] / "tabs" / "md_run_tab.py"
        source = source_path.read_text(encoding="utf-8")

        for marker in ("start_mock_run", "Pipeline preview completed", "-420000", "299.7"):
            self.assertNotIn(marker, source)


if __name__ == "__main__":
    unittest.main()
