from pathlib import Path
import tempfile
import unittest
import pytest

from core.complex_builder import (
    LigandTopologyCoordinator,
    combine_gro,
    normalize_ligand_topology,
    patch_topology_for_ligand,
)
from utils.topology_builder import LigandParamWorker, LigandParams, LigandTopologyArtifacts


def gro_row(resid, resname, atomname, serial, x):
    return f"{resid:5d}{resname:<5}{atomname:>5}{serial:5d}{x:8.3f}{.2:8.3f}{.3:8.3f}"


def test_ligand_coordinates_and_topology_precede_existing_water_and_ions(tmp_path):
    protein, ligand, output = (tmp_path / name for name in ("protein.gro", "ligand.gro", "complex.gro"))
    source = [gro_row(1,"ALA","CA",1,.1), gro_row(2,"SOL","OW",2,.4), gro_row(3,"SOD","SOD",3,.6)]
    protein.write_text("Protein\n3\n" + "\n".join(source) + "\n1 1 1\n")
    ligand_rows = [gro_row(1,"TWB","C1",1,.7), gro_row(2,"TWB","C1",2,.9)]
    ligand.write_text("Ligands\n2\n" + "\n".join(ligand_rows) + "\n1 1 1\n")
    combine_gro(protein, ligand, output)
    assert b'\r' not in output.read_bytes()
    combined = output.read_text().splitlines()[2:-1]
    assert [row[5:10].strip() for row in combined] == ["ALA","TWB","TWB","SOL","SOD"]
    assert [row[20:] for row in combined] == [source[0][20:], *[row[20:] for row in ligand_rows], source[1][20:],source[2][20:]]
    top, itp, prm = (tmp_path / name for name in ("topol.top", "ligand.itp", "parameters.itp"))
    top.write_text('#include "charmm.ff/forcefield.itp"\n[ moleculetype ]\nProtein 3\n[ atoms ]\n1 C 1 ALA CA 1 0 12\n[ system ]\nComplex\n[ molecules ]\nProtein 1\nSOL 1\nSOD 1\n')
    itp.write_text("[ moleculetype ]\nTWB 3\n")
    prm.write_text("[ atomtypes ]\nX 6 12 0 A .3 .2\n")
    patch_topology_for_ligand(top,itp,molecule_count=2,parameter_includes=(prm,))
    assert b'\r' not in top.read_bytes()
    text = top.read_text()
    assert text.index("parameters.itp") < text.index("ligand.itp") < text.index("[ moleculetype ]")
    assert text.index("TWB                2") < text.index("SOL 1")
    patch_topology_for_ligand(top,itp,molecule_count=2,parameter_includes=(prm,))
    assert top.read_text() == text


def test_conflicting_existing_ligand_count_is_rejected(tmp_path):
    top, itp = tmp_path / "topol.top", tmp_path / "ligand.itp"
    top.write_text("[ system ]\nComplex\n[ molecules ]\nProtein 1\nTWB 1\n")
    itp.write_text("[ moleculetype ]\nTWB 3\n")
    with pytest.raises(ValueError, match="count"):
        patch_topology_for_ligand(top,itp,molecule_count=2)


def test_raw_charmm_parameter_syntax_is_not_included_as_gromacs(tmp_path):
    top, itp, prm = (tmp_path/name for name in ("topol.top", "ligand.itp", "ligand.prm"))
    top.write_text("[ system ]\nComplex\n[ molecules ]\nProtein 1\n")
    itp.write_text("[ moleculetype ]\nTWB 3\n")
    prm.write_text("* CHARMM parameters\nBONDS\nCT CT 300 1.5\nEND\n")
    with pytest.raises(ValueError, match="GROMACS"):
        patch_topology_for_ligand(top,itp,parameter_includes=(prm,))


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

    def test_patch_topology_uses_output_directory_for_relative_include(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = root / "template"
            run = root / "run"
            (run / "ligand").mkdir(parents=True)
            source = template / "topol.top"
            source.parent.mkdir()
            source.write_text(
                '#include "amber99sb.ff/forcefield.itp"\n\n'
                '[ system ]\nComplex\n\n[ molecules ]\nProtein_chain_A 1\nSOL 1\n',
                encoding="utf-8")
            itp = run / "ligand" / "ligand.itp"
            itp.write_text("[ moleculetype ]\nMOL 3\n", encoding="utf-8")
            output = run / "topol.top"
            patch_topology_for_ligand(source, itp, output)
            self.assertIn('#include "ligand/ligand.itp"', output.read_text(encoding="utf-8"))

    def test_patch_topology_defaults_to_in_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            top = root / "topol.top"
            itp = root / "ligand.itp"
            top.write_text("[ system ]\nComplex\n\n[ molecules ]\nProtein 1\n", encoding="utf-8")
            itp.write_text("[ moleculetype ]\nMOL 3\n", encoding="utf-8")
            result = patch_topology_for_ligand(top, itp)
            self.assertEqual(result, top)
            self.assertIn("MOL", top.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
