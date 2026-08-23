from __future__ import annotations

import unittest
from unittest import mock

from core import gromacs_runner


class GromacsRunnerPlatformTests(unittest.TestCase):
    def test_linux_conda_commands_use_bridge_profile_loader(self):
        spec = gromacs_runner.CommandSpec("gmx", ("--version",))
        process = mock.Mock()
        process.poll.return_value = None
        process.stdout = iter(["GROMACS version: 2024\n"])
        process.wait.return_value = 0

        with (
            mock.patch.object(gromacs_runner.os, "name", "posix"),
            mock.patch.object(gromacs_runner.wsl_bridge, "popen", return_value=process) as popen,
            mock.patch.object(gromacs_runner.subprocess, "Popen") as direct_popen,
        ):
            runner = gromacs_runner.GROMACSRunner([spec])
            runner.run()

        popen.assert_called_once_with(["gmx", "--version"], cwd=None, env_name="moldynstudio")
        direct_popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
