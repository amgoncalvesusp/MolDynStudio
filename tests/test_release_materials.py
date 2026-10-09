from pathlib import Path
import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile

from build import prepare_release_materials as release


class ReleaseMaterialsTests(unittest.TestCase):
    def test_openmm_requires_exact_wheel_revision_instead_of_assuming_version_tag(self):
        revision = "b399af4725573963b46d6c1083fdcf7a37615857"
        result = release.resolve_source({"name": "openmm", "version": "8.6.1", "source_revision": revision})
        self.assertEqual(result["url"], f"https://codeload.github.com/openmm/openmm/tar.gz/{revision}")
        self.assertEqual(result["source_revision"], revision)
        self.assertIsNone(result["upstream_sha256"])
        with self.assertRaisesRegex(ValueError, "revision"):
            release.resolve_source({"name": "openmm", "version": "8.6.1"})

    def test_pypi_resolution_uses_exact_version_and_validated_sdist(self):
        url = "https://files.pythonhosted.org/packages/example/example-1.2.3.tar.gz"
        payload = {"urls": [{"packagetype": "sdist", "filename": "example-1.2.3.tar.gz",
                              "url": url, "digests": {"sha256": "1" * 64}}]}
        with mock.patch.object(release, "_request_json", return_value=payload) as request:
            source = release.resolve_source({"name": "example", "version": "1.2.3"})
        request.assert_called_once_with("https://pypi.org/pypi/example/1.2.3/json")
        self.assertEqual(source["upstream_sha256"], "1" * 64)
        payload["urls"][0]["url"] = "https://untrusted.example/source.tar.gz"
        with mock.patch.object(release, "_request_json", return_value=payload):
            with self.assertRaisesRegex(ValueError, "host"):
                release.resolve_source({"name": "example", "version": "1.2.3"})

    def test_corrupt_cached_source_is_never_silently_reused_or_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "example.tar.gz"
            source.write_bytes(b"corrupt")
            record = {"filename": source.name, "url": "https://files.pythonhosted.org/example.tar.gz",
                      "upstream_sha256": "1" * 64}
            with self.assertRaisesRegex(ValueError, "checksum"):
                release.download_source(record, root)
            self.assertEqual(source.read_bytes(), b"corrupt")

    def test_download_checks_digest_and_reuses_verified_cache_without_network(self):
        content = b"original upstream source archive"
        record = {"filename": "example.tar.gz", "url": "https://files.pythonhosted.org/example.tar.gz",
                  "upstream_sha256": hashlib.sha256(content).hexdigest()}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(release.urllib.request, "urlopen", return_value=io.BytesIO(content)):
                result = release.download_source(record, root)
            self.assertEqual(result.read_bytes(), content)
            with mock.patch.object(release.urllib.request, "urlopen", side_effect=AssertionError("offline cache")):
                self.assertEqual(release.download_source(record, root), result)
            mismatched = {**record, "filename": "mismatched.tar.gz", "upstream_sha256": "1" * 64}
            with mock.patch.object(release.urllib.request, "urlopen", return_value=io.BytesIO(content)):
                with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                    release.download_source(mismatched, root)
            self.assertFalse((root / "mismatched.tar.gz").exists())
            self.assertFalse(tuple(root.glob("download-*")))

    def test_commit_archive_cache_requires_its_recorded_download_digest(self):
        content = b"exact commit archive"
        record = {"filename": "openmm-commit.tar.gz", "url": "https://codeload.github.com/openmm/openmm/tar.gz/commit",
                  "upstream_sha256": None}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(release.urllib.request, "urlopen", return_value=io.BytesIO(content)):
                result = release.download_source(record, root)
            self.assertEqual(release.download_source(record, root), result)
            (root / (result.name + ".sha256")).unlink()
            with self.assertRaisesRegex(ValueError, "checksum"):
                release.download_source(record, root)

    def test_qt_source_uses_official_published_checksum(self):
        checksum = "3" * 64
        with mock.patch.object(release.urllib.request, "urlopen",
                               return_value=io.BytesIO((checksum + "  qt-everywhere-src-5.15.2.tar.xz\n").encode())):
            result = release.resolve_source({"name": "pyqt5-qt5", "version": "5.15.2"})
        self.assertEqual(result["upstream_sha256"], checksum)
        self.assertEqual(result["filename"], "qt-everywhere-src-5.15.2.tar.xz")
        with self.assertRaisesRegex(ValueError, "version"):
            release.resolve_source({"name": "pyqt5-qt5", "version": "5.15.2/invalid"})

    def test_source_notice_reader_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "bad.zip"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("../LICENSE", "untrusted")
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                tuple(release.archive_notices(source))

    def test_root_readme_license_notices_are_retained_when_no_license_file_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.zip"
            content = b"# Library\n## License\nCopyright original author\nLGPL\n"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("package-1.2.3/README.md", content)
            self.assertEqual(tuple(release.archive_notices(source)), (("package-1.2.3/README.md", content),))

    def test_complete_assembly_preserves_notices_and_writes_correct_digests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            windows, linux, application = root / "windows.zip", root / "linux.run", root / "source.zip"
            with zipfile.ZipFile(windows, "w") as archive:
                archive.writestr("MolDynStudio.exe", b"fixture exe")
                archive.writestr("licenses/manifest.json", json.dumps([{"name": "example", "version": "1.2.3"}]))
            linux.write_bytes(b"#!/bin/bash\nexit 0\n")
            with zipfile.ZipFile(application, "w") as archive:
                archive.writestr("main.py", "# source\n")
            upstream = root / "example-1.2.3.tar.gz"
            with tarfile.open(upstream, "w:gz") as archive:
                content = b"Copyright example\r\nLicense text\n"
                member = tarfile.TarInfo("example-1.2.3/LICENSE")
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
            record = {"name": "example", "version": "1.2.3", "filename": upstream.name,
                      "url": "https://files.pythonhosted.org/example.tar.gz",
                      "upstream_sha256": hashlib.sha256(upstream.read_bytes()).hexdigest()}
            original_windows = windows.read_bytes()
            with (mock.patch.object(release, "resolve_source", return_value=record),
                  mock.patch.object(release, "download_source", return_value=upstream)):
                output = release.prepare_release("v1.2.0", "a" * 40, windows, linux, application,
                                                 root / "output", root / "cache")
            with zipfile.ZipFile(output / "MolDynStudio-windows-x86_64-v1.2.0.zip") as archive:
                self.assertEqual(archive.read("licenses/source-notices/example-1.2.3/example-1.2.3/LICENSE"), content)
                self.assertIn("licenses/SOURCES.json", archive.namelist())
            for line in (output / "SHA256SUMS-v1.2.0.txt").read_text().splitlines():
                digest, name = line.split("  ", 1)
                self.assertEqual(hashlib.sha256((output / name).read_bytes()).hexdigest(), digest)
            self.assertEqual(windows.read_bytes(), original_windows)
            previous = {path.name: path.read_bytes() for path in output.iterdir()}
            with (mock.patch.object(release, "resolve_source", return_value=record),
                  mock.patch.object(release, "download_source", return_value=upstream)):
                with self.assertRaisesRegex(FileExistsError, "nothing replaced"):
                    release.prepare_release("v1.2.0", "a" * 40, windows, linux, application,
                                            output, root / "cache")
            self.assertEqual({path.name: path.read_bytes() for path in output.iterdir()}, previous)

    def test_source_archive_must_exclude_unrelated_worktrees(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.zip"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("MolDynStudio/.worktrees/unrelated/main.py", "do not publish")
            with self.assertRaisesRegex(ValueError, "worktrees"):
                release.validate_application_archive(source)
