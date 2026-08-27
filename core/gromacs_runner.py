"""Threaded subprocess execution for GROMACS and related tools."""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import threading
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from core import wsl_bridge

try:
    from PyQt5.QtCore import QThread, pyqtSignal
except Exception:  # pragma: no cover - command builders remain usable without PyQt5
    class _Signal:
        def connect(self, *_args, **_kwargs) -> None:
            return None

        def emit(self, *_args, **_kwargs) -> None:
            return None

    def pyqtSignal(*_args, **_kwargs):  # type: ignore[no-redef]
        return _Signal()

    class QThread:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            pass

        def start(self) -> None:
            self.run()


@dataclass(frozen=True)
class CommandSpec:
    executable: str
    args: tuple[str, ...]
    cwd: str | None = None
    use_conda: bool = True
    conda_env: str = "moldynstudio"
    use_wsl: bool = False
    stdin_text: str | None = None
    stage: str | None = None
    command_name: str | None = None

    def as_list(self) -> list[str]:
        command = [self.executable, *self.args]
        if self.use_wsl:
            command = ["wsl", *command]
        if self.use_conda:
            command = ["conda", "run", "-n", self.conda_env, *command]
        return command

    @property
    def command_label(self) -> str:
        """Return a stable command identifier for log metadata."""

        if self.command_name:
            return self.command_name
        if self.args:
            return self.args[0]
        return self.executable


@dataclass(frozen=True)
class CommandLogLine:
    """One streamed output line with its orchestration context."""

    line: str
    stage: str | None
    command: str


class GromacsCommandBuilder:
    """Build command lines without starting a process."""

    def __init__(self, conda_env: str = "moldynstudio", executable: str = "gmx"):
        self.conda_env = conda_env
        self.executable = executable

    def build(
        self,
        subcommand: str,
        args: Iterable[str],
        cwd: str | None = None,
        *,
        stdin_text: str | None = None,
        stage: str | None = None,
        command_name: str | None = None,
    ) -> CommandSpec:
        return CommandSpec(
            executable=self.executable,
            args=(subcommand, *tuple(args)),
            cwd=cwd,
            use_conda=True,
            conda_env=self.conda_env,
            use_wsl=should_use_wsl(),
            stdin_text=stdin_text,
            stage=stage,
            command_name=command_name,
        )


def should_use_wsl() -> bool:
    return os.name == "nt" and shutil.which("wsl") is not None


WSL_PGID_PREFIX = "__MOLDYNSTUDIO_PGID__="


def _wsl_process_group_script(spec: CommandSpec) -> str:
    """Build a WSL script whose child PID is also its private process-group ID."""

    command = shlex.join([spec.executable, *spec.args])
    if spec.use_conda:
        environment = shlex.quote(spec.conda_env)
        command = f"conda run --no-capture-output -n {environment} {command}"
        prelude = f"{wsl_bridge.CONDA_PRELUDE}; "
    else:
        prelude = ""
    return (
        f"{prelude}setsid {command} <&0 &\n"
        f"moldynstudio_pgid=$!\n"
        f"printf '{WSL_PGID_PREFIX}%s\\n' \"$moldynstudio_pgid\"\n"
        f"wait \"$moldynstudio_pgid\""
    )


