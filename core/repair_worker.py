"""Crash-isolated PDBFixer process entry point for source and frozen builds."""
from dataclasses import asdict
import json
from pathlib import Path
import sys


def main(request_path: str | Path) -> int:
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    result_path = Path(request["result_path"])
    arguments = {key: value for key, value in request.items() if key != "result_path"}
    try:
        from core.pdbfixer_backend import _build_missing_structure_local

        result = _build_missing_structure_local(**arguments, on_progress=lambda text: print(text, flush=True))
        payload = {"status": "complete", "result": {**asdict(result), "output_path": str(result.output_path)}}
        code = 0
    except Exception as error:
        payload = {"status": "failed", "error_type": type(error).__name__, "error": str(error)}
        code = 1
    result_path.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Expected a reconstruction request JSON path.")
    raise SystemExit(main(sys.argv[1]))
