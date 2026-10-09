from __future__ import annotations

import os
import importlib.util
from pathlib import Path
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtGui import QCloseEvent
from PyQt5.QtCore import QElapsedTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from core.trajectory_frames import FrameExport, FrameSnapshot
from windows.trajectory_viewer import TrajectoryViewerWindow


class TrajectoryViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        with mock.patch("windows.trajectory_viewer.QTimer.singleShot"):
            self.window = TrajectoryViewerWindow("topology.pdb", "trajectory.xtc")
        self.addCleanup(self.window.close)

    def test_initial_state_has_no_export_or_launch(self):
        self.assertFalse(self.window.export_button.isEnabled())
        self.assertFalse(self.window.launch_button.isEnabled())
        self.assertFalse(self.window.is_busy())

    def test_loaded_frame_reports_index_units_and_atom_counts(self):
        snapshot = FrameSnapshot(1, 3, 10.0, 1, 1, ((0., 0., 0.),), ((1., 1., 1.),), False)
        self.window._session = mock.Mock()
        self.window._show_snapshot(snapshot)
        self.window._set_busy(False)
        self.assertEqual(self.window.frame_spin.maximum(), 2)
        self.assertIn("10", self.window.frame_label.text())
        self.assertIn("ps", self.window.frame_label.text())
        self.assertFalse(self.window.export_button.isEnabled())
        self.window.pbc_check.setChecked(True)
        self.assertTrue(self.window.export_button.isEnabled())

    def test_changes_invalidate_previously_exported_frame(self):
        self.window._session = mock.Mock()
        self.window._export = FrameExport(Path("out"), Path("out/launch.json"), Path("out/provenance.json"), 0)
        self.window.launch_button.setEnabled(True)
        self.window.pbc_check.setChecked(True)
        self.window.ligand_edit.setText("resname DRG")
        self.assertIsNone(self.window._export)
        self.assertFalse(self.window.launch_button.isEnabled())
        self.assertFalse(self.window.pbc_check.isChecked())

    def test_busy_window_defers_close(self):
        worker = mock.Mock()
        worker.isRunning.return_value = True
        self.window._worker = worker
        event = QCloseEvent()
        self.window.closeEvent(event)
        self.assertFalse(event.isAccepted())
        self.window._worker = None

    def test_launch_error_is_visible_and_export_is_preserved(self):
        from core.trajectory_frames import TrajectoryError

        exported = FrameExport(Path("out"), Path("out/launch.json"), Path("out/provenance.json"), 0)
        self.window._export = exported
        with mock.patch("windows.trajectory_viewer.launch_docklens", side_effect=TrajectoryError("not executable")):
            self.window.open_docklens()
        self.assertIn("not executable", self.window.status.text())
        self.assertIs(self.window._export, exported)

    @unittest.skipUnless(importlib.util.find_spec("MDAnalysis"), "MDAnalysis is optional")
    def test_real_async_load_frame_change_and_export(self):
        from tests.test_trajectory_frames import TrajectoryFramesTests

        fixture = TrajectoryFramesTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.window.topology = str(fixture.topology)
        self.window.trajectory = str(fixture.trajectory)
        self.window.output.setText(str(fixture.root / "gui_exports"))
        self.window.load_trajectory()
        self._wait_for_idle()
        self.assertIsNotNone(self.window._session, self.window.status.text())
        self.window.frame_spin.setValue(1)
        self.window.show_button.click()
        self._wait_for_idle()
        self.assertIn("Frame 1", self.window.frame_label.text())
        self.window.pbc_check.setChecked(True)
        self.window.export_button.click()
        self._wait_for_idle()
        self.assertTrue(self.window._export.manifest_path.is_file())
        self.assertTrue(self.window.launch_button.isEnabled())
        self.window.close()

    def _wait_for_idle(self):
        timer = QElapsedTimer()
        timer.start()
        while self.window.is_busy() and timer.elapsed() < 15000:
            self.app.processEvents()
            QTest.qWait(5)
        self.assertFalse(self.window.is_busy(), self.window.status.text())


if __name__ == "__main__":
    unittest.main()
