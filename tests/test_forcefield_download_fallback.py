"""Timeout fallback retains verified HTTPS and archive installation boundaries."""
from __future__ import annotations

import io
import ssl
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError, URLError

from core import forcefield_manager as fields


class ForceFieldDownloadFallbackTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.cache = Path(temporary.name) / "cache"

    def write_curl_output(self, command, **_):
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "curl 8.5.0 (Linux) libcurl/8.5.0\n", "")
        self.command = command
        self.output = Path(command[command.index("--output") + 1])
        self.output.write_bytes(b"downloaded official bytes")
        return subprocess.CompletedProcess(command, 0, "", "")

    def test_timeout_or_reset_uses_bounded_https_curl_and_verified_installer(self):
        errors = (TimeoutError("handshake timeout"), URLError(TimeoutError("handshake timeout")),
                  ConnectionResetError("connection reset"), URLError(ConnectionResetError("connection reset")))
        for error in errors:
            with (
                self.subTest(error=error),
                mock.patch.object(fields, "urlopen", side_effect=error),
                mock.patch.object(fields.shutil, "which", return_value="curl.exe"),
                mock.patch("subprocess.run", side_effect=self.write_curl_output) as run,
                mock.patch.object(fields, "install_charmm36_archive", return_value=self.cache) as install,
            ):
                self.assertEqual(fields.download_charmm36_force_field(cache_root=self.cache), self.cache)
                command = run.call_args.args[0]
                self.assertEqual(run.call_count, 2)
                self.assertEqual(run.call_args_list[0].args[0], ["curl.exe", "--disable", "--version"])
                self.assertLessEqual(run.call_args_list[0].kwargs["timeout"], 5)
                self.assertEqual(command[0:2], ["curl.exe", "--disable"])
                self.assertEqual(command[command.index("--proto") + 1], "=https")
                self.assertEqual(command[command.index("--proto-redir") + 1], "=https")
                self.assertIn("--fail", command)
                self.assertIn("--location", command)
                self.assertIn("--http1.1", command)
                self.assertNotIn("--tls-max", command)
                self.assertNotIn("--insecure", command)
                self.assertNotIn("-k", command)
                self.assertEqual(command[command.index("--max-filesize") + 1], str(fields.MAX_DOWNLOAD_BYTES))
                self.assertLessEqual(float(command[command.index("--connect-timeout") + 1]), 30)
                self.assertGreater(float(command[command.index("--max-time") + 1]), 0)
                self.assertLessEqual(float(command[command.index("--max-time") + 1]), 120)
                self.assertEqual(command[-2:], ["--", fields.CHARMM36_URL])
                self.assertIs(run.call_args.kwargs["shell"], False)
                self.assertIs(run.call_args.kwargs["check"], True)
                self.assertLessEqual(run.call_args.kwargs["timeout"], 120)
                install.assert_called_once_with(self.output, cache_root=self.cache)
                self.assertFalse(self.output.exists(), "Temporary download must be removed.")

    def test_partial_primary_body_is_removed_before_curl(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.side_effect = [b"partial", TimeoutError("body timed out")]

        def write_fresh(command, **kwargs):
            if command[-1] == "--version":
                return self.write_curl_output(command, **kwargs)
            output = Path(command[command.index("--output") + 1])
            self.assertFalse(output.exists(), "Fallback must not reuse a partial archive.")
            return self.write_curl_output(command, **kwargs)

        with (
            mock.patch.object(fields, "urlopen", return_value=response),
            mock.patch.object(fields.shutil, "which", return_value="curl.exe"),
            mock.patch("subprocess.run", side_effect=write_fresh),
            mock.patch.object(fields, "install_charmm36_archive") as install,
        ):
            fields.download_charmm36_force_field(cache_root=self.cache)
            install.assert_called_once()

    def test_certificate_http_and_other_network_failures_do_not_use_curl(self):
        errors = (URLError(ssl.SSLCertVerificationError("bad certificate")),
                  HTTPError(fields.CHARMM36_URL, 403, "Forbidden", None, None),
                  URLError("DNS failure"), OSError("offline"))
        for error in errors:
            with (
                self.subTest(error=error),
                mock.patch.object(fields, "urlopen", side_effect=error),
                mock.patch("subprocess.run") as run,
            ):
                with self.assertRaises(type(error)):
                    fields.download_charmm36_force_field(cache_root=self.cache)
                run.assert_not_called()

    def test_missing_curl_reports_failure_for_timeout_and_reset(self):
        for error in (TimeoutError("handshake timeout"), ConnectionResetError("connection reset")):
            with (
                self.subTest(error=error),
                mock.patch.object(fields, "urlopen", side_effect=error),
                mock.patch.object(fields.shutil, "which", return_value=None),
                mock.patch("subprocess.run") as run,
                mock.patch.object(fields, "install_charmm36_archive") as install,
            ):
                with self.assertRaisesRegex(OSError, "download failed.*curl.*unavailable"):
                    fields.download_charmm36_force_field(cache_root=self.cache)
                run.assert_not_called()
                install.assert_not_called()

    def test_failed_curl_never_installs_and_reports_failure(self):
        failures = (subprocess.CalledProcessError(22, ["curl"], stderr="HTTP 503"),
                    subprocess.TimeoutExpired(["curl"], 90), OSError("cannot start curl"))
        for failure in failures:
            def fail_transfer(command, **kwargs):
                if command[-1] == "--version":
                    return self.write_curl_output(command, **kwargs)
                raise failure

            with (
                self.subTest(failure=failure),
                mock.patch.object(fields, "urlopen", side_effect=TimeoutError("handshake timeout")),
                mock.patch.object(fields.shutil, "which", return_value="curl.exe"),
                mock.patch("subprocess.run", side_effect=fail_transfer),
                mock.patch.object(fields, "install_charmm36_archive") as install,
            ):
                with self.assertRaisesRegex((OSError, RuntimeError), "HTTPS.*failed|HTTPS.*timed out"):
                    fields.download_charmm36_force_field(cache_root=self.cache)
                install.assert_not_called()

    def test_oversized_fallback_is_rejected_before_installing(self):
        with (
            mock.patch.object(fields, "urlopen", side_effect=TimeoutError("handshake timeout")),
            mock.patch.object(fields.shutil, "which", return_value="curl.exe"),
            mock.patch("subprocess.run", side_effect=self.write_curl_output),
            mock.patch.object(fields, "MAX_DOWNLOAD_BYTES", 4),
            mock.patch.object(fields, "install_charmm36_archive") as install,
        ):
            with self.assertRaisesRegex(ValueError, "size limit"):
                fields.download_charmm36_force_field(cache_root=self.cache)
            install.assert_not_called()

    def test_corrupt_fallback_still_fails_original_checksum_boundary(self):
        with (
            mock.patch.object(fields, "urlopen", side_effect=TimeoutError("handshake timeout")),
            mock.patch.object(fields.shutil, "which", return_value="curl.exe"),
            mock.patch("subprocess.run", side_effect=self.write_curl_output),
        ):
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                fields.download_charmm36_force_field(cache_root=self.cache)
            self.assertFalse(self.cache.exists())

    def test_primary_success_does_not_use_fallback(self):
        with (
            mock.patch.object(fields, "urlopen", return_value=io.BytesIO(b"official archive")),
            mock.patch("subprocess.run") as run,
            mock.patch.object(fields, "install_charmm36_archive") as install,
        ):
            fields.download_charmm36_force_field(cache_root=self.cache)
            run.assert_not_called()
            install.assert_called_once()

    def test_expired_total_deadline_does_not_start_another_download(self):
        with (
            mock.patch.object(fields, "urlopen", side_effect=TimeoutError("handshake timeout")),
            mock.patch.object(fields.time, "monotonic", side_effect=(0, 121)),
            mock.patch("subprocess.run") as run,
        ):
            with self.assertRaisesRegex(TimeoutError, "handshake timeout"):
                fields.download_charmm36_force_field(cache_root=self.cache)
            run.assert_not_called()

    def test_primary_body_deadline_does_not_extend_budget_with_curl(self):
        with (
            mock.patch.object(fields, "urlopen", return_value=io.BytesIO(b"body")),
            mock.patch.object(fields.time, "monotonic", side_effect=(0, 121, 122)),
            mock.patch("subprocess.run") as run,
            mock.patch.object(fields, "install_charmm36_archive") as install,
        ):
            with self.assertRaisesRegex(TimeoutError, "download timed out"):
                fields.download_charmm36_force_field(cache_root=self.cache)
            run.assert_not_called()
            install.assert_not_called()

    def test_primary_oversized_or_corrupt_body_does_not_use_curl(self):
        for limit, message in ((4, "size limit"), (fields.MAX_DOWNLOAD_BYTES, "checksum mismatch")):
            with (
                self.subTest(limit=limit),
                mock.patch.object(fields, "urlopen", return_value=io.BytesIO(b"bad archive bytes")),
                mock.patch.object(fields, "MAX_DOWNLOAD_BYTES", limit),
                mock.patch("subprocess.run") as run,
            ):
                with self.assertRaisesRegex(ValueError, message):
                    fields.download_charmm36_force_field(cache_root=self.cache)
                run.assert_not_called()

    def test_old_or_malformed_curl_version_refuses_transfer(self):
        for version in ("curl 8.3.0 libcurl/8.3.0", "curl 7.88.1", "curl 8.4", "unknown client"):
            with (
                self.subTest(version=version),
                mock.patch.object(fields, "urlopen", side_effect=TimeoutError("handshake timeout")),
                mock.patch.object(fields.shutil, "which", return_value="curl.exe"),
                mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, version, "")) as run,
                mock.patch.object(fields, "install_charmm36_archive") as install,
            ):
                with self.assertRaisesRegex(RuntimeError, "curl 8.4.0.*update|update.*curl 8.4.0"):
                    fields.download_charmm36_force_field(cache_root=self.cache)
                self.assertEqual(run.call_count, 1)
                self.assertEqual(run.call_args.args[0], ["curl.exe", "--disable", "--version"])
                install.assert_not_called()

    def test_version_probe_deducts_time_from_transfer_budget(self):
        with (
            mock.patch.object(fields, "urlopen", side_effect=TimeoutError("handshake timeout")),
            mock.patch.object(fields.shutil, "which", return_value="curl.exe"),
            mock.patch.object(fields.time, "monotonic", side_effect=(0, 30, 30, 32)),
            mock.patch("subprocess.run", side_effect=self.write_curl_output) as run,
            mock.patch.object(fields, "install_charmm36_archive"),
        ):
            fields.download_charmm36_force_field(cache_root=self.cache)
            probe, transfer = run.call_args_list
            self.assertEqual(probe.kwargs["timeout"], 5)
            self.assertEqual(transfer.kwargs["timeout"], 88)
            command = transfer.args[0]
            self.assertEqual(float(command[command.index("--max-time") + 1]), 88)

    def test_expired_or_failed_version_probe_does_not_transfer(self):
        scenarios = ((0, 119, 119, 121), (0, 30, 30))
        for times in scenarios:
            with (
                self.subTest(times=times),
                mock.patch.object(fields, "urlopen", side_effect=TimeoutError("handshake timeout")),
                mock.patch.object(fields.shutil, "which", return_value="curl.exe"),
                mock.patch.object(fields.time, "monotonic", side_effect=times),
                mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, "curl 8.5.0", ""),
                           side_effect=None if len(times) == 4 else subprocess.TimeoutExpired(["curl"], 5)) as run,
                mock.patch.object(fields, "install_charmm36_archive") as install,
            ):
                with self.assertRaisesRegex((RuntimeError, TimeoutError), "curl|budget|timed out"):
                    fields.download_charmm36_force_field(cache_root=self.cache)
                self.assertEqual(run.call_count, 1)
                install.assert_not_called()


if __name__ == "__main__":
    unittest.main()
