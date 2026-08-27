from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from core.artifact_validation import (
    ValidationResult,
    validate_checkpoint,
    validate_gro,
    validate_stage_outputs,
    validate_topology,
    validate_tpr,
)


VALID_GRO = """Test
2
    1ALA      N    1   0.000   0.000   0.000
    1ALA     CA    2   0.100   0.100   0.100
   1.00000   1.00000   1.00000
"""


class ArtifactValidationTests(unittest.TestCase):
    def write(self, directory: str, name: str, content: str) -> Path:
        path = Path(directory) / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_validate_gro_accepts_minimal_structure(self):
        with tempfile.TemporaryDirectory() as directory:
            result = validate_gro(self.write(directory, "conf.gro", VALID_GRO))
        self.assertTrue(result.ok)

    def test_validate_gro_rejects_malformed_files(self):
        cases = (
            "Test\n    1ALA      N    1   0.000   0.000   0.000\n",
            "Test\nnope\n1ALA\n1 1 1\n",
            "Test\n2\n1ALA\n1 1 1\n",
            "Test\n1\n    1ALA      N    1   0.000   0.000   0.000\nnot-a-box\n",
            "Test\n0\n1 1 1\n",
        )
        with tempfile.TemporaryDirectory() as directory:
            for index, content in enumerate(cases):
                with self.subTest(index=index):
                    result = validate_gro(self.write(directory, f"bad{index}.gro", content))
                    self.assertFalse(result.ok)

    def test_validate_topology_requires_sections_and_ligand(self):
        with tempfile.TemporaryDirectory() as directory:
            valid = self.write(
                directory,
                "topol.top",
                "[ system ]\nMy system\n\n[ molecules ]\n; name number\nProtein 1\nLIG 2\n",
            )
            self.assertTrue(validate_topology(valid, ligand_name="LIG").ok)
            missing = self.write(directory, "missing.top", "[ system ]\nX\n")
            result = validate_topology(missing, ligand_name="LIG")
        self.assertFalse(result.ok)
        self.assertIn("molecules", result.message.lower())

    def test_external_checks_preserve_diagnostic_output(self):
        with tempfile.TemporaryDirectory() as directory:
            tpr = self.write(directory, "topol.tpr", "binary placeholder")
            cpt = self.write(directory, "state.cpt", "binary placeholder")
            bridge = Mock()
            bridge.run.side_effect = [
                SimpleNamespace(returncode=0, stdout="ok", stderr=""),
                SimpleNamespace(returncode=2, stdout="stdout diagnostic", stderr="fatal cpt diagnostic"),
            ]
            self.assertTrue(validate_tpr(tpr, bridge=bridge).ok)
            result = validate_checkpoint(cpt, bridge=bridge)
        self.assertFalse(result.ok)
        self.assertIn("fatal cpt diagnostic", result.message)

    def test_validate_stage_outputs_reports_each_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            gro = self.write(directory, "conf.gro", VALID_GRO)
            result = validate_stage_outputs({"structure": gro})
        self.assertIsInstance(result, ValidationResult)
        self.assertTrue(result.ok)
        self.assertIn("structure", result.details)


if __name__ == "__main__":
    unittest.main()
