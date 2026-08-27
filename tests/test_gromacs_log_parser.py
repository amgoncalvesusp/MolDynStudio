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
MDRUN_ENERGY_HEADER = (
    "      Potential    Kinetic En.   Total Energy  Conserved En.    Temperature"
)
MDRUN_ENERGY_VALUES = (
    "   -6.31538e+05    9.35119e+04   -5.38026e+05   -5.37901e+05    2.99834e+02"
)
MDRUN_PRESSURE_HEADER = " Pres. DC (bar) Pressure (bar)   Constr. rmsd"
MDRUN_PRESSURE_VALUES = "   -1.25501e+02    1.16602e+02    6.48970e-06"
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

    def test_parser_reads_standard_multiline_gromacs_energy_tables(self):
        from core.gromacs_log_parser import GromacsLogParser

        parser = GromacsLogParser()

        self.assertIsNone(parser.feed_line("Energies (kJ/mol)"))
        self.assertIsNone(parser.feed_line(MDRUN_ENERGY_HEADER))
        energy = parser.feed_line(MDRUN_ENERGY_VALUES)
        self.assertIsNone(parser.feed_line(MDRUN_PRESSURE_HEADER))
        pressure = parser.feed_line(MDRUN_PRESSURE_VALUES)

        self.assertIsNotNone(energy)
        self.assertEqual(energy.potential_kj_mol, -631538.0)
        self.assertEqual(energy.temperature_k, 299.834)
        self.assertIsNone(energy.pressure_bar)
        self.assertIsNone(energy.step)

        self.assertIsNotNone(pressure)
        self.assertEqual(pressure.pressure_bar, 116.602)
        self.assertIsNone(pressure.potential_kj_mol)
        self.assertIsNone(pressure.temperature_k)

    def test_parser_tolerates_tabular_label_variants_and_empty_cells(self):
        from core.gromacs_log_parser import GromacsLogParser

        parser = GromacsLogParser()

        self.assertIsNone(
            parser.feed_line("Potential Energy\tTemperature (K)\tPressure")
        )
        update = parser.feed_line("-4.2e+05\t\t1.01325")

        self.assertIsNotNone(update)
        self.assertEqual(update.potential_kj_mol, -420000.0)
        self.assertEqual(update.pressure_bar, 1.01325)
        self.assertIsNone(update.temperature_k)
        self.assertIsNone(update.step)

    def test_parser_rejects_ambiguous_whitespace_rows_with_missing_cells(self):
        from core.gromacs_log_parser import GromacsLogParser

        parser = GromacsLogParser()

        self.assertIsNone(
            parser.feed_line("Potential Energy  Temperature (K)  Pressure")
        )
        update = parser.feed_line("-4.2e+05    1.01325")

        self.assertIsNone(update)

    def test_parser_rejects_underfilled_tab_rows_without_empty_placeholders(self):
        from core.gromacs_log_parser import GromacsLogParser

        parser = GromacsLogParser()

        self.assertIsNone(
            parser.feed_line("Potential Energy\tTemperature (K)\tPressure")
        )
        update = parser.feed_line("-4.2e+05\t1.01325")

        self.assertIsNone(update)


if __name__ == "__main__":
    unittest.main()
