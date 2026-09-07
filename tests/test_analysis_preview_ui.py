from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QPushButton

from tabs.analysis_tab import AnalysisTab
from windows.mmpbsa_dialog import MMPBSADialog


class AnalysisPreviewUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def button(self, widget, text):
        matches = [button for button in widget.findChildren(QPushButton) if button.text() == text]
        self.assertEqual(len(matches), 1, f"Expected exactly one {text!r} button")
        return matches[0]

    def test_selected_analysis_action_describes_preview_and_reports_no_execution(self):
        page = AnalysisTab()
        self.addCleanup(page.close)
        self.button(page, "Preview Selected").click()
        self.assertIn("RMSD", page.results.toPlainText())
        self.assertIn("No analysis was executed", page.results.toPlainText())
        self.assertNotIn("Run Selected", [item.text() for item in page.findChildren(QPushButton)])

    def test_mmpbsa_dialog_generates_input_preview_from_current_options(self):
        dialog = MMPBSADialog()
        self.addCleanup(dialog.close)
        previews = []
        dialog.input_previewed.connect(previews.append)
        dialog.start.setValue(25)
        self.button(dialog, "Generate Input Preview").click()
        self.assertEqual(previews[0].startframe, 25)
        self.assertIn("startframe=25", dialog.preview.toPlainText())
        self.assertNotIn("Run", [item.text() for item in dialog.findChildren(QPushButton)])

    def test_mmpbsa_configuration_reports_preview_in_analysis_page(self):
        page = AnalysisTab()
        self.addCleanup(page.close)

        def preview_input(dialog):
            self.button(dialog, "Generate Input Preview").click()

        with patch.object(MMPBSADialog, "exec_", preview_input):
            self.button(page, "Configure MM-PBSA / MM-GBSA").click()
        self.assertIn("input preview", page.results.toPlainText())
        self.assertIn("No analysis was executed", page.results.toPlainText())


if __name__ == "__main__":
    unittest.main()
