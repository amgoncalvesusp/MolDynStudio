"""Assemble versioned release files and exact upstream sources without publishing."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tarfile
import tempfile
from urllib.parse import quote, urlparse
import urllib.request
import zipfile


SOURCE_HOSTS = frozenset({"files.pythonhosted.org", "download.qt.io", "codeload.github.com"})


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _request_json(url: str):
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.load(response)


def _safe_member(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or (path.parts and ":" in path.parts[0]):
        raise ValueError(f"Unsafe archive member: {name}")
    return path


def _source_record(package: dict, filename: str, url: str, checksum: str | None) -> dict:
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", package["name"])
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+!_-]*", package["version"])):
        raise ValueError("Invalid package name or version in the runtime manifest.")
    if Path(filename).name != filename or "/" in filename or "\\" in filename:
        raise ValueError("Unsafe upstream source filename.")
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in SOURCE_HOSTS:
        raise ValueError("Unrecognized upstream source host.")
    if checksum is not None and not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise ValueError("Invalid upstream source checksum.")
    return {"name": package["name"], "version": package["version"], "filename": filename,
            "url": url, "upstream_sha256": checksum}


def resolve_source(package: dict) -> dict:
    """Resolve published sdists, Qt's official archive, or the exact OpenMM commit."""
    name, version = package["name"], package["version"]
    if name == "openmm":
        revision = package.get("source_revision", "")
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("The OpenMM runtime manifest must include its exact source_revision.")
        record = _source_record(package, f"openmm-{revision}.tar.gz",
                                f"https://codeload.github.com/openmm/openmm/tar.gz/{revision}", None)
        return {**record, "source_revision": revision}
    if name in {"pyqt5-qt5", "pyqtwebengine-qt5"}:
        if not re.fullmatch(r"\d+\.\d+\.\d+", version):
            raise ValueError("Unsupported Qt source version.")
        filename = f"qt-everywhere-src-{version}.tar.xz"
        series = ".".join(version.split(".")[:2])
        url = f"https://download.qt.io/archive/qt/{series}/{version}/single/{filename}"
        with urllib.request.urlopen(url + ".sha256", timeout=60) as response:
            checksum = response.read(4096).decode("ascii").split()[0]
        return _source_record(package, filename, url, checksum)
    url = f"https://pypi.org/pypi/{quote(name, safe='')}/{quote(version, safe='')}/json"
    candidates = [item for item in _request_json(url)["urls"] if item["packagetype"] == "sdist"]
    if len(candidates) != 1:
        raise ValueError(f"Expected one source distribution for {name}=={version}; found {len(candidates)}.")
    source = candidates[0]
    return _source_record(package, source["filename"], source["url"], source["digests"]["sha256"])


def download_source(record: dict, cache: Path) -> Path:
    """Reuse only checksum-verified cache files; preserve corrupt files for review."""
    if Path(record["filename"]).name != record["filename"] or "\\" in record["filename"]:
        raise ValueError("Unsafe upstream source filename.")
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / record["filename"]
    receipt = destination.with_name(destination.name + ".sha256")
    expected = record["upstream_sha256"]
    if destination.exists():
        expected = expected or (receipt.read_text().strip() if receipt.is_file() else None)
        if expected is None or digest(destination) != expected:
            raise ValueError(f"Cached source checksum is missing or mismatched: {destination}")
        return destination
    with tempfile.NamedTemporaryFile(dir=cache, prefix="download-", delete=False) as handle:
        temporary = Path(handle.name)
        try:
            with urllib.request.urlopen(record["url"], timeout=60) as response:
                shutil.copyfileobj(response, handle)
        except BaseException:
            handle.close()
            temporary.unlink(missing_ok=True)
            raise
    actual = digest(temporary)
    if expected is not None and actual != expected:
        temporary.unlink()
        raise ValueError(f"Downloaded source checksum mismatch: {record['filename']}")
    temporary.replace(destination)
    receipt.write_text(actual + "\n", encoding="ascii")
    return destination


def _is_notice(path: PurePosixPath) -> bool:
    return any(word in path.name.lower() for word in (
        "license", "licence", "copying", "notice", "copyright", "readme.chromium", "gpl_exception",
    )) or any(part.lower() in {"licenses", "licences"} for part in path.parts) or (
        len(path.parts) <= 2 and path.name.lower().startswith("readme")
    )