class GROMACSRunner(QThread):
    progress = pyqtSignal(int, str)
    log_line = pyqtSignal(str)
    log_event = pyqtSignal(object)
    finished_with_status = pyqtSignal(bool, int)

    def __init__(self, commands: Sequence[CommandSpec], parent=None):
        super().__init__(parent)
        self.commands = tuple(commands)
        self._process: Optional[subprocess.Popen[str]] = None
        self._cancel_requested = False
        self._finished_emitted = False
        self._termination_sent = False
        self._process_mode = "process"
        self._wsl_pgid: int | None = None
        self._wsl_pgid_ready = threading.Event()
        self._state_lock = threading.Lock()

    def cancel(self) -> None:
        with self._state_lock:
            self._cancel_requested = True
        self._terminate_active_process()

    def _terminate_active_process(self) -> None:
        with self._state_lock:
            process = self._process
            mode = self._process_mode
            already_sent = self._termination_sent
        if process is None or process.poll() is not None or already_sent:
            return

        if mode == "wsl_group" and self._wsl_pgid is None:
            # The first bridge line publishes the scoped Linux PGID. Give the
            # streaming thread a brief chance to consume it before falling
            # back to the equally scoped Windows wrapper tree.
            self._wsl_pgid_ready.wait(timeout=1.0)

        with self._state_lock:
            if self._termination_sent:
                return
            self._termination_sent = True
            mode = self._process_mode
            wsl_pgid = self._wsl_pgid

        try:
            if mode == "posix_group":
                getpgid = getattr(os, "getpgid")
                killpg = getattr(os, "killpg")
                killpg(getpgid(process.pid), signal.SIGTERM)
            elif mode == "wsl_group" and wsl_pgid is not None:
                subprocess.run(
                    ["wsl.exe", "--", "/bin/kill", "-TERM", "--", f"-{wsl_pgid}"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
            elif os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
            else:
                process.terminate()
        except (OSError, subprocess.SubprocessError):
            # The process may have exited between poll() and termination.
            if process.poll() is None:
                process.terminate()

    def _set_process(self, process: subprocess.Popen[str], mode: str) -> None:
        with self._state_lock:
            self._process = process
            self._process_mode = mode
            self._termination_sent = False
            self._wsl_pgid = None
            self._wsl_pgid_ready.clear()
            cancelled = self._cancel_requested
        if cancelled:
            self._terminate_active_process()

    def _start_process(self, spec: CommandSpec) -> tuple[subprocess.Popen[str], str]:
        inner_cmd = [spec.executable, *spec.args]
        stdin_options = {"stdin": subprocess.PIPE} if spec.stdin_text is not None else {}
        wsl_available = os.name == "nt" and shutil.which("wsl") is not None
        use_bridge = spec.use_conda and (os.name != "nt" or wsl_available)

        if wsl_available and (spec.use_wsl or use_bridge):
            options = dict(stdin_options)
            creation_flag = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            if creation_flag:
                options["creationflags"] = creation_flag
            process = wsl_bridge.popen_raw_shell(
                _wsl_process_group_script(spec), cwd=spec.cwd, **options
            )
            return process, "wsl_group"
        if use_bridge:
            process = wsl_bridge.popen(
                inner_cmd,
                cwd=spec.cwd,
                env_name=spec.conda_env,
                start_new_session=True,
                **stdin_options,
            )
            return process, "posix_group"

        command = spec.as_list()
        options = dict(stdin_options)
        mode = "process"
        if os.name == "posix":
            options["start_new_session"] = True
            mode = "posix_group"
        elif os.name == "nt":
            creation_flag = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            if creation_flag:
                options["creationflags"] = creation_flag
            mode = "windows_tree"
        process = subprocess.Popen(
            command,
            cwd=spec.cwd or None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            **options,
        )
        return process, mode

    def _write_stdin(self, spec: CommandSpec) -> None:
        if spec.stdin_text is None:
            return
        assert self._process is not None
        if self._process.stdin is None:
            raise OSError("Failed to open process stdin.")
        try:
            self._process.stdin.write(spec.stdin_text)
            self._process.stdin.flush()
        except BrokenPipeError:
            # The command's return code and stdout provide the useful failure.
            pass
        finally:
            self._process.stdin.close()

    def _emit_log(self, spec: CommandSpec, line: str) -> None:
        self.log_line.emit(line)
        self.log_event.emit(CommandLogLine(line, spec.stage, spec.command_label))

    def _emit_finished(self, success: bool, return_code: int) -> None:
        with self._state_lock:
            if self._finished_emitted:
                return
            self._finished_emitted = True
        self.finished_with_status.emit(success, return_code)

    def run(self) -> None:
        with self._state_lock:
            self._finished_emitted = False
        total = max(1, len(self.commands))
        for index, spec in enumerate(self.commands, start=1):
            if self._cancel_requested:
                self.log_line.emit("Run cancelled before starting next command.")
                self._emit_finished(False, -1)
                return
            try:
                process, mode = self._start_process(spec)
                self._set_process(process, mode)
                display_cmd = " ".join([spec.executable, *spec.args])
                self.progress.emit(int((index - 1) / total * 100), display_cmd)
                self._write_stdin(spec)
                if self._process.stdout is None:
                    self.log_line.emit("Failed to capture process stdout.")
                    self._emit_finished(False, 1)
                    return
                try:
                    for raw_line in self._process.stdout:
                        if mode == "wsl_group" and raw_line.startswith(WSL_PGID_PREFIX):
                            pgid_text = raw_line[len(WSL_PGID_PREFIX):].strip()
                            if pgid_text.isdigit():
                                with self._state_lock:
                                    self._wsl_pgid = int(pgid_text)
                                self._wsl_pgid_ready.set()
                                if self._cancel_requested:
                                    self._terminate_active_process()
                            continue
                        if self._cancel_requested:
                            self._terminate_active_process()
                            break
                        self._emit_log(spec, raw_line.rstrip("\r\n"))
                finally:
                    return_code = self._process.wait()
                    with self._state_lock:
                        self._process = None
                if self._cancel_requested:
                    self._emit_finished(False, -1)
                    return
                if return_code != 0:
                    self._emit_finished(False, return_code)
                    return
            except FileNotFoundError as exc:
                self.log_line.emit(f"Executable not found: {exc}")
                self._emit_finished(False, 127)
                return
            except OSError as exc:
                self.log_line.emit(f"Execution failed: {exc}")
                self._emit_finished(False, 1)
                return
        self.progress.emit(100, "Done")
        self._emit_finished(True, 0)
