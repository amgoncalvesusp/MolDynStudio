from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

from core.project_manager import (
    ProjectFormatError,
    ProjectManager,
    is_windows_absolute_path,
    migrate_project_payload,
)


class ProjectManagerTests(unittest.TestCase):
    def test_save_and_load_round_trip(self):
        manager = ProjectManager()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.mds"
            manager.save(path, {"current_page": 2, "pages": {"MD Setup": {"duration": 100}}})
            loaded = manager.load(path)
        self.assertEqual(loaded["current_page"], 2)
        self.assertEqual(loaded["pages"]["MD Setup"]["duration"], 100)

    def test_rejects_incompatible_project_version(self):
        manager = ProjectManager()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.mds"
            path.write_text('{"version": "0.1", "state": {}}', encoding="utf-8")
            with self.assertRaises(ProjectFormatError):
                manager.load(path)

    def test_migrates_legacy_1_0_payload_without_mutating_input(self):
        legacy = {
            "version": "1.0",
            "created": "2026-08-27T12:00:00",
            "state": {"current_page": 1, "pages": {"MD Setup": {}}},
        }
        original = deepcopy(legacy)

        migrated = migrate_project_payload(legacy)

        self.assertEqual(migrated["version"], ProjectManager().project_version)
        self.assertEqual(migrated["state"], legacy["state"])
        self.assertEqual(legacy, original)

    def test_current_payload_loads_directly(self):
        manager = ProjectManager()
        current = {
            "version": manager.project_version,
            "created": "2026-08-27T12:00:00",
            "state": {
                "project_context": {
                    "project_dir": "C:/simulations/protein",
                    "manifest_path": "moldynstudio_run.json",
                }
            },
        }

        migrated = migrate_project_payload(current)

        self.assertEqual(migrated, current)

    def test_rejects_unknown_future_schema_with_clear_error(self):
        with self.assertRaisesRegex(
            ProjectFormatError,
            "newer than the supported",
        ):
            migrate_project_payload({"version": "99.0", "state": {}})

    def test_load_migrates_legacy_project_file(self):
        manager = ProjectManager()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.mds"
            path.write_text(
                json.dumps(
                    {
                        "version": "1.0",
                        "state": {"current_page": 2, "pages": {}},
                    }
                ),
                encoding="utf-8",
            )

            loaded = manager.load(path)

        self.assertEqual(loaded, {"current_page": 2, "pages": {}})

    def test_project_context_is_relative_on_disk_and_resolved_on_load(self):
        manager = ProjectManager()
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp).resolve()
            project_dir = session_dir / "portable-project"
            project_dir.mkdir()
            manifest_file = project_dir / "moldynstudio_run.json"
            path = session_dir / "portable.mds"

            manager.save(
                path,
                {
                    "project_context": {
                        "project_dir": str(project_dir),
                        "manifest_path": str(manifest_file),
                    }
                },
            )

            on_disk = json.loads(path.read_text(encoding="utf-8"))
            loaded = manager.load(path)

        self.assertEqual(
            on_disk["state"]["project_context"]["project_dir"],
            "portable-project",
        )
        self.assertEqual(
            on_disk["state"]["project_context"]["manifest_path"],
            "moldynstudio_run.json",
        )
        self.assertNotIn(
            "\\",
            on_disk["state"]["project_context"]["project_dir"],
        )
        self.assertEqual(
            loaded["project_context"],
            {
                "project_dir": str(project_dir),
                "manifest_path": str(manifest_file),
            },
        )

    def test_windows_relative_separators_are_portable_on_load(self):
        manager = ProjectManager()
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp).resolve()
            path = session_dir / "windows-authored.mds"
            path.write_text(
                json.dumps(
                    {
                        "version": manager.project_version,
                        "state": {
                            "project_context": {
                                "project_dir": "portable\\project",
                                "manifest_path": "state\\moldynstudio_run.json",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            loaded = manager.load(path)

        project_dir = (session_dir / "portable" / "project").resolve()
        self.assertEqual(
            loaded["project_context"],
            {
                "project_dir": str(project_dir),
                "manifest_path": str(
                    project_dir / "state" / "moldynstudio_run.json"
                ),
            },
        )

    def test_windows_absolute_paths_are_not_rehomed_on_posix(self):
        self.assertTrue(is_windows_absolute_path("C:/simulations/protein"))
        self.assertTrue(is_windows_absolute_path(r"\\server\share\protein"))
        self.assertFalse(is_windows_absolute_path("portable/project"))
        manager = ProjectManager()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "windows-absolute.mds"
            path.write_text(
                json.dumps(
                    {
                        "version": manager.project_version,
                        "state": {
                            "project_context": {
                                "project_dir": "C:\\simulations\\protein",
                                "manifest_path": "moldynstudio_run.json",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch("core.project_manager.sys.platform", "linux"):
                loaded = manager.load(path)
                resaved_path = manager.save(Path(tmp) / "resaved.mds", loaded)

            resaved = json.loads(resaved_path.read_text(encoding="utf-8"))

        self.assertEqual(
            loaded["project_context"],
            {
                "project_dir": "C:/simulations/protein",
                "manifest_path": "C:/simulations/protein/moldynstudio_run.json",
            },
        )
        self.assertEqual(
            resaved["state"]["project_context"],
            {
                "project_dir": "C:/simulations/protein",
                "manifest_path": "moldynstudio_run.json",
            },
        )


if __name__ == "__main__":
    unittest.main()
