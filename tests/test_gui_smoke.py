from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

from PyQt5.QtWidgets import QApplication
from PyQt5.QtWidgets import QPushButton

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
        self.assertIs(window.pages["MD Setup"].settings, window.settings)
        self.assertIs(window.pages["MD Run"].settings, window.settings)
        self.assertFalse(
            any(
                button.text() == "Pause"
                for button in window.pages["MD Run"].findChildren(QPushButton)
            )
        )

    def test_setup_wizard_uses_the_host_platform_label(self):
        with mock.patch.object(SetupWizard, "refresh_all"):
            wizard = SetupWizard()
        self.addCleanup(wizard.close)

        expected = "WSL2" if sys.platform == "win32" else "Linux"
        self.assertIn(expected, wizard.row_wsl.name_label.text())

    def test_navigating_to_md_run_hands_off_the_exact_setup_project(self):
        window = studio.MainWindow()
        self.addCleanup(window.close)
        project_dir = "C:/prepared-md-project"
        window.pages["MD Setup"].fields["project_dir"].setText(project_dir)
        run_page = window.pages["MD Run"]

        with mock.patch.object(run_page, "load_project") as load_project:
            window.switch_to_page("MD Run")

        load_project.assert_called_once_with(project_dir)

    def test_empty_setup_project_clears_the_md_run_context(self):
        window = studio.MainWindow()
        self.addCleanup(window.close)
        run_page = window.pages["MD Run"]
        run_page.load_project("C:/old-md-project")
        window.pages["MD Setup"].fields["project_dir"].setText("")

        window.switch_to_page("MD Setup")
        window.switch_to_page("MD Run")

        self.assertIn("Project: No project loaded", run_page.preview_text())


if __name__ == "__main__":
    unittest.main()
