from __future__ import annotations

from dataclasses import replace

from core.preparation_orchestrator import PreparationOrchestrator
from core.run_manifest import load_manifest
from core.run_review import setup_review_snapshot
from tests.test_preparation_orchestrator import MaterializingRunner, request
from tests.test_run_review import state


def test_completed_preparation_persists_exact_reviewable_choices(tmp_path):
    preparation = request(tmp_path)
    service = PreparationOrchestrator(preparation, runner=MaterializingRunner(),
                                     structure_validator=lambda _path: None)
    result = service.run()
    manifest = load_manifest(result.manifest)
    snapshot = manifest.metadata["preparation_review"]["setup_snapshot"]
    assert snapshot == setup_review_snapshot(state(preparation))
    assert manifest.schema_version == "1.0"


def test_repreparation_replaces_scientific_snapshot_instead_of_reusing_old_values(tmp_path):
    preparation = request(tmp_path)
    for temperature in (300.0, 310.0):
        changed = replace(preparation, md_parameters=replace(preparation.md_parameters, temperature_k=temperature))
        result = PreparationOrchestrator(changed, runner=MaterializingRunner(),
                                         structure_validator=lambda _path: None).run()
        review = load_manifest(result.manifest).metadata["preparation_review"]
        assert review["setup_snapshot"]["temperature"] == temperature


def test_failed_repreparation_does_not_keep_previously_prepared_snapshot(tmp_path):
    import pytest

    from core.preparation_orchestrator import PreparationError

    preparation = request(tmp_path)
    first = PreparationOrchestrator(preparation, runner=MaterializingRunner(),
                                    structure_validator=lambda _path: None).run()
    with pytest.raises(PreparationError):
        PreparationOrchestrator(preparation, runner=MaterializingRunner(fail_on="pdb2gmx"),
                                structure_validator=lambda _path: None).run()
    assert "preparation_review" not in load_manifest(first.manifest).metadata
