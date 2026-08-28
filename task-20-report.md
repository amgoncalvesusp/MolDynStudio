# Task 20 Report — Post-stage MD quality gates

## Status

DONE

## Outcome

- Added deterministic post-stage QC sourced only from each stage's validated
  GROMACS log artifact.
- Recorded observed potential-energy endpoint drift, temperature range, and
  pressure range without synthesizing missing values.
- Kept scientific outliers, missing optional diagnostics, and malformed
  optional diagnostics as visible warnings. A selected required diagnostic
  becomes fatal only when it is missing or unusable.
- Integrated QC after required artifact validation and before `COMPLETED` is
  persisted. Explicit fatal QC routes through the existing stage failure and
  downstream invalidation path; warnings still allow completion and resume.
- Persisted deterministic per-stage payloads under
  `manifest.metadata["stage_qc"]`, preserving manifest schema 1.0 and unrelated
  metadata. Reruns and downstream invalidation clear stale QC payloads.
- Reused the existing MD Run stage labels and terminal log. Warning completion
  displays as `Completed (QC warning)`, fatal QC as `Failed (QC)`, and QC lines
  use a dedicated `[QC]` prefix without feeding the live GROMACS plot parser.

## TDD evidence

RED:

- `tests/test_stage_qc.py` initially failed because `core.stage_qc` did not
  exist.
- Runner integration tests initially failed because `MDPipelineService` had no
  QC injection or persistence boundary.
- MD Run tests initially failed because persisted/live QC had no dedicated
  status or log presentation.
- Reviewer regressions reproduced valid `Potential Energy = ...` data being
  labeled malformed and checkpoint resume being enabled without `md.log` and
  `md.edr`.

GREEN:

- Focused and adjacent suite:
  `python -m pytest tests/test_stage_qc.py tests/test_md_pipeline_runner.py tests/test_md_run_tab.py tests/test_run_manifest.py tests/test_md_pipeline.py tests/test_gromacs_log_parser.py tests/test_project_manager.py tests/test_md_setup_tab.py -q`
  — 91 passed, 38 subtests passed.
- Full suite: `python -m pytest -q` — 268 passed, 1 skipped, 73 subtests passed.
- Focused QC coverage: 92% (177/193 statements).
- Ruff, Python compilation, `git diff --check`, and the Impeccable detector all
  passed. The detector returned no findings for `tabs/md_run_tab.py`.

## Reviewer follow-up

- Aligned the production-checkpoint UI gate with the backend by requiring all
  four resume inputs: `md.tpr`, `md.cpt`, `md.log`, and `md.edr`.
- Added direct support for the valid `Potential Energy = ...` diagnostic form
  so it is recorded as observed energy data rather than a malformed warning.
- Confirmed fatal QC is persisted before the stage is failed and that stale
  downstream QC entries are removed during invalidation.

## Strict-JSON hardening follow-up

- Reproduced Python overflow parsing (`1e309`) as non-finite QC samples that
  could otherwise reach manifest observations as `Infinity` or `NaN`.
- Sanitized observations at QC-check construction and again at manifest
  serialization. Non-finite numeric values are omitted, affected messages
  explain the omission, and the existing warning/fatal decision is preserved.
- Added unit coverage for raw and serialized observations plus an integration
  regression that reads the persisted manifest with a strict JSON decoder.
- TDD RED confirmed raw `QCCheck.observations` contained non-finite values;
  GREEN confirmed strict serialization and persistence while finite behavior
  remained covered by the existing diagnostic tests.
- Verification: focused QC tests passed (8 tests, 92% coverage); the full suite
  passed (270 tests, 1 skipped, 73 subtests); touched-file Ruff, Python
  compilation, and `git diff --check` passed. Repository-wide Ruff remains
  blocked by 40 pre-existing findings in `tests/test_run_manifest.py`.

## Limits

- No live GROMACS or WSL execution was performed or claimed. Validation used
  deterministic unit, pipeline, manifest, and offscreen Qt tests.
- No secrets, network calls, pushes, releases, or SDD ledger edits were made.
