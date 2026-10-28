"""Static structure inspection uses the existing reader without MD claims."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from core.trajectory_frames import TrajectoryError, TrajectorySession
from tests.test_trajectory_frames import pdb_atom


class StructureFrameTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.structure = self.root / "estrutura de inspeção.pdb"
        self.structure.write_text(
            pdb_atom(1, "N", "GLY", "A", 12, 0, "N")
            + pdb_atom(2, "CA", "GLY", "A", 12, 1.4, "C")
            + pdb_atom(3, "C1", "LIG", "B", 42, 3, "C", "HETATM")
            + "CONECT    1    2\nEND\n",
            encoding="ascii",
        )

    def open_structure(self):
        session = TrajectorySession.open(str(self.structure))
        self.addCleanup(session.close)
        return session

    def test_structure_has_one_frame_without_simulation_time_or_source_changes(self):
        before = hashlib.sha256(self.structure.read_bytes()).hexdigest()
        session = self.open_structure()
        frame = session.read_frame(0, "all", "")
        self.assertTrue(session.structure_only)
        self.assertTrue(frame.structure_only)
        self.assertEqual((frame.frame_index, frame.n_frames), (0, 1))
        self.assertIsNone(frame.time_ps)
        self.assertEqual((frame.receptor_count, frame.ligand_count), (3, 0))
        self.assertAlmostEqual(frame.receptor_coordinates[1][0], 1.4)
        self.assertEqual(hashlib.sha256(self.structure.read_bytes()).hexdigest(), before)

    def test_chain_and_residue_selections_remain_available(self):
        frame = self.open_structure().read_frame(0, "chainID A and resid 12", "chainID B and resid 42")
        self.assertEqual((frame.receptor_count, frame.ligand_count), (2, 1))
        self.assertEqual(frame.receptor_bonds, ((0, 1),))

    def test_structure_cannot_be_exported_as_a_molecular_dynamics_frame(self):
        session = self.open_structure()
        with self.assertRaisesRegex(TrajectoryError, "trajectory"):
            session.export_frame(self.root / "exports", 0, "protein", "resname LIG", True)
        self.assertFalse((self.root / "exports").exists())

    def test_blank_trajectory_uses_structure_mode_and_multimodel_is_explicitly_first_model(self):
        atoms = self.structure.read_text(encoding="ascii").replace("END\n", "")
        self.structure.write_text(f"MODEL        1\n{atoms}ENDMDL\nMODEL        2\n{atoms}ENDMDL\nEND\n", encoding="ascii")
        session = TrajectorySession.open(str(self.structure), "  ")
        self.addCleanup(session.close)
        self.assertEqual(session.read_frame(0, "all", "").n_frames, 1)
        with self.assertRaisesRegex(TrajectoryError, "outside"):
            session.read_frame(1, "all", "")

    def test_missing_invalid_and_empty_input_paths_have_readable_errors(self):
        for topology, trajectory in (("", ""), (None, ""), (True, ""),
                                     (str(self.root / "missing.pdb"), ""),
                                     ("bad\x00file.pdb", ""), (str(self.structure), None)):
            with self.subTest(topology=topology, trajectory=trajectory), self.assertRaises(TrajectoryError):
                TrajectorySession.open(topology, trajectory)

    def test_structure_mutation_after_open_requires_reload(self):
        session = self.open_structure()
        self.structure.write_text(self.structure.read_text(encoding="ascii") + "REMARK changed\n", encoding="ascii")
        with self.assertRaisesRegex(TrajectoryError, "changed"):
            session.read_frame(0, "all", "")


if __name__ == "__main__":
    unittest.main()
