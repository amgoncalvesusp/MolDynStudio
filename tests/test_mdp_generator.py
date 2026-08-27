from __future__ import annotations

import unittest

from utils.mdp_generator import MDParameters, generate_all_mdp, generate_production_mdp


def _settings(content: str) -> dict[str, str]:
    return {
        key.strip(): value.strip()
        for line in content.splitlines()
        if "=" in line and not line.lstrip().startswith(";")
        for key, value in [line.split("=", 1)]
    }


class MDPGeneratorTests(unittest.TestCase):
    def test_production_converts_gui_time_units_to_gromacs_steps(self):
        content = generate_production_mdp(
            MDParameters(
                duration_ns=100.0,
                timestep_fs=2.0,
                save_every_ps=10.0,
            )
        )
        settings = _settings(content)

        self.assertEqual(float(settings["dt"]), 0.002)
        self.assertEqual(int(settings["nsteps"]), 50_000_000)
        self.assertEqual(int(settings["nstxout-compressed"]), 5_000)
        self.assertEqual(int(settings["nstenergy"]), 5_000)
        self.assertEqual(int(settings["nstlog"]), 5_000)

    def test_small_positive_timestep_is_not_rendered_as_zero(self):
        content = generate_production_mdp(
            MDParameters(
                duration_ns=0.001,
                timestep_fs=0.001,
                save_every_ps=0.001,
            )
        )

        self.assertEqual(float(_settings(content)["dt"]), 0.000001)

    def test_all_standard_mdp_files_are_generated(self):
        files = generate_all_mdp(MDParameters())
        self.assertEqual(set(files), {"em.mdp", "nvt.mdp", "npt.mdp", "md.mdp"})
        self.assertTrue(all("MolDynStudio" in content for content in files.values()))

    def test_timing_inputs_reject_invalid_nonfinite_and_nonpositive_values(self):
        cases = {
            "duration_ns": (0, -1, float("nan"), float("inf"), "invalid"),
            "timestep_fs": (0, -1, float("nan"), float("-inf"), "invalid"),
            "save_every_ps": (0, -1, float("nan"), float("inf"), "invalid"),
        }

        for field, values in cases.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    params = MDParameters(**{field: value})
                    with self.assertRaisesRegex(ValueError, field):
                        generate_production_mdp(params)

    def test_output_cadence_shorter_than_one_timestep_is_rejected_clearly(self):
        with self.assertRaisesRegex(ValueError, "save_every_ps.*one timestep"):
            generate_production_mdp(
                MDParameters(timestep_fs=2.0, save_every_ps=0.001)
            )

    def test_only_restrained_equilibration_stages_define_posres(self):
        files = generate_all_mdp(MDParameters())

        self.assertNotIn("define", _settings(files["em.mdp"]))
        self.assertEqual(_settings(files["nvt.mdp"])["define"], "-DPOSRES")
        self.assertEqual(_settings(files["npt.mdp"])["define"], "-DPOSRES")
        self.assertNotIn("define", _settings(files["md.mdp"]))

    def test_charmm36_uses_force_switch_nonbonded_settings(self):
        content = generate_production_mdp(
            MDParameters(force_field="CHARMM36m", duration_ns=0.001)
        )

        self.assertIn("vdw-modifier            = force-switch", content)
        self.assertIn("rvdw-switch             = 1.0", content)
        self.assertIn("rvdw                    = 1.2", content)
        self.assertIn("rcoulomb                = 1.2", content)
        self.assertIn("DispCorr                = no", content)


if __name__ == "__main__":
    unittest.main()
