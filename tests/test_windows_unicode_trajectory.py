"""Windows XDR readers keep original Unicode source identity through ASCII aliases."""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import MDAnalysis as mda

from core.trajectory_frames import TrajectoryError, TrajectorySession
from tests.test_trajectory_frames import pdb_atom


@unittest.skipUnless(os.name == "nt", "Windows XDR filename handling")
class WindowsUnicodeTrajectoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        source = self.root / "source.pdb"
        source.write_text(pdb_atom(1, "N", "GLY", "A", 1, 0, "N")
                          + pdb_atom(2, "CA", "GLY", "A", 1, 1.4, "C")
                          + "CONECT    1    2\nEND\n", encoding="ascii")
        self.directory = self.root / "preparação da molécula"
        self.directory.mkdir()
        self.topology = self.directory / "proteína.pdb"
        shutil.copyfile(source, self.topology)
        universe = mda.Universe(str(source), to_guess=())
        for suffix in ("xtc", "trr"):
            generated = self.root / f"source.{suffix}"
            with mda.Writer(str(generated), n_atoms=2) as writer:
                for frame in range(3):
                    universe.atoms.positions = [[frame * 10, 0, 0], [frame * 10 + 1.4, 0, 0]]
                    writer.write(universe.atoms)
            shutil.copyfile(generated, self.directory / f"trajetória.{suffix}")
        universe.trajectory.close()

    def test_real_xtc_and_trr_open_unicode_names_without_changing_originals(self):
        for suffix in ("xtc", "trr"):
            trajectory = self.directory / f"trajetória.{suffix}"
            before = tuple(hashlib.sha256(path.read_bytes()).hexdigest()
                           for path in (self.topology, trajectory))
            with self.subTest(format=suffix):
                session = TrajectorySession.open(str(self.topology), str(trajectory))
                try:
                    frame = session.read_frame(2, "protein", "")
                    self.assertEqual((frame.frame_index, frame.n_frames), (2, 3))
                    self.assertAlmostEqual(frame.receptor_coordinates[0][0], 20, places=4)
                    self.assertEqual(session.topology, self.topology.resolve())
                    self.assertEqual(session.trajectory, trajectory.resolve())
                finally:
                    session.close()
                self.assertEqual(before, tuple(hashlib.sha256(path.read_bytes()).hexdigest()
                                               for path in (self.topology, trajectory)))

    def test_unavailable_short_alias_has_actionable_error_without_source_copies(self):
        before = tuple(sorted(path.name for path in self.directory.iterdir()))
        with mock.patch("ctypes.WinDLL") as library:
            library.return_value.GetShortPathNameW.return_value = 0
            with self.assertRaisesRegex(TrajectoryError, "ASCII"):
                TrajectorySession.open(str(self.topology), str(self.directory / "trajetória.xtc"))
        self.assertEqual(before, tuple(sorted(path.name for path in self.directory.iterdir())))


if __name__ == "__main__":
    unittest.main()
