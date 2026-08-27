"""Parse live GROMACS log output into structured progress updates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


_FLOAT_PATTERN = r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
_STEP_TIME_HEADER = re.compile(r"^\s*Step\s+Time\s*$", re.IGNORECASE)
_STEP_TIME_VALUES = re.compile(
    rf"^\s*(\d+)\s+({_FLOAT_PATTERN})\s*$",
    re.IGNORECASE,
)
_INLINE_PROGRESS = re.compile(
    rf"\bstep\s+(\d+)\b.*?\btime\s+({_FLOAT_PATTERN})\b",
    re.IGNORECASE,
)
_TEMPERATURE = re.compile(rf"\bTemperature\b\s*=\s*({_FLOAT_PATTERN})\b", re.IGNORECASE)
_PRESSURE = re.compile(
    rf"\bPressure(?:\s*\(bar\))?\s*=\s*({_FLOAT_PATTERN})\b",
    re.IGNORECASE,
)
_POTENTIAL = re.compile(rf"\b(?:Epot|Potential)\b\s*=\s*({_FLOAT_PATTERN})\b", re.IGNORECASE)
_NSTEPS = re.compile(r"^\s*nsteps\s*=\s*(-?\d+)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class MDProgress:
    step: int | None = None
    time_ps: float | None = None
    total_steps: int | None = None
    fraction: float | None = None
    temperature_k: float | None = None
    pressure_bar: float | None = None
    potential_kj_mol: float | None = None


def read_mdp_nsteps(path: str | Path) -> int | None:
    """Return the last active ``nsteps`` assignment from an MDP file."""

    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    parsed: int | None = None
    for raw_line in lines:
        active_line = raw_line.split(";", 1)[0].strip()
        if not active_line:
            continue
        match = _NSTEPS.match(active_line)
        if match is not None:
            parsed = int(match.group(1))
    return parsed


class GromacsLogParser:
    """Consume streamed log lines and emit partial progress snapshots."""

    def __init__(
        self,
        mdp_path: str | Path | None = None,
        *,
        total_steps: int | None = None,
    ) -> None:
        self.total_steps = total_steps
        if self.total_steps is None and mdp_path is not None:
            self.total_steps = read_mdp_nsteps(mdp_path)
        self._awaiting_step_time_values = False

    def feed_line(self, line: str) -> MDProgress | None:
        text = line.strip()
        if not text:
            return None

        if _STEP_TIME_HEADER.match(text):
            self._awaiting_step_time_values = True
            return None

        if self._awaiting_step_time_values:
            self._awaiting_step_time_values = False
            match = _STEP_TIME_VALUES.match(text)
            if match is not None:
                return self._progress_from_numbers(match.group(1), match.group(2))

        inline = _INLINE_PROGRESS.search(text)
        if inline is not None:
            self._awaiting_step_time_values = False
            return self._progress_from_numbers(inline.group(1), inline.group(2))

        temperature = _TEMPERATURE.search(text)
        if temperature is not None:
            return MDProgress(temperature_k=float(temperature.group(1)))

        pressure = _PRESSURE.search(text)
        if pressure is not None:
            return MDProgress(pressure_bar=float(pressure.group(1)))

        potential = _POTENTIAL.search(text)
        if potential is not None:
            return MDProgress(potential_kj_mol=float(potential.group(1)))

        return None

    def _progress_from_numbers(self, step_text: str, time_text: str) -> MDProgress:
        step = int(step_text)
        time_ps = float(time_text)
        fraction: float | None = None
        if self.total_steps is not None and self.total_steps > 0:
            fraction = min(max(step / self.total_steps, 0.0), 1.0)
        return MDProgress(
            step=step,
            time_ps=time_ps,
            total_steps=self.total_steps,
            fraction=fraction,
        )


__all__ = ["MDProgress", "GromacsLogParser", "read_mdp_nsteps"]
