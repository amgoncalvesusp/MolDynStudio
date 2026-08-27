"""Resolve and inspect the GROMACS executable selected by the user."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from core import wsl_bridge


GPU_MODE_AUTO = "Auto"
GPU_MODE_CPU = "CPU only"
GPU_MODE_SUPPORTED = "GPU if supported"
GPU_MODES = (GPU_MODE_AUTO, GPU_MODE_CPU, GPU_MODE_SUPPORTED)

_CPU_FLAGS = ("-nt", "-nb", "-pme", "-bonded", "-update")
_UNUSABLE_GPU_VALUES = (
    "disabled",
    "none",
    "no",
    "not available",
    "not supported",
    "n/a",
)


class GromacsCapabilityError(ValueError):
    """The selected resource mode is unsupported by the active GROMACS."""


@dataclass(frozen=True)
class GromacsCapabilities:
    executable: str
    version_text: str
    version: str | None
    executable_ok: bool
    gpu_support_text: str | None
    hardware_notes: tuple[str, ...]
    mdrun_help_text: str = ""


def _looks_like_windows_path(value: str) -> bool:
    path = PureWindowsPath(value)
    return bool(path.drive) or value.startswith("\\\\")


def resolve_gromacs_binary(configured_value: str) -> str:
    """Resolve ``auto`` or validate an explicit binary for Linux/WSL execution.

    The application executes scientific commands in Linux, either natively or
    through WSL. Windows PE executables are therefore rejected instead of being
    passed to a WSL shell. Other Windows paths are translated to ``/mnt/...``
    and their executability is verified by :func:`probe_gromacs` in WSL.
    """

    selected = str(configured_value or "").strip()
    if not selected or selected.casefold() == "auto":
        return "gmx"

    if wsl_bridge.IS_WINDOWS:
        if selected.casefold().endswith(".exe"):
            raise ValueError(
                "A Windows .exe cannot be used as the GROMACS binary inside WSL. "
                "Select a Linux GROMACS executable or use Auto."
            )
        if _looks_like_windows_path(selected):
            return wsl_bridge.win_to_wsl(selected)
        # Absolute POSIX paths and command names are resolved inside WSL by the
        # capability probe, not against the Windows filesystem.
        return selected

    candidate = Path(selected).expanduser()
    if candidate.is_absolute() or candidate.parent != Path("."):
        if not candidate.is_file():
            raise FileNotFoundError(f"GROMACS executable not found: {candidate}")
        if not os.access(candidate, os.X_OK):
            raise PermissionError(f"GROMACS path is not executable: {candidate}")
        return str(candidate)

    discovered = shutil.which(selected)
    if discovered is None:
        raise FileNotFoundError(f"GROMACS executable not found: {selected}")
    return discovered


def _combined_output(result: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(
        part.strip() for part in (result.stdout or "", result.stderr or "") if part.strip()
    )


def _parse_version(version_text: str) -> str | None:
    match = re.search(
        r"^\s*GROMACS\s+version\s*:\s*([^\s]+)",
        version_text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    return match.group(1) if match else None


def _parse_gpu_support(version_text: str) -> tuple[str | None, tuple[str, ...]]:
    notes: list[str] = []
    support: str | None = None
    for raw_line in version_text.splitlines():
        line = raw_line.strip()
        if not line or "gpu" not in line.casefold():
            continue
        notes.append(line)
        label, separator, value = line.partition(":")
        if not separator:
            continue
        normalized_value = value.strip()
        unusable = normalized_value.casefold() in _UNUSABLE_GPU_VALUES
        if label.strip().casefold() == "gpu support" and normalized_value and not unusable:
            support = normalized_value
    return support, tuple(notes)


def probe_gromacs(executable: str, env_name: str) -> GromacsCapabilities:
    """Capture version and ``mdrun`` capabilities in the chosen environment."""

    try:
        version_result = wsl_bridge.run(
            [executable, "--version"], env_name=env_name, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return GromacsCapabilities(
            executable=executable,
            version_text=str(exc),
            version=None,
            executable_ok=False,
            gpu_support_text=None,
            hardware_notes=(),
        )

    version_text = _combined_output(version_result)
    if version_result.returncode != 0:
        return GromacsCapabilities(
            executable=executable,
            version_text=version_text or f"exit code {version_result.returncode}",
            version=None,
            executable_ok=False,
            gpu_support_text=None,
            hardware_notes=(),
        )

    help_text = ""
    hardware_notes: tuple[str, ...]
    gpu_support_text, hardware_notes = _parse_gpu_support(version_text)
    try:
        help_result = wsl_bridge.run(
            [executable, "mdrun", "-h"], env_name=env_name, timeout=60
        )
        help_text = _combined_output(help_result)
        if help_result.returncode != 0:
            hardware_notes = (
                *hardware_notes,
                f"mdrun help probe failed with exit code {help_result.returncode}",
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        hardware_notes = (*hardware_notes, f"mdrun help probe failed: {exc}")

    return GromacsCapabilities(
        executable=executable,
        version_text=version_text,
        version=_parse_version(version_text),
        executable_ok=True,
        gpu_support_text=gpu_support_text,
        hardware_notes=hardware_notes,
        mdrun_help_text=help_text,
    )


def _safe_thread_count(cores: int) -> int:
    try:
        requested = int(cores)
    except (TypeError, ValueError) as exc:
        raise GromacsCapabilityError("CPU core count must be an integer.") from exc
    requested = max(1, requested)
    available = os.cpu_count()
    return min(requested, available) if available and available > 0 else requested


def _require_help_flags(capabilities: GromacsCapabilities, flags: tuple[str, ...]) -> None:
    missing = tuple(
        flag
        for flag in flags
        if re.search(
            rf"(?<![\w-]){re.escape(flag)}(?![\w-])",
            capabilities.mdrun_help_text,
        )
        is None
    )
    if missing:
        raise GromacsCapabilityError(
            "The selected GROMACS mdrun help does not report required options: "
            + ", ".join(missing)
        )


def build_mdrun_resource_args(
    gpu_mode: str,
    cores: int,
    capabilities: GromacsCapabilities,
) -> tuple[str, ...]:
    """Build conservative GROMACS 2026.3 resource arguments."""

    if not capabilities.executable_ok:
        raise GromacsCapabilityError("The selected GROMACS executable is not callable.")
    threads = str(_safe_thread_count(cores))
    mode = str(gpu_mode or GPU_MODE_AUTO).strip()
    args = ("-nt", threads)

    if mode == GPU_MODE_AUTO:
        # GROMACS chooses any supported offload backend in Auto mode.
        return args
    if mode == GPU_MODE_CPU:
        _require_help_flags(capabilities, _CPU_FLAGS)
        return (
            *args,
            "-nb",
            "cpu",
            "-pme",
            "cpu",
            "-bonded",
            "cpu",
            "-update",
            "cpu",
        )
    if mode == GPU_MODE_SUPPORTED:
        if not capabilities.gpu_support_text:
            raise GromacsCapabilityError(
                "The selected GROMACS executable does not report a usable GPU backend. "
                "Switch GPU acceleration to Auto/CPU only or select a GPU-enabled "
                "GROMACS build."
            )
        _require_help_flags(capabilities, ("-nt", "-nb"))
        return (*args, "-nb", "gpu")
    raise GromacsCapabilityError(f"Unknown GPU acceleration mode: {mode}")


__all__ = [
    "GPU_MODE_AUTO",
    "GPU_MODE_CPU",
    "GPU_MODE_SUPPORTED",
    "GPU_MODES",
    "GromacsCapabilities",
    "GromacsCapabilityError",
    "build_mdrun_resource_args",
    "probe_gromacs",
    "resolve_gromacs_binary",
]
