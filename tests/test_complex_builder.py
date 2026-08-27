from pathlib import Path
import tempfile
import unittest

from core.complex_builder import normalize_ligand_topology
from utils.topology_builder import LigandTopologyArtifacts


class ComplexBuilderTests(unittest.TestCase):
    def test_normalize_ligand_topology_returns_structured_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "arbitrary_name.acpype"
            project = Path(tmp) / "project"
            source.mkdir()
            (source / "ligand_GMX.itp").write_text("[ moleculetype ]\nLIG 3\n", encoding="utf-8")
            (source / "ligand_GMX.gro").write_text("LIG\n1\natom\n1 1 1\n", encoding="utf-8")
            artifacts = normalize_ligand_topology(source, project)
            self.assertIsInstance(artifacts, LigandTopologyArtifacts)
            self.assertEqual(artifacts.itp.name, "ligand.itp")


if __name__ == "__main__":
    unittest.main()
