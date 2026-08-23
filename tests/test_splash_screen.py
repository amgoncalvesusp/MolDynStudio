from __future__ import annotations

import unittest
from subprocess import CompletedProcess
from unittest import mock

from windows import splash_screen


class SplashScreenDependencyTests(unittest.TestCase):
    def test_import_check_runs_inside_scientific_environment(self):
        completed = CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with mock.patch.object(
            splash_screen.wsl_bridge,
            "run",
            return_value=completed,
        ) as run:
            ok, message = splash_screen._import_check("MDAnalysis")

        self.assertTrue(ok)
        self.assertEqual(message, "MDAnalysis OK")
        self.assertIn("MDAnalysis", run.call_args.args[0][-1])

    def test_mmpbsa_probe_uses_temporary_working_directory(self):
        completed = CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with mock.patch.object(
            splash_screen.wsl_bridge,
            "run",
            return_value=completed,
        ) as run:
            ok, message = splash_screen._gmx_mmpbsa_check()

        self.assertTrue(ok)
        self.assertEqual(message, "gmx_MMPBSA OK")
        self.assertIsNotNone(run.call_args.kwargs["cwd"])


if __name__ == "__main__":
    unittest.main()
