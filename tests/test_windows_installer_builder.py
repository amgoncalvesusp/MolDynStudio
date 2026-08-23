from __future__ import annotations

import unittest
from unittest import mock

from build import create_installer


class WindowsInstallerBuilderTests(unittest.TestCase):
    def test_ensure_pyinstaller_uses_pinned_build_requirements_when_missing(self):
        missing = mock.Mock(returncode=1)

        with (
            mock.patch.object(create_installer.subprocess, "run", return_value=missing),
            mock.patch.object(create_installer.subprocess, "check_call") as check_call,
        ):
            create_installer.ensure_pyinstaller()

        check_call.assert_called_once_with(
            [
                create_installer.sys.executable,
                "-m",
                "pip",
                "install",
                "-r",
                str(create_installer.BUILD_REQUIREMENTS),
            ]
        )

    def test_ensure_pyinstaller_skips_install_when_available(self):
        present = mock.Mock(returncode=0)

        with (
            mock.patch.object(create_installer.subprocess, "run", return_value=present),
            mock.patch.object(create_installer.subprocess, "check_call") as check_call,
        ):
            create_installer.ensure_pyinstaller()

        check_call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
