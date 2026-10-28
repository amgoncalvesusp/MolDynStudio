"""Display bonds retain source topology and remap only displayed atoms."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import MDAnalysis as mda
import numpy as np

from core.trajectory_frames import TrajectorySession
from tests.test_trajectory_frames import pdb_atom


class FrameBondTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.topology = self.root / "bonds.pdb"
        self.topology.write_text(
            "".join(pdb_atom(index + 1, name, "GLY", "A", 12, index * 1.4, "C")
                    for index, name in enumerate(("N", "CA", "C", "O")))
            + pdb_atom(5, "C1", "LIG", "B", 42, 6.0, "C", "HETATM")
            + pdb_atom(6, "O1", "LIG", "B", 42, 7.2, "O", "HETATM")
            + "CONECT    1    3\nCONECT    2    4\nCONECT    5    6\nEND\n",
            encoding="ascii",
        )
        self.trajectory = self.root / "three_frames.dcd"
        universe = mda.Universe(str(self.topology), to_guess=())
        with mda.Writer(str(self.trajectory), n_atoms=6) as writer:
            for _ in range(3):
                writer.write(universe.atoms)
                universe.atoms.positions += 10
        universe.trajectory.close()

    def open_trajectory(self):
        session = TrajectorySession.open(str(self.topology), str(self.trajectory))
        self.addCleanup(session.close)
        return session

    def test_explicit_bonds_follow_the_requested_frame_and_atom_groups(self):
        session = self.open_trajectory()
        snapshot = session.read_frame(2, "protein", "resname LIG")
        self.assertFalse(session.structure_only)
        self.assertFalse(snapshot.structure_only)
        self.assertEqual(snapshot.n_frames, 3)
        self.assertEqual(snapshot.receptor_bonds, ((0, 2), (1, 3)))
        self.assertEqual(snapshot.ligand_bonds, ((0, 1),))
        self.assertAlmostEqual(snapshot.receptor_coordinates[0][0], 20)
        self.assertAlmostEqual(snapshot.ligand_coordinates[0][0], 26)

    def test_noncontiguous_selection_remaps_bonds_to_display_coordinates(self):
        snapshot = self.open_trajectory().read_frame(0, "index 0 2", "index 4 5")
        self.assertEqual(snapshot.receptor_bonds, ((0, 1),))
        self.assertEqual(snapshot.ligand_bonds, ((0, 1),))

    def test_absent_bonds_are_not_inferred_from_close_coordinates(self):
        text = self.topology.read_text(encoding="ascii")
        self.topology.write_text("\n".join(line for line in text.splitlines() if not line.startswith("CONECT")) + "\n", encoding="ascii")
        snapshot = self.open_trajectory().read_frame(0, "all", "")
        self.assertEqual(snapshot.receptor_bonds, ())
        self.assertEqual(snapshot.ligand_bonds, ())

    def test_display_reduction_never_connects_missing_atoms(self):
        count = 12002
        universe = mda.Universe.empty(count, n_residues=1, atom_resindex=np.zeros(count, dtype=int), trajectory=True)
        universe.add_TopologyAttr("resnames", ["GLY"])
        universe.add_bonds([(0, 1), (0, 3), (11997, 12000), (12000, 12001)])
        session = TrajectorySession(universe, self.topology, self.trajectory)
        self.addCleanup(session.close)
        snapshot = session.read_frame(0, "all", "")
        self.assertTrue(snapshot.display_reduced)
        self.assertEqual(snapshot.receptor_count, count)
        self.assertEqual(len(snapshot.receptor_coordinates), 4001)
        self.assertEqual(snapshot.receptor_bonds, ((0, 1), (3999, 4000)))
        self.assertTrue(all(0 <= left < right < len(snapshot.receptor_coordinates)
                            for left, right in snapshot.receptor_bonds))


if __name__ == "__main__":
    unittest.main()
