from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


MDRUN_STEP_TIME_HEADER = "           Step           Time"
MDRUN_STEP_TIME_VALUES = "           2500        5.00000"
MDRUN_INLINE_PROGRESS = "step 5000, time 10.000 (ps)"
MDRUN_TEMPERATURE = "Temperature  =  300.15"
MDRUN_PRESSURE = "Pressure (bar) =  1.01325e+00"
MDRUN_POTENTIAL = "Epot = -4.20000e+05"
UNRELATED_LINE = "Writing checkpoint, step 5000 at Wed Aug 26 12:34:56 2026"


class GromacsLogParserTests(unittest.TestCase):
    def test_module_is_importable(self):
        from core.gromacs_log_parser import GromacsLogParser, MDProgress, read_mdp_nsteps

        self.assertIsNotNone(GromacsLogParser)
        self.assertIsNotNone(MDProgress)
        self.assertIsNotNone(read_mdp_nsteps)

    def test_read_mdp_nsteps_ignores_comments_and_returns_last_active_value(self):
        from core.gromacs_log_parser import read_mdp_nsteps

        with tempfile.TemporaryDirectory() as directory:
            mdp = Path(directory) / "nvt.mdp"
            mdp.write_text(
                "\n".join(
                    (
                        "; nsteps = 100",
                        "integrator = md",
                        "nsteps = 250000 ; original production target",
                        "dt = 0.002",
                        "   ; nsteps = 500",
                        "nsteps = 5000",
                    )
                ),
                encoding="utf-8",
            )

            self.assertEqual(read_mdp_nsteps(mdp), 5000)

    def test_parser_reads_total_steps_and_fraction_from_step_time_block(self):
        from core.gromacs_log_parser import GromacsLogParser

        with tempfile.TemporaryDirectory() as directory:
            mdp = Path(directory) / "md.mdp"
            mdp.write_text("nsteps = 10000\n", encoding="utf-8")
            parser = GromacsLogParser(mdp)

            self.assertIsNone(parser.feed_line(MDRUN_STEP_TIME_HEADER))

            progress = parser.feed_line(MDRUN_STEP_TIME_VALUES)

            self.assertIsNotNone(progress)
            self.assertEqual(progress.step, 2500)
            self.assertEqual(progress.time_ps, 5.0)
            self.assertEqual(progress.total_steps, 10000)
            self.assertEqual(progress.fraction, 0.25)
            self.assertIsNone(progress.temperature_k)
            self.assertIsNone(progress.pressure_bar)
            self.assertIsNone(progress.potential_kj_mol)

    def test_parser_tolerates_inline_progress_and_unrelated_lines(self):
        from core.gromacs_log_parser import GromacsLogParser

        parser = GromacsLogParser()

        self.assertIsNone(parser.feed_line(UNRELATED_LINE))

        progress = parser.feed_line(MDRUN_INLINE_PROGRESS)

        self.assertIsNotNone(progress)
        self.assertEqual(progress.step, 5000)
        self.assertEqual(progress.time_ps, 10.0)
        self.assertIsNone(progress.total_steps)
        self.assertIsNone(progress.fraction)

    def test_parser_emits_partial_thermodynamic_updates_without_synthesizing_missing_values(self):
        from core.gromacs_log_parser import GromacsLogParser

        parser = GromacsLogParser()

        temperature = parser.feed_line(MDRUN_TEMPERATURE)
        pressure = parser.feed_line(MDRUN_PRESSURE)
        potential = parser.feed_line(MDRUN_POTENTIAL)
        progress = parser.feed_line(MDRUN_INLINE_PROGRESS)

        self.assertEqual(temperature.temperature_k, 300.15)
        self.assertIsNone(temperature.step)
        self.assertIsNone(temperature.pressure_bar)
        self.assertIsNone(temperature.potential_kj_mol)

        self.assertEqual(pressure.pressure_bar, 1.01325)
        self.assertIsNone(pressure.temperature_k)
        self.assertIsNone(pressure.step)

        self.assertEqual(potential.potential_kj_mol, -420000.0)
        self.assertIsNone(potential.temperature_k)
        self.assertIsNone(potential.pressure_bar)

        self.assertEqual(progress.step, 5000)
        self.assertEqual(progress.time_ps, 10.0)
        self.assertIsNone(progress.temperature_k)
        self.assertIsNone(progress.pressure_bar)
        self.assertIsNone(progress.potential_kj_mol)


if __name__ == "__main__":
    unittest.main()
