"""Behavioral checks for the narrowly supported MATCH-to-GROMACS adapter."""

from __future__ import annotations

import tempfile
import unittest
import os
from pathlib import Path
import subprocess
import sys

from core.charmm_conversion import convert_charmm_ligand, load_charmm_ligand


RTF = """* Synthetic branched ligand; force-conversion fixture, not a chemical model.
*
22 0
MASS 1 CT 12.011 C
AUTO ANGLES DIHE
RESI LIG 0.0
GROUP
ATOM A CT -0.3
ATOM B CT  0.1
ATOM C CT  0.1
ATOM D CT  0.1
ATOM E CT  0.0
BOND A B B C C D B E
IMPR B A C E
PATCH FIRST NONE LAST NONE
END
"""
PAR = """* Synthetic parameters with Urey-Bradley and distinct LJ14 terms.
*
ATOMS
MASS 1 CT 12.011 C
BONDS
CT CT 300.0 1.50
ANGLES
CT CT CT 50.0 109.5 10.0 2.50
DIHEDRALS
X CT CT X 0.2 1 180.0
X CT CT X 0.3 3 0.0
IMPROPER
CT CT CT CT 5.0 0 0.0
NONBONDED nbxmod 5 atom cdiel shift vatom vdistance vswitch -
cutnb 14.0 ctofnb 12.0 ctonnb 10.0 eps 1.0 e14fac 1.0 wmin 1.5
CT 0.0 -0.10 2.0 0.0 -0.025 1.70
END
"""
MOL2 = """@<TRIPOS>MOLECULE
LIG
5 4 1 0 0
SMALL
USER_CHARGES
@<TRIPOS>ATOM
1 E 1.0 1.0 1.0 C.3 1 LIG 0.0
2 D 3.5 1.0 0.4 C.3 1 LIG 0.1
3 C 2.5 0.1 0.0 C.3 1 LIG 0.1
4 B 1.0 0.0 0.0 C.3 1 LIG 0.1
5 A 0.0 0.4 -0.1 C.3 1 LIG -0.3
@<TRIPOS>BOND
1 5 4 1
2 4 3 1
3 3 2 1
4 4 1 1
@<TRIPOS>SUBSTRUCTURE
1 LIG 1
"""


class CharmmConversionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.rtf, self.par, self.mol2 = (self.root / name for name in ("lig.rtf", "lig.par", "lig.mol2"))
        for path, content in ((self.rtf, RTF), (self.par, PAR), (self.mol2, MOL2)):
            path.write_text(content, encoding="utf-8")

    def load(self):
        return load_charmm_ligand(self.rtf, self.par, self.mol2, net_charge=0)

    def test_builds_complete_graph_and_keeps_rtf_order_coordinates_charge_and_mass(self):
        structure = self.load()
        self.assertEqual([atom.name for atom in structure.atoms], list("ABCDE"))
        self.assertEqual((len(structure.bonds), len(structure.angles), len(structure.dihedrals)), (4, 4, 2))
        self.assertEqual((len(structure.impropers), len(structure.urey_bradleys)), (1, 4))
        self.assertEqual(tuple(structure.coordinates[0]), (0.0, 0.4, -0.1))
        self.assertAlmostEqual(sum(atom.charge for atom in structure.atoms), 0.0)
        self.assertTrue(all(abs(atom.mass - 12.011) < 1e-8 for atom in structure.atoms))
        self.assertTrue(all(len(term.type) == 2 for term in structure.dihedrals))
        self.assertEqual([atom.name for atom in (structure.impropers[0].atom1, structure.impropers[0].atom2,
                                              structure.impropers[0].atom3, structure.impropers[0].atom4)], list("BACE"))

    def test_export_preserves_ub_improper_multiple_torsions_and_explicit_lj14(self):
        result = convert_charmm_ligand(self.rtf, self.par, self.mol2, self.root / "out", net_charge=0)
        text = result.ligand_itp.read_text()
        params = result.parameters_itp.read_text()
        self.assertNotIn("[ defaults ]", text + params)
        self.assertIn("[ atomtypes ]", params)
        self.assertNotIn("[ atomtypes ]", text)
        self.assertIn("[ moleculetype ]", text)
        self.assertIn("[ pairs ]", text)
        self.assertIn("0.104600000", text)  # epsilon14 = 0.025 kcal/mol, not 0.10.
        self.assertIn("8368.000", text)  # 2 * 10 kcal/mol/A^2 -> kJ/mol/nm^2.
        self.assertTrue(result.psf_path.is_file())
        self.assertTrue(result.ligand_gro.is_file())
        self.assertEqual(result.atom_names, tuple("ABCDE"))

    def test_net_charge_mismatch_is_rejected_before_output(self):
        with self.assertRaisesRegex(ValueError, "charge"):
            convert_charmm_ligand(self.rtf, self.par, self.mol2, self.root / "out", net_charge=-2)
        self.assertFalse((self.root / "out").exists())

    def test_missing_parameter_is_fatal(self):
        self.par.write_text(PAR.replace("CT CT CT 50.0 109.5 10.0 2.50", ""))
        with self.assertRaisesRegex(ValueError, "parameter|angle"):
            self.load()

    def test_graph_mismatch_is_fatal(self):
        self.mol2.write_text(MOL2.replace("4 4 1 1", "4 5 1 1"))
        with self.assertRaisesRegex(ValueError, "connectivity|bond"):
            self.load()

    def test_missing_atom_duplicate_name_and_nonfinite_coordinates_are_rejected(self):
        for replacement in (MOL2.replace("1 E ", "1 A "), MOL2.replace("1.0 1.0 1.0", "nan 1.0 1.0")):
            with self.subTest(replacement=replacement[:40]):
                self.mol2.write_text(replacement)
                with self.assertRaises(ValueError):
                    self.load()

    def test_unsupported_rtf_features_are_rejected(self):
        for statement in ("CMAP A B C D A B C E", "LONEPAIR relative A B C D DIST 0.2 ANGL 0 DIHE 0",
                          "PATCH FIRST NTER LAST NONE", "BOND A +B", "ANGL A B C"):
            with self.subTest(statement=statement):
                self.rtf.write_text(RTF.replace("END", statement + "\nEND"))
                with self.assertRaises(ValueError):
                    self.load()

    def test_malformed_rtf_contract_and_disconnected_graph_are_rejected(self):
        variants = (RTF.replace("AUTO ANGLES DIHE", ""), RTF.replace("RESI LIG 0.0", "RESI LIG 0.0\nRESI TWO 0.0"),
                    RTF.replace("ATOM E CT  0.0", "ATOM A CT 0.0"),
                    RTF.replace("IMPR B A C E", "IMPR B A C A"),
                    RTF.replace("BOND A B B C C D B E", "BOND A B B C C D"),
                    RTF.replace("BOND A B B C C D B E", "BOND A B B C C D B E A B"),
                    RTF.replace("ATOM A CT -0.3", "ATOM -A CT -0.3"))
        for value in variants:
            with self.subTest(rtf=value):
                self.rtf.write_text(value)
                with self.assertRaises(ValueError):
                    self.load()

    def test_charge_scaling_element_and_input_limits_are_rejected(self):
        self.par.write_text(PAR.replace("e14fac 1.0", "e14fac 0.5"))
        with self.assertRaisesRegex(ValueError, "E14FAC"):
            self.load()
        self.par.write_text(PAR)
        self.mol2.write_text(MOL2.replace("C.3", "N.3"))
        with self.assertRaisesRegex(ValueError, "Element"):
            self.load()
        self.mol2.write_text(MOL2)
        for charge in (None, True, 0.5, -21, "0"):
            with self.subTest(charge=charge), self.assertRaises(ValueError):
                load_charmm_ligand(self.rtf, self.par, self.mol2, net_charge=charge)
        self.rtf.write_text("*" * (5 * 1024 * 1024 + 1))
        with self.assertRaisesRegex(ValueError, "5 MiB"):
            self.load()

    def test_nbfix_is_not_silently_omitted(self):
        self.par.write_text(PAR.replace("END", "NBFIX\nCT CT -0.2 3.0\nEND"))
        with self.assertRaisesRegex(ValueError, "NBFIX"):
            self.load()

    def test_geometric_lj_mixing_cannot_use_parent_charmm_defaults(self):
        self.par.write_text(PAR.replace("NONBONDED nbxmod", "NONBONDED GEOM nbxmod"))
        with self.assertRaisesRegex(ValueError, "Lorentz|mixing"):
            self.load()

    def test_nonfinite_bonded_parameters_are_rejected(self):
        for original, replacement in (("300.0 1.50", "nan 1.50"),
                                      ("50.0 109.5", "50.0 inf"),
                                      ("10.0 2.50", "nan 2.50"),
                                      ("0.2 1 180.0", "inf 1 180.0"),
                                      ("5.0 0 0.0", "nan 0 0.0")):
            with self.subTest(parameter=original):
                self.par.write_text(PAR.replace(original, replacement))
                with self.assertRaisesRegex(ValueError, "finite|parameter"):
                    self.load()

    def test_sources_and_existing_destination_remain_unchanged(self):
        before = tuple(path.read_bytes() for path in (self.rtf, self.par, self.mol2))
        output = self.root / "out"
        convert_charmm_ligand(self.rtf, self.par, self.mol2, output, net_charge=0)
        sentinel = output / "sentinel.txt"
        sentinel.write_text("keep")
        with self.assertRaises(FileExistsError):
            convert_charmm_ligand(self.rtf, self.par, self.mol2, output, net_charge=0)
        self.assertEqual(sentinel.read_text(), "keep")
        self.assertEqual(tuple(path.read_bytes() for path in (self.rtf, self.par, self.mol2)), before)

    def test_independent_openmm_readers_agree_on_energy_and_forces(self):
        """Exercise units, exclusions, UB, LJ14 and both torsion conventions."""
        # Isolate native OpenMM from Qt/numerical runtimes imported elsewhere
        # in the full application suite (they can conflict on Windows).
        code = ("from tests.test_charmm_conversion import CharmmConversionTests; "
                "case=CharmmConversionTests(); case.setUp(); "
                "case._check_energy_and_forces(); case.doCleanups()")
        process = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                                 timeout=60, env={**os.environ, "OPENMM_CPU_THREADS": "1"})
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)

    def _check_energy_and_forces(self):
        import numpy as np
        import openmm
        from openmm import app, unit

        # Native OpenMM's PSF reader reserves names starting with D for Drude
        # particles, even without a DRUDE header. Avoid that upstream convention.
        self.rtf.write_text(RTF.replace("ATOM D ", "ATOM F ").replace("C D ", "C F "))
        self.mol2.write_text(MOL2.replace("2 D ", "2 F "))
        result = convert_charmm_ligand(self.rtf, self.par, self.mol2, self.root / "parity", net_charge=0)
        wrapper = result.ligand_itp.parent / "test.top"
        wrapper.write_text('[ defaults ]\n1 2 no 1.0 1.0\n#include "ligand_parameters.itp"\n'
                           '#include "ligand.itp"\n[ system ]\nTest\n[ molecules ]\nsystem 1\n')
        original = app.CharmmPsfFile(str(result.psf_path))
        parameters = app.CharmmParameterSet(str(self.rtf), str(self.par))
        systems = (original.createSystem(parameters, nonbondedMethod=app.NoCutoff, constraints=None),
                   app.GromacsTopFile(str(wrapper)).createSystem(nonbondedMethod=app.NoCutoff, constraints=None))
        positions = self.load().coordinates * unit.angstrom
        measured = []
        for system in systems:
            integrator = openmm.VerletIntegrator(0.001)
            platform = openmm.Platform.getPlatformByName("CPU" if os.name == "nt" else "Reference")
            context = openmm.Context(system, integrator, platform)
            context.setPositions(positions)
            state = context.getState(getEnergy=True, getForces=True)
            measured.append((state.getPotentialEnergy().value_in_unit(unit.kilojoule_per_mole),
                             state.getForces(asNumpy=True).value_in_unit(unit.kilojoule_per_mole / unit.nanometer)))
            del context, integrator
        self.assertAlmostEqual(measured[0][0], measured[1][0], places=5)
        np.testing.assert_allclose(measured[0][1], measured[1][1], rtol=2e-7, atol=2e-5)


if __name__ == "__main__":
    unittest.main()
