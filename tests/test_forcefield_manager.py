from __future__ import annotations

import io
from hashlib import sha256
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.forcefield_manager import (
    CHARMM36_ARCHIVE_NAME,
    CHARMM36_DIRECTORY,
    ForceFieldUnavailableError,
    MAX_ARCHIVE_MEMBERS,
    _validate_archive_members,
    install_charmm36_archive,
    stage_charmm36_force_field,
)


def _archive_bytes(*, unsafe_name: str | None = None) -> bytes:
    output = io.BytesIO()
    payload = b"; minimal force field fixture\n"
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        names = (
            [unsafe_name]
            if unsafe_name
            else [
                f"{CHARMM36_DIRECTORY}/forcefield.itp",
                f"{CHARMM36_DIRECTORY}/atomtypes.atp",
                f"{CHARMM36_DIRECTORY}/aminoacids.rtp",
                f"{CHARMM36_DIRECTORY}/tip3p.itp",
            ]
        )
        for name in names:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return output.getvalue()


class ForceFieldManagerTests(unittest.TestCase):
    def test_download_checks_size_and_uses_existing_verified_installer(self):
        from core.forcefield_manager import download_charmm36_force_field

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            response = io.BytesIO(b"downloaded archive")
            with (
                mock.patch("core.forcefield_manager.urlopen", return_value=response) as fetch,
                mock.patch("core.forcefield_manager.install_charmm36_archive") as install,
            ):
                download_charmm36_force_field(cache_root=cache)
                fetch.assert_called_once()
                self.assertEqual(fetch.call_args.kwargs["timeout"], 30)
                install.assert_called_once()
                self.assertEqual(install.call_args.kwargs["cache_root"], cache)
                self.assertFalse(install.call_args.args[0].exists())

            with (
                mock.patch("core.forcefield_manager.urlopen", return_value=io.BytesIO(b"12345")),
                mock.patch("core.forcefield_manager.MAX_DOWNLOAD_BYTES", 4),
                mock.patch("core.forcefield_manager.install_charmm36_archive") as install,
            ):
                with self.assertRaisesRegex(ValueError, "size limit"):
                    download_charmm36_force_field(cache_root=cache)
                install.assert_not_called()

    def test_download_never_installs_network_errors_or_corrupt_payloads(self):
        from core.forcefield_manager import download_charmm36_force_field

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            with mock.patch("core.forcefield_manager.urlopen", side_effect=OSError("offline")):
                with self.assertRaisesRegex(OSError, "offline"):
                    download_charmm36_force_field(cache_root=cache)
            with mock.patch("core.forcefield_manager.urlopen", return_value=io.BytesIO(b"bad")):
                with self.assertRaisesRegex(ValueError, "checksum"):
                    download_charmm36_force_field(cache_root=cache)
            self.assertFalse((cache / CHARMM36_DIRECTORY).exists())

    def test_imports_official_archive_to_persistent_cache(self):
        payload = _archive_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / CHARMM36_ARCHIVE_NAME
            archive.write_bytes(payload)

            installed = install_charmm36_archive(
                archive,
                cache_root=root / "cache",
                expected_sha256=sha256(payload).hexdigest(),
                expected_tree_sha256=None,
            )

            self.assertTrue((installed / "forcefield.itp").is_file())

    def test_rejects_archive_with_wrong_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / CHARMM36_ARCHIVE_NAME
            archive.write_bytes(_archive_bytes())

            with self.assertRaisesRegex(ValueError, "checksum"):
                install_charmm36_archive(
                    archive,
                    cache_root=root / "cache",
                    expected_sha256="0" * 64,
                )

    def test_rejects_archive_path_traversal(self):
        payload = _archive_bytes(unsafe_name="../outside.txt")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / CHARMM36_ARCHIVE_NAME
            archive.write_bytes(payload)

            with self.assertRaisesRegex(ValueError, "Unsafe path"):
                install_charmm36_archive(
                    archive,
                    cache_root=root / "cache",
                    expected_sha256=sha256(payload).hexdigest(),
                    expected_tree_sha256=None,
                )

    def test_rejects_archive_with_excessive_member_count(self):
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w:gz") as archive:
            for index in range(MAX_ARCHIVE_MEMBERS + 1):
                archive.addfile(tarfile.TarInfo(f"files/{index}"))
        payload.seek(0)

        with (
            tempfile.TemporaryDirectory() as tmp,
            tarfile.open(fileobj=payload, mode="r:gz") as archive,
            self.assertRaisesRegex(ValueError, "too many files"),
        ):
            _validate_archive_members(archive, Path(tmp))

    def test_rejects_link_or_reparse_point_as_archive_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / CHARMM36_ARCHIVE_NAME
            archive.write_bytes(_archive_bytes())

            with (
                mock.patch(
                    "core.forcefield_manager._is_link_or_reparse",
                    return_value=True,
                ),
                self.assertRaisesRegex(ValueError, "reparse points"),
            ):
                install_charmm36_archive(archive)

    def test_extracts_from_the_same_open_archive_handle_that_was_hashed(self):
        payload = _archive_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / CHARMM36_ARCHIVE_NAME
            archive.write_bytes(payload)

            with mock.patch(
                "core.forcefield_manager.tarfile.open",
                wraps=tarfile.open,
            ) as open_tar:
                install_charmm36_archive(
                    archive,
                    cache_root=root / "cache",
                    expected_sha256=sha256(payload).hexdigest(),
                    expected_tree_sha256=None,
                )

        self.assertIn("fileobj", open_tar.call_args.kwargs)
        self.assertNotIn("name", open_tar.call_args.kwargs)

    def test_rejects_force_field_cache_containing_links(self):
        payload = _archive_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / CHARMM36_ARCHIVE_NAME
            archive.write_bytes(payload)
            cache = root / "cache"
            install_charmm36_archive(
                archive,
                cache_root=cache,
                expected_sha256=sha256(payload).hexdigest(),
                expected_tree_sha256=None,
            )

            with (
                mock.patch(
                    "core.forcefield_manager._tree_contains_links",
                    return_value=True,
                ),
                self.assertRaisesRegex(ValueError, "links or junctions"),
            ):
                stage_charmm36_force_field(
                    root / "project",
                    cache_root=cache,
                    expected_tree_sha256=None,
                )

    def test_stages_cached_force_field_without_network(self):
        payload = _archive_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / CHARMM36_ARCHIVE_NAME
            archive.write_bytes(payload)
            cache = root / "cache"
            install_charmm36_archive(
                archive,
                cache_root=cache,
                expected_sha256=sha256(payload).hexdigest(),
                expected_tree_sha256=None,
            )

            with mock.patch("socket.socket") as socket_constructor:
                staged = stage_charmm36_force_field(
                    root / "project",
                    cache_root=cache,
                    bundled_archive=root / "missing.tgz",
                    expected_tree_sha256=None,
                )

            self.assertTrue((staged / "forcefield.itp").is_file())
            socket_constructor.assert_not_called()

    def test_rejects_modified_cached_force_field_tree(self):
        payload = _archive_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / CHARMM36_ARCHIVE_NAME
            archive.write_bytes(payload)
            cache = root / "cache"
            installed = install_charmm36_archive(
                archive,
                cache_root=cache,
                expected_sha256=sha256(payload).hexdigest(),
                expected_tree_sha256=None,
            )
            (installed / "forcefield.itp").write_text(
                "modified",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "integrity"):
                stage_charmm36_force_field(
                    root / "project",
                    cache_root=cache,
                    expected_tree_sha256="0" * 64,
                )

    def test_missing_local_package_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            with self.assertRaisesRegex(
                ForceFieldUnavailableError,
                "Import package",
            ):
                stage_charmm36_force_field(
                    root / "project",
                    cache_root=root / "cache",
                    bundled_archive=root / "missing.tgz",
                )


if __name__ == "__main__":
    unittest.main()
