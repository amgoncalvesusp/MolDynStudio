# Task 8 — Orchestrated MD system preparation

Implemented a deterministic `PreparationOrchestrator` service and a thin
`PreparationWorker` Qt wrapper.

- Protein-only preparation now runs `pdb2gmx`, `editconf`, `solvate`, ion
  `grompp`, and `genion` as one high-level operation.
- Protein–ligand preparation runs ACPYPE, normalizes its structured artifacts,
  combines coordinates, patches topology, and only then boxes, solvates, and
  ionizes the complex.
- GROMACS commands contain the configured executable and Conda environment;
  `genion` receives `SOL` through stdin without an inner shell or embedded
  `gmx` command.
- Command execution and command construction are injectable for tests.
- Final `system.gro`, `topol.top`, and normalized ligand files are validated
  before completion.
- `em.mdp`, `nvt.mdp`, `npt.mdp`, and `md.mdp` are generated automatically.
- The run manifest records exact artifact paths and command results, persists
  preparation as `RUNNING`/`FAILED`/`COMPLETED`, and marks minimization `READY`
  only after successful validation.
- `MDSetupTab` now starts exactly one `PreparationWorker`; it no longer starts
  independent system-preparation and ligand-parameterization workers.
- The legacy `SystemPrepWorker` API remains as a compatibility wrapper.

Validation:

- `python -m unittest tests.test_system_prep tests.test_md_setup_tab tests.test_preparation_orchestrator -v` — passed (23 tests).
- Focused coverage — 89% total; `core/preparation_orchestrator.py` 84%,
  `core/system_prep.py` 97%, and `tabs/md_setup_tab.py` 92%.
- Full discovery ran 152 tests: 150 passed. The 2 pre-existing failures are in
  `tests/test_md_run_tab.py` and concern the unrelated `start_mock_run` mock
  pipeline; the same failures are documented in the Task 6 and Task 7 reports.
