"""Measured-point activation keeps numeric row and file identity."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from matplotlib.backend_bases import MouseEvent, PickEvent
from PyQt5.QtWidgets import QApplication

from analysis.execution import fingerprint
from tabs.qc_dashboard_tab import QCDashboardWidget


class QCAnalysisLinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.widget = QCDashboardWidget()
        self.addCleanup(self.widget.close)
        self.requests = []
        self.widget.analysis_point_requested.connect(lambda path, row: self.requests.append((path, row)))

    def result(self, name="rmsd", content="time_ns,rmsd_nm,frame\n0,0.1,0\n0.002,0.2,5\n0.004,0.3,10\n"):
        path = self.root / f"{name}.csv"
        path.write_text(content, encoding="utf-8")
        columns = content.splitlines()[0].split(",")
        manifest_path = self.root / "analysis_manifest.json"
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {
            "schema_version": 1, "status": "complete", "source_changed": False,
            "inputs": {}, "results": [],
        }
        manifest["results"].append({"name": "RMSF" if "rmsf" in name else "RMSD", "status": "complete",
                                    **fingerprint(path), "columns": columns,
                                    "rows": len(content.splitlines()) - 1, "metadata": {}})
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return path

    def test_measured_table_retains_frame_column_and_button_emits_exact_row(self):
        path = self.result()
        self.widget.load_files([str(path)])
        table = self.widget.points_table
        self.assertEqual(table.rowCount(), 3)
        self.assertEqual(table.horizontalHeaderItem(3).text(), "frame")
        self.assertEqual(table.item(1, 3).text(), "5")
        table.selectRow(1)
        self.widget.point_button.click()
        self.assertEqual(self.requests, [(str(path), 1)])
        self.assertEqual(table.currentRow(), 1)
        self.assertIsNotNone(self.widget._point_marker)

    def test_point_double_click_uses_current_file_and_row(self):
        path = self.result()
        self.widget.load_files([str(path)])
        self.widget.points_table.cellDoubleClicked.emit(2, 1)
        self.assertEqual(self.requests, [(str(path), 2)])

    def test_pick_uses_artist_and_numeric_row_for_duplicate_residues(self):
        first = self.result("rep1_rmsf", "residue_id,rmsf_nm,atom_index\n7,0.1,0\n7,0.2,1\n8,0.3,2\n")
        second = self.result("rep2_rmsf", "residue_id,rmsf_nm,atom_index\n7,0.4,3\n7,0.5,4\n8,0.6,5\n")
        self.widget.load_files([str(first), str(second)])
        artist = next(line for line, index in self.widget._plot_sources.items() if index == 1)
        self.widget._on_pick(SimpleNamespace(artist=artist, ind=[1]))
        self.assertEqual(self.requests, [(str(second), 1)])
        self.assertEqual(self.widget.points_series.currentIndex(), 1)
        self.assertEqual(self.widget.points_table.item(1, 3).text(), "4")

    def test_overlapping_pick_requires_exact_table_row(self):
        path = self.result("rmsf", "residue_id,rmsf_nm,atom_index\n7,0.1,0\n7,0.1,1\n8,0.3,2\n")
        self.widget.load_files([str(path)])
        artist = next(iter(self.widget._plot_sources))
        self.widget._on_pick(SimpleNamespace(artist=artist, ind=[0, 1]))
        self.assertEqual(self.requests, [])
        self.assertIn("overlap", self.widget.link_status.text().lower())
        self.widget.points_table.cellDoubleClicked.emit(1, 0)
        self.assertEqual(self.requests, [(str(path), 1)])

    def test_one_mouse_click_on_overlapping_replicas_requires_file_and_row(self):
        first = self.result("rep1_rmsd")
        second = self.result("rep2_rmsd")
        self.widget.load_files([str(first), str(second)])
        self.widget.canvas.draw()
        artists = tuple(self.widget._plot_sources)
        mouse = MouseEvent("button_press_event", self.widget.canvas,
                           *artists[0].axes.transData.transform((0.002, 0.2)), button=1)
        for artist in artists:
            self.widget._on_pick(PickEvent("pick_event", self.widget.canvas, mouse, artist, ind=[1]))
        self.assertEqual(self.requests, [])
        self.assertIn("source file", self.widget.link_status.text())
        self.widget.points_series.setCurrentIndex(1)
        self.widget.points_table.cellDoubleClicked.emit(1, 0)
        self.assertEqual(self.requests, [(str(second), 1)])

    def test_right_mouse_click_does_not_inspect_point(self):
        path = self.result()
        self.widget.load_files([str(path)])
        artist = next(iter(self.widget._plot_sources))
        self.widget.canvas.draw()
        mouse = MouseEvent("button_press_event", self.widget.canvas,
                           *artist.axes.transData.transform((0.002, 0.2)), button=3)
        self.widget._on_pick(PickEvent("pick_event", self.widget.canvas, mouse, artist, ind=[1]))
        self.assertEqual(self.requests, [])

    def test_unknown_artist_and_invalid_row_do_not_emit(self):
        path = self.result()
        self.widget.load_files([str(path)])
        self.widget._on_pick(SimpleNamespace(artist=object(), ind=[0]))
        artist = next(iter(self.widget._plot_sources))
        self.widget._on_pick(SimpleNamespace(artist=artist, ind=[99]))
        self.widget._on_pick(SimpleNamespace(artist=artist, ind=[]))
        self.assertEqual(self.requests, [])

    def test_plain_xvg_remains_readable_and_explains_unlinked_state(self):
        path = self.root / "rmsd.xvg"
        path.write_text('@ xaxis label "Time (ps)"\n0 0.1\n1000 0.2\n2000 0.3\n', encoding="utf-8")
        self.widget.load_files([str(path)])
        self.assertEqual(self.widget.points_table.rowCount(), 3)
        self.assertEqual(self.widget.points_table.item(1, 1).text(), "1")
        self.assertFalse(self.widget.point_button.isEnabled())
        self.assertIn("manifest", self.widget.link_status.text().lower())
        self.widget.points_table.cellDoubleClicked.emit(1, 0)
        self.assertEqual(self.requests, [])

    def test_summary_row_selects_exact_points_source(self):
        first = self.result("rep1_rmsd")
        second = self.result("rep2_rmsd")
        self.widget.load_files([str(first), str(second)])
        self.widget.table.setCurrentCell(1, 0)
        self.assertEqual(self.widget.points_series.currentIndex(), 1)
        self.widget.points_table.selectRow(0)
        self.widget.point_button.click()
        self.assertEqual(self.requests, [(str(second), 0)])

    def test_clear_removes_stale_points_and_pick_sources(self):
        path = self.result()
        self.widget.load_files([str(path)])
        self.widget.points_table.cellDoubleClicked.emit(1, 0)
        self.widget.clear()
        self.assertEqual(self.widget.points_table.rowCount(), 0)
        self.assertEqual(self.widget.points_series.count(), 0)
        self.assertEqual(self.widget._plot_sources, {})
        self.assertIsNone(self.widget._point_marker)
        self.assertFalse(self.widget.point_button.isEnabled())

    def test_reimport_replaces_table_and_invalidates_old_link(self):
        path = self.result()
        self.widget.load_files([str(path)])
        self.widget.points_table.cellDoubleClicked.emit(1, 0)
        path.write_text("time_ns,rmsd_nm,frame\n0,1,0\n1,2,1\n", encoding="utf-8")
        self.widget.load_files([str(path)])
        self.assertEqual(self.widget.points_table.rowCount(), 2)
        self.assertFalse(self.widget.point_button.isEnabled())
        self.assertIsNone(self.widget._point_marker)

    def test_changed_frame_mapping_with_same_plot_values_requires_reimport(self):
        path = self.result()
        self.widget.load_files([str(path)])
        path.write_text(path.read_text().replace("0.002,0.2,5", "0.002,0.2,8"), encoding="utf-8")
        manifest_path = self.root / "analysis_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["results"][0] = {**manifest["results"][0], **fingerprint(path)}
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.widget.points_table.cellDoubleClicked.emit(1, 0)
        self.assertEqual(self.requests, [])
        self.assertFalse(self.widget.point_button.isEnabled())
        self.assertIn("mapping changed", self.widget.link_status.text())
        self.widget.load_files([str(path)])
        self.widget.points_table.cellDoubleClicked.emit(1, 0)
        self.assertEqual(self.requests, [(str(path), 1)])
        self.assertEqual(self.widget.points_table.item(1, 3).text(), "8")

    def test_changed_plotted_values_with_valid_new_manifest_requires_reimport(self):
        path = self.result()
        self.widget.load_files([str(path)])
        path.write_text(path.read_text().replace("0.002,0.2,5", "0.002,0.9,5"), encoding="utf-8")
        manifest_path = self.root / "analysis_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["results"][0] = {**manifest["results"][0], **fingerprint(path)}
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.widget.points_table.cellDoubleClicked.emit(1, 0)
        self.assertEqual(self.requests, [])
        self.assertIn("changed since plotting", self.widget.link_status.text())

    def test_switching_replica_does_not_adopt_changed_frame_mapping(self):
        first = self.result("rep1_rmsd")
        second = self.result("rep2_rmsd")
        self.widget.load_files([str(first), str(second)])
        self.widget.points_series.setCurrentIndex(1)
        first.write_text(first.read_text().replace("0.002,0.2,5", "0.002,0.2,8"), encoding="utf-8")
        manifest_path = self.root / "analysis_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["results"][0] = {**manifest["results"][0], **fingerprint(first)}
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.widget.recalculate()
        artist = next(line for line, index in self.widget._plot_sources.items() if index == 0)
        self.widget._on_pick(SimpleNamespace(artist=artist, ind=[1]))
        self.assertEqual(self.requests, [])
        self.assertFalse(self.widget.point_button.isEnabled())
        self.assertIn("mapping changed", self.widget.link_status.text())
        self.widget.load_files([str(first)])
        self.widget.points_table.cellDoubleClicked.emit(1, 0)
        self.assertEqual(self.requests, [(str(first), 1)])


if __name__ == "__main__":
    unittest.main()
