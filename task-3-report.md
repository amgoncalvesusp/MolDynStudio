# Task 3 report: MD artifact validators

## Changes

- Added `core/artifact_validation.py` with immutable `ValidationResult`.
- Added strict GRO validation (title, positive atom count, fixed-width atom coordinates, and 3/9-value positive box).
- Added topology validation for non-empty `[ system ]`, valid positive-count entries in `[ molecules ]`, and an optional ligand molecule name.
- Topology molecule entries are strict two-field records (`molecule_name count`); non-integer, non-positive, and extra-field records are rejected.
- Added isolated TPR and checkpoint checks through the injectable `wsl_bridge.run` and preserved subprocess diagnostics on failure.
- Added aggregate `validate_stage_outputs` reporting per-artifact results. This is the API gate that a later Task 11 runner integration will consume; no runner/manifest callsite is part of Task 3.
- Added fixture-style unit tests in `tests/test_artifact_validation.py`, including box variants, malformed topology, bridge arguments, bridge exceptions, and aggregate propagation.

## Tests

- `python -m unittest tests.test_artifact_validation -v` — 10 tests passed.
- `python -m unittest discover -s tests -p 'test_*.py'` — 124 tests run; 122 passed and 2 pre-existing MD run tab regression tests failed because `tabs/md_run_tab.py` still exposes `start_mock_run`.

## Risks

- TPR/checkpoint validation requires a functional GROMACS installation and bridge at runtime.
- Aggregate validation recognizes the standard keys (`structure`/`gro`, `topology`/`top`, `tpr`, `checkpoint`/`cpt`).
