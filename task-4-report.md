# Task 4 report

Implemented deterministic tokenized MD stage command builders in
`core/md_pipeline.py` and coverage in `tests/test_md_pipeline.py`.

- `PipelineCommand` stores tuple arguments and project working directory.
- `PipelineStage` stores an immutable stage name and command sequence.
- Builders cover preparation checkpoint, minimization, NVT, NPT, and
  production stages using relative artifact names.
- `build_pipeline` keeps canonical dependency order and supports resume slicing.
- No pipeline command adds `-maxwarn`.

Validation:

- `python -m unittest tests.test_md_pipeline -v` — passed (4 tests).
- `python -m unittest discover -v` — 128 tests observed: 126 passed and 2
  failures in `tests/test_md_run_tab.py` related to the mock MD UI path
  (Task 1/13 scope).
