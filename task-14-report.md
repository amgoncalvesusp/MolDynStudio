# Task 14 Report — Prepared MD project context

## Outcome

MD Setup now hands a successfully prepared project directly to MD Run. The
active project and manifest survive `.mds` save/load, and reopening a session
reconstructs MD Run stage state from `moldynstudio_run.json`.

## Implementation

- Added `MDSetupTab.project_prepared(str, str)` and emit it only after successful
  preparation. Each worker completion captures its own immutable project path,
  so overlapping preparations cannot cross-wire their handoffs.
- Added `MainWindow.active_project_dir` and `active_manifest_path`, connected the
  setup signal to MD Run, and explicitly clear both values for a new session.
- Added schema `2.0` project persistence with `migrate_project_payload()`:
  legacy `1.0` migrates, `2.0` loads directly, and unknown future schemas fail
  with an actionable `ProjectFormatError`.
- Persisted only project/manifest path references. Relative paths use POSIX
  separators on disk, are resolved against the `.mds` location/project root,
  and accept Windows-authored separators when loaded on another platform.
- Preserved the legacy MD Setup-to-MD Run fallback when a migrated `1.0`
  session does not contain the new `project_context` field.
- Added regressions for success/failure signaling, overlapping preparations,
  portable path serialization, legacy migration, explicit clearing, deterministic
  handoff, and manifest-backed reopen.

## Verification

- `python -m unittest tests.test_project_manager tests.test_md_setup_tab tests.test_md_run_tab -v`
  — 34 tests passed.
- `python -m unittest discover -s tests` — 226 tests passed.
- Focused coverage for the newly persisted/setup code — 91% total
  (`core/project_manager.py` 87%, `tabs/md_setup_tab.py` 92%).
- Ruff, Python compilation, and `git diff --check` passed.
- Impeccable detector ran on the changed Qt UI targets. Its only warning is the
  pre-existing sidebar `border-left` style in the incumbent application theme;
  Task 14 does not add or alter that visual rule.
- Independent correctness/security review findings were fixed and covered by
  regression tests before final verification.

No push or release was performed.
