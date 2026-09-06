"""Offline installation and staging of the CHARMM36m GROMACS force field."""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import shutil
import stat
import sys
import tarfile
import tempfile
import time
from typing import BinaryIO
from urllib.request import urlopen


CHARMM36_RELEASE = "feb2026_cgenff-5.0"
CHARMM36_BASENAME = f"charmm36-{CHARMM36_RELEASE}"
CHARMM36_DIRECTORY = f"{CHARMM36_BASENAME}.ff"
CHARMM36_ARCHIVE_NAME = f"{CHARMM36_DIRECTORY}.tgz"
CHARMM36_URL = (
    "https://mackerell.umaryland.edu/download.php?filename="
    f"CHARMM_ff_params_files%2F{CHARMM36_ARCHIVE_NAME}"
)
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
CHARMM36_SHA256 = "c2b0c70e21cced150528ee25d205e5bd3367a5e55e85c2ff0403911980528151"
CHARMM36_TREE_SHA256 = "5247f6a1db2c55e282c81ba7a55423d8fe08eb45231511ddca849f49a69b2799"
REQUIRED_FORCE_FIELD_FILES = (
    "forcefield.itp",
    "atomtypes.atp",
    "aminoacids.rtp",
    "tip3p.itp",
)
MAX_ARCHIVE_MEMBERS = 1_000
MAX_ARCHIVE_MEMBER_BYTES = 25 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 100 * 1024 * 1024


class ForceFieldUnavailableError(FileNotFoundError):
    """Raised when CHARMM36m is neither cached nor bundled locally."""


def resource_root() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parents[1]


def bundled_archive_path() -> Path:
    return resource_root() / "assets" / "forcefields" / CHARMM36_ARCHIVE_NAME


def default_cache_root() -> Path:
    override = os.environ.get("MOLDYNSTUDIO_DATA_DIR")
    if override:
        return Path(override).expanduser() / "forcefields"
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "MolDynStudio" / "forcefields"
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "moldynstudio" / "forcefields"


def cached_force_field_dir(cache_root: Path | None = None) -> Path:
    return (cache_root or default_cache_root()) / CHARMM36_DIRECTORY


