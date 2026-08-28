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
    ):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        log_path = root / "nvt.log"
        log_path.write_text(content, encoding="utf-8")
        result = evaluate_stage_qc(
            StageName.NVT,
            {"nvt.log": str(log_path.resolve())},
            policy=policy,
        )
        return result, log_path.resolve()

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
