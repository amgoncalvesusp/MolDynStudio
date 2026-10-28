"""Read exact numeric rows from a completed, fingerprinted analysis result."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from pathlib import Path


def load_result_rows(source: str) -> tuple[tuple[str, ...], tuple[tuple[float, ...], ...], dict, dict]:
    """Return CSV columns/rows and their manifest/result; do not open MD sources."""
    try:
        path = Path(source).expanduser().resolve()
        manifest = json.loads((path.parent / "analysis_manifest.json").read_text(encoding="utf-8"))
        if (not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int
                or manifest["schema_version"] != 1 or manifest.get("status") not in {"complete", "partial"}
                or manifest.get("source_changed") is not False):
            raise ValueError("The analysis manifest is incomplete, unsupported or reports changed sources.")
        results = manifest.get("results")
        if not isinstance(results, list) or any(not isinstance(item, dict) for item in results):
            raise ValueError("The analysis manifest has invalid results.")
        matches = [item for item in results if item.get("status") == "complete"
                   and isinstance(item.get("path"), str) and Path(item["path"]).resolve() == path]
        if len(matches) != 1:
            raise ValueError("The analysis manifest does not identify this exact completed result.")
        result = matches[0]
        before = path.stat()
        raw = path.read_bytes()
        after = path.stat()
        if ((before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
                or type(result.get("size_bytes")) is not int or len(raw) != result["size_bytes"]
                or hashlib.sha256(raw).hexdigest() != result.get("sha256")):
            raise ValueError("Analysis output changed or its hash does not match the manifest. Regenerate the analysis.")
        parsed = list(csv.reader(io.StringIO(raw.decode("utf-8"))))
        columns = tuple(parsed[0]) if parsed else ()
        if (not columns or len(set(columns)) != len(columns) or list(columns) != result.get("columns")
                or not isinstance(result.get("metadata"), dict)):
            raise ValueError("Analysis columns or metadata do not match the manifest.")
        if result.get("name") == "RMSF":
            if columns != ("residue_id", "rmsf_nm", "atom_index"):
                raise ValueError("RMSF needs explicit atom indices and residue IDs for a 3D link.")
        elif "time_ns" not in columns or "frame" not in columns:
            raise ValueError("Analysis needs explicit frame indices and time_ns for a 3D link.")
        rows = tuple(tuple(float(value) for value in row) for row in parsed[1:])
        if (type(result.get("rows")) is not int or not rows or len(rows) != result["rows"]
                or any(len(row) != len(columns) or not all(math.isfinite(value) for value in row) for row in rows)):
            raise ValueError("Analysis rows are missing, non-finite or do not match the manifest.")
        return columns, rows, manifest, result
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, KeyError, ValueError) as exc:
        raise ValueError(f"3D link unavailable: {exc}. Use a completed analysis with its analysis_manifest.json.") from exc
