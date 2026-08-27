# Task 3 report: MD artifact validators

## Changes

- Added `core/artifact_validation.py` with immutable `ValidationResult`.
- Added strict GRO validation (title, positive atom count, fixed-width atom coordinates, and 3/9-value positive box).
- Added topology validation for `[ system ]`, `[ molecules ]`, and an optional ligand molecule name.
- Added isolated TPR and checkpoint checks through the injectable `wsl_bridge.run` and preserved subprocess diagnostics on failure.
- Added aggregate `validate_stage_outputs` reporting per-artifact results.
- Added fixture-style unit tests in `tests/test_artifact_validation.py`.

## Tests

- `python -m unittest tests.test_artifact_validation -v` — 5 tests passed.
- `python -m unittest discover -s tests -p 'test_*.py'` — 119 tests run; 117 passed and 2 pre-existing MD run tab regression tests failed because `tabs/md_run_tab.py` still exposes `start_mock_run`.

## Risks

- TPR/checkpoint validation requires a functional GROMACS installation and bridge at runtime.
- Aggregate validation recognizes the standard keys (`structure`/`gro`, `topology`/`top`, `tpr`, `checkpoint`/`cpt`).
