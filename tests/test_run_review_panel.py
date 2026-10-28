"""User-visible prepared-run review and draft isolation contracts."""
from __future__ import annotations

import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from PyQt5.QtCore import QTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from core.run_manifest import load_manifest, save_manifest
from tabs.md_run_tab import MDRunTab
from tests.test_md_run_tab import _capabilities, _manifest, _validators


class RunReviewPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.path = _manifest(self.root)
        prepared = load_manifest(self.path)
        self.snapshot = {"protein": "protein.pdb", "duration": 5.0}
        save_manifest(replace(prepared, metadata={
            "preparation_review": {"setup_snapshot": self.snapshot},
        }), self.path)
        self.tab = MDRunTab(
            artifact_validators=_validators(),
            capability_probe=lambda *_: _capabilities(),
        )
        self.addCleanup(self.tab.deleteLater)
        self.tab.load_project(str(self.root))

    def test_review_identifies_prepared_inputs_and_real_mdp_files(self):
        panel = self.tab.review_panel
        self.assertIn("amber99sb", panel.summary.text())
        self.assertIn("gmx", panel.summary.text())
        self.assertTrue(panel.review_button.isEnabled())
        (self.root / "md.mdp").write_text("integrator = md\nnsteps = 2000\ndt = 0.001\n")
        QTimer.singleShot(0, lambda: self.app.activeModalWidget().accept())
        panel.open_review()
        self.assertIn("nsteps = 2000", dict(panel.review.mdp_files)["md.mdp"])

    def test_pending_setup_blocks_buttons_and_direct_launch_then_undo_restores(self):
        self.tab.set_setup_state(self.snapshot)
        self.assertTrue(self.tab.run_button.isEnabled())
        self.tab.set_setup_state({**self.snapshot, "duration": 10.0})
        self.assertIn("Pending", self.tab.review_panel.setup_status.text())
        self.assertFalse(self.tab.run_button.isEnabled())
        self.assertFalse(self.tab.resume_button.isEnabled())
        before = self.path.read_bytes()
        with mock.patch.object(self.tab, "_start_runner") as start:
            self.tab.start_run()
            self.tab.resume_run()
        start.assert_not_called()
        self.assertEqual(before, self.path.read_bytes())
        self.tab.set_setup_state(self.snapshot)
        self.assertTrue(self.tab.run_button.isEnabled())

    def test_legacy_manifest_keeps_existing_execution_contract_explicit(self):
        manifest = load_manifest(self.path)
        save_manifest(replace(manifest, metadata={}), self.path)
        self.tab.load_project(str(self.root))
        self.tab.set_setup_state(self.snapshot)
        self.assertTrue(self.tab.run_button.isEnabled())
        self.assertIn("not recorded", self.tab.review_panel.setup_status.text().lower())

    def test_environment_check_reports_detected_build_and_releases_worker(self):
        panel = self.tab.review_panel
        panel.environment_button.click()
        deadline = time.monotonic() + 3
        while panel.is_busy() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.app.processEvents()
        self.assertFalse(panel.is_busy())
        self.assertIn("2026.3", panel.environment_status.text())
        self.assertIn("GPU", panel.environment_status.text())
        self.assertNotIn("GPU active", panel.environment_status.text())

    def test_environment_failure_names_recovery_action(self):
        panel = self.tab.review_panel
        panel._capability_probe = lambda *_: replace(_capabilities(), executable_ok=False,
                                                    version_text="gmx: command not found")
        panel.environment_button.click()
        deadline = time.monotonic() + 3
        while panel.is_busy() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.app.processEvents()
        self.assertIn("command not found", panel.environment_status.text())
        self.assertIn("Settings", panel.environment_status.text())
        self.assertTrue(self.tab.run_button.isEnabled())

    def test_environment_check_respects_other_project_operations(self):
        self.tab.operation_blocked = lambda: True
        panel = self.tab.review_panel
        with mock.patch("windows.run_review_panel.EnvironmentProbeWorker") as worker:
            panel.check_environment()
        worker.assert_not_called()

    def test_repreparation_invalidates_previous_environment_observation(self):
        panel = self.tab.review_panel
        panel.show_capabilities(_capabilities())
        manifest = load_manifest(self.path)
        save_manifest(replace(manifest, requested_cores=2), self.path)
        self.tab.refresh_from_manifest()
        self.assertIn("not checked", panel.environment_status.text())

    def test_successful_repreparation_clears_failure_for_previous_preparation(self):
        self.tab.review_panel.show_failure("Old invalid topology")
        manifest = load_manifest(self.path)
        preparation = replace(manifest.stages["preparation"], finished_at="2026-10-27T12:00:00+00:00")
        save_manifest(replace(manifest, stages={**manifest.stages, "preparation": preparation}), self.path)
        self.tab.refresh_from_manifest()
        self.assertTrue(self.tab.review_panel.failure_status.isHidden())

    def test_run_failure_exposes_project_logs_and_keeps_prepared_inputs(self):
        self.tab._on_runner_finished(False, "NVT failed: invalid topology")
        panel = self.tab.review_panel
        self.assertIn("NVT failed", panel.failure_status.text())
        self.assertIn("MD Setup", panel.failure_status.text())
        self.assertTrue(panel.folder_button.isEnabled())
        self.assertTrue((self.root / "topol.top").is_file())


if __name__ == "__main__":
    unittest.main()
