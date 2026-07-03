"""Threaded subprocess execution for GROMACS and related tools."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

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

    def as_list(self) -> list[str]:
        command = [self.executable, *self.args]
        if self.use_wsl:
            command = ["wsl", *command]
        if self.use_conda:
            command = ["conda", "run", "-n", self.conda_env, *command]
        return command


class GromacsCommandBuilder:
    """Build command lines without starting a process."""

    def __init__(self, conda_env: str = "moldynstudio", executable: str = "gmx"):
        self.conda_env = conda_env
        self.executable = executable

    def build(self, subcommand: str, args: Iterable[str], cwd: str | None = None) -> CommandSpec:
        return CommandSpec(
            executable=self.executable,
            args=(subcommand, *tuple(args)),
            cwd=cwd,
            use_conda=True,
            conda_env=self.conda_env,
            use_wsl=should_use_wsl(),
        )


def should_use_wsl() -> bool:
    return os.name == "nt" and shutil.which("wsl") is not None


class GROMACSRunner(QThread):
    progress = pyqtSignal(int, str)
    log_line = pyqtSignal(str)
    finished_with_status = pyqtSignal(bool, int)

    def __init__(self, commands: Sequence[CommandSpec], parent=None):
        super().__init__(parent)
        self.commands = tuple(commands)
        self._process: Optional[subprocess.Popen[str]] = None
        self._cancel_requested = False

    def cancel(self) -> None:
        self._cancel_requested = True
        if self._process and self._process.poll() is None:
            self._process.terminate()

    def run(self) -> None:
        total = max(1, len(self.commands))
        for index, spec in enumerate(self.commands, start=1):
            if self._cancel_requested:
                self.log_line.emit("Run cancelled before starting next command.")
                self.finished_with_status.emit(False, -1)
                return
            inner_cmd = [spec.executable, *spec.args]
            use_bridge = (
                os.name == "nt"
                and shutil.which("wsl") is not None
                and spec.use_conda
            )
            try:
                if use_bridge:
                    # Public API — guards on wsl.exe + handles env quoting.
                    self._process = wsl_bridge.popen(
                        inner_cmd, cwd=spec.cwd, env_name=spec.conda_env
                    )
                    display_cmd = " ".join(inner_cmd)
                else:
                    command = spec.as_list()
                    self._process = subprocess.Popen(
                        command,
                        cwd=spec.cwd or None,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                    )
                    display_cmd = " ".join(command)
                self.progress.emit(int((index - 1) / total * 100), display_cmd)
                if self._process.stdout is None:
                    self.log_line.emit("Failed to capture process stdout.")
                    self.finished_with_status.emit(False, 1)
                    return
                try:
                    for line in self._process.stdout:
                        if self._cancel_requested:
                            self.cancel()
                            break
                        self.log_line.emit(line.rstrip())
                finally:
                    return_code = self._process.wait()
                if return_code != 0:
                    self.finished_with_status.emit(False, return_code)
                    return
            except FileNotFoundError as exc:
                self.log_line.emit(f"Executable not found: {exc}")
                self.finished_with_status.emit(False, 127)
                return
            except OSError as exc:
                self.log_line.emit(f"Execution failed: {exc}")
                self.finished_with_status.emit(False, 1)
                return
        self.progress.emit(100, "Done")
        self.finished_with_status.emit(True, 0)
