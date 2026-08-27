from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from utils.topology_builder import (
    AcpypeOutputError,
    LigandTopologyArtifacts,
    UnsupportedLigandChemistryError,
    normalize_acpype_outputs,
    read_molecule_name_from_itp,
    validate_acpype_input,
)


class TopologyBuilderTests(unittest.TestCase):
    def test_reads_strict_molecule_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.itp"
            path.write_text("[ moleculetype ]\n; name nrexcl\nLIG 3\n", encoding="utf-8")
            self.assertEqual(read_molecule_name_from_itp(path), "LIG")

    def test_rejects_ambiguous_molecule_types(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.itp"
            path.write_text("[ moleculetype ]\nLIG 3\n[ moleculetype ]\nOTHER 3\n", encoding="utf-8")
            with self.assertRaises(AcpypeOutputError):
                read_molecule_name_from_itp(path)

    def test_normalizes_acpype_outputs_by_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, project = Path(tmp) / "random.acpype", Path(tmp) / "project"
            root.mkdir()
            (root / "foo_GMX.itp").write_text("[ moleculetype ]\nFOO 3\n", encoding="utf-8")
            (root / "foo_GMX.gro").write_text("FOO\n1\n    1FOO C1 1 0 0 0\n1.0 1.0 1.0\n", encoding="utf-8")
            (root / "foo_GMX_posre.itp").write_text("[ position_restraints ]\n1 1 1000 1000 1000\n", encoding="utf-8")
            artifacts = normalize_acpype_outputs(root, project)
            self.assertIsInstance(artifacts, LigandTopologyArtifacts)
            self.assertEqual(artifacts.ligand_itp, project / "ligand" / "ligand.itp")
            self.assertTrue(artifacts.ligand_gro.exists())
            self.assertTrue(artifacts.ligand_posre_itp.exists())

    def test_normalization_omits_optional_posre(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, project = Path(tmp) / "x", Path(tmp) / "p"
            root.mkdir()
            (root / "x.itp").write_text("[ moleculetype ]\nX 3\n", encoding="utf-8")
            (root / "x.gro").write_text("X\n1\natom\n1 1 1\n", encoding="utf-8")
            result = normalize_acpype_outputs(root, project)
            self.assertIsNone(result.ligand_posre_itp)
    def test_rejects_boron_in_sdf_before_running_acpype(self):
        sdf = """C6S
  MolDynStudio

  2  1  0  0  0  0  0  0  0  0999 V2000
    0.0000    0.0000    0.0000 B   0  0  0  0  0  0  0  0  0  0  0  0
    1.4000    0.0000    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0
  1  2  1  0  0  0  0
M  END
$$$$
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c6s.sdf"
            path.write_text(sdf, encoding="utf-8")

            with self.assertRaisesRegex(
                UnsupportedLigandChemistryError,
                "boron.*CGenFF.*covalent",
            ):
                validate_acpype_input(path)

    def test_accepts_common_organic_elements_in_mol2(self):
        mol2 = """@<TRIPOS>MOLECULE
ligand
2 1 0 0 0
SMALL
USER_CHARGES
@<TRIPOS>ATOM
1 C1 0.0 0.0 0.0 C.3 1 LIG 0.0
2 O1 1.4 0.0 0.0 O.2 1 LIG 0.0
@<TRIPOS>BOND
1 1 2 1
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ligand.mol2"
            path.write_text(mol2, encoding="utf-8")

            self.assertEqual(validate_acpype_input(path), frozenset({"C", "O"}))

    def test_rejects_boron_in_v3000_sdf(self):
        sdf = """C6S
  MolDynStudio

  0  0  0     0  0            999 V3000
M  V30 BEGIN CTAB
M  V30 COUNTS 2 1 0 0 0
M  V30 BEGIN ATOM
M  V30 1 B 0.0 0.0 0.0 0
M  V30 2 O 1.4 0.0 0.0 0
M  V30 END ATOM
M  V30 BEGIN BOND
M  V30 1 1 1 2
M  V30 END BOND
M  V30 END CTAB
M  END
$$$$
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c6s-v3000.sdf"
            path.write_text(sdf, encoding="utf-8")

            with self.assertRaises(UnsupportedLigandChemistryError):
                validate_acpype_input(path)

    def test_pdb_fallback_preserves_two_letter_elements(self):
        pdb = (
            "HETATM    1 BE   BE  A   1       0.000   0.000   0.000\n"
            "HETATM    2 NA   NA  A   2       1.000   0.000   0.000\n"
            "HETATM    3 SI   SIL A   3       2.000   0.000   0.000\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "elements.pdb"
            path.write_text(pdb, encoding="utf-8")

            self.assertEqual(
                validate_acpype_input(path),
                frozenset({"Be", "Na", "Si"}),
            )


if __name__ == "__main__":
    unittest.main()
