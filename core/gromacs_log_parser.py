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
_FIXED_COLUMN_WIDTH = 15
_THERMODYNAMIC_FIELDS = {
    "potential": "potential_kj_mol",
    "potential energy": "potential_kj_mol",
    "temperature": "temperature_k",
    "temperature (k)": "temperature_k",
    "pressure": "pressure_bar",
    "pressure (bar)": "pressure_bar",
}


@dataclass(frozen=True)
class _ThermodynamicHeader:
    fields: tuple[tuple[int, str], ...]
    layout: str
    column_count: int


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


def _field_for_label(label: str) -> str | None:
    normalized = " ".join(label.casefold().split())
    return _THERMODYNAMIC_FIELDS.get(normalized)


def _indexed_fields(cells: list[str]) -> tuple[tuple[int, str], ...]:
    return tuple(
        (index, field)
        for index, cell in enumerate(cells)
        if (field := _field_for_label(cell)) is not None
    )


def _thermodynamic_header(line: str) -> _ThermodynamicHeader | None:
    if "=" in line:
        return None

    if "\t" in line:
        fields = _indexed_fields(line.split("\t"))
        if fields:
            return _ThermodynamicHeader(fields, "tab", len(line.split("\t")))

    fixed_cells = [
        line[offset : offset + _FIXED_COLUMN_WIDTH]
        for offset in range(0, len(line), _FIXED_COLUMN_WIDTH)
    ]
    fields = _indexed_fields(fixed_cells)
    if fields:
        return _ThermodynamicHeader(fields, "fixed", len(fixed_cells))

    split_cells = re.split(r"\s{2,}", line.strip())
    fields = _indexed_fields(split_cells)
    if fields:
        return _ThermodynamicHeader(fields, "whitespace", len(split_cells))
    return None


def _value_cells(line: str, header: _ThermodynamicHeader) -> list[str] | None:
    nonempty_tokens = [token for token in re.split(r"\s+", line.strip()) if token]
    if not nonempty_tokens or any(
        re.fullmatch(_FLOAT_PATTERN, token) is None for token in nonempty_tokens
    ):
        return None

    if header.layout == "tab":
        tab_cells = line.split("\t")
        return tab_cells if len(tab_cells) == header.column_count else None
    if header.layout == "fixed":
        return [
            line[offset : offset + _FIXED_COLUMN_WIDTH]
            for offset in range(0, len(line), _FIXED_COLUMN_WIDTH)
        ]
    if len(nonempty_tokens) != header.column_count:
        return None
    return nonempty_tokens


def _thermodynamic_update(
    line: str,
    header: _ThermodynamicHeader,
) -> MDProgress | None:
    cells = _value_cells(line, header)
    if cells is None:
        return None

    values: dict[str, float] = {}
    for index, field in header.fields:
        if index >= len(cells):
            continue
        value = cells[index].strip()
        if value and re.fullmatch(_FLOAT_PATTERN, value) is not None:
            values[field] = float(value)
    return MDProgress(**values) if values else None


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
        self._pending_thermodynamic_header: _ThermodynamicHeader | None = None

    def feed_line(self, line: str) -> MDProgress | None:
        text = line.strip()
        if not text:
            return None

        if _STEP_TIME_HEADER.match(text):
            self._awaiting_step_time_values = True
            self._pending_thermodynamic_header = None
            return None

        if self._awaiting_step_time_values:
            self._awaiting_step_time_values = False
            match = _STEP_TIME_VALUES.match(text)
            if match is not None:
                return self._progress_from_numbers(match.group(1), match.group(2))

        inline = _INLINE_PROGRESS.search(text)
        if inline is not None:
            self._awaiting_step_time_values = False
            self._pending_thermodynamic_header = None
            return self._progress_from_numbers(inline.group(1), inline.group(2))

        temperature = _TEMPERATURE.search(text)
        if temperature is not None:
            self._pending_thermodynamic_header = None
            return MDProgress(temperature_k=float(temperature.group(1)))

        pressure = _PRESSURE.search(text)
        if pressure is not None:
            self._pending_thermodynamic_header = None
            return MDProgress(pressure_bar=float(pressure.group(1)))

        potential = _POTENTIAL.search(text)
        if potential is not None:
            self._pending_thermodynamic_header = None
            return MDProgress(potential_kj_mol=float(potential.group(1)))

        if self._pending_thermodynamic_header is not None:
            header = self._pending_thermodynamic_header
            self._pending_thermodynamic_header = None
            update = _thermodynamic_update(line, header)
            if update is not None:
                return update

        header = _thermodynamic_header(line)
        if header is not None:
            self._pending_thermodynamic_header = header

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
