"""Embedded viewer handoffs, session isolation, and project file ownership."""
from __future__ import annotations

import os
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtGui import QCloseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

import gromacs_analysis_studio_v11 as studio
from core.project_manager import ProjectFormatError
from tests import test_trajectory_frames as frame_tests


class ProjectViewerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.fixture = frame_tests.TrajectoryFramesTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window = studio.MainWindow()
        self.window._autosave_timer.stop()
        self.addCleanup(self.window.close)

    def wait_for_viewer(self):
        viewer = self.window.viewer_dock.viewer
        deadline = time.monotonic() + 10
        while viewer.is_busy() and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(5)
        self.assertFalse(viewer.is_busy(), viewer.status.text())
        self.app.processEvents()
        return viewer

    def open_trajectory(self):
        self.window.open_project_viewer(str(self.fixture.topology), str(self.fixture.trajectory))
        return self.wait_for_viewer()

    def test_setup_inspection_uses_embedded_single_frame_and_keeps_navigation(self):
        before = self.window.nav.currentRow()
        setup = self.window.pages["MD Setup"]
        setup.fields["protein"].setText(str(self.fixture.topology))
        setup.inspect_3d_button.click()
        viewer = self.wait_for_viewer()
        self.assertIsNotNone(viewer.parent())
        self.assertFalse(viewer.isWindow())
        self.assertTrue(viewer._session.structure_only)
        self.assertEqual(viewer.frame_spin.maximum(), 0)
        self.assertEqual(self.window.nav.currentRow(), before)
        self.assertFalse(viewer.export_button.isEnabled())

    def test_analysis_opens_shared_dock_and_never_spawns_standalone_window(self):
        analysis = self.window.pages["Analysis"]
        analysis.set_input_files(str(self.fixture.topology), str(self.fixture.trajectory))
        with mock.patch("tabs.analysis_tab.TrajectoryViewerWindow") as standalone:
            analysis.open_trajectory_viewer()
        standalone.assert_not_called()
        viewer = self.wait_for_viewer()
        self.assertEqual(viewer.topology, str(self.fixture.topology))
        self.assertEqual(viewer.trajectory, str(self.fixture.trajectory))
        self.assertFalse(viewer._session.structure_only)

    def test_saved_selection_and_frame_restore_without_old_reader_or_export(self):
        viewer = self.open_trajectory()
        viewer.frame_spin.setValue(1)
        viewer.show_frame()
        self.wait_for_viewer()
        viewer.pbc_check.setChecked(True)
        before_page = self.window.nav.currentRow()
        saved = self.window.session_data()
        self.assertEqual(saved["viewer_state"]["frame_index"], 1)
        self.window.apply_session_data(saved)
        dock = self.window.viewer_dock
        self.assertIsNone(dock.viewer._session)
        self.assertFalse(dock.viewer.pbc_check.isChecked())
        self.assertFalse(dock.viewer.launch_button.isEnabled())
        self.assertEqual(self.window.nav.currentRow(), before_page)
        dock.load_sources()
        self.wait_for_viewer()
        self.assertEqual(dock.viewer.frame_spin.value(), 1)

    def test_new_or_legacy_session_clears_previous_3d_context(self):
        self.open_trajectory()
        self.window.apply_session_data({"pages": {}, "current_page": 4})
        self.assertIsNone(self.window.viewer_dock.viewer._session)
        self.assertEqual(self.window.viewer_dock.topology.text(), "")
        self.assertEqual(self.window.nav.currentRow(), 4)

    def test_reader_is_released_before_preparation_and_run_writes(self):
        viewer = self.open_trajectory()
        self.assertTrue(self.window.pages["MD Setup"].before_preparation())
        self.assertIsNone(viewer._session)
        self.assertEqual(self.window.viewer_dock.topology.text(), str(self.fixture.topology))
        self.window.viewer_dock.load_sources()
        self.wait_for_viewer()
        self.assertTrue(self.window.pages["MD Run"].before_run())
        self.assertIsNone(viewer._session)

    def test_busy_viewer_blocks_session_replacement_and_main_window_close(self):
        viewer = self.open_trajectory()
        viewer._worker = mock.Mock()
        self.addCleanup(setattr, viewer, "_worker", None)
        self.window.new_session()
        self.assertEqual(self.window.viewer_dock.topology.text(), str(self.fixture.topology))
        event = QCloseEvent()
        self.window.closeEvent(event)
        self.assertFalse(event.isAccepted())

    def test_invalid_saved_viewer_state_rejects_before_mutating_pages(self):
        old_duration = self.window.pages["MD Setup"].fields["duration"].value()
        with self.assertRaises(ProjectFormatError):
            self.window.apply_session_data({"pages": {"MD Setup": {"duration": 12.0}},
                                           "viewer_state": {"frame_index": True}})
        self.assertEqual(self.window.pages["MD Setup"].fields["duration"].value(), old_duration)

    def test_production_offers_sources_without_starting_reader_in_handoff(self):
        self.window.open_project_viewer(str(self.fixture.topology), "")
        viewer = self.wait_for_viewer()
        viewer.clear_sources()
        inputs = {"topology": str(self.fixture.topology), "trajectory": str(self.fixture.trajectory)}
        self.window._on_production_completed(inputs)
        self.assertEqual(self.window.viewer_dock.trajectory.text(), str(self.fixture.trajectory))
        self.assertIsNone(viewer._session)
        self.assertFalse(viewer.is_busy())

    def test_new_analysis_inputs_clear_the_previous_scene_and_offer_exact_sources(self):
        viewer = self.open_trajectory()
        inputs = {"topology": str(self.fixture.topology), "trajectory": ""}
        self.assertTrue(self.window._on_analysis_inputs(inputs))
        self.assertIsNone(viewer._session)
        self.assertEqual(self.window.viewer_dock.trajectory.text(), "")
        self.assertFalse(viewer.is_busy())

    def test_analysis_reopen_preserves_restored_frame_and_selection(self):
        viewer = self.open_trajectory()
        viewer.receptor_edit.setText("protein and name N")
        viewer.frame_spin.setValue(1)
        viewer.show_frame()
        self.wait_for_viewer()
        saved = self.window.session_data()
        self.window.apply_session_data(saved)
        self.window.pages["Analysis"].set_input_files(str(self.fixture.topology), str(self.fixture.trajectory))
        self.window.pages["Analysis"].open_trajectory_viewer()
        self.wait_for_viewer()
        self.assertEqual(viewer.frame_spin.value(), 1)
        self.assertEqual(viewer.receptor_edit.text(), "protein and name N")


if __name__ == "__main__":
    unittest.main()
