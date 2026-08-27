# Task 6 — ACPYPE output normalization

Implemented ACPYPE artifact discovery and normalization in `utils/topology_builder.py`.

- Added strict `[ moleculetype ]` parsing via `read_molecule_name_from_itp`.
- Added immutable `LigandTopologyArtifacts` contract.
- Added content/pattern-based ACPYPE output discovery and canonical copies under `project/ligand/`.
- Position restraints are copied only when a valid restraint section is present.
- Preserved `LigandParamWorker.done(bool, str)` and added `artifacts_ready(object)` for structured consumers.
- Added `core.complex_builder` integration boundary and focused tests.

Validation:

- `python -m unittest tests.test_topology_builder tests.test_complex_builder -v` — passed (9 tests).
- Full test discovery has 2 pre-existing failures in `tests/test_md_run_tab.py` concerning `start_mock_run`; no Task 6 files are involved.
