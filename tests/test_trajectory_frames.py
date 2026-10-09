"""Frame handoff tests use real small structures when MDAnalysis is available."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from core.trajectory_frames import TrajectoryError, TrajectorySession, launch_docklens


def pdb_atom(serial, name, residue, chain, resid, x, element, record="ATOM"):
    return (
        f"{record:<6}{serial:5d} {name:<4} {residue:>3} {chain}{resid:4d}    "
        f"{x:8.3f}{0:8.3f}{0:8.3f}{1:6.2f}{0:6.2f}          {element:>2}  \n"
    )


class LaunchTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows executables only")
    def test_windows_rejects_non_executable_launchers_before_starting(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "launch.json"
            manifest.write_text("{}")
            for suffix in (".cmd", ".BAT", ".py", ".com", ""):
                with self.subTest(suffix=suffix):
                    binary = root / f"DockLens{suffix}"
                    binary.touch()
                    with mock.patch("core.trajectory_frames.subprocess.Popen") as popen:
                        with self.assertRaisesRegex(TrajectoryError, r"\.exe"):
                            launch_docklens(str(binary), manifest)
                    popen.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows executables only")
    def test_windows_auto_rejects_batch_launcher_from_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "docklens.cmd"
            binary.touch()
            manifest = root / "launch.json"
            manifest.write_text("{}")
            with mock.patch("core.trajectory_frames.shutil.which", return_value=str(binary)), mock.patch("core.trajectory_frames.subprocess.Popen") as popen:
                with self.assertRaisesRegex(TrajectoryError, r"\.exe"):
                    launch_docklens("auto", manifest)
            popen.assert_not_called()

    def test_command_preserves_spaces_and_never_uses_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "Dock Lens.exe"
            binary.touch()
            manifest = root / "launch file.json"
            manifest.write_text("{}")
            with mock.patch("core.trajectory_frames.subprocess.Popen") as popen:
                launch_docklens(str(binary), manifest)
            self.assertEqual(popen.call_args.args[0], [str(binary.resolve()), "--manifest", str(manifest.resolve())])
            self.assertIs(popen.call_args.kwargs["shell"], False)

    def test_missing_executable_has_actionable_error(self):
        with self.assertRaisesRegex(TrajectoryError, "executable"):
            launch_docklens("", Path("missing.json"))

    def test_auto_resolves_installed_docklens_on_path(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "DockLens.exe"
            binary.touch()
            manifest = Path(directory) / "launch.json"
            manifest.write_text("{}")
            with mock.patch("core.trajectory_frames.shutil.which", return_value=str(binary)), mock.patch("core.trajectory_frames.subprocess.Popen") as popen:
                launch_docklens("auto", manifest)
            self.assertEqual(popen.call_args.args[0][0], str(binary.resolve()))


@unittest.skipUnless(importlib.util.find_spec("MDAnalysis"), "MDAnalysis is optional")
class TrajectoryFramesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.topology = self.root / "complex.pdb"
        self.topology.write_text(
            pdb_atom(1, "N", "GLY", "A", 1, 0, "N")
            + pdb_atom(2, "CA", "GLY", "A", 1, 1.4, "C")
            + pdb_atom(3, "C1", "LIG", "B", 2, 3.0, "C", "HETATM")
            + pdb_atom(4, "O1", "LIG", "B", 2, 4.2, "O", "HETATM")
            + "CONECT    1    2\nCONECT    3    4\nEND\n",
            encoding="ascii",
        )
        import MDAnalysis as mda

        self.trajectory = self.root / "trajectory.dcd"
        universe = mda.Universe(str(self.topology), to_guess=())
        with mda.Writer(str(self.trajectory), n_atoms=4) as writer:
            writer.write(universe.atoms)
            universe.atoms.positions = universe.atoms.positions + 10
            writer.write(universe.atoms)
        universe.trajectory.close()
        self.session = TrajectorySession.open(str(self.topology), str(self.trajectory))
        self.addCleanup(self.session.close)

    def export(self, **kwargs):
        values = dict(
            output_dir=self.root / "exports", frame_index=1,
            receptor_selection="protein", ligand_selection="resname LIG", pbc_reviewed=True,
        )
        return self.session.export_frame(**{**values, **kwargs})

    def test_frame_inspector_uses_requested_frame_and_counts(self):
        snapshot = self.session.read_frame(1, "protein", "resname LIG")
        self.assertEqual((snapshot.frame_index, snapshot.n_frames), (1, 2))
        self.assertEqual((snapshot.receptor_count, snapshot.ligand_count), (2, 2))
        self.assertAlmostEqual(snapshot.receptor_coordinates[0][0], 10)

    def test_tpr_molecule_chain_names_export_with_original_identity(self):
        self.session.universe.atoms.chainIDs = ["Protein_A", "Protein_A", "LIG", "LIG"]
        exported = self.export()
        provenance = json.loads(exported.provenance_path.read_text())
        receptor = provenance["receptor_atom_map"]
        ligand = provenance["ligand_atom_map"]
        self.assertEqual({row["chain"] for row in receptor}, {"A"})
        self.assertEqual({row["chain"] for row in ligand}, {"B"})
        self.assertEqual({row["source_chain"] for row in receptor}, {"Protein_A"})
        self.assertEqual({row["source_chain"] for row in ligand}, {"LIG"})
        self.assertEqual({row["segid"] for row in receptor}, {"A"})
        self.assertEqual({row["source_segindex"] for row in ligand}, {1})

    def test_mapped_protein_and_blank_ligand_do_not_reuse_valid_chain(self):
        import MDAnalysis as mda

        original = self.session.universe
        universe = mda.Merge(original.select_atoms("protein"), original.select_atoms("protein"), original.select_atoms("resname LIG"))
        self.session.close()
        self.session.universe = universe
        universe.atoms.chainIDs = ["Protein_A", "Protein_A", "A", "A", "", ""]
        exported = self.export(frame_index=0)
        provenance = json.loads(exported.provenance_path.read_text())
        receptor = provenance["receptor_atom_map"]
        ligand = provenance["ligand_atom_map"]
        self.assertEqual([row["chain"] for row in receptor], ["B", "B", "A", "A"])
        self.assertEqual({row["chain"] for row in ligand}, {"C"})
        self.assertEqual([row["source_segindex"] for row in receptor], [0, 0, 1, 1])
        self.assertEqual({row["source_chain"] for row in ligand}, {""})
        repeated = json.loads(self.export(frame_index=0).provenance_path.read_text())
        self.assertEqual(repeated["receptor_atom_map"], receptor)
        self.assertEqual(repeated["ligand_atom_map"], ligand)

    def test_repeated_single_letter_chain_labels_keep_segments_distinct(self):
        import MDAnalysis as mda

        original = self.session.universe
        universe = mda.Merge(original.select_atoms("protein"), original.select_atoms("protein"), original.select_atoms("resname LIG"))
        self.session.close()
        self.session.universe = universe
        universe.atoms.chainIDs = ["A"] * 6
        exported = self.export(frame_index=0)
        provenance = json.loads(exported.provenance_path.read_text())
        self.assertEqual([row["chain"] for row in provenance["receptor_atom_map"]], ["A", "A", "B", "B"])
        self.assertEqual({row["chain"] for row in provenance["ligand_atom_map"]}, {"C"})

    def test_export_pairs_same_frame_and_hashes_actual_files(self):
        exported = self.export()
        manifest = json.loads(exported.manifest_path.read_text())
        provenance = json.loads(exported.provenance_path.read_text())
        self.assertEqual(manifest["schema"], "docklens-launch-v1")
        self.assertEqual(provenance["frame_index"], 1)
        self.assertEqual(provenance["coordinate_unit"], "angstrom")
        self.assertEqual(provenance["preprocessing"], "none; source coordinates preserved")
        for key, expected_x in (("receptor", 10.0), ("poses", 13.0)):
            path = Path(manifest["project_root"]) / manifest[key]["path"]
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), manifest[key]["sha256"])
            atom = next(line for line in path.read_text().splitlines() if line.startswith(("ATOM", "HETATM")))
            self.assertAlmostEqual(float(atom[30:38]), expected_x)
            self.assertIn("CONECT", path.read_text())
        self.assertEqual(provenance["ligand_atom_map"][0]["source_index"], 2)
        self.assertEqual(provenance["sources"]["trajectory"]["sha256"], hashlib.sha256(self.trajectory.read_bytes()).hexdigest())
        self.assertTrue((exported.directory / "reports").is_dir())

    def test_rejects_missing_pbc_review(self):
        with self.assertRaisesRegex(TrajectoryError, "periodic"):
            self.export(pbc_reviewed=False)

    def test_rejects_empty_overlap_and_out_of_range(self):
        for kwargs in (
            {"ligand_selection": "resname ABSENT"},
            {"ligand_selection": "protein"},
            {"frame_index": 2},
            {"frame_index": -1},
            {"frame_index": True},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(TrajectoryError):
                self.export(**kwargs)

    def test_invalid_selection_error_is_readable(self):
        with self.assertRaisesRegex(TrajectoryError, "selection"):
            self.session.read_frame(0, "this_is_not_a_selection", "resname LIG")

    def test_sources_changed_after_open_cannot_be_exported(self):
        self.topology.write_text(self.topology.read_text() + "REMARK changed\n")
        with self.assertRaisesRegex(TrajectoryError, "changed"):
            self.export()

    def test_export_fails_without_inventing_element_identity(self):
        self.session.universe.del_TopologyAttr("elements")
        with self.assertRaisesRegex(TrajectoryError, "element"):
            self.export()
        self.assertFalse((self.root / "exports").exists())

    def test_covalent_receptor_ligand_bond_is_rejected(self):
        self.session.universe.add_bonds([(1, 2)])
        with self.assertRaisesRegex(TrajectoryError, "covalent"):
            self.export()

    def test_ligand_selection_cannot_cut_bonds_to_omitted_atoms(self):
        with self.assertRaisesRegex(TrajectoryError, "complete ligand"):
            self.export(ligand_selection="name C1")

    def test_residue_insertion_code_is_not_silently_dropped(self):
        self.session.universe.residues[0].icode = "A"
        with self.assertRaisesRegex(TrajectoryError, "insertion"):
            self.export()

    def test_read_frame_allows_protein_only_inspection(self):
        snapshot = self.session.read_frame(0, "protein", "")
        self.assertEqual(snapshot.ligand_count, 0)

    def test_invalid_profile_and_empty_output_are_rejected(self):
        for kwargs in ({"profile": "unknown"}, {"output_dir": ""}):
            with self.subTest(kwargs=kwargs), self.assertRaises(TrajectoryError):
                self.export(**kwargs)

    def test_partial_write_does_not_publish_export(self):
        with mock.patch("core.trajectory_frames._write_json", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(TrajectoryError, "disk full"):
                self.export()
        self.assertEqual(list((self.root / "exports").iterdir()), [])

    def test_repeated_export_never_overwrites_first(self):
        first = self.export()
        second = self.export()
        self.assertNotEqual(first.directory, second.directory)
        self.assertTrue(first.manifest_path.is_file())


if __name__ == "__main__":
    unittest.main()
