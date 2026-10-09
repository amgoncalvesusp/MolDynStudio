"""Consent-gated, resumable client for the documented SwissParam MATCH API.

Downloaded files are data only. Chemical identity and charge must additionally
be checked by the caller before converting the returned CHARMM parameters.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import re
import stat
import tarfile
import tempfile
import time
import uuid
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path, PurePosixPath
from urllib.request import HTTPRedirectHandler, Request, build_opener

ENDPOINT = "https://www.swissparam.ch:8443"
METHOD = "SwissParam MATCH (CGenFF-derived)"
SCHEMA_VERSION = 1
MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_MEMBER_BYTES = 16 * 1024 * 1024
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_MEMBERS = 256
MAX_TEXT_BYTES = 64 * 1024
REQUIRED_SUFFIXES = (".rtf", ".par", ".prm", ".mol2")


class SwissParamError(RuntimeError):
    """Parameterization could not be completed or verified safely."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SwissParamError("SwissParam redirected the request; upload was stopped.")


def _https(request: Request, max_bytes: int) -> bytes:
    with build_opener(_NoRedirect()).open(request, timeout=30) as response:
        if response.status != 200:
            raise SwissParamError(f"SwissParam HTTP status {response.status}.")
        return response.read(max_bytes + 1)


def _read(path: Path, limit: int) -> bytes:
    details = path.lstat()
    if not stat.S_ISREG(details.st_mode) or _is_link(path) or details.st_size > limit:
        raise SwissParamError("SwissParam file integrity or size limit check failed.")
    with path.open("rb") as handle:
        result = handle.read(limit + 1)
    if len(result) > limit:
        raise SwissParamError("SwissParam response exceeds its size limit.")
    return result


def _is_link(path: Path) -> bool:
    details = path.lstat()
    return stat.S_ISLNK(details.st_mode) or bool(
        getattr(details, "st_reparse_tag", 0) & 0x20000000
    )


def _atomic_write(path: Path, data: bytes) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".swissparam-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _safe_relative(name: str) -> str:
    path = PurePosixPath(name)
    if path.is_absolute() or "\\" in name or not path.parts:
        raise SwissParamError("Unsafe path in SwissParam package.")
    for part in path.parts:
        stem = part.split(".")[0].upper()
        if (part == ".." or any(char in part for char in ':<>"|?*') or part.endswith((".", " "))
                or any(ord(char) < 32 for char in part)
                or stem in {"CON", "PRN", "AUX", "NUL"}
                or re.fullmatch(r"(?:COM|LPT)[0-9]", stem)):
            raise SwissParamError("Unsafe path in SwissParam package.")
    return path.as_posix()


def _validate_package(files: dict[str, bytes]) -> None:
    for suffix in REQUIRED_SUFFIXES:
        if suffix != ".mol2" and sum(name.lower().endswith(suffix) for name in files) != 1:
            raise SwissParamError(f"SwissParam MATCH package needs exactly one {suffix} file.")
    topology = next(name for name in files if name.lower().endswith(".rtf"))
    if str(PurePosixPath(topology).with_suffix(".mol2")) not in files:
        raise SwissParamError("SwissParam MATCH package lacks the topology's MOL2 file.")
    logs = [data for name, data in files.items() if PurePosixPath(name).name == "MATCH.log"]
    if len(logs) != 1 or not re.search(rb"\bSuccess!\s*$", logs[0]):
        raise SwissParamError("SwissParam MATCH did not report successful parameterization.")
    for name, data in files.items():
        if name.lower().endswith((".rtf", ".par", ".prm")) and re.search(
            rb"(?im)^\s*(?:stream\b|system\b|#?include\b|open\s+.*\bname\b)", data
        ):
            raise SwissParamError("SwissParam parameters contain unsupported external directives.")


