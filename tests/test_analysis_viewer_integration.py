"""Measured rows reach the shared viewer without changing export selections."""
from __future__ import annotations

import csv
import json
import os
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

import gromacs_analysis_studio_v11 as studio
from analysis.execution import fingerprint
from tests import test_trajectory_frames as frame_tests


class AnalysisViewerIntegrationTests(unittest.TestCase):
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
        self.dashboard = self.window.pages["Results Viewer"].qc_dashboard

    def result(self, name="RMSD"):
        path = self.fixture.root / (name.lower() + ".csv")
        metadata = {}
        if name == "RMSF":
            columns = ["residue_id", "rmsf_nm", "atom_index"]
            rows = [(1, 0.1, 0), (1, 0.2, 1)]
            metadata = {"atom_indices_zero_based": [0, 1], "residue_ids": [1, 1],
                        "residue_indices_zero_based": [0, 0], "segment_indices_zero_based": [0, 0],
                        "atom_names": ["N", "CA"]}
        else:
            columns = ["time_ns", "rmsd_nm", "frame"]
            rows = [(self.fixture.session.read_frame(i).time_ps / 1000, i * 0.1, i) for i in range(2)]
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(columns)
            writer.writerows(rows)
        manifest = {"schema_version": 1, "status": "complete", "source_changed": False,
                    "inputs": {"topology": fingerprint(self.fixture.topology),
                               "trajectory": fingerprint(self.fixture.trajectory)},
                    "results": [{"name": name, "status": "complete", **fingerprint(path),
                                 "columns": columns, "rows": len(rows), "metadata": metadata}]}
        (path.parent / "analysis_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return path

    def wait_idle(self):
        viewer = self.window.viewer_dock.viewer
        deadline = time.monotonic() + 15
        while viewer.is_busy() and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(5)
        self.app.processEvents()
        self.assertFalse(viewer.is_busy(), viewer.status.text())
        return viewer

    def activate(self, source, row):
        self.dashboard.analysis_point_requested.emit(str(source), row)
        return self.wait_idle()

    def test_dashboard_row_opens_manifest_sources_and_exact_frame(self):
        source = self.result()
        viewer = self.activate(source, 1)
        self.assertEqual(viewer.frame_spin.value(), 1)
        self.assertEqual(viewer._snapshot.time_ps, self.fixture.session.read_frame(1).time_ps)
        self.assertEqual(self.window.viewer_dock.topology.text(), str(self.fixture.topology.resolve()))
        self.assertEqual(self.window.viewer_dock.trajectory.text(), str(self.fixture.trajectory.resolve()))
        self.assertIn("rmsd.csv", viewer.status.text())
        self.assertFalse(viewer.pbc_check.isChecked())

    def test_rmsf_overlay_preserves_selection_camera_and_export_coordinates(self):
        self.window.open_project_viewer(str(self.fixture.topology), str(self.fixture.trajectory))
        viewer = self.wait_idle()
        viewer.receptor_edit.setText("protein and name CA")
        viewer.show_frame()
        self.wait_idle()
        viewer.axes.view_init(elev=12, azim=34)
        viewer = self.activate(self.result("RMSF"), 0)
        self.assertEqual(viewer.receptor_edit.text(), "protein and name CA")
        self.assertEqual(viewer.ligand_edit.text(), "resname LIG")
        self.assertEqual(len(viewer._snapshot.highlight_coordinates), 2)
        self.assertIn("RMSF", viewer._snapshot.highlight_label)
        self.assertEqual((viewer.axes.elev, viewer.axes.azim), (12, 34))
        self.assertIn("Analysis residue", viewer.axes.get_legend_handles_labels()[1])
        self.assertFalse(viewer.axes.computed_zorder)
        self.assertEqual(viewer.axes.collections[-1].zorder, 10)
        viewer.output.setText(str(self.fixture.root / "linked_export"))
        viewer.pbc_check.setChecked(True)
        viewer.export_frame()
        self.wait_idle()
        self.assertIsNotNone(viewer._export, viewer.status.text())
        self.assertEqual(viewer._snapshot.receptor_count, 1)
        viewer.frame_spin.setValue(1)
        self.wait_idle()
        self.assertEqual(viewer._snapshot.highlight_coordinates, ())
        self.assertIsNone(viewer._export)

    def test_unlinked_or_modified_result_keeps_sources_and_invalidates_export(self):
        source = self.result()
        viewer = self.activate(source, 0)
        old_session = viewer._session
        old_sources = (viewer.topology, viewer.trajectory)
        source.write_text(source.read_text() + "1,2,3\n")
        viewer = self.activate(source, 1)
        self.assertIs(viewer._session, old_session)
        self.assertEqual((viewer.topology, viewer.trajectory), old_sources)
        self.assertIn("failed", viewer.status.text())
        self.assertFalse(viewer.export_button.isEnabled())
        self.assertIsNone(viewer._display_selection)

    def test_existing_project_operation_blocks_analysis_navigation(self):
        with mock.patch.object(self.window, "_work_in_progress", return_value=True):
            self.dashboard.analysis_point_requested.emit(str(self.result()), 0)
        self.assertIsNone(self.window.viewer_dock)
        self.assertIn("Point not opened", self.dashboard.link_status.text())

    def test_active_frame_worker_rejects_second_point_with_visible_message(self):
        source = self.result()
        viewer = self.activate(source, 0)
        viewer._worker = mock.Mock()
        try:
            with mock.patch.object(viewer, "inspect_analysis_point") as inspect:
                self.window.open_analysis_point(str(source), 1)
            inspect.assert_not_called()
            self.assertIn("Point not opened", self.dashboard.link_status.text())
            self.assertEqual(viewer.frame_spin.value(), 0)
        finally:
            viewer._worker = None

    def test_new_session_clears_analysis_points_from_previous_project(self):
        self.dashboard.load_files([str(self.result())])
        self.window.new_session()
        self.assertEqual(self.dashboard.series, [])
        self.assertEqual(self.dashboard.points_table.rowCount(), 0)


if __name__ == "__main__":
    unittest.main()
