from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

from core.run_manifest import StageName
from core.stage_qc import (
    QCSeverity,
    StageQCPolicy,
    evaluate_stage_qc,
)


class StageQCTests(unittest.TestCase):
    def _evaluate(
        self,
        content: str,
        *,
        policy: StageQCPolicy | None = None,
        stage: StageName = StageName.NVT,
    ):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        log_path = root / "nvt.log"
        log_path.write_text(content, encoding="utf-8")
        result = evaluate_stage_qc(
            stage,
            {("em.log" if stage == StageName.MINIMIZATION else "nvt.log"): str(log_path.resolve())},
            policy=policy,
        )
        return result, log_path.resolve()

    def test_minimization_downhill_energy_and_nonthermal_values_are_not_outliers(self):
        result, _ = self._evaluate(
            "Epot = -172514\nPressure = 2.0e6\nTemperature = 0\n"
            "Steepest Descents converged to Fmax < 1000 in 1095 steps\n"
            "Potential Energy = -745615\nMaximum force = 894.854 on atom 4022\n",
            stage=StageName.MINIMIZATION,
        )
        self.assertEqual(result.outcome, "passed")
        checks = {check.gate: check for check in result.checks}
        energy = checks["energy_drift"]
        self.assertEqual(energy.observations["energy_change_kj_mol"], -573101)
        self.assertEqual(energy.observations["maximum_force_kj_mol_nm"], 894.854)
        self.assertEqual(energy.observations["force_tolerance_kj_mol_nm"], 1000)
        self.assertEqual(energy.observations["minimization_steps"], 1095)
        self.assertIn("convergence", energy.message.lower())
        self.assertNotIn("drift", energy.message.lower())
        self.assertEqual(checks["pressure"].observations["maximum"], 2e6)
        self.assertIn("not applicable", checks["temperature"].message)

    def test_minimization_missing_thermal_diagnostics_are_not_missing_data_warnings(self):
        result, _ = self._evaluate("Potential Energy = -100\n", stage=StageName.MINIMIZATION)
        checks = {check.gate: check for check in result.checks}
        self.assertEqual(checks["temperature"].severity, QCSeverity.PASS)
        self.assertEqual(checks["pressure"].severity, QCSeverity.PASS)
        self.assertIn("not reported", checks["energy_drift"].message)
        self.assertNotIn("maximum_force_kj_mol_nm", checks["energy_drift"].observations)

    def test_minimization_explicit_failure_and_contradictory_force_warn(self):
        for detail in (
            "Steepest Descents did not converge to Fmax < 1000 in 2 steps\nMaximum force = 2000",
            "Steepest Descents converged to Fmax < 1000 in 2 steps\nMaximum force = 2000",
            "Steepest Descents converged to Fmax < 1000 in 2 steps\nMaximum force = nan",
        ):
            with self.subTest(detail=detail):
                result, _ = self._evaluate("Potential Energy = -100\n" + detail, stage=StageName.MINIMIZATION)
                self.assertEqual(result.checks[0].severity, QCSeverity.WARNING)

    def test_minimization_still_rejects_malformed_and_nonfinite_diagnostics(self):
        for value in ("nan", "1e309", "unavailable"):
            with self.subTest(value=value):
                result, _ = self._evaluate(
                    f"Epot = {value}\nTemperature = {value}\nPressure = {value}\n",
                    stage=StageName.MINIMIZATION,
                    policy=StageQCPolicy(required_gates=frozenset({"energy_drift", "temperature", "pressure"})),
                )
                self.assertTrue(result.has_fatal)
                self.assertTrue(all(check.severity != QCSeverity.PASS for check in result.checks))
                json.dumps(result.to_manifest(), allow_nan=False)

    def test_minimization_required_unavailable_thermal_gate_is_not_silently_satisfied(self):
        result, _ = self._evaluate(
            "Potential Energy = -100\n", stage=StageName.MINIMIZATION,
            policy=StageQCPolicy(required_gates=frozenset({"temperature"})),
        )
        self.assertEqual({check.gate for check in result.fatals}, {"temperature"})

    def test_log_averages_and_rms_are_not_temporal_endpoint_samples(self):
        for heading in ("<====  A V E R A G E S  ====>", "<====  R M S - F L U C T U A T I O N S  ====>"):
            with self.subTest(heading=heading):
                result, _ = self._evaluate(
                    "Step Time\n0 0\nEpot = -1000\nTemperature = 300\nPressure = 1\n"
                    "Step Time\n2000 2.0\nEpot = -1001\nTemperature = 301\nPressure = 2\n"
                    + heading + "\nEpot = 99999\nTemperature = 2\nPressure = 9000\n"
                )
                energy, temperature, pressure = result.checks
                self.assertEqual(energy.observations["sample_count"], 2)
                self.assertEqual(energy.observations["last_kj_mol"], -1001)
                self.assertEqual(temperature.observations["minimum"], 300)
                self.assertEqual(pressure.observations["maximum"], 2)

    def test_temporal_samples_after_an_appended_summary_are_retained(self):
        result, _ = self._evaluate(
            "Step Time\n0 0\nEpot = -1000\n"
            "<==== A V E R A G E S ====>\nEpot = -999\n"
            "Step Time\n2000 2\nEpot = -1001\nTemperature = 300\nPressure = 1\n"
        )
        self.assertEqual(result.checks[0].observations["last_kj_mol"], -1001)
        self.assertEqual(result.checks[0].observations["sample_count"], 2)

    def test_summary_cannot_hide_an_incomplete_temporal_energy_table(self):
        result, _ = self._evaluate(
            "Epot = -1000\nEpot = -1001\nTemperature = 300\nPressure = 1\n"
            "  Potential    Temperature    Pressure\n<==== A V E R A G E S ====>\n",
            policy=StageQCPolicy(required_gates=frozenset({"energy_drift", "temperature", "pressure"})),
        )
        self.assertEqual({check.gate for check in result.fatals}, {"energy_drift", "temperature", "pressure"})

    def test_valid_log_diagnostics_pass_with_artifact_sourced_observations(self):
        result, log_path = self._evaluate(
            "\n".join(
                (
                    "Potential Energy = -4.20000e+05",
                    "Temperature = 300.15",
                    "Pressure (bar) = 1.01325",
                    "Epot = -4.21000e+05",
                )
            )
        )

        self.assertEqual(result.outcome, "passed")
        self.assertFalse(result.has_fatal)
        self.assertEqual({check.gate for check in result.checks}, {
            "energy_drift",
            "temperature",
            "pressure",
        })
        self.assertTrue(all(check.source == str(log_path) for check in result.checks))
        energy = next(check for check in result.checks if check.gate == "energy_drift")
        self.assertEqual(energy.severity, QCSeverity.PASS)
        self.assertEqual(energy.observations["sample_count"], 2)
        self.assertAlmostEqual(energy.observations["relative_drift_percent"], 0.238095, places=5)

    def test_missing_optional_diagnostics_are_non_fatal_warnings(self):
        result, _log_path = self._evaluate("Finished mdrun on rank 0\n")

        self.assertEqual(result.outcome, "warning")
        self.assertFalse(result.has_fatal)
        self.assertEqual(len(result.warnings), 3)
        self.assertTrue(all("not present" in check.message for check in result.warnings))

    def test_malformed_optional_diagnostics_are_non_fatal_warnings(self):
        result, _log_path = self._evaluate(
            "Temperature = unavailable\nPressure = ???\nEpot = nan\n"
        )

        self.assertEqual(result.outcome, "warning")
        self.assertFalse(result.has_fatal)
        self.assertEqual(len(result.warnings), 3)
        self.assertTrue(all("malformed" in check.message for check in result.warnings))

    def test_missing_or_malformed_selected_gate_is_fatal(self):
        policy = StageQCPolicy(required_gates=frozenset({"temperature", "pressure"}))
        result, _log_path = self._evaluate(
            "Temperature = unavailable\n",
            policy=policy,
        )

        self.assertTrue(result.has_fatal)
        fatal_gates = {check.gate for check in result.fatals}
        self.assertEqual(fatal_gates, {"temperature", "pressure"})
        self.assertEqual(result.outcome, "fatal")

    def test_extreme_parsed_values_warn_without_blocking_valid_artifacts(self):
        result, _log_path = self._evaluate(
            "\n".join(
                (
                    "Epot = 2.0e+15",
                    "Epot = 2.1e+15",
                    "Temperature = -1.0",
                    "Pressure = 2.0e+6",
                )
            )
        )

        self.assertEqual(result.outcome, "warning")
        self.assertFalse(result.has_fatal)
        self.assertEqual(
            {check.gate for check in result.warnings},
            {"energy_drift", "temperature", "pressure"},
        )

    def test_suspicious_but_finite_values_warn_without_blocking(self):
        result, _log_path = self._evaluate(
            "\n".join(
                (
                    "Epot = -1000",
                    "Epot = -100",
                    "Temperature = 450",
                    "Pressure = 5000",
                )
            )
        )

        self.assertEqual(result.outcome, "warning")
        self.assertFalse(result.has_fatal)
        self.assertEqual(
            {check.gate for check in result.warnings},
            {"energy_drift", "temperature", "pressure"},
        )

    def test_manifest_payload_is_deterministic_and_json_compatible(self):
        result, _log_path = self._evaluate(
            "Epot = -1000\nTemperature = 300\nPressure = 1\nEpot = -1001\n"
        )

        first = result.to_manifest()
        second = result.to_manifest()

        self.assertEqual(first, second)
        self.assertEqual(first["stage"], StageName.NVT.value)
        self.assertEqual(first["outcome"], "passed")
        self.assertIsInstance(first["checks"], list)

    def test_non_finite_diagnostics_never_enter_manifest_observations(self):
        result, _log_path = self._evaluate(
            "\n".join(
                (
                    "Epot = 1e309",
                    "Potential Energy = 1e309",
                    "Temperature = 1e309",
                    "Pressure = 1e309",
                )
            )
        )

        self.assertEqual(result.outcome, "warning")
        self.assertTrue(all(check.severity == QCSeverity.WARNING for check in result.checks))
        self.assertTrue(
            all(
                all(
                    not isinstance(value, float) or math.isfinite(value)
                    for value in check.observations.values()
                )
                for check in result.checks
            )
        )
        self.assertTrue(
            all(
                "Non-finite numeric observations were omitted." in check.message
                for check in result.checks
            )
        )

        payload = result.to_manifest()
        encoded = json.dumps(payload, allow_nan=False)

        def reject_constant(value: str):
            raise AssertionError(f"Non-standard JSON constant persisted: {value}")

        decoded = json.loads(encoded, parse_constant=reject_constant)
        for check in decoded["checks"]:
            self.assertTrue(
                all(
                    not isinstance(value, float) or math.isfinite(value)
                    for value in check["observations"].values()
                )
            )


if __name__ == "__main__":
    unittest.main()
