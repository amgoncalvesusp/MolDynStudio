from pathlib import Path
import tempfile
import unittest

from core.complex_builder import (
    LigandTopologyCoordinator,
    combine_gro,
    normalize_ligand_topology,
    patch_topology_for_ligand,
)
from utils.topology_builder import LigandParamWorker, LigandParams, LigandTopologyArtifacts


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

    def test_coordinator_exposes_worker_and_tracks_artifacts(self):
        params = LigandParams("input.mol2", ".")
        coordinator = LigandTopologyCoordinator(params)
        self.assertIsInstance(coordinator.worker, LigandParamWorker)
        artifacts = LigandTopologyArtifacts(Path("a.gro"), Path("a.itp"))
        coordinator._set_artifacts(artifacts)
        self.assertIs(coordinator.artifacts, artifacts)

    def test_combine_gro_preserves_protein_then_ligand_and_box(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            protein = root / "protein.gro"
            ligand = root / "ligand.gro"
            output = root / "complex.gro"
            protein.write_text(
                "Protein\n2\n"
                f"{1:5d}{'ALA':<5}{'CA':>5}{1:5d}{0.100:8.3f}{0.200:8.3f}{0.300:8.3f}\n"
                f"{1:5d}{'ALA':<5}{'N':>5}{2:5d}{0.400:8.3f}{0.500:8.3f}{0.600:8.3f}\n"
                "1.00000 1.00000 1.00000\n", encoding="utf-8")
            ligand.write_text(
                "Ligand\n1\n"
                f"{1:5d}{'MOL':<5}{'C':>5}{1:5d}{0.700:8.3f}{0.800:8.3f}{0.900:8.3f}\n"
                "1.00000 1.00000 1.00000\n", encoding="utf-8")
            result = combine_gro(protein, ligand, output)
            lines = result.read_text(encoding="utf-8").splitlines()
            self.assertEqual(int(lines[1]), 3)
            self.assertIn("ALA", lines[2])
            self.assertIn("ALA", lines[3])
            self.assertIn("MOL", lines[4])
            self.assertEqual(lines[5], "1.00000 1.00000 1.00000")

    def test_patch_topology_orders_include_and_molecules_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            top = root / "topol.top"
            itp = root / "ligand.itp"
            output = root / "patched.top"
            top.write_text(
                '#include "amber99sb.ff/forcefield.itp"\n'
                '#include "amber99sb.ff/tip3p.itp"\n'
                '#include "amber99sb.ff/ions.itp"\n\n'
                '[ system ]\nComplex\n\n[ molecules ]\n'
                'Protein_chain_A    1\nSOL 100\nNA 2\n', encoding="utf-8")
            itp.write_text("[ moleculetype ]\nMOL 3\n", encoding="utf-8")
            result = patch_topology_for_ligand(top, itp, output)
            text = result.read_text(encoding="utf-8")
            self.assertLess(text.index('#include "ligand/ligand.itp"'), text.index("tip3p"))
            self.assertLess(text.index("MOL                1"), text.index("SOL 100"))
            patch_topology_for_ligand(result, itp, result)
            again = result.read_text(encoding="utf-8")
            self.assertEqual(again.count('#include "ligand/ligand.itp"'), 1)
            self.assertEqual(again.count("MOL                1"), 1)


if __name__ == "__main__":
    unittest.main()
