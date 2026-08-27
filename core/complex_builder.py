"""Coordination helpers for assembling protein--ligand project inputs."""

from __future__ import annotations

from pathlib import Path

from utils.topology_builder import (
    LigandParamWorker,
    LigandParams,
    LigandTopologyArtifacts,
    normalize_acpype_outputs,
)


def normalize_ligand_topology(
    acpype_dir: str | Path, project_dir: str | Path
) -> LigandTopologyArtifacts:
    """Normalize ACPYPE output for consumption by the complex pipeline."""

    return normalize_acpype_outputs(acpype_dir, project_dir)


class LigandTopologyCoordinator:
    """Small integration boundary exposing structured worker results."""

    def __init__(self, params: LigandParams, parent=None):
        self.worker = LigandParamWorker(params, parent=parent)
        self.artifacts: LigandTopologyArtifacts | None = None
        self.worker.artifacts_ready.connect(self._set_artifacts)

    def _set_artifacts(self, artifacts: object) -> None:
        if isinstance(artifacts, LigandTopologyArtifacts):
            self.artifacts = artifacts

    def start(self) -> LigandParamWorker:
        self.worker.start()
        return self.worker