def archive_notices(source: Path):
    """Read original notice bytes without extracting or following archive links."""
    if zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            for member in archive.infolist():
                path = _safe_member(member.filename)
                if member.is_dir() or stat.S_ISLNK(member.external_attr >> 16) or not _is_notice(path):
                    continue
                if member.file_size > 32 * 1024 * 1024:
                    raise ValueError(f"Oversized notice: {member.filename}")
                yield path.as_posix(), archive.read(member)
        return
    with tarfile.open(source, "r|*") as archive:
        for member in archive:
            path = _safe_member(member.name)
            if not member.isfile() or not _is_notice(path):
                continue
            if member.size > 32 * 1024 * 1024:
                raise ValueError(f"Oversized notice: {member.name}")
            with archive.extractfile(member) as handle:
                yield path.as_posix(), handle.read()


def validate_application_archive(source: Path) -> None:
    with zipfile.ZipFile(source) as archive:
        for member in archive.infolist():
            path = _safe_member(member.filename)
            if ".worktrees" in path.parts:
                raise ValueError("Application source archive includes unrelated .worktrees content.")


def _write_source_materials(stage: Path, windows: Path, records: tuple, paths: tuple, version: str) -> None:
    manifest = json.dumps(records, indent=2) + "\n"
    sources_zip = stage / f"MolDynStudio-third-party-sources-{version}.zip"
    with zipfile.ZipFile(sources_zip, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("SOURCES.json", manifest)
        unique = {path.name: path for path in paths}
        for name, path in sorted(unique.items()):
            if name.startswith("qt-everywhere-src-"):
                shutil.copy2(path, stage / name)
            else:
                archive.write(path, name)
    with zipfile.ZipFile(windows, "a", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("licenses/SOURCES.json", manifest)
        for record, path in zip(records, paths):
            prefix = f"licenses/source-notices/{record['name']}-{record['version']}/"
            for name, content in archive_notices(path):
                archive.writestr(prefix + name, content)


def prepare_release(version: str, revision: str, windows: Path, linux: Path, source: Path,
                    output: Path, cache: Path) -> Path:
    if not re.fullmatch(r"v\d+\.\d+\.\d+(?:[.-][A-Za-z0-9]+)*", version):
        raise ValueError("Use a version tag such as v1.2.0.")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("An exact application Git revision is required.")
    validate_application_archive(source)
    with zipfile.ZipFile(windows) as archive:
        if "licenses/SOURCES.json" in archive.namelist():
            raise ValueError("Use the fresh Windows build archive, not an already assembled release archive.")
        packages = json.loads(archive.read("licenses/manifest.json"))
    records = tuple(resolve_source(package) for package in packages)
    paths = tuple(download_source(record, cache) for record in records)
    records = tuple({**record, "sha256": digest(path)} for record, path in zip(records, paths))
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="assembly-", dir=output) as directory:
        stage = Path(directory)
        windows_copy = stage / f"MolDynStudio-windows-x86_64-{version}.zip"
        shutil.copy2(windows, windows_copy)
        shutil.copy2(linux, stage / f"MolDynStudio-linux-x86_64-{version}.run")
        shutil.copy2(source, stage / f"MolDynStudio-source-{version}.zip")
        _write_source_materials(stage, windows_copy, records, paths, version)
        provenance = {"version": version, "source_revision": revision,
                      "build_inputs": [{"name": path.name, "sha256": digest(path)}
                                       for path in (windows, linux, source)]}
        (stage / f"RELEASE-PROVENANCE-{version}.json").write_text(
            json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
        sums = "".join(f"{digest(path)}  {path.name}\n" for path in sorted(stage.iterdir()))
        (stage / f"SHA256SUMS-{version}.txt").write_text(sums, encoding="ascii")
        if any((output / path.name).exists() for path in stage.iterdir()):
            raise FileExistsError("Release output already contains an artifact with this name; nothing replaced.")
        for path in stage.iterdir():
            path.replace(output / path.name)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("version", "revision"):
        parser.add_argument("--" + option, required=True)
    for option in ("windows", "linux", "source", "output", "cache"):
        parser.add_argument("--" + option, type=Path, required=True)
    arguments = parser.parse_args()
    result = prepare_release(**vars(arguments))
    print(f"Release files prepared without publishing: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
