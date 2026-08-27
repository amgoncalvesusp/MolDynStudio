"""Deterministic post-stage quality checks over validated GROMACS logs.

This module never runs GROMACS and never invents samples.  Callers provide the
already-validated stage outputs; the evaluator reads only the recorded log
artifact and reports scientific warnings separately from fatal sanity gates.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from pathlib import Path
import re
from statistics import fmean
from types import MappingProxyType
from typing import Mapping

from core.gromacs_log_parser import GromacsLogParser, MDProgress
from core.run_manifest import StageName


ENERGY_DRIFT_GATE = "energy_drift"
TEMPERATURE_GATE = "temperature"
PRESSURE_GATE = "pressure"
QC_GATES = frozenset({ENERGY_DRIFT_GATE, TEMPERATURE_GATE, PRESSURE_GATE})

_FLOAT_PATTERN = r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
_POTENTIAL_ENERGY_VALUE = re.compile(
    rf"\bPotential\s+Energy\b\s*=\s*({_FLOAT_PATTERN})\b",
    re.I,
)

_STAGE_STEMS = {
    StageName.MINIMIZATION: "em",
    StageName.NVT: "nvt",
    StageName.NPT: "npt",
    StageName.PRODUCTION: "md",
}
_KEY_VALUE_CANDIDATES = {
    ENERGY_DRIFT_GATE: re.compile(r"\b(?:Epot|Potential(?:\s+Energy)?)\b\s*=", re.I),
    TEMPERATURE_GATE: re.compile(r"\bTemperature(?:\s*\(K\))?\b\s*=", re.I),
    PRESSURE_GATE: re.compile(r"\bPressure(?:\s*\(bar\))?\b\s*=", re.I),
}
_HEADER_CANDIDATES = {
    ENERGY_DRIFT_GATE: re.compile(r"\b(?:Potential|Potential\s+Energy)\b", re.I),
    TEMPERATURE_GATE: re.compile(r"\bTemperature(?:\s*\(K\))?\b", re.I),
    PRESSURE_GATE: re.compile(r"\bPressure(?:\s*\(bar\))?\b", re.I),
}


class QCSeverity(str, Enum):
    """Stable manifest values for one quality check."""

    PASS = "pass"
    WARNING = "warning"
    FATAL = "fatal"


@dataclass(frozen=True)
class StageQCPolicy:
    """Select diagnostics that must be parseable for stage completion."""

    required_gates: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        unknown = self.required_gates - QC_GATES
        if unknown:
            raise ValueError(f"Unknown stage QC gates: {sorted(unknown)}")


@dataclass(frozen=True)
class QCCheck:
    """One artifact-backed QC decision."""

    gate: str
    severity: QCSeverity
    message: str
    source: str
    observations: Mapping[str, float | int]

    def to_manifest(self) -> dict[str, object]:
        return {
            "gate": self.gate,
            "severity": self.severity.value,
            "message": self.message,
            "source": self.source,
            "observations": dict(self.observations),
        }


@dataclass(frozen=True)
class StageQCResult:
    """Complete post-stage QC outcome suitable for manifest persistence."""

    stage: str
    checks: tuple[QCCheck, ...]

    @property
    def warnings(self) -> tuple[QCCheck, ...]:
        return tuple(check for check in self.checks if check.severity == QCSeverity.WARNING)

    @property
    def fatals(self) -> tuple[QCCheck, ...]:
        return tuple(check for check in self.checks if check.severity == QCSeverity.FATAL)

    @property
    def has_fatal(self) -> bool:
        return bool(self.fatals)

    @property
    def outcome(self) -> str:
        if self.fatals:
            return "fatal"
        if self.warnings:
            return "warning"
        return "passed"

    def to_manifest(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "outcome": self.outcome,
            "checks": [check.to_manifest() for check in self.checks],
        }


def evaluate_stage_qc(
    stage: StageName | str,
    validated_outputs: Mapping[str, str],
    *,
    policy: StageQCPolicy | None = None,
) -> StageQCResult:
    """Evaluate a validated stage log without changing any pipeline state.

    Missing or malformed diagnostics are warnings by default.  A caller may
    make a diagnostic mandatory through ``StageQCPolicy.required_gates``.
    Parsed scientific outliers remain warnings because useful ranges are
    ensemble- and system-dependent. Fatal results are reserved for selected
    required diagnostics that are missing or unusable.
    """

    stage_name = StageName(stage)
    if stage_name not in _STAGE_STEMS:
        raise ValueError(f"Post-stage QC is unavailable for {stage_name.value}")
    if not isinstance(validated_outputs, Mapping) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in validated_outputs.items()
    ):
        raise TypeError("validated_outputs must be a string mapping")

    selected_policy = policy or StageQCPolicy()
    log_name = f"{_STAGE_STEMS[stage_name]}.log"
    recorded_source = validated_outputs.get(log_name, log_name)
    log_path = Path(recorded_source).expanduser()
    source = str(log_path.resolve())
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        checks = tuple(
            _unavailable_check(
                gate,
                source,
                selected_policy,
                f"The validated stage log could not be read ({exc}).",
            )
            for gate in (ENERGY_DRIFT_GATE, TEMPERATURE_GATE, PRESSURE_GATE)
        )
        return StageQCResult(stage_name.value, checks)

    samples, mentioned, malformed = _parse_diagnostics(lines)
    checks = (
        _energy_check(samples[ENERGY_DRIFT_GATE], mentioned, malformed, source, selected_policy),
        _temperature_check(samples[TEMPERATURE_GATE], mentioned, malformed, source, selected_policy),
        _pressure_check(samples[PRESSURE_GATE], mentioned, malformed, source, selected_policy),
    )
    return StageQCResult(stage_name.value, checks)


def _parse_diagnostics(
    lines: list[str],
) -> tuple[dict[str, tuple[float, ...]], frozenset[str], frozenset[str]]:
    parser = GromacsLogParser()
    values: dict[str, tuple[float, ...]] = {gate: () for gate in QC_GATES}
    mentioned: set[str] = set()
    malformed: set[str] = set()
    pending_header: frozenset[str] = frozenset()

    for raw_line in lines:
        text = raw_line.strip()
        if not text:
            continue
        update = parser.feed_line(raw_line)
        parsed = _progress_values(update)
        potential_energy = _POTENTIAL_ENERGY_VALUE.search(text)
        if potential_energy is not None and ENERGY_DRIFT_GATE not in parsed:
            parsed = {
                **parsed,
                ENERGY_DRIFT_GATE: float(potential_energy.group(1)),
            }
        values = {
            gate: (*values[gate], parsed[gate]) if gate in parsed else values[gate]
            for gate in QC_GATES
        }

        if pending_header:
            malformed.update(pending_header - parsed.keys())
            pending_header = frozenset()

        key_candidates = frozenset(
            gate for gate, pattern in _KEY_VALUE_CANDIDATES.items() if pattern.search(text)
        )
        if key_candidates:
            mentioned.update(key_candidates)
            malformed.update(key_candidates - parsed.keys())

        header_candidates = _header_candidates(text)
        if header_candidates:
            mentioned.update(header_candidates)
            pending_header = header_candidates

    malformed.update(pending_header)
    return values, frozenset(mentioned), frozenset(malformed)


def _progress_values(update: MDProgress | None) -> dict[str, float]:
    if update is None:
        return {}
    candidates = {
        ENERGY_DRIFT_GATE: update.potential_kj_mol,
        TEMPERATURE_GATE: update.temperature_k,
        PRESSURE_GATE: update.pressure_bar,
    }
    return {
        gate: value
        for gate, value in candidates.items()
        if value is not None
    }


def _header_candidates(text: str) -> frozenset[str]:
    if "=" in text or not ("\t" in text or re.search(r"\s{2,}", text)):
        return frozenset()
    return frozenset(
        gate for gate, pattern in _HEADER_CANDIDATES.items() if pattern.search(text)
    )


def _energy_check(
    values: tuple[float, ...],
    mentioned: frozenset[str],
    malformed: frozenset[str],
    source: str,
    policy: StageQCPolicy,
) -> QCCheck:
    unavailable = _diagnostic_availability(
        ENERGY_DRIFT_GATE, values, mentioned, malformed, source, policy
    )
    if unavailable is not None:
        return unavailable
    if len(values) < 2:
        return _check(
            ENERGY_DRIFT_GATE,
            _required_severity(ENERGY_DRIFT_GATE, policy),
            "Energy drift needs at least two parsed potential-energy samples.",
            source,
            {"sample_count": len(values)},
        )

    first, last = values[0], values[-1]
    absolute_drift = last - first
    relative_percent = abs(absolute_drift) / max(abs(first), 1.0) * 100.0
    observations = {
        "sample_count": len(values),
        "first_kj_mol": first,
        "last_kj_mol": last,
        "absolute_drift_kj_mol": absolute_drift,
        "relative_drift_percent": relative_percent,
    }
    if not all(math.isfinite(value) for value in values) or max(map(abs, values)) > 1.0e15:
        severity = QCSeverity.WARNING
        message = "Potential energy exceeds the deterministic physical-sanity limit."
    elif relative_percent > 50.0:
        severity = QCSeverity.WARNING
        message = f"Potential-energy endpoint drift is high ({relative_percent:.2f}%)."
    else:
        severity = QCSeverity.PASS
        message = f"Potential-energy endpoint drift is {relative_percent:.2f}%."
    return _with_malformed_policy(
        _check(ENERGY_DRIFT_GATE, severity, message, source, observations),
        malformed,
        policy,
    )


def _temperature_check(
    values: tuple[float, ...],
    mentioned: frozenset[str],
    malformed: frozenset[str],
    source: str,
    policy: StageQCPolicy,
) -> QCCheck:
    unavailable = _diagnostic_availability(
        TEMPERATURE_GATE, values, mentioned, malformed, source, policy
    )
    if unavailable is not None:
        return unavailable
    observations = _range_observations(values)
    minimum = min(values)
    maximum = max(values)
    if not all(math.isfinite(value) for value in values) or minimum <= 0.0 or maximum > 10_000.0:
        severity = QCSeverity.WARNING
        message = "Temperature is outside the deterministic physical-sanity limits."
    elif minimum < 250.0 or maximum > 400.0:
        severity = QCSeverity.WARNING
        message = f"Temperature range is broad ({minimum:.2f} to {maximum:.2f} K)."
    else:
        severity = QCSeverity.PASS
        message = f"Temperature range is {minimum:.2f} to {maximum:.2f} K."
    return _with_malformed_policy(
        _check(TEMPERATURE_GATE, severity, message, source, observations),
        malformed,
        policy,
    )


def _pressure_check(
    values: tuple[float, ...],
    mentioned: frozenset[str],
    malformed: frozenset[str],
    source: str,
    policy: StageQCPolicy,
) -> QCCheck:
    unavailable = _diagnostic_availability(
        PRESSURE_GATE, values, mentioned, malformed, source, policy
    )
    if unavailable is not None:
        return unavailable
    observations = _range_observations(values)
    maximum_absolute = max(map(abs, values))
    if not all(math.isfinite(value) for value in values) or maximum_absolute > 1.0e6:
        severity = QCSeverity.WARNING
        message = "Pressure is outside the deterministic physical-sanity limits."
    elif maximum_absolute > 1_000.0:
        severity = QCSeverity.WARNING
        message = f"Pressure magnitude is high (up to {maximum_absolute:.2f} bar)."
    else:
        severity = QCSeverity.PASS
        message = f"Pressure range is {min(values):.2f} to {max(values):.2f} bar."
    return _with_malformed_policy(
        _check(PRESSURE_GATE, severity, message, source, observations),
        malformed,
        policy,
    )


def _diagnostic_availability(
    gate: str,
    values: tuple[float, ...],
    mentioned: frozenset[str],
    malformed: frozenset[str],
    source: str,
    policy: StageQCPolicy,
) -> QCCheck | None:
    if values:
        return None
    label = gate.replace("_", " ").capitalize()
    if gate in malformed or gate in mentioned:
        message = f"{label} diagnostics are malformed in the validated stage log."
    else:
        message = f"{label} diagnostics are not present in the validated stage log."
    return _unavailable_check(gate, source, policy, message)


def _unavailable_check(
    gate: str,
    source: str,
    policy: StageQCPolicy,
    message: str,
) -> QCCheck:
    return _check(gate, _required_severity(gate, policy), message, source, {})


def _required_severity(gate: str, policy: StageQCPolicy) -> QCSeverity:
    return QCSeverity.FATAL if gate in policy.required_gates else QCSeverity.WARNING


def _with_malformed_policy(
    check: QCCheck,
    malformed: frozenset[str],
    policy: StageQCPolicy,
) -> QCCheck:
    if check.gate not in malformed:
        return check
    severity = (
        QCSeverity.FATAL
        if check.gate in policy.required_gates
        else max(check.severity, QCSeverity.WARNING, key=_severity_rank)
    )
    return _check(
        check.gate,
        severity,
        f"{check.message} Some {check.gate.replace('_', ' ')} diagnostics were malformed.",
        check.source,
        check.observations,
    )


def _severity_rank(severity: QCSeverity) -> int:
    return {
        QCSeverity.PASS: 0,
        QCSeverity.WARNING: 1,
        QCSeverity.FATAL: 2,
    }[severity]


def _range_observations(values: tuple[float, ...]) -> dict[str, float | int]:
    return {
        "sample_count": len(values),
        "minimum": min(values),
        "maximum": max(values),
        "mean": fmean(values),
    }


def _check(
    gate: str,
    severity: QCSeverity,
    message: str,
    source: str,
    observations: Mapping[str, float | int],
) -> QCCheck:
    return QCCheck(
        gate=gate,
        severity=severity,
        message=message,
        source=source,
        observations=MappingProxyType(dict(observations)),
    )


__all__ = [
    "ENERGY_DRIFT_GATE",
    "PRESSURE_GATE",
    "QCCheck",
    "QCSeverity",
    "StageQCPolicy",
    "StageQCResult",
    "TEMPERATURE_GATE",
    "evaluate_stage_qc",
]
