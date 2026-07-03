from __future__ import annotations

import unittest
from unittest import mock

from core import wsl_bridge


class WSLBridgePathTests(unittest.TestCase):
    def test_win_to_wsl_basic(self):
        with mock.patch.object(wsl_bridge, "IS_WINDOWS", True):
            self.assertEqual(
                wsl_bridge.win_to_wsl(r"C:\Users\foo\bar"),
                "/mnt/c/Users/foo/bar",
            )

    def test_win_to_wsl_root_drive(self):
        with mock.patch.object(wsl_bridge, "IS_WINDOWS", True):
            self.assertEqual(wsl_bridge.win_to_wsl("D:\\"), "/mnt/d")

    def test_win_to_wsl_passthrough_on_unix(self):
        with mock.patch.object(wsl_bridge, "IS_WINDOWS", False):
            self.assertEqual(
                wsl_bridge.win_to_wsl("/home/foo"),
                "/home/foo",
            )

    def test_wsl_to_win_basic(self):
        with mock.patch.object(wsl_bridge, "IS_WINDOWS", True):
            self.assertEqual(
                wsl_bridge.wsl_to_win("/mnt/c/Users/foo/bar"),
                "C:/Users/foo/bar",
            )

    def test_wsl_to_win_passthrough_for_non_mnt(self):
        with mock.patch.object(wsl_bridge, "IS_WINDOWS", True):
            self.assertEqual(
                wsl_bridge.wsl_to_win("/home/foo"),
                "/home/foo",
            )


class WSLBridgeWrapTests(unittest.TestCase):
    def test_wrap_on_unix_uses_conda_run(self):
        with (
            mock.patch.object(wsl_bridge, "IS_WINDOWS", False),
            mock.patch.object(wsl_bridge.shutil, "which", return_value=None),
        ):
            cmd = wsl_bridge._wrap(
                ["gmx", "--version"], cwd=None, env_name="moldynstudio"
            )
        self.assertEqual(
            cmd[:5], ["conda", "run", "--no-capture-output", "-n", "moldynstudio"]
        )
        self.assertIn("gmx", cmd)

    def test_wrap_on_windows_with_wsl_uses_bash_lc(self):
        with (
            mock.patch.object(wsl_bridge, "IS_WINDOWS", True),
            mock.patch.object(
                wsl_bridge.shutil, "which", return_value="C:\\Windows\\System32\\wsl.exe"
            ),
        ):
            cmd = wsl_bridge._wrap(
                ["gmx", "--version"], cwd=None, env_name="moldynstudio"
            )
        self.assertEqual(cmd[:4], ["wsl.exe", "--", "bash", "-lc"])
        self.assertIn(
            "conda run --no-capture-output -n moldynstudio gmx --version", cmd[4]
        )

    def test_wrap_includes_cwd_when_provided(self):
        with (
            mock.patch.object(wsl_bridge, "IS_WINDOWS", True),
            mock.patch.object(wsl_bridge.shutil, "which", return_value="wsl.exe"),
        ):
            cmd = wsl_bridge._wrap(
                ["gmx", "mdrun"], cwd="C:\\work", env_name="moldynstudio"
            )
        self.assertIn("cd /mnt/c/work &&", cmd[4])
        self.assertIn("/mnt/c/work", cmd[4])

    def test_wrap_raw_on_windows_does_not_prefix_conda_env(self):
        with (
            mock.patch.object(wsl_bridge, "IS_WINDOWS", True),
            mock.patch.object(wsl_bridge.shutil, "which", return_value="wsl.exe"),
        ):
            cmd = wsl_bridge._wrap_raw(
                ["conda", "env", "create", "-f", "/mnt/c/work/environment.yml"],
                cwd=None,
            )
        self.assertEqual(cmd[:4], ["wsl.exe", "--", "bash", "-lc"])
        self.assertIn("conda env create -f /mnt/c/work/environment.yml", cmd[4])
        self.assertNotIn("conda run", cmd[4])

    def test_wrap_raw_on_native_returns_command_without_conda_run(self):
        with (
            mock.patch.object(wsl_bridge, "IS_WINDOWS", False),
            mock.patch.object(wsl_bridge.shutil, "which", return_value=None),
        ):
            cmd = wsl_bridge._wrap_raw(["conda", "env", "list"], cwd=None)
        self.assertEqual(cmd, ["conda", "env", "list"])

    def test_wrap_raw_shell_on_windows_uses_wsl_bash(self):
        with (
            mock.patch.object(wsl_bridge, "IS_WINDOWS", True),
            mock.patch.object(wsl_bridge.shutil, "which", return_value="wsl.exe"),
        ):
            cmd = wsl_bridge._wrap_raw_shell("echo ok", cwd=None)
        self.assertEqual(cmd, ["wsl.exe", "--", "bash", "-lc", "echo ok"])

    def test_wrap_raw_shell_escapes_dollars_for_wsl_exe(self):
        with (
            mock.patch.object(wsl_bridge, "IS_WINDOWS", True),
            mock.patch.object(wsl_bridge.shutil, "which", return_value="wsl.exe"),
        ):
            cmd = wsl_bridge._wrap_raw_shell('x=/tmp/foo; echo "$x"', cwd=None)
        self.assertIn(r"\$x", cmd[4])

    def test_wrap_escapes_conda_prelude_dollars_for_wsl_exe(self):
        with (
            mock.patch.object(wsl_bridge, "IS_WINDOWS", True),
            mock.patch.object(wsl_bridge.shutil, "which", return_value="wsl.exe"),
        ):
            cmd = wsl_bridge._wrap(["gmx", "--version"], cwd=None, env_name="moldynstudio")
        self.assertIn(r"\$HOME", cmd[4])
        self.assertIn(r"\$f", cmd[4])

    def test_conda_env_sync_script_bootstraps_miniforge(self):
        script = wsl_bridge.build_conda_env_sync_script(
            "/mnt/c/work/environment.yml", "moldynstudio"
        )

        self.assertIn("Miniforge3-Linux-x86_64.sh", script)
        self.assertIn("conda env create -f /mnt/c/work/environment.yml", script)
        self.assertIn("conda env update -n moldynstudio", script)
        self.assertNotIn("sudo", script)

    def test_check_conda_env_reports_missing_conda(self):
        completed = wsl_bridge.subprocess.CompletedProcess(
            args=["wsl.exe"],
            returncode=127,
            stdout="",
            stderr="bash: line 1: conda: command not found",
        )
        with (
            mock.patch.object(wsl_bridge, "IS_WINDOWS", True),
            mock.patch.object(wsl_bridge.shutil, "which", return_value="wsl.exe"),
            mock.patch.object(wsl_bridge.subprocess, "run", return_value=completed),
        ):
            ok, message = wsl_bridge.check_conda_env()

        self.assertFalse(ok)
        self.assertIn("conda not found", message.lower())


if __name__ == "__main__":
    unittest.main()
