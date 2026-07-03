from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from utils.file_validators import validate_file, validate_md_inputs


class FileValidatorTests(unittest.TestCase):
    def test_validate_file_accepts_existing_allowed_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "protein.pdb"
            path.write_text("ATOM\n", encoding="utf-8")
            result = validate_file(str(path), (".pdb",), "Protein")
        self.assertTrue(result.ok)

    def test_validate_file_rejects_wrong_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "protein.txt"
            path.write_text("ATOM\n", encoding="utf-8")
            result = validate_file(str(path), (".pdb",), "Protein")
        self.assertFalse(result.ok)
        self.assertIn(".pdb", result.message)

    def test_optional_md_inputs_do_not_fail_when_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "protein.pdb"
            path.write_text("ATOM\n", encoding="utf-8")
            results = validate_md_inputs({"protein": str(path), "ligand": ""})
        self.assertTrue(results["protein"].ok)
        self.assertTrue(results["ligand"].ok)


if __name__ == "__main__":
    unittest.main()