def _is_link_or_reparse(path: Path) -> bool:
    try:
        details = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(details.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(details, "st_file_attributes", 0)
    return bool(reparse_flag and attributes & reparse_flag)


def _tree_contains_links(directory: Path) -> bool:
    if _is_link_or_reparse(directory):
        return True
    for root, directories, files in os.walk(directory, followlinks=False):
        base = Path(root)
        for name in (*directories, *files):
            if _is_link_or_reparse(base / name):
                return True
    return False


def is_valid_force_field(directory: Path) -> bool:
    if not directory.is_dir() or _is_link_or_reparse(directory):
        return False
    return all(
        (directory / relative).is_file()
        and not _is_link_or_reparse(directory / relative)
        for relative in REQUIRED_FORCE_FIELD_FILES
    )


def _file_sha256(handle: BinaryIO) -> str:
    digest = sha256()
    while chunk := handle.read(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def _force_field_tree_sha256(directory: Path) -> str:
    digest = sha256()
    files = sorted(
        (path for path in directory.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(directory).as_posix(),
    )
    for path in files:
        relative = path.relative_to(directory).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _validate_force_field_tree(
    directory: Path,
    expected_tree_sha256: str | None,
) -> None:
    if _tree_contains_links(directory):
        raise ValueError(
            "The CHARMM36m directory contains links or junctions."
        )
    if (
        expected_tree_sha256 is not None
        and _force_field_tree_sha256(directory) != expected_tree_sha256
    ):
        raise ValueError(
            "CHARMM36m local files failed integrity validation. Re-import "
            "the official package."
        )


def _validate_archive_members(
    archive: tarfile.TarFile,
    extraction_root: Path,
) -> list[tarfile.TarInfo]:
    root = extraction_root.resolve()
    members = archive.getmembers()
    if len(members) > MAX_ARCHIVE_MEMBERS:
        raise ValueError("CHARMM36m archive contains too many files.")
    total_size = 0
    for member in members:
        destination = (root / member.name).resolve()
        if destination != root and root not in destination.parents:
            raise ValueError(f"Unsafe path in CHARMM36 archive: {member.name}")
        if member.issym() or member.islnk():
            raise ValueError(
                f"Links are not accepted in CHARMM36 archive: {member.name}"
            )
        if not (member.isfile() or member.isdir()):
            raise ValueError(
                f"Unsupported special file in CHARMM36 archive: {member.name}"
            )
        if member.size > MAX_ARCHIVE_MEMBER_BYTES:
            raise ValueError(
                f"File is too large in CHARMM36 archive: {member.name}"
            )
        total_size += member.size
        if total_size > MAX_ARCHIVE_TOTAL_BYTES:
            raise ValueError("CHARMM36m archive expands beyond the safe limit.")
    return members


def install_charmm36_archive(
    archive_path: str | Path,
    *,
    cache_root: Path | None = None,
    expected_sha256: str = CHARMM36_SHA256,
    expected_tree_sha256: str | None = CHARMM36_TREE_SHA256,
) -> Path:
    """Validate and install an official CHARMM36m archive into local cache."""

    source = Path(archive_path).expanduser()
    if not source.is_file():
        raise ForceFieldUnavailableError(f"CHARMM36m archive not found: {source}")
    if _is_link_or_reparse(source):
        raise ValueError(
            "Links and filesystem reparse points are not accepted as "
            "CHARMM36m archives."
        )

    with source.open("rb") as source_handle:
        actual_sha256 = _file_sha256(source_handle)
        if actual_sha256 != expected_sha256:
            raise ValueError(
                "CHARMM36m archive checksum mismatch. Select the official "
                f"{CHARMM36_ARCHIVE_NAME} package."
            )

        cache = cache_root or default_cache_root()
        cache.mkdir(parents=True, exist_ok=True)
        destination = cache / CHARMM36_DIRECTORY
        if is_valid_force_field(destination):
            _validate_force_field_tree(destination, expected_tree_sha256)
            return destination
        if destination.exists():
            raise ValueError(
                "An invalid CHARMM36m cache directory already exists. "
                "Remove it from the MolDynStudio data directory and retry."
            )

        with tempfile.TemporaryDirectory(
            prefix=".charmm36-install-",
            dir=cache,
        ) as temporary:
            extraction_root = Path(temporary)
            source_handle.seek(0)
            with tarfile.open(fileobj=source_handle, mode="r:gz") as archive:
                members = _validate_archive_members(archive, extraction_root)
                archive.extractall(extraction_root, members=members)
            extracted = extraction_root / CHARMM36_DIRECTORY
            if not is_valid_force_field(extracted):
                raise ValueError(
                    "The CHARMM36m archive is incomplete or does not contain "
                    f"the expected {CHARMM36_DIRECTORY} directory."
                )
            _validate_force_field_tree(extracted, expected_tree_sha256)
            extracted.replace(destination)
    return destination


def download_charmm36_force_field(*, cache_root: Path | None = None) -> Path:
    """Explicit first-use download; simulations themselves remain offline."""
    with tempfile.TemporaryDirectory(prefix="moldynstudio-charmm-download-") as tmp:
        archive = Path(tmp) / CHARMM36_ARCHIVE_NAME
        deadline = time.monotonic() + 120
        with urlopen(CHARMM36_URL, timeout=30) as response, archive.open("wb") as output:
            total = 0
            while chunk := response.read(64 * 1024):
                if time.monotonic() > deadline:
                    raise TimeoutError("CHARMM36m download timed out. Retry or import a local package.")
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise ValueError("CHARMM36m download exceeds the size limit.")
                output.write(chunk)
        return install_charmm36_archive(archive, cache_root=cache_root)


def stage_charmm36_force_field(
    work_dir: str | Path,
    *,
    cache_root: Path | None = None,
    bundled_archive: Path | None = None,
    expected_tree_sha256: str | None = CHARMM36_TREE_SHA256,
) -> Path:
    """Copy cached/bundled CHARMM36m into a simulation project directory."""

    project = Path(work_dir)
    project.mkdir(parents=True, exist_ok=True)
    destination = project / CHARMM36_DIRECTORY
    if is_valid_force_field(destination):
        _validate_force_field_tree(destination, expected_tree_sha256)
        return destination
    if destination.exists():
        raise ValueError(
            f"Invalid CHARMM36m directory already exists: {destination}"
        )

    cached = cached_force_field_dir(cache_root)
    if is_valid_force_field(cached):
        _validate_force_field_tree(cached, expected_tree_sha256)
    if not is_valid_force_field(cached):
        archive = bundled_archive or bundled_archive_path()
        if archive.is_file():
            cached = install_charmm36_archive(
                archive,
                cache_root=cache_root,
                expected_tree_sha256=expected_tree_sha256,
            )
        else:
            raise ForceFieldUnavailableError(
                "CHARMM36m is not installed locally. Use 'Download CHARMM36m' "
                "in MD Setup, or 'Import package' and select the official "
                f"{CHARMM36_ARCHIVE_NAME} file."
            )

    shutil.copytree(cached, destination)
    return destination
