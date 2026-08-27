# Task 2 — Persistent MD run manifest

## Changes

- Implemented the official `RunManifest` data contract with five stages and explicit status values.
- Added typed `RunManifestError` handling and strict validation for schema, top-level scalar fields, stage records, command records, and input/output mappings.
- Added atomic persistence using a same-directory temporary file, `flush`, `os.fsync`, and `os.replace`, including temporary-file cleanup on failure.

## Tests

- `python -m unittest tests.test_run_manifest -v`: 8 tests passed.
- `python -m unittest discover -v`: existing suite still has the two known Task 1 failures in `tests.test_md_run_tab` because the unrelated mock UI has not yet been replaced.

## Risks

- Full-suite green status depends on completion of Task 1; no files from that task were modified here.
- Manifest schema is intentionally fixed at version `1.0`; future schema changes require an explicit migration/version update.
