from __future__ import annotations

import signal
import subprocess
import unittest
from unittest import mock

from core import gromacs_runner


def _process(lines: list[str], return_code: int = 0, pid: int = 1001) -> mock.Mock:
    process = mock.Mock()
    process.pid = pid
    process.poll.return_value = None
    process.stdout = iter(lines)
    process.wait.return_value = return_code
    process.stdin = mock.Mock()
    return process


class GromacsRunnerPlatformTests(unittest.TestCase):
    def test_linux_conda_commands_use_bridge_profile_loader_and_new_session(self):
        spec = gromacs_runner.CommandSpec("gmx", ("--version",))
        process = _process(["GROMACS version: 2024\n"])

        with (
            mock.patch.object(gromacs_runner.os, "name", "posix"),
            mock.patch.object(gromacs_runner.wsl_bridge, "popen", return_value=process) as popen,
            mock.patch.object(gromacs_runner.subprocess, "Popen") as direct_popen,
        ):
            runner = gromacs_runner.GROMACSRunner([spec])
            runner.run()

        popen.assert_called_once_with(
            ["gmx", "--version"],
            cwd=None,
            env_name="moldynstudio",
            start_new_session=True,
        )
        direct_popen.assert_not_called()

    def test_native_posix_cancel_terminates_the_active_process_group_once(self):
        spec = gromacs_runner.CommandSpec("gmx", ("mdrun",), use_conda=False)
        process = _process(["running\n"], return_code=-signal.SIGTERM, pid=4321)
        statuses: list[tuple[bool, int]] = []

        with (
            mock.patch.object(gromacs_runner.os, "name", "posix"),
            mock.patch.object(gromacs_runner.subprocess, "Popen", return_value=process),
            mock.patch.object(
                gromacs_runner.os, "getpgid", return_value=4321, create=True
            ) as getpgid,
            mock.patch.object(gromacs_runner.os, "killpg", create=True) as killpg,
        ):
            runner = gromacs_runner.GROMACSRunner([spec])
            runner.finished_with_status.connect(lambda ok, code: statuses.append((ok, code)))
            runner.log_line.connect(lambda _line: runner.cancel())
            runner.run()

        getpgid.assert_called_once_with(4321)
        killpg.assert_called_once_with(4321, signal.SIGTERM)
        self.assertEqual(statuses, [(False, -1)])

    def test_wsl_cancel_targets_only_the_current_linux_process_group(self):
        spec = gromacs_runner.CommandSpec(
            "/opt/gromacs/bin/gmx",
            ("mdrun", "-deffnm", "md"),
            use_wsl=True,
            stage="production",
            command_name="mdrun",
        )
        process = _process(
            [f"{gromacs_runner.WSL_PGID_PREFIX}7654\n", "running\n"],
            return_code=-signal.SIGTERM,
        )
        statuses: list[tuple[bool, int]] = []

        with (
            mock.patch.object(gromacs_runner.os, "name", "nt"),
            mock.patch.object(gromacs_runner.shutil, "which", return_value="wsl.exe"),
            mock.patch.object(
                gromacs_runner.wsl_bridge, "popen_raw_shell", return_value=process
            ) as popen,
            mock.patch.object(gromacs_runner.subprocess, "run") as scoped_kill,
        ):
            runner = gromacs_runner.GROMACSRunner([spec])
            runner.finished_with_status.connect(lambda ok, code: statuses.append((ok, code)))
            runner.log_line.connect(lambda _line: runner.cancel())
            runner.run()

        script = popen.call_args.args[0]
        self.assertIn("setsid", script)
        self.assertIn("/opt/gromacs/bin/gmx mdrun -deffnm md", script)
        self.assertNotIn("production", script)
        self.assertNotIn("pkill", script)
        self.assertNotIn("killall", script)
        scoped_kill.assert_called_once_with(
            ["wsl.exe", "--", "/bin/kill", "-TERM", "--", "-7654"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(statuses, [(False, -1)])


class GromacsRunnerLifecycleTests(unittest.TestCase):
    def test_failure_of_second_command_prevents_third_command(self):
        commands = [
            gromacs_runner.CommandSpec("tool", (str(index),), use_conda=False)
            for index in range(1, 4)
        ]
        processes = [_process(["one\n"]), _process(["two\n"], return_code=1)]
        statuses: list[tuple[bool, int]] = []

        with mock.patch.object(
            gromacs_runner.subprocess, "Popen", side_effect=processes
        ) as popen:
            runner = gromacs_runner.GROMACSRunner(commands)
            runner.finished_with_status.connect(lambda ok, code: statuses.append((ok, code)))
            runner.run()

        self.assertEqual(popen.call_count, 2)
        self.assertEqual(statuses, [(False, 1)])

    def test_stdin_text_is_written_and_stdin_is_closed_before_streaming(self):
        spec = gromacs_runner.CommandSpec(
            "gmx", ("genion",), use_conda=False, stdin_text="SOL\n"
        )
        process = _process([])

        with mock.patch.object(gromacs_runner.subprocess, "Popen", return_value=process) as popen:
            runner = gromacs_runner.GROMACSRunner([spec])
            runner.run()

        self.assertIs(popen.call_args.kwargs["stdin"], subprocess.PIPE)
        process.stdin.write.assert_called_once_with("SOL\n")
        process.stdin.close.assert_called_once_with()

    def test_output_is_emitted_line_by_line_without_waiting_for_completion(self):
        emitted: list[str] = []
        self_outer = self

        class StreamingOutput:
            def __init__(self):
                self.index = 0

            def __iter__(self):
                return self

            def __next__(self):
                if self.index == 0:
                    self.index += 1
                    return "first  \n"
                if self.index == 1:
                    self.index += 1
                    self_outer.assertEqual(emitted, ["first  "])
                    return "second\n"
                raise StopIteration

        process = _process([])
        process.stdout = StreamingOutput()
        spec = gromacs_runner.CommandSpec("tool", (), use_conda=False)

        with mock.patch.object(gromacs_runner.subprocess, "Popen", return_value=process):
            runner = gromacs_runner.GROMACSRunner([spec])
            runner.log_line.connect(emitted.append)
            runner.run()

        self.assertEqual(emitted, ["first  ", "second"])

    def test_structured_log_event_identifies_stage_and_command(self):
        spec = gromacs_runner.CommandSpec(
            "gmx-custom",
            ("mdrun", "-deffnm", "nvt"),
            use_conda=False,
            stage="nvt",
            command_name="integrate",
        )
        process = _process(["step 1\n"])
        events: list[gromacs_runner.CommandLogLine] = []

        with mock.patch.object(gromacs_runner.subprocess, "Popen", return_value=process):
            runner = gromacs_runner.GROMACSRunner([spec])
            runner.log_event.connect(events.append)
            runner.run()

        self.assertEqual(
            events,
            [gromacs_runner.CommandLogLine("step 1", "nvt", "integrate")],
        )

    def test_builder_uses_the_configured_executable(self):
        spec = gromacs_runner.GromacsCommandBuilder(
            executable="/opt/gromacs/bin/gmx_mpi"
        ).build("grompp", ["-f", "nvt.mdp"])

        self.assertEqual(spec.executable, "/opt/gromacs/bin/gmx_mpi")
        self.assertEqual(spec.args, ("grompp", "-f", "nvt.mdp"))


if __name__ == "__main__":
    unittest.main()
