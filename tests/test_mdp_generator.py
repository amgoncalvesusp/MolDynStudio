from __future__ import annotations

import unittest

from utils.mdp_generator import MDParameters, generate_all_mdp, generate_production_mdp


class MDPGeneratorTests(unittest.TestCase):
    def test_production_steps_are_derived_from_duration_and_timestep(self):
        content = generate_production_mdp(MDParameters(duration_ns=1.0, timestep_fs=2.0, save_every_ps=10.0))
        self.assertIn("nsteps                  = 500000", content)
        self.assertIn("nstxout-compressed      = 5000", content)

    def test_all_standard_mdp_files_are_generated(self):
        files = generate_all_mdp(MDParameters())
        self.assertEqual(set(files), {"em.mdp", "nvt.mdp", "npt.mdp", "md.mdp"})
        self.assertTrue(all("MolDynStudio" in content for content in files.values()))

    def test_parameters_are_clamped_without_mutating_original(self):
        params = MDParameters(duration_ns=-1, timestep_fs=-2, save_every_ps=-3)
        normalized = params.normalized()
        self.assertEqual(params.duration_ns, -1)
        self.assertGreater(normalized.duration_ns, 0)
        self.assertGreater(normalized.timestep_fs, 0)
        self.assertGreater(normalized.save_every_ps, 0)


if __name__ == "__main__":
    unittest.main()

