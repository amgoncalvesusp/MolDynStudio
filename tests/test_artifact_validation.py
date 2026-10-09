from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core import wsl_bridge

from core.artifact_validation import (
    ValidationResult,
    validate_checkpoint,
    validate_gro,
    validate_mdp,
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

    def test_gro_rejects_nonfinite_coordinates_and_box(self):
        with tempfile.TemporaryDirectory() as directory:
            for value in ('nan', 'inf', '-inf'):
                for content in (
                    VALID_GRO.replace('   0.000', f'{value:>8}', 1),
                    VALID_GRO.replace('1.00000', value, 1),
                    VALID_GRO.replace('1.00000   1.00000   1.00000', f'1 1 1 0 0 0 0 0 {value}'),
                ):
                    with self.subTest(value=value, content=content):
                        self.assertFalse(validate_gro(self.write(directory, 'bad.gro', content)).ok)

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

    def test_validate_gro_rejects_zero_box_and_accepts_full_box(self):
        with tempfile.TemporaryDirectory() as directory:
            zero = VALID_GRO.replace("1.00000   1.00000   1.00000", "0.00000   1.00000   1.00000")
            self.assertFalse(validate_gro(self.write(directory, "zero.gro", zero)).ok)
            full = VALID_GRO.replace("1.00000   1.00000   1.00000", "1 1 1 0 0 0 0 0 0")
            self.assertTrue(validate_gro(self.write(directory, "full.gro", full)).ok)

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

    def test_validate_topology_rejects_empty_sections_and_missing_ligand(self):
        with tempfile.TemporaryDirectory() as directory:
            empty = self.write(directory, "empty.top", "[ system ]\n\n[ molecules ]\n")
            self.assertFalse(validate_topology(empty).ok)
            no_molecules = self.write(directory, "none.top", "[ system ]\nName\n[ molecules ]\n")
            self.assertFalse(validate_topology(no_molecules).ok)
            no_ligand = self.write(directory, "nolig.top", "[ system ]\nName\n[ molecules ]\nProtein 1\n")
            result = validate_topology(no_ligand, ligand_name="LIG")
        self.assertFalse(result.ok)
        self.assertIn("ligand", result.message.lower())

    def test_validate_topology_rejects_non_integer_zero_and_extra_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            contents = (
                "Protein nope",
                "Protein 0",
                "Protein -1",
                "Protein 1 unexpected",
            )
            for index, molecule in enumerate(contents):
                topology = f"[ system ]\nName\n[ molecules ]\n{molecule}\n"
                result = validate_topology(self.write(directory, f"invalid{index}.top", topology))
                with self.subTest(molecule=molecule):
                    self.assertFalse(result.ok)

    def test_validate_mdp_requires_positive_nsteps_and_dynamics_timestep(self):
        with tempfile.TemporaryDirectory() as directory:
            valid = self.write(directory, "md.mdp", "integrator = md\nnsteps = 500\ndt = 0.002\n")
            self.assertTrue(validate_mdp(valid, require_dt=True).ok)

            for index, content in enumerate((
                "integrator = md\ndt = 0.002\n",
                "integrator = md\nnsteps = 0\ndt = 0.002\n",
                "integrator = md\nnsteps = 500\ndt = -0.002\n",
                "integrator = md\nnsteps = 500\n",
            )):
                with self.subTest(index=index):
                    result = validate_mdp(
                        self.write(directory, f"bad-{index}.mdp", content),
                        require_dt=True,
                    )
                    self.assertFalse(result.ok)

    def test_validate_mdp_allows_minimization_without_dt_but_not_bad_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            valid = self.write(directory, "em.mdp", "integrator = steep\nnsteps = 50000\n")
            invalid = self.write(directory, "bad-em.mdp", "integrator = steep\nnsteps = nope\n")
            self.assertTrue(validate_mdp(valid, require_dt=False).ok)
            self.assertFalse(validate_mdp(invalid, require_dt=False).ok)

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
            self.assertEqual(bridge.run.call_args_list[0].args[0], ["gmx", "dump", "-s", wsl_bridge.win_to_wsl(str(tpr))])
            self.assertEqual(bridge.run.call_args_list[1].args[0], ["gmx", "dump", "-cp", wsl_bridge.win_to_wsl(str(cpt))])
        self.assertFalse(result.ok)
        self.assertIn("fatal cpt diagnostic", result.message)

    def test_binary_checks_convert_host_paths_before_crossing_wsl_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            for validator, suffix, flag in ((validate_tpr, "tpr", "-s"), (validate_checkpoint, "cpt", "-cp")):
                with self.subTest(suffix=suffix):
                    path = self.write(directory, f"system with spaces.{suffix}", "placeholder")
                    converted = f"/mnt/c/MD project/system with spaces.{suffix}"
                    bridge = Mock()
                    bridge.run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
                    with patch("core.artifact_validation.wsl_bridge.win_to_wsl", return_value=converted) as convert:
                        self.assertTrue(validator(path, bridge=bridge).ok)
                    convert.assert_called_once_with(str(path))
                    self.assertEqual(bridge.run.call_args.args[0], ["gmx", "dump", flag, converted])

    def test_external_check_exception_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, "topol.tpr", "placeholder")
            bridge = Mock()
            bridge.run.side_effect = RuntimeError("bridge unavailable")
            result = validate_tpr(path, bridge=bridge)
        self.assertFalse(result.ok)
        self.assertIn("bridge unavailable", result.message)

    def test_binary_checks_use_configured_gromacs_and_conda_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            for validator, suffix, flag in ((validate_tpr, "tpr", "-s"), (validate_checkpoint, "cpt", "-cp")):
                with self.subTest(suffix=suffix):
                    path = self.write(directory, f"system.{suffix}", "placeholder")
                    bridge = Mock()
                    bridge.run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
                    result = validator(path, bridge=bridge,
                                       gromacs_binary="/opt/gromacs/bin/gmx_mpi",
                                       conda_environment="configured-md-env")
                    self.assertTrue(result.ok)
                    bridge.run.assert_called_once_with(
                        ["/opt/gromacs/bin/gmx_mpi", "dump", flag, wsl_bridge.win_to_wsl(str(path))],
                        env_name="configured-md-env",
                    )

    def test_stage_output_checks_forward_the_configured_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            tpr = self.write(directory, "system.tpr", "placeholder")
            cpt = self.write(directory, "system.cpt", "placeholder")
            bridge = Mock()
            bridge.run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
            result = validate_stage_outputs(
                {"tpr": tpr, "checkpoint": cpt}, bridge=bridge,
                gromacs_binary="gmx_mpi", conda_environment="configured-md-env",
            )
            self.assertTrue(result.ok)
            self.assertEqual(bridge.run.call_count, 2)
            for call in bridge.run.call_args_list:
                self.assertEqual(call.args[0][0], "gmx_mpi")
                self.assertEqual(call.kwargs, {"env_name": "configured-md-env"})

    def test_validate_stage_outputs_reports_each_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            gro = self.write(directory, "conf.gro", VALID_GRO)
            result = validate_stage_outputs({"structure": gro})
        self.assertIsInstance(result, ValidationResult)
        self.assertTrue(result.ok)
        self.assertIn("structure", result.details)

    def test_validate_stage_outputs_propagates_ligand_and_bridge_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            top = self.write(directory, "topol.top", "[ system ]\nName\n[ molecules ]\nProtein 1\n")
            tpr = self.write(directory, "topol.tpr", "placeholder")
            bridge = Mock()
            bridge.run.return_value = SimpleNamespace(returncode=3, stdout="", stderr="bad tpr")
            result = validate_stage_outputs({"topology": top, "tpr": tpr}, ligand_name="LIG", bridge=bridge)
        self.assertFalse(result.ok)
        self.assertFalse(result.details["topology"].ok)
        self.assertIn("LIG", result.details["topology"].message)
        self.assertIn("bad tpr", result.details["tpr"].message)


if __name__ == "__main__":
    unittest.main()
