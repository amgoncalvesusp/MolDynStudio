# Task 5 report

Removed the unsafe `-maxwarn 5` override from the ion-preparation `grompp`
command. Preparation failures now include the most recent non-empty output
lines, preserving warning/fatal diagnostics for the user.

Tests added in `tests/test_system_prep.py` verify that the ion `grompp` args do
not contain `-maxwarn` or `5`, and that recent failure output is included in
the emitted error message.

Validation:

- `python -m unittest tests.test_system_prep -v` — passed (8 tests).
- `python -m unittest discover -v` — 130 tests run: 128 passed and 2 failed
  in the pre-existing `tests.test_md_run_tab.py` mock-timer regression checks
  (`start_mock_run`), outside Task 5 scope.