def _archive_files(payload: bytes) -> dict[str, bytes]:
    files, seen, total = {}, set(), 0
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(payload)) as compressed:
            unpacked = compressed.read(MAX_EXPANDED_BYTES + 1)
        if len(unpacked) > MAX_EXPANDED_BYTES:
            raise SwissParamError("SwissParam expanded archive exceeds its size limit.")
        with tarfile.open(fileobj=io.BytesIO(unpacked), mode="r:") as archive:
            for count, member in enumerate(archive, start=1):
                if count > MAX_MEMBERS:
                    raise SwissParamError("SwissParam archive contains too many members.")
                if member.isdir() and member.name in (".", "./"):
                    continue
                name = _safe_relative(member.name)
                if name.casefold() in seen:
                    raise SwissParamError("SwissParam archive contains duplicate paths.")
                seen.add(name.casefold())
                if not (member.isfile() or member.isdir()):
                    raise SwissParamError("SwissParam archive contains links or special files.")
                if member.isdir():
                    continue
                total += member.size
                if not 0 <= member.size <= MAX_MEMBER_BYTES or total > MAX_EXPANDED_BYTES:
                    raise SwissParamError("SwissParam expanded archive exceeds its size limit.")
                handle = archive.extractfile(member)
                if handle is None:
                    raise SwissParamError("SwissParam archive contains an unreadable member.")
                with handle:
                    files[name] = handle.read(MAX_MEMBER_BYTES + 1)
                if len(files[name]) != member.size:
                    raise SwissParamError("SwissParam archive member is incomplete.")
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise SwissParamError("SwissParam returned an invalid results archive.") from exc
    if any(PurePosixPath(name).parts[0].casefold() == "provenance.json" for name in files):
        raise SwissParamError("SwissParam archive uses a reserved provenance path.")
    regular_paths = {name.casefold() for name in files}
    if any(parent.as_posix().casefold() in regular_paths
           for name in files for parent in PurePosixPath(name).parents):
        raise SwissParamError("SwissParam archive contains conflicting file and directory paths.")
    _validate_package(files)
    return files


def _verified_cache(package: Path, expected: dict) -> Path:
    try:
        if _is_link(package):
            raise SwissParamError("Linked cache directory.")
        manifest = json.loads(_read(package / "provenance.json", MAX_TEXT_BYTES))
        if not isinstance(manifest, dict) or any(manifest.get(key) != value for key, value in expected.items()):
            raise SwissParamError("Unexpected provenance.")
        hashes = manifest["output_sha256"]
        if not isinstance(hashes, dict) or not hashes or len(hashes) > MAX_MEMBERS:
            raise SwissParamError("Invalid cached file list.")
        files = {}
        for root, directories, names in os.walk(package, followlinks=False):
            for name in (*directories, *names):
                if _is_link(Path(root) / name):
                    raise SwissParamError("Linked cache content.")
            for name in names:
                path = Path(root) / name
                relative = path.relative_to(package).as_posix()
                if relative != "provenance.json":
                    files[_safe_relative(relative)] = _read(path, MAX_MEMBER_BYTES)
                    if len(files) > MAX_MEMBERS or sum(map(len, files.values())) > MAX_EXPANDED_BYTES:
                        raise SwissParamError("Cached files exceed package limits.")
        if {name: sha256(data).hexdigest() for name, data in files.items()} != hashes:
            raise SwissParamError("Changed cached files.")
        archive = _read(package.parent / "results.tar.gz", MAX_ARCHIVE_BYTES)
        archive_hash = sha256(archive).hexdigest()
        if archive_hash != manifest["archive_sha256"]:
            raise SwissParamError("Changed cached archive.")
        if _archive_files(archive) != files:
            raise SwissParamError("Cached files do not match the downloaded archive.")
        if sha256(_read(package.parent / "input.mol2", MAX_INPUT_BYTES)).hexdigest() != expected["input_sha256"]:
            raise SwissParamError("Changed cached input.")
        return package
    except (OSError, ValueError, KeyError, TypeError, SwissParamError) as exc:
        raise SwissParamError("SwissParam cache integrity check failed; preserve and inspect this cache.") from exc


