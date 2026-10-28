"""Analysis navigation checks source identity and preserves export selections."""
from __future__ import annotations

import csv
import json
import unittest
from dataclasses import replace
from unittest import mock

from analysis.execution import fingerprint
from analysis.trajectory import atom_metadata
from core.trajectory_frames import TrajectoryError
from tests import test_trajectory_frames as frame_tests


class AnalysisFrameLinksTests(unittest.TestCase):
    def setUp(self):
        self.fixture = frame_tests.TrajectoryFramesTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.session = self.fixture.session
        self.source = self.fixture.root / "rmsd.csv"
        self.manifest = {
            "schema_version": 1, "status": "complete", "source_changed": False,
            "inputs": {key: fingerprint(path) for key, path in (
                ("topology", self.fixture.topology), ("trajectory", self.fixture.trajectory))},
            "results": [],
        }

    def write_result(self, columns, rows, *, name="RMSD", metadata=None):
        with self.source.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(columns)
            writer.writerows(rows)
        self.manifest["results"] = [{"name": name, "status": "complete", **fingerprint(self.source),
            "columns": list(columns), "rows": len(rows), "metadata": metadata or {}}]
        self.save_manifest()

    def save_manifest(self):
        (self.fixture.root / "analysis_manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def write_time_result(self):
        rows = []
        for index in (0, 1):
            frame = self.session.read_frame(index)
            rows.append((frame.time_ps / 1000, 0.1 + index / 10, index))
        self.write_result(("time_ns", "rmsd_nm", "frame"), rows)

    def test_exact_frame_and_time_preserve_selection_and_coordinates(self):
        self.write_time_result()
        result = self.session.read_analysis_point(str(self.source), 0, "name N", "resname LIG")
        self.assertEqual(result.frame_index, 0)
        self.assertEqual(result.receptor_count, 1)
        self.assertAlmostEqual(result.receptor_coordinates[0][0], 0)
        result = self.session.read_analysis_point(str(self.source), 1)
        self.assertEqual(result.frame_index, 1)
        self.assertAlmostEqual(result.receptor_coordinates[0][0], 10)
        self.assertEqual(result.highlight_coordinates, ())
        self.assertIn("rmsd.csv", result.highlight_label)
        self.assertIn("numeric row 1 (0-based)", result.highlight_label)

    def test_time_is_verified_not_inferred_from_index_or_spacing(self):
        self.write_result(("time_ns", "rmsd_nm", "frame"), [(0.010, 0.1, 0), (0.023, 0.2, 1)])
        original = self.session.read_frame
        with mock.patch.object(self.session, "read_frame", side_effect=lambda i, *s: replace(original(i, *s), time_ps=(10, 23)[i])):
            self.assertEqual(self.session.read_analysis_point(str(self.source), 1).frame_index, 1)
        with self.assertRaisesRegex(TrajectoryError, "time"):
            self.session.read_analysis_point(str(self.source), 1)

    def test_time_mismatch_keeps_previous_frame_for_next_residue_inspection(self):
        self.session.read_frame(0)
        self.write_result(("time_ns", "rmsd_nm", "frame"), [(0.010, .1, 1)])
        with self.assertRaisesRegex(TrajectoryError, "time"):
            self.session.read_analysis_point(str(self.source), 0)
        self.assertEqual(self.session.universe.trajectory.frame, 0)
        atoms = self.session.universe.atoms[:2]
        self.write_result(("residue_id", "rmsf_nm", "atom_index"), [(1, .1, 0), (1, .2, 1)],
                          name="RMSF", metadata=atom_metadata(atoms))
        self.assertEqual(self.session.read_analysis_point(str(self.source), 0).frame_index, 0)

    def test_selection_failure_during_temporal_navigation_restores_previous_cursor(self):
        self.write_time_result()
        self.session.read_frame(0)
        with self.assertRaisesRegex(TrajectoryError, "selection"):
            self.session.read_analysis_point(str(self.source), 1, "not_a_selection", "resname LIG")
        self.assertEqual(self.session.universe.trajectory.frame, 0)

    def test_ordinary_frame_selection_failure_does_not_move_next_rmsf_highlight(self):
        self.session.read_frame(0)
        for receptor, ligand in (("not_a_selection", "resname LIG"), ("protein", "protein")):
            with self.subTest(receptor=receptor, ligand=ligand), self.assertRaises(TrajectoryError):
                self.session.read_frame(1, receptor, ligand)
            self.assertEqual(self.session.universe.trajectory.frame, 0)
        atoms = self.session.universe.atoms[:2]
        self.write_result(("residue_id", "rmsf_nm", "atom_index"), [(1, .1, 0), (1, .2, 1)],
                          name="RMSF", metadata=atom_metadata(atoms))
        self.assertEqual(self.session.read_analysis_point(str(self.source), 0).frame_index, 0)

    def test_ordinary_source_change_fails_without_attempting_cursor_restore(self):
        self.session.read_frame(0)
        self.fixture.topology.write_text(self.fixture.topology.read_text() + "REMARK changed\n")
        reader_type = type(self.session.universe.trajectory)
        with (mock.patch.object(reader_type, "__getitem__") as read,
              self.assertRaisesRegex(TrajectoryError, "changed")):
            self.session.read_frame(1)
        read.assert_not_called()

    def test_restore_failure_requires_explicit_reload_and_keeps_both_errors(self):
        self.write_time_result()
        original_frame = self.session.read_frame

        def failed_frame(index, *selections):
            original_frame(index, *selections)
            return replace(original_frame(index, *selections), time_ps=-999)

        reader_type = type(self.session.universe.trajectory)
        original_getitem = reader_type.__getitem__

        def failed_restore(reader, index):
            if index == 0:
                raise OSError("reader closed")
            return original_getitem(reader, index)

        self.session.read_frame(0)
        with (mock.patch.object(self.session, "read_frame", side_effect=failed_frame),
              mock.patch.object(reader_type, "__getitem__", failed_restore),
              self.assertRaisesRegex(TrajectoryError, "time.*reader closed.*Reload")):
            self.session.read_analysis_point(str(self.source), 1)

    def test_invalid_row_frame_and_nonintegral_indices_are_rejected(self):
        self.write_time_result()
        for row in (-1, 2, True, 0.1, "0", None):
            with self.subTest(row=row), self.assertRaises(TrajectoryError):
                self.session.read_analysis_point(str(self.source), row)
        for index in (-1, 2, 0.5):
            self.write_result(("time_ns", "rmsd_nm", "frame"), [(0, 0.1, index)])
            with self.subTest(index=index), self.assertRaises(TrajectoryError):
                self.session.read_analysis_point(str(self.source), 0)

    def test_wrong_source_or_hash_rejected_before_navigation(self):
        self.write_time_result()
        for key, field, value in (("topology", "sha256", "0" * 64),
                ("trajectory", "size_bytes", 0), ("topology", "path", str(self.source))):
            original = self.manifest["inputs"][key][field]
            self.manifest["inputs"][key][field] = value
            self.save_manifest()
            with self.subTest(field=field), self.assertRaises(TrajectoryError):
                self.session.read_analysis_point(str(self.source), 1)
            self.manifest["inputs"][key][field] = original
        self.save_manifest()
        self.session.read_analysis_point(str(self.source), 0)
        self.fixture.topology.write_text(self.fixture.topology.read_text() + "REMARK changed\n")
        with self.assertRaisesRegex(TrajectoryError, "changed"):
            self.session.read_analysis_point(str(self.source), 1)

    def test_rmsf_highlights_true_resindex_with_repeated_resids(self):
        import MDAnalysis as mda

        original = self.session.universe
        universe = mda.Merge(original.select_atoms("protein"), original.select_atoms("protein"), original.select_atoms("resname LIG"))
        self.session.close()
        self.session.universe = universe
        universe.atoms.positions[2:4] += 20
        atoms = universe.atoms[[2, 3]]
        self.write_result(("residue_id", "rmsf_nm", "atom_index"), [(1, .1, 2), (1, .2, 3)],
                          name="RMSF", metadata=atom_metadata(atoms))
        result = self.session.read_analysis_point(str(self.source), 0)
        expected = tuple(tuple(map(float, xyz)) for xyz in universe.residues[1].atoms.positions)
        self.assertEqual(result.highlight_coordinates, expected)
        self.assertEqual(len(result.highlight_coordinates), 2)
        self.assertIn("resindex 1", result.highlight_label)
        self.assertIn("segment 1", result.highlight_label)
        self.assertEqual(result.receptor_count, 4)
        self.assertEqual(result.ligand_count, 2)
        self.assertEqual(self.session.read_frame(0).highlight_coordinates, ())

    def test_rmsf_mapping_must_match_actual_atom_identity(self):
        atoms = self.session.universe.atoms[:2]
        for field, value in (("atom_indices_zero_based", [1, 0]), ("residue_ids", [9, 1]),
                ("residue_indices_zero_based", [1, 0]), ("segment_indices_zero_based", [1, 0]),
                ("atom_names", ["BAD", "CA"])):
            metadata = {**atom_metadata(atoms), field: value}
            self.write_result(("residue_id", "rmsf_nm", "atom_index"), [(1, .1, 0), (1, .2, 1)],
                              name="RMSF", metadata=metadata)
            with self.subTest(field=field), self.assertRaises(TrajectoryError):
                self.session.read_analysis_point(str(self.source), 0)

    def test_export_after_rmsf_keeps_original_receptor_ligand_and_frame(self):
        atoms = self.session.universe.atoms[:2]
        self.write_result(("residue_id", "rmsf_nm", "atom_index"), [(1, .1, 0), (1, .2, 1)],
                          name="RMSF", metadata=atom_metadata(atoms))
        self.session.read_frame(1)
        result = self.session.read_analysis_point(str(self.source), 0)
        self.assertEqual(result.frame_index, 1)
        exported = self.fixture.export()
        provenance = json.loads(exported.provenance_path.read_text())
        self.assertEqual(provenance["frame_index"], 1)
        self.assertEqual(len(provenance["receptor_atom_map"]), 2)
        self.assertEqual(len(provenance["ligand_atom_map"]), 2)

    def test_rmsf_invalid_indices_and_incomplete_identity_fail_readably(self):
        atoms = self.session.universe.atoms[:2]
        for index, metadata in ((-1, atom_metadata(atoms)), (.5, atom_metadata(atoms)),
                                 (9, atom_metadata(atoms)), (0, {}), (0, {"atom_indices_zero_based": True})):
            self.write_result(("residue_id", "rmsf_nm", "atom_index"), [(1, .1, index), (1, .2, 1)],
                              name="RMSF", metadata=metadata)
            with self.subTest(index=index, metadata=metadata), self.assertRaises(TrajectoryError):
                self.session.read_analysis_point(str(self.source), 0)

    def test_rmsf_residue_column_is_checked_separately(self):
        atoms = self.session.universe.atoms[:2]
        self.write_result(("residue_id", "rmsf_nm", "atom_index"), [(9, .1, 0), (1, .2, 1)],
                          name="RMSF", metadata=atom_metadata(atoms))
        with self.assertRaisesRegex(TrajectoryError, "residue ID"):
            self.session.read_analysis_point(str(self.source), 0)

    def test_missing_input_fingerprints_and_structure_only_are_rejected(self):
        self.write_time_result()
        for inputs in (None, [], {}):
            self.manifest["inputs"] = inputs
            self.save_manifest()
            with self.subTest(inputs=inputs), self.assertRaises(TrajectoryError):
                self.session.read_analysis_point(str(self.source), 0)
        self.session.structure_only = True
        with self.assertRaisesRegex(TrajectoryError, "trajectory"):
            self.session.read_analysis_point(str(self.source), 0)


if __name__ == "__main__":
    unittest.main()
