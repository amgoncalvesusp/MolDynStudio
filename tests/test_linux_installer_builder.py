from __future__ import annotations

import tarfile
import tempfile
import unittest
from pathlib import Path

from build import create_linux_installer


class LinuxInstallerBuilderTests(unittest.TestCase):
    def test_payload_file_selection_excludes_build_outputs_and_caches(self):
        files = create_linux_installer.iter_payload_files()
        rels = {
            path.relative_to(create_linux_installer.REPO_ROOT).as_posix()
            for path in files
        }

        self.assertIn("main.py", rels)
        self.assertIn("requirements.txt", rels)
        self.assertIn("assets/logo_512.png", rels)
        self.assertIn("assets/forcefields/README.md", rels)
        self.assertFalse(any(rel.endswith((".tgz", ".tar.gz")) for rel in rels))
        self.assertNotIn("dist/MolDynStudio.exe", rels)
        self.assertFalse(any("__pycache__" in rel for rel in rels))
        self.assertFalse(any(rel.startswith("build/") for rel in rels))

    def test_payload_is_valid_tarball_with_linux_launcher_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            launcher = root / "launch_analysis_studio.sh"
            launcher.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            payload = create_linux_installer.build_payload([launcher], root)
            archive = root / "payload.tar.gz"
            archive.write_bytes(payload)

            with tarfile.open(archive, mode="r:gz") as tar:
                info = tar.getmember("launch_analysis_studio.sh")

        self.assertTrue(info.mode & 0o111)

    def test_installer_refuses_to_overwrite_unmanaged_directory(self):
        stub = create_linux_installer.INSTALLER_STUB

        self.assertIn(".moldynstudio-install", stub)
        self.assertIn("was not created by this installer", stub)

    def test_installer_rejects_dangerous_install_directories_before_deletion(self):
        stub = create_linux_installer.INSTALLER_STUB
        guard = 'case "$INSTALL_DIR" in'
        dangerous_patterns = '"/"|"$HOME")'
        recursive_delete = 'rm -rf "$INSTALL_DIR"'

        self.assertIn(guard, stub)
        self.assertIn(dangerous_patterns, stub)
        self.assertLess(stub.index(guard), stub.index(recursive_delete))

    def test_installer_launcher_defaults_to_xcb_without_overriding_user_choice(self):
        stub = create_linux_installer.INSTALLER_STUB

        self.assertIn('QT_QPA_PLATFORM="\\${QT_QPA_PLATFORM:-xcb}"', stub)
        self.assertIn("sudo apt install -y $UBUNTU_QT_DEPS", stub)


if __name__ == "__main__":
    unittest.main()
