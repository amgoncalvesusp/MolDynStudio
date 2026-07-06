"""WSL2 bridge for running GROMACS/conda commands from Windows.

On Windows, every call is wrapped with ``wsl.exe -- bash -lc "<cmd>"`` and
prefixed with ``conda run -n moldynstudio`` so the moldynstudio environment is
activated inside WSL. On Linux/macOS, the same calls run natively (still
through ``conda run``).
"""

from __future__ import annotations

import platform
import shlex
import shutil
import subprocess
from pathlib import PureWindowsPath
from typing import Iterable, Optional

IS_WINDOWS = platform.system() == "Windows"
DEFAULT_ENV = "moldynstudio"
MINIFORGE_INSTALLER_URL = (
    "https://github.com/conda-forge/miniforge/releases/latest/download/"
    "Miniforge3-Linux-x86_64.sh"
)
CONDA_PRELUDE = (
    'for f in "$HOME/miniforge3/etc/profile.d/conda.sh" '
    '"$HOME/mambaforge/etc/profile.d/conda.sh" '
    '"$HOME/miniconda3/etc/profile.d/conda.sh" '
    '"$HOME/anaconda3/etc/profile.d/conda.sh"; do '
    '[ -f "$f" ] && . "$f" && break; '
    "done"
)


def _escape_wsl_bash_arg(script: str) -> str:
    """Prevent ``wsl.exe`` from consuming ``$VAR`` before bash sees it."""

    return script.replace("$", r"\$")


def win_to_wsl(path: str) -> str:
    """Convert ``C:\\Users\\foo\\bar`` -> ``/mnt/c/Users/foo/bar``.

    UNC paths (``\\\\server\\share\\...``) are rejected explicitly because
    they have no valid ``/mnt/<drive>`` mapping in WSL. The caller must copy
    files to a local drive first.
    """

    if not path:
        return path
    if not IS_WINDOWS:
        return path
    if str(path).startswith("\\\\") or str(path).startswith("//"):
        raise ValueError(
            f"UNC/network paths are not supported in WSL bridge: {path!r}. "
            "Copy files to a local drive first."
        )
    p = PureWindowsPath(path)
    if not p.drive:
        return str(path).replace("\\", "/")
    drive = p.drive.rstrip(":").lower()
    rest = "/".join(p.parts[1:]) if len(p.parts) > 1 else ""
    converted = f"/mnt/{drive}/{rest}".rstrip("/")
    return converted or f"/mnt/{drive}"


def _join_shell_args(cmd: Iterable[str]) -> str:
    return " ".join(shlex.quote(str(c)) for c in cmd)


def _wrap_native_shell(script: str, cwd: Optional[str]) -> list[str]:
    inner = f"{CONDA_PRELUDE}; {script}"
    if cwd:
        inner = f"{CONDA_PRELUDE}; cd {shlex.quote(cwd)} && {script}"
    return ["bash", "-lc", inner]


def wsl_to_win(path: str) -> str:
    """Convert ``/mnt/c/Users/foo/bar`` -> ``C:/Users/foo/bar``."""

    if not IS_WINDOWS or not path.startswith("/mnt/"):
        return path
    parts = path.split("/", 3)
    if len(parts) < 3:
        return path
    drive = parts[2].upper()
    rest = parts[3] if len(parts) > 3 else ""
    return f"{drive}:/{rest}"


def _wrap(cmd: Iterable[str], cwd: Optional[str], env_name: str) -> list[str]:
    cmd_list = [str(c) for c in cmd]
    if IS_WINDOWS and shutil.which("wsl") is not None:
        joined = _join_shell_args(cmd_list)
        quoted_env = shlex.quote(env_name)
        if cwd:
            wsl_cwd = win_to_wsl(cwd)
            inner = (
                f"{CONDA_PRELUDE}; "
                f"cd {shlex.quote(wsl_cwd)} && "
                f"conda run --no-capture-output -n {quoted_env} {joined}"
            )
        else:
            inner = (
                f"{CONDA_PRELUDE}; "
                f"conda run --no-capture-output -n {quoted_env} {joined}"
            )
        return ["wsl.exe", "--", "bash", "-lc", _escape_wsl_bash_arg(inner)]
    joined = _join_shell_args(cmd_list)
    quoted_env = shlex.quote(env_name)
    return _wrap_native_shell(
        f"conda run --no-capture-output -n {quoted_env} {joined}", cwd
    )


