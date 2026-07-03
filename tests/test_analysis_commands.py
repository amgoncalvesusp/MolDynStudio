from __future__ import annotations

import unittest
from unittest import mock

from analysis.mmpbsa import MMPBSAAnalysis
from core import wsl_bridge


class MMPBSACommandTests(unittest.TestCase):
    def test_command_uses_tool_args_and_translates_windows_paths(self):
        analysis = MMPBSAAnalysis()
        with mock.patch.object(wsl_bridge, "IS_WINDOWS", True):
            command = analysis.build_command(
                r"C:\work\topol.tpr",
                r"C:\work\traj.xtc",
                "Protein",
                "LIG",
                r"C:\work",
            )

        self.assertEqual(command[0], "gmx_MMPBSA")
        self.assertNotIn("conda", command)
        self.assertIn("/mnt/c/work/topol.tpr", command)
        self.assertIn("/mnt/c/work/traj.xtc", command)


if __name__ == "__main__":
    unittest.main()
