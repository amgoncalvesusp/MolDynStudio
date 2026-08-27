# Task 7 — Protein–ligand complex assembly

Implemented `combine_gro()` in `core/complex_builder.py` to validate both
inputs, preserve protein atoms before ligand atoms, carry the protein box, and
validate the generated `complex.gro`.

Implemented `patch_topology_for_ligand()` to insert the normalized ligand ITP
after force-field includes and add the ligand molecule before solvent/ions.
The operation is idempotent, validates the patched topology, and reparses its
required sections before returning.

Validation:

- `python -m unittest tests.test_complex_builder -v` — passed (4 tests).
- Full discovery has 2 pre-existing failures in `tests/test_md_run_tab.py`
  related to `start_mock_run`; no Task 7 files are involved.