def _wrap_raw(cmd: Iterable[str], cwd: Optional[str]) -> list[str]:
    """Wrap a command without activating ``DEFAULT_ENV`` first.

    Use this for setup commands such as ``conda env create`` where the target
    environment may not exist yet.
    """

    cmd_list = [str(c) for c in cmd]
    if IS_WINDOWS:
        if shutil.which("wsl") is None:
            raise FileNotFoundError("wsl.exe not found")
        joined = _join_shell_args(cmd_list)
        if cwd:
            wsl_cwd = win_to_wsl(cwd)
            joined = f"cd {shlex.quote(wsl_cwd)} && {joined}"
        inner = f"{CONDA_PRELUDE}; {joined}"
        return ["wsl.exe", "--", "bash", "-lc", _escape_wsl_bash_arg(inner)]
    return _wrap_native_shell(_join_shell_args(cmd_list), cwd)


def _wrap_raw_shell(script: str, cwd: Optional[str]) -> list[str]:
    """Wrap a raw shell script for setup/bootstrap work."""

    inner = script
    if cwd:
        wsl_cwd = win_to_wsl(cwd)
        inner = f"cd {shlex.quote(wsl_cwd)} && {inner}"
    if IS_WINDOWS:
        if shutil.which("wsl") is None:
            raise FileNotFoundError("wsl.exe not found")
        return ["wsl.exe", "--", "bash", "-lc", _escape_wsl_bash_arg(inner)]
    return _wrap_native_shell(script, cwd)


def run(
    cmd: Iterable[str],
    cwd: Optional[str] = None,
    env_name: str = DEFAULT_ENV,
    timeout: Optional[float] = None,
    **kwargs,
) -> subprocess.CompletedProcess:
    """Run a command synchronously and capture its output."""

    full = _wrap(cmd, cwd, env_name)
    return subprocess.run(
        full,
        capture_output=True,
        text=True,
        timeout=timeout,
        **kwargs,
    )


def run_raw(
    cmd: Iterable[str],
    cwd: Optional[str] = None,
    timeout: Optional[float] = None,
    **kwargs,
) -> subprocess.CompletedProcess:
    """Run a setup command without prefixing it with ``conda run -n``."""

    full = _wrap_raw(cmd, cwd)
    return subprocess.run(
        full,
        capture_output=True,
        text=True,
        timeout=timeout,
        **kwargs,
    )


def run_raw_shell(
    script: str,
    cwd: Optional[str] = None,
    timeout: Optional[float] = None,
    **kwargs,
) -> subprocess.CompletedProcess:
    """Run a setup shell script without prefixing it with ``conda run -n``."""

    full = _wrap_raw_shell(script, cwd)
    return subprocess.run(
        full,
        capture_output=True,
        text=True,
        timeout=timeout,
        **kwargs,
    )


def popen(
    cmd: Iterable[str],
    cwd: Optional[str] = None,
    env_name: str = DEFAULT_ENV,
    **kwargs,
) -> subprocess.Popen:
    """Spawn a process for streaming stdout (combined with stderr)."""

    full = _wrap(cmd, cwd, env_name)
    return subprocess.Popen(
        full,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        **kwargs,
    )


def popen_raw(
    cmd: Iterable[str],
    cwd: Optional[str] = None,
    **kwargs,
) -> subprocess.Popen:
    """Spawn a setup command without prefixing it with ``conda run -n``."""

    full = _wrap_raw(cmd, cwd)
    return subprocess.Popen(
        full,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        **kwargs,
    )


def popen_raw_shell(
    script: str,
    cwd: Optional[str] = None,
    **kwargs,
) -> subprocess.Popen:
    """Spawn a setup shell script without prefixing it with ``conda run -n``."""

    full = _wrap_raw_shell(script, cwd)
    return subprocess.Popen(
        full,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        **kwargs,
    )


def gmx(args: Iterable[str], cwd: Optional[str] = None, **kwargs) -> subprocess.CompletedProcess:
    return run(["gmx", *[str(a) for a in args]], cwd=cwd, **kwargs)


def gmx_popen(args: Iterable[str], cwd: Optional[str] = None, **kwargs) -> subprocess.Popen:
    return popen(["gmx", *[str(a) for a in args]], cwd=cwd, **kwargs)


