from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.project_manager import ProjectFormatError, ProjectManager


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


if __name__ == "__main__":
    unittest.main()

