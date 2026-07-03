"""Quality-control helpers for molecular dynamics time series.

The functions in this module intentionally work on plain XVG/CSV-like files so
the dashboard can run before heavyweight trajectory libraries are available.
"""

from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


@dataclass(frozen=True)
class TimeSeries:
    metric: str
    time: tuple[float, ...]
    values: tuple[float, ...]
    source: str = ""
    replicate: str = ""


@dataclass(frozen=True)
class QCSummary:
    metric: str
    replicate: str
    source: str
    n_points: int
    time_start: float
    time_end: float
    mean: float
    std: float
    final_mean: float
    final_std: float
    drift: float
    slope: float
    burn_in_time: float | None
    status: str
    message: str


@dataclass(frozen=True)
class ReplicateComparison:
    metric: str
    n_replicates: int
    mean: float
    std: float
    relative_spread: float
    status: str
    message: str


METRIC_ALIASES: tuple[tuple[str, str], ...] = (
    ("rmsd", "RMSD"),
    ("rmsf", "RMSF"),
    ("gyrate", "Radius of gyration"),
    ("gyration", "Radius of gyration"),
    ("rg", "Radius of gyration"),
    ("sasa", "SASA"),
    ("area", "SASA"),
    ("temperature", "Temperature"),
    ("temp", "Temperature"),
    ("pressure", "Pressure"),
    ("density", "Density"),
    ("potential", "Potential energy"),
    ("kinetic", "Kinetic energy"),
    ("total_energy", "Total energy"),
    ("total-energy", "Total energy"),
    ("energy", "Energy"),
)


def infer_metric_name(path_or_name: str) -> str:
    name = Path(path_or_name).stem.lower()
    for token, metric in METRIC_ALIASES:
        if token in name:
            return metric
    return Path(path_or_name).stem or "Series"


def infer_replicate_name(path_or_name: str) -> str:
    stem = Path(path_or_name).stem
    match = re.search(r"(?:rep(?:lica)?|run)[-_ ]?(\d+)", stem, re.IGNORECASE)
    if match:
        return f"rep{match.group(1)}"
    return stem


def parse_numeric_series(
    path: str | Path,
    x_col: int = 0,
    y_col: int = 1,
    metric: str | None = None,
    replicate: str | None = None,
) -> TimeSeries:
    source = Path(path)
    rows = _numeric_rows(source)
    times: list[float] = []
    values: list[float] = []
    for row in rows:
        if len(row) <= max(x_col, y_col):
            continue
        times.append(row[x_col])
        values.append(row[y_col])
    if not times:
        raise ValueError(f"No numeric columns found in {source}")
    return TimeSeries(
        metric=metric or infer_metric_name(source.name),
        time=tuple(times),
        values=tuple(values),
        source=str(source),
        replicate=replicate or infer_replicate_name(source.name),
    )


def summarize_series(
    metric: str,
    time: Sequence[float],
    values: Sequence[float],
    source: str = "",
    replicate: str = "",
) -> QCSummary:
    clean = tuple(
        (float(t), float(v))
        for t, v in zip(time, values)
        if math.isfinite(float(t)) and math.isfinite(float(v))
    )
    if len(clean) < 3:
        return _empty_summary(metric, source, replicate, len(clean), "FAIL", "Not enough finite points.")

    clean = tuple(sorted(clean, key=lambda item: item[0]))
    x = tuple(item[0] for item in clean)
    y = tuple(item[1] for item in clean)
    if metric.lower() == "rmsf":
        return _distribution_summary(metric, x, y, source, replicate)
    final_start = max(0, int(len(y) * 0.6))
    final_y = y[final_start:] or y
    final_x = x[final_start:] or x

    mean = _mean(y)
    std = _std(y)
    final_mean = _mean(final_y)
    final_std = _std(final_y)
    drift = final_y[-1] - final_y[0]
    slope = _slope(final_x, final_y)
    burn_in = _estimate_burn_in(x, y, final_mean, final_std)
    status, message = _classify(metric, x, y, final_mean, final_std, drift, slope, burn_in)

    return QCSummary(
        metric=metric,
        replicate=replicate,
        source=source,
        n_points=len(y),
        time_start=x[0],
        time_end=x[-1],
        mean=mean,
        std=std,
        final_mean=final_mean,
        final_std=final_std,
        drift=drift,
        slope=slope,
        burn_in_time=burn_in,
        status=status,
        message=message,
    )


def summarize_file(path: str | Path, metric: str | None = None) -> QCSummary:
    series = parse_numeric_series(path, metric=metric)
    return summarize_series(
        series.metric,
        series.time,
        series.values,
        source=series.source,
        replicate=series.replicate,
    )