class SwissParamClient:
    """MATCH-only client; transport receives a Request and maximum response size."""

    def __init__(self, *, transport=None, sleep=None, monotonic=None, max_polls=180,
                 poll_interval=5.0, max_wait=900.0):
        if max_polls < 1 or poll_interval < 0 or max_wait <= 0:
            raise ValueError("SwissParam polling limits must be positive.")
        self.transport = transport or _https
        self.sleep = sleep or time.sleep
        self.monotonic = monotonic or time.monotonic
        self.max_polls, self.poll_interval, self.max_wait = max_polls, poll_interval, max_wait

    def _request(self, route: str, *, data=None, headers=None, limit=MAX_TEXT_BYTES) -> bytes:
        request = Request(ENDPOINT + route, data=data, headers=headers or {})
        try:
            result = self.transport(request, limit)
        except (OSError, TimeoutError) as exc:
            raise SwissParamError("SwissParam network request failed; retry to resume any saved session.") from exc
        if not isinstance(result, bytes) or len(result) > limit:
            raise SwissParamError("SwissParam response exceeds its size limit.")
        return result

    def _submit(self, payload: bytes) -> str:
        boundary = "MolDynStudio" + uuid.uuid4().hex
        data = (f'--{boundary}\r\nContent-Disposition: form-data; name="myMol2"; '
                'filename="ligand.mol2"\r\nContent-Type: chemical/x-mol2\r\n\r\n').encode()
        data += payload + f"\r\n--{boundary}--\r\n".encode()
        response = self._request("/startparam?approach=match", data=data,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        match = re.search(rb"(?m)^Session number: ([0-9]{1,20})\s*$", response)
        if not match:
            raise SwissParamError("SwissParam did not return a valid session number.")
        return match[1].decode("ascii")

    def _wait(self, session: str, on_log) -> None:
        started = self.monotonic()
        for _ in range(self.max_polls):
            if self.monotonic() - started >= self.max_wait:
                break
            status = self._request(f"/checksession?sessionNumber={session}").decode("utf-8", errors="replace")
            if re.search(r"(?im)^\s*(?:error\b|.*\b(?:failed|cancelled|expired)\b)", status):
                raise SwissParamError("SwissParam reported a failed, cancelled, or expired session.")
            if re.search(r"(?m)^Calculation is finished\.?\s*$", status):
                return
            if not re.search(r"(?m)^Calculation (?:is in the queue|currently running)\b", status):
                raise SwissParamError("SwissParam returned an unrecognized session status.")
            if on_log:
                on_log("SwissParam MATCH parameterization is queued or running.")
            self.sleep(self.poll_interval)
        raise SwissParamError("SwissParam timed out; retry to resume the saved session.")

    def parameterize_match(self, mol2_path: Path, output_dir: Path, *, allow_upload: bool,
                           on_log=None) -> Path:
        """Return a verified package; no network occurs without explicit consent."""
        payload = _read(Path(mol2_path), MAX_INPUT_BYTES)
        if payload.count(b"@<TRIPOS>MOLECULE") != 1 or b"@<TRIPOS>ATOM" not in payload:
            raise SwissParamError("SwissParam requires one explicit, hydrogenated MOL2 molecule.")
        expected = {"schema_version": SCHEMA_VERSION, "method": METHOD, "endpoint": ENDPOINT,
                    "input_sha256": sha256(payload).hexdigest()}
        cache = Path(output_dir) / f"match-v{SCHEMA_VERSION}-{expected['input_sha256']}"
        package = cache / "package"
        if cache.exists() and _is_link(cache):
            raise SwissParamError("SwissParam cache integrity check rejected a linked directory.")
        if package.exists():
            return _verified_cache(package, expected)
        if not allow_upload:
            raise SwissParamError("SwissParam upload consent is required for an uncached ligand.")
        cache.mkdir(parents=True, exist_ok=True)
        state_path = cache / "session.json"
        if state_path.exists():
            try:
                state = json.loads(_read(state_path, MAX_TEXT_BYTES))
                if not isinstance(state, dict) or any(state.get(key) != value for key, value in expected.items()):
                    raise ValueError("Mismatched session provenance.")
                session = state["session"]
                if not isinstance(session, str) or not re.fullmatch(r"[0-9]{1,20}", session):
                    raise ValueError("Invalid session identifier.")
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise SwissParamError("SwissParam saved session integrity check failed.") from exc
        else:
            _atomic_write(cache / "input.mol2", payload)
            session = self._submit(payload)
            _atomic_write(state_path, _json_bytes({**expected, "session": session}))
        self._wait(session, on_log)
        archive = self._request(f"/retrievesession?sessionNumber={session}", limit=MAX_ARCHIVE_BYTES)
        files = _archive_files(archive)
        provenance = {**expected, "session": session, "retrieved_at": datetime.now(timezone.utc).isoformat(),
                      "archive_sha256": sha256(archive).hexdigest(),
                      "output_sha256": {name: sha256(data).hexdigest() for name, data in files.items()}}
        _atomic_write(cache / "results.tar.gz", archive)
        with tempfile.TemporaryDirectory(prefix=".match-", dir=cache) as temporary:
            staged = Path(temporary) / "package"
            staged.mkdir()
            for name, data in files.items():
                destination = staged / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
            _atomic_write(staged / "provenance.json", _json_bytes(provenance))
            staged.replace(package)
        return _verified_cache(package, expected)


def parameterize_match(mol2_path: Path, output_dir: Path, *, allow_upload: bool,
                       on_log=None) -> Path:
    """Parameterize an explicit MOL2 with SwissParam MATCH or reuse verified cache."""
    return SwissParamClient().parameterize_match(
        mol2_path, output_dir, allow_upload=allow_upload, on_log=on_log
    )
