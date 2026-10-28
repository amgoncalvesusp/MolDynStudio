from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

from PyQt5.QtWidgets import QApplication

import gromacs_analysis_studio_v11 as studio


class SetupRunHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = studio.MainWindow()
        self.window._autosave_timer.stop()
        self.addCleanup(self.window.close)
        self.setup = self.window.pages["MD Setup"]
        self.run = self.window.pages["MD Run"]

    def test_edits_are_forwarded_without_replacing_prepared_project(self):
        with tempfile.TemporaryDirectory() as directory:
            self.window.active_project_dir = Path(directory).resolve()
            with mock.patch.object(self.run, "set_setup_state", create=True) as update:
                self.setup.fields["duration"].setValue(17.0)
            update.assert_called_once()
            self.assertEqual(update.call_args.args[0]["duration"], 17.0)
            self.assertEqual(self.window.active_project_dir, Path(directory).resolve())

    def test_preparation_handoff_refreshes_draft_after_loading_project(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                mock.patch.object(self.run, "load_project") as load,
                mock.patch.object(self.run, "set_setup_state", create=True) as update,
            ):
                self.window._on_project_prepared(directory, "moldynstudio_run.json")
            load.assert_called_once_with(str(Path(directory).resolve()))
            update.assert_called_once_with(self.setup.review_snapshot())

    def test_reopen_preserves_pending_draft_and_prepared_context(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            data = {"pages": {"MD Setup": {"duration": 18.0, "project_dir": str(project)}},
                    "project_context": {"project_dir": str(project),
                                        "manifest_path": "moldynstudio_run.json"}}
            with mock.patch.object(self.run, "set_setup_state", create=True) as update:
                self.window.apply_session_data(data)
            self.assertEqual(update.call_args.args[0], self.setup.review_snapshot())
            self.assertEqual(self.window.active_project_dir, project)
            self.assertEqual(self.setup.fields["duration"].value(), 18.0)

    def test_review_button_opens_run_page_with_latest_draft(self):
        with (
            mock.patch.object(self.run, "set_setup_state", create=True) as update,
            mock.patch.object(self.run, "open_review", create=True) as review,
        ):
            self.setup.review_run_button.click()
        self.assertEqual(self.window.nav.currentRow(), studio.NAV_ITEMS.index("MD Run"))
        update.assert_called_with(self.setup.review_snapshot())
        review.assert_called_once_with()

    def test_toolbar_dispatch_stays_available_but_blocks_pending_setup(self):
        self.window.switch_to_page("MD Run")
        self.assertTrue(self.window.run_btn.isEnabled())
        with mock.patch.object(type(self.run.review_panel), "pending", new_callable=mock.PropertyMock, return_value=True):
            self.window._sync_setup_review()
            self.assertFalse(self.window.run_btn.isEnabled())


if __name__ == "__main__":
    unittest.main()
