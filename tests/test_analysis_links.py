"""Analysis point links use their result manifest, never filename inference."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from analysis.execution import fingerprint
from analysis.result_links import load_result_rows


class AnalysisLinksTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "rmsd.csv"
        self.source.write_text("time_ns,rmsd_nm,frame\n0.003,0.1,0\n0.007,0.2,1\n", encoding="utf-8")
        self.manifest = {
            "schema_version": 1, "status": "complete", "source_changed": False,
            "inputs": {}, "results": [{"name": "RMSD", "status": "complete",
                **fingerprint(self.source), "columns": ["time_ns", "rmsd_nm", "frame"],
                "rows": 2, "metadata": {}}],
        }
        self.save()

    def save(self):
        (self.root / "analysis_manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def test_reads_exact_columns_and_rows_without_opening_trajectory(self):
        columns, rows, manifest, result = load_result_rows(str(self.source))
        self.assertEqual(columns, ("time_ns", "rmsd_nm", "frame"))
        self.assertEqual(rows, ((0.003, 0.1, 0.0), (0.007, 0.2, 1.0)))
        self.assertEqual(manifest["inputs"], {})
        self.assertEqual(result["name"], "RMSD")

    def test_missing_manifest_explains_unlinked_data(self):
        (self.root / "analysis_manifest.json").unlink()
        with self.assertRaisesRegex(ValueError, "manifest"):
            load_result_rows(str(self.source))

    def test_tampered_output_rejects_link(self):
        self.source.write_text(self.source.read_text().replace("0.2", "0.9"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed|hash"):
            load_result_rows(str(self.source))

    def test_invalid_manifest_fields_and_result_shape(self):
        original = json.dumps(self.manifest)
        mutations = (
            lambda m: m.update(schema_version=True),
            lambda m: m.update(status="running"),
            lambda m: m.update(source_changed=True),
            lambda m: m.update(results="bad"),
            lambda m: m["results"][0].update(status="failed"),
            lambda m: m["results"][0].update(path=str(self.root / "other.csv")),
            lambda m: m["results"][0].update(columns=["frame", "rmsd_nm", "time_ns"]),
            lambda m: m["results"][0].update(rows=3),
            lambda m: m["results"][0].update(rows=True),
            lambda m: m["results"][0].update(sha256="bad"),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.manifest = json.loads(original)
                mutation(self.manifest)
                self.save()
                with self.assertRaises(ValueError):
                    load_result_rows(str(self.source))

    def test_invalid_numeric_data_rejects_even_with_matching_hash(self):
        for row in ("nan,0.2,1", "0.007,0.2", "0.007,no,1", "0.007,inf,1"):
            with self.subTest(row=row):
                self.source.write_text("time_ns,rmsd_nm,frame\n0.003,0.1,0\n" + row + "\n", encoding="utf-8")
                self.manifest["results"][0].update(fingerprint(self.source))
                self.save()
                with self.assertRaises(ValueError):
                    load_result_rows(str(self.source))

    def test_partial_manifest_allows_only_its_complete_result(self):
        self.manifest.update(status="partial")
        self.manifest["results"].append({"name": "SASA", "status": "failed", "error": "missing tool"})
        self.save()
        self.assertEqual(load_result_rows(str(self.source))[3]["name"], "RMSD")

    def test_malformed_json_and_missing_csv_fail_readably(self):
        (self.root / "analysis_manifest.json").write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "manifest"):
            load_result_rows(str(self.source))
        self.save()
        self.source.unlink()
        with self.assertRaises(ValueError):
            load_result_rows(str(self.source))


if __name__ == "__main__":
    unittest.main()