def compare_replicates(summaries: Iterable[QCSummary]) -> ReplicateComparison:
    usable = tuple(s for s in summaries if s.n_points >= 3 and math.isfinite(s.final_mean))
    if not usable:
        return ReplicateComparison("", 0, 0.0, 0.0, 0.0, "FAIL", "No usable replicate summaries.")
    metric = usable[0].metric
    final_means = tuple(s.final_mean for s in usable)
    mean = _mean(final_means)
    std = _std(final_means)
    scale = max(abs(mean), max(abs(v) for v in final_means), 1e-12)
    relative_spread = (max(final_means) - min(final_means)) / scale
    if len(usable) < 2:
        status = "WARN"
        message = "Only one replicate loaded."
    elif relative_spread > 0.25:
        status = "WARN"
        message = "Replicate spread is large; inspect trajectories before pooling results."
    else:
        status = "OK"
        message = "Replicate final means are broadly consistent."
    return ReplicateComparison(metric, len(usable), mean, std, relative_spread, status, message)


def recommended_analysis_start(summaries: Iterable[QCSummary]) -> float | None:
    starts = tuple(s.burn_in_time for s in summaries if s.burn_in_time is not None)
    if not starts:
        return None
    return max(starts)


def _numeric_rows(path: Path) -> tuple[tuple[float, ...], ...]:
    rows: list[tuple[float, ...]] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "@")):
            continue
        parts = _split_numeric_line(line)
        try:
            numbers = tuple(float(part) for part in parts)
        except ValueError:
            continue
        if numbers:
            rows.append(numbers)
    return tuple(rows)


def _split_numeric_line(line: str) -> list[str]:
    if "," in line:
        return next(csv.reader([line]))
    return line.split()


def _estimate_burn_in(
    time: Sequence[float],
    values: Sequence[float],
    final_mean: float,
    final_std: float,
) -> float | None:
    if len(values) < 8:
        return time[0]
    value_range = max(values) - min(values)
    tolerance = max(2.0 * final_std, 0.05 * value_range, 1e-12)
    window = max(3, int(round(len(values) * 0.1)))
    required = 3
    hits = 0
    for start in range(0, len(values) - window + 1):
        local_mean = _mean(values[start : start + window])
        if abs(local_mean - final_mean) <= tolerance:
            hits += 1
            if hits >= required:
                first = max(0, start - required + 1)
                return time[first]
        else:
            hits = 0
    return None


def _classify(
    metric: str,
    time: Sequence[float],
    values: Sequence[float],
    final_mean: float,
    final_std: float,
    drift: float,
    slope: float,
    burn_in: float | None,
) -> tuple[str, str]:
    duration = max(time[-1] - time[0], 1e-12)
    value_range = max(values) - min(values)
    scale = max(abs(final_mean), value_range, 1e-12)
    normalized_drift = abs(drift) / scale
    normalized_slope = abs(slope) * duration / scale
    final_cv = final_std / max(abs(final_mean), 1e-12)

    if burn_in is None:
        return "WARN", "No stable plateau detected; inspect drift and equilibration."
    if normalized_drift > 0.20 or normalized_slope > 0.20:
        return "WARN", "Late drift remains high; extend simulation or exclude early frames."
    if metric.lower() in {"rmsd", "radius of gyration", "sasa"} and final_cv > 0.20:
        return "WARN", "Final-window fluctuations are high for this structural metric."
    return "OK", "Stable plateau detected with modest late drift."


def _empty_summary(
    metric: str,
    source: str,
    replicate: str,
    n_points: int,
    status: str,
    message: str,
) -> QCSummary:
    return QCSummary(
        metric=metric,
        replicate=replicate,
        source=source,
        n_points=n_points,
        time_start=0.0,
        time_end=0.0,
        mean=0.0,
        std=0.0,
        final_mean=0.0,
        final_std=0.0,
        drift=0.0,
        slope=0.0,
        burn_in_time=None,
        status=status,
        message=message,
    )


def _distribution_summary(
    metric: str,
    x: Sequence[float],
    y: Sequence[float],
    source: str,
    replicate: str,
) -> QCSummary:
    mean = _mean(y)
    std = _std(y)
    slope = _slope(x, y)
    return QCSummary(
        metric=metric,
        replicate=replicate,
        source=source,
        n_points=len(y),
        time_start=x[0],
        time_end=x[-1],
        mean=mean,
        std=std,
        final_mean=mean,
        final_std=std,
        drift=y[-1] - y[0],
        slope=slope,
        burn_in_time=None,
        status="OK",
        message="Distribution metric; inspect residue peaks rather than burn-in.",
    )


def _mean(values: Sequence[float]) -> float:
    return sum(values) / max(len(values), 1)


def _std(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _mean(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(max(variance, 0.0))


def _slope(x: Sequence[float], y: Sequence[float]) -> float:
    if len(x) < 2:
        return 0.0
    x_mean = _mean(x)
    y_mean = _mean(y)
    denom = sum((value - x_mean) ** 2 for value in x)
    if denom <= 0.0:
        return 0.0
    return sum((xv - x_mean) * (yv - y_mean) for xv, yv in zip(x, y)) / denom
