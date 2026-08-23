from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

from PyQt5.QtWidgets import QApplication

import gromacs_analysis_studio_v11 as studio
from setup_wizard import SetupWizard


class MainWindowSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_main_window_builds_all_primary_pages(self):
        window = studio.MainWindow()
        self.addCleanup(window.close)

        self.assertEqual(window.windowTitle(), f"{studio.APP_NAME} - {studio.APP_VERSION}")
        self.assertGreaterEqual(window.stack.count(), 5)

        for index in range(window.stack.count()):
            window.stack.setCurrentIndex(index)
            self.app.processEvents()

        self.assertEqual(window.stack.currentIndex(), window.stack.count() - 1)

    def test_setup_wizard_uses_the_host_platform_label(self):
        with mock.patch.object(SetupWizard, "refresh_all"):
            wizard = SetupWizard()
        self.addCleanup(wizard.close)

        expected = "WSL2" if sys.platform == "win32" else "Linux"
        self.assertIn(expected, wizard.row_wsl.name_label.text())


if __name__ == "__main__":
    unittest.main()