def check_wsl_available() -> tuple[bool, str]:
    """Return ``(ok, distro_or_error)``."""

    if not IS_WINDOWS:
        return True, "native (non-Windows)"
    if shutil.which("wsl") is None:
        return False, "wsl.exe not found. Run install_wsl.ps1 as Administrator."
    try:
        r = subprocess.run(
            ["wsl.exe", "--list", "--quiet"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, f"WSL probe failed: {exc}"
    distros = [
        line.strip().replace("\x00", "")
        for line in r.stdout.splitlines()
        if line.strip().replace("\x00", "")
    ]
    if distros:
        return True, distros[0]
    return False, "WSL installed but no distro found. Run install_wsl.ps1."


def check_conda_env(env_name: str = DEFAULT_ENV) -> tuple[bool, str]:
    """Verify the conda env exists (inside WSL on Windows)."""

    try:
        if IS_WINDOWS and shutil.which("wsl") is not None:
            r = subprocess.run(
                [
                    "wsl.exe",
                    "--",
                    "bash",
                    "-lc",
                    _escape_wsl_bash_arg(f"{CONDA_PRELUDE}; conda env list"),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        else:
            r = subprocess.run(
                _wrap_native_shell("conda env list", cwd=None),
                capture_output=True,
                text=True,
                timeout=30,
            )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        return False, f"conda probe failed: {exc}"
    if r.returncode != 0:
        detail = (r.stderr or r.stdout or "").strip()
        if "conda: command not found" in detail or r.returncode == 127:
            where = "WSL" if IS_WINDOWS else "this Linux/macOS installation"
            return (
                False,
                f"conda not found in {where}. Click Create env to install Miniforge "
                "and create the moldynstudio environment.",
            )
        return False, f"conda probe failed: {detail[:200] or f'exit code {r.returncode}'}"
    # conda env list emits one env per line: "<name>   <path>". Match name
    # exactly to avoid prefix collisions (e.g. moldynstudio vs moldynstudio-dev).
    for raw in r.stdout.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if parts and parts[0] == env_name:
            return True, f"conda env '{env_name}' found"
    return (
        False,
        f"conda env '{env_name}' not found. Run: conda env create -f environment.yml",
    )


def check_gmx(env_name: str = DEFAULT_ENV) -> tuple[bool, str]:
    """Confirm the ``gmx`` binary is reachable inside the env."""

    try:
        r = run(["gmx", "--version"], env_name=env_name, timeout=60)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        return False, f"gmx probe failed: {exc}"
    if r.returncode == 0:
        version = next(
            (l.strip() for l in r.stdout.splitlines() if "GROMACS version" in l),
            "gmx OK",
        )
        return True, version
    return False, f"gmx not callable: {(r.stderr or r.stdout)[:200]}"


def build_conda_bootstrap_script() -> str:
    """Return a WSL shell script that makes ``conda`` available.

    The launcher runs this before creating/updating the scientific environment,
    because a clean WSL distro often has no Conda installation yet.
    """

    quoted_url = shlex.quote(MINIFORGE_INSTALLER_URL)
    return f"""
set -e
{CONDA_PRELUDE}
if ! command -v conda >/dev/null 2>&1; then
  if [ -x "$HOME/miniforge3/bin/conda" ]; then
    . "$HOME/miniforge3/etc/profile.d/conda.sh"
  elif [ -x "$HOME/miniconda3/bin/conda" ]; then
    . "$HOME/miniconda3/etc/profile.d/conda.sh"
  elif [ -x "$HOME/anaconda3/bin/conda" ]; then
    . "$HOME/anaconda3/etc/profile.d/conda.sh"
  else
    echo "[conda] Miniforge not found; installing to $HOME/miniforge3"
    installer="/tmp/miniforge3-latest.sh"
    rm -f "$installer"
    if command -v curl >/dev/null 2>&1; then
      curl -L {quoted_url} -o "$installer"
    elif command -v wget >/dev/null 2>&1; then
      wget -O "$installer" {quoted_url}
    elif command -v python3 >/dev/null 2>&1; then
      python3 - "$installer" <<'PY'
import sys
import urllib.request

urllib.request.urlretrieve("{MINIFORGE_INSTALLER_URL}", sys.argv[1])
PY
    else
      echo "[conda] Cannot download Miniforge: install curl, wget, or python3 in WSL."
      exit 127
    fi
    bash "$installer" -b -p "$HOME/miniforge3"
    rm -f "$installer"
    profile_line='. "$HOME/miniforge3/etc/profile.d/conda.sh"'
    touch "$HOME/.profile"
    grep -qxF "$profile_line" "$HOME/.profile" || echo "$profile_line" >> "$HOME/.profile"
    . "$HOME/miniforge3/etc/profile.d/conda.sh"
    conda config --set channel_priority strict
  fi
fi
{CONDA_PRELUDE}
if ! command -v conda >/dev/null 2>&1; then
  echo "[conda] conda is still not available after bootstrap."
  exit 127
fi
conda --version
""".strip()


def build_conda_env_sync_script(env_file: str, env_name: str = DEFAULT_ENV) -> str:
    """Return a WSL shell script that bootstraps Conda and creates/updates env."""

    quoted_env_file = shlex.quote(env_file)
    quoted_env_name = shlex.quote(env_name)
    return (
        build_conda_bootstrap_script()
        + "\n"
        + f"""
if conda env list | awk '{{print $1}}' | grep -qx {quoted_env_name}; then
  echo "[env] updating existing {env_name} environment from {env_file}"
  conda env update -n {quoted_env_name} -f {quoted_env_file} --prune
else
  echo "[env] creating {env_name} environment from {env_file}"
  conda env create -f {quoted_env_file}
fi
""".strip()
    )
