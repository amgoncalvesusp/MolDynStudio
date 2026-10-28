"""Embedded/offline viewer lifecycle, navigation and preserved exports."""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QElapsedTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QWidget

from core.trajectory_frames import FrameExport, FrameSnapshot
from windows.trajectory_viewer import TrajectoryViewerWindow


class IntegratedViewerWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.parent = QWidget()
        self.viewer = TrajectoryViewerWindow("", "", self.parent, embedded=True, autoload=False)
        self.addCleanup(self.parent.close)
        self.addCleanup(self.viewer.close)

    def wait_idle(self):
        timer = QElapsedTimer()
        timer.start()
        while self.viewer.is_busy() and timer.elapsed() < 15000:
            self.app.processEvents()
            QTest.qWait(10)
        self.assertFalse(self.viewer.is_busy(), self.viewer.status.text())

    def fixture(self):
        from tests.test_trajectory_frames import TrajectoryFramesTests
        fixture = TrajectoryFramesTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_embedded_mode_is_child_and_does_not_start_unsolicited_load(self):
        self.assertFalse(self.viewer.isWindow())
        self.assertFalse(self.viewer.is_busy())
        self.assertIn("Choose", self.viewer.status.text())

    def test_source_change_closes_reader_and_discards_frame_export(self):
        session = mock.Mock()
        self.viewer._session = session
        self.viewer._export = FrameExport(Path("old"), Path("old/launch.json"), Path("old/provenance.json"), 2)
        self.assertTrue(self.viewer.set_sources("new.pdb", "", autoload=False))
        session.close.assert_called_once()
        self.assertIsNone(self.viewer._session)
        self.assertIsNone(self.viewer._export)
        self.assertEqual(self.viewer.receptor_edit.text(), "all")
        self.assertEqual(self.viewer.ligand_edit.text(), "")
        self.assertFalse(self.viewer.export_button.isEnabled())
        self.assertIn("Structure", self.viewer.source_label.text())

    def test_busy_source_change_is_rejected_without_losing_context(self):
        self.viewer.topology = "old.pdb"
        self.viewer._worker = mock.Mock()
        self.assertFalse(self.viewer.set_sources("new.pdb", autoload=False))
        self.assertEqual(self.viewer.topology, "old.pdb")
        self.viewer._worker = None

    def test_external_project_operation_blocks_load_show_and_export(self):
        self.viewer.operation_blocked = lambda: True
        self.viewer._session = mock.Mock()
        self.viewer.pbc_check.setChecked(True)
        with mock.patch.object(self.viewer, "_start") as start:
            self.viewer.load_trajectory()
            self.viewer.show_frame()
            self.viewer.export_frame()
            self.viewer.toggle_playback()
        start.assert_not_called()
        self.assertIn("operation", self.viewer.status.text())

    def test_static_structure_loads_without_trajectory_and_export_stays_disabled(self):
        fixture = self.fixture()
        self.assertTrue(self.viewer.set_sources(str(fixture.topology)))
        self.wait_idle()
        self.assertIsNotNone(self.viewer._session)
        self.assertIn("Structure", self.viewer.frame_label.text())
        self.assertEqual(self.viewer.frame_spin.maximum(), 0)
        self.viewer.pbc_check.setChecked(True)
        self.assertFalse(self.viewer.export_button.isEnabled())
        self.assertFalse(self.viewer.play_button.isEnabled())

    def test_frame_slider_and_next_previous_follow_reader_times(self):
        fixture = self.fixture()
        self.viewer.set_sources(str(fixture.topology), str(fixture.trajectory))
        self.wait_idle()
        self.viewer.next_button.click()
        self.wait_idle()
        self.assertEqual(self.viewer.frame_spin.value(), 1)
        self.assertEqual(self.viewer.frame_slider.value(), 1)
        expected_time = fixture.session.read_frame(1).time_ps
        self.assertIn(f"{expected_time:g} ps", self.viewer.frame_label.text())
        self.viewer.previous_button.click()
        self.wait_idle()
        self.assertEqual(self.viewer.frame_spin.value(), 0)

    def test_playback_stops_at_last_frame_and_releases_busy_state(self):
        fixture = self.fixture()
        self.viewer.set_sources(str(fixture.topology), str(fixture.trajectory))
        self.wait_idle()
        self.viewer.play_button.click()
        self.wait_idle()
        self.assertEqual(self.viewer.frame_spin.value(), self.viewer.frame_spin.maximum())
        self.assertEqual(self.viewer.play_button.text(), "Play")

    def test_camera_is_preserved_between_frames_and_reset_is_explicit(self):
        first = FrameSnapshot(0, 2, 0., 1, 0, ((0., 0., 0.),), (), False)
        second = FrameSnapshot(1, 2, 5., 1, 0, ((2., 0., 0.),), (), False)
        self.viewer._show_snapshot(first)
        self.viewer.axes.view_init(elev=12, azim=34)
        self.viewer.axes.set_xlim3d(-5, 5)
        self.viewer._show_snapshot(second)
        self.assertEqual(self.viewer.axes.elev, 12)
        self.assertEqual(self.viewer.axes.azim, 34)
        self.assertEqual(self.viewer.axes.get_xlim3d(), (-5., 5.))
        self.viewer.reset_view()
        self.assertNotEqual(self.viewer.axes.get_xlim3d(), (-5., 5.))

    def test_view_state_restores_selection_and_requested_frame_without_consent(self):
        fixture = self.fixture()
        state = {"topology": str(fixture.topology), "trajectory": str(fixture.trajectory),
                 "receptor_selection": "protein and resid 1", "ligand_selection": "resname LIG",
                 "frame_index": 1, "pbc_reviewed": True}
        self.assertTrue(self.viewer.set_view_state(state))
        self.assertFalse(self.viewer.is_busy())
        self.assertFalse(self.viewer.pbc_check.isChecked())
        self.assertEqual(self.viewer.get_view_state()["frame_index"], 1)
        self.viewer.load_trajectory()
        self.wait_idle()
        self.assertEqual(self.viewer.frame_spin.value(), 1)

    def test_view_state_preserves_explicitly_empty_receptor_draft(self):
        self.viewer.set_view_state({"topology": "source.pdb", "trajectory": "frames.dcd",
                                    "receptor_selection": "", "ligand_selection": "resname LIG"})
        self.assertEqual(self.viewer.receptor_edit.text(), "")
        self.assertEqual(self.viewer.get_view_state()["receptor_selection"], "")

    def test_invalid_saved_state_does_not_change_existing_sources(self):
        self.viewer.set_sources("kept.pdb", autoload=False)
        for invalid in ({"frame_index": True}, {"frame_index": -1}, {"topology": ["wrong"]}, []):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.viewer.set_view_state(invalid)
            self.assertEqual(self.viewer.topology, "kept.pdb")

    def test_known_bonds_are_drawn_and_frame_export_remains_invalid_after_navigation(self):
        first = FrameSnapshot(0, 2, 0., 2, 0, ((0., 0., 0.), (1., 0., 0.)), (), False,
                              receptor_bonds=((0, 1),))
        self.viewer._show_snapshot(first)
        self.assertTrue(any(type(item).__name__ == "Line3DCollection" for item in self.viewer.axes.collections))
        self.viewer._export = FrameExport(Path("out"), Path("out/m.json"), Path("out/p.json"), 0)
        self.viewer.frame_spin.setValue(1)
        self.assertIsNone(self.viewer._export)

    def test_failed_navigation_rolls_back_to_displayed_frame_before_export(self):
        fixture = self.fixture()
        self.viewer.set_sources(str(fixture.topology), str(fixture.trajectory))
        self.wait_idle()
        self.viewer.receptor_edit.setText("invalid selection")
        self.viewer.next_button.click()
        self.wait_idle()
        self.assertEqual(self.viewer.frame_spin.value(), self.viewer._snapshot.frame_index)
        self.assertEqual(self.viewer.frame_slider.value(), self.viewer._snapshot.frame_index)
        self.viewer.receptor_edit.setText("protein")
        self.assertFalse(self.viewer.export_button.isEnabled())
        self.viewer.show_button.click()
        self.wait_idle()
        self.viewer.pbc_check.setChecked(True)
        self.viewer.output.setText(str(fixture.root / "failed_frame_exports"))
        self.viewer.export_button.click()
        self.wait_idle()
        self.assertEqual(self.viewer._export.frame_index, self.viewer._snapshot.frame_index)

    def test_changed_selection_cannot_export_until_displayed_selection_is_applied(self):
        fixture = self.fixture()
        self.viewer.set_sources(str(fixture.topology), str(fixture.trajectory))
        self.wait_idle()
        self.viewer.receptor_edit.setText("protein and name N")
        self.viewer.pbc_check.setChecked(True)
        self.assertFalse(self.viewer.export_button.isEnabled())
        with mock.patch.object(self.viewer, "_start") as start:
            self.viewer.export_frame()
        start.assert_not_called()
        self.viewer.show_button.click()
        self.wait_idle()
        self.viewer.pbc_check.setChecked(True)
        self.assertTrue(self.viewer.export_button.isEnabled())

    def test_failed_read_of_changed_source_invalidates_export_even_at_same_frame(self):
        fixture = self.fixture()
        self.viewer.set_sources(str(fixture.topology), str(fixture.trajectory))
        self.wait_idle()
        self.viewer.output.setText(str(fixture.root / "changed_source_exports"))
        self.viewer.pbc_check.setChecked(True)
        self.viewer.export_button.click()
        self.wait_idle()
        self.assertIsNotNone(self.viewer._export)
        fixture.topology.write_text(fixture.topology.read_text(encoding="ascii") + "REMARK changed\n", encoding="ascii")
        self.viewer.show_button.click()
        self.wait_idle()
        self.assertIsNone(self.viewer._export)
        self.assertFalse(self.viewer.launch_button.isEnabled())
        self.assertFalse(self.viewer.pbc_check.isChecked())
        self.viewer.pbc_check.setChecked(True)
        self.assertFalse(self.viewer.export_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
