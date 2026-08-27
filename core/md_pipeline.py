"""Deterministic, tokenized command definitions for the MD pipeline.

The preparation screen creates ``system.gro`` and ``topol.top``.  The stages
below therefore describe only the reproducible equilibration/production
chain; the preparation stage is retained as an explicit checkpoint with no
commands of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from core.run_manifest import StageName


@dataclass(frozen=True)
class PipelineCommand:
    """One executable invocation, represented as tokens rather than a shell string."""

    args: tuple[str, ...]
    cwd: str


@dataclass(frozen=True)
class PipelineStage:
    """A named stage and the commands executed sequentially within it."""

    name: StageName
    commands: tuple[PipelineCommand, ...]


def _command(project_dir: str | Path, *args: str) -> PipelineCommand:
    return PipelineCommand(tuple(args), str(project_dir))


def build_prepare_stage(project_dir: str | Path) -> PipelineStage:
    """Return the preparation checkpoint.

    Structure/topology preparation is performed by ``SystemPrepWorker``;
    keeping this stage command-free prevents the MD runner from repeating
    interactive ion generation.
    """

    return PipelineStage(StageName.PREPARATION, ())


def build_preparation_stage(project_dir: str | Path) -> PipelineStage:
    """Descriptive alias for :func:`build_prepare_stage`."""

    return build_prepare_stage(project_dir)


def build_minimize_stage(project_dir: str | Path) -> PipelineStage:
    return PipelineStage(
        StageName.MINIMIZATION,
        (
            _command(project_dir, "grompp", "-f", "em.mdp", "-c", "system.gro",
                     "-p", "topol.top", "-o", "em.tpr"),
            _command(project_dir, "mdrun", "-deffnm", "em"),
        ),
    )


def build_minimization_stage(project_dir: str | Path) -> PipelineStage:
    return build_minimize_stage(project_dir)


def build_nvt_stage(project_dir: str | Path) -> PipelineStage:
    return PipelineStage(
        StageName.NVT,
        (
            _command(project_dir, "grompp", "-f", "nvt.mdp", "-c", "em.gro",
                     "-r", "em.gro", "-p", "topol.top", "-o", "nvt.tpr"),
            _command(project_dir, "mdrun", "-deffnm", "nvt"),
        ),
    )


def build_npt_stage(project_dir: str | Path) -> PipelineStage:
    return PipelineStage(
        StageName.NPT,
        (
            _command(project_dir, "grompp", "-f", "npt.mdp", "-c", "nvt.gro",
                     "-r", "nvt.gro", "-p", "topol.top", "-o", "npt.tpr"),
            _command(project_dir, "mdrun", "-deffnm", "npt"),
        ),
    )


def build_production_stage(project_dir: str | Path) -> PipelineStage:
    return PipelineStage(
        StageName.PRODUCTION,
        (
            _command(project_dir, "grompp", "-f", "md.mdp", "-c", "npt.gro",
                     "-p", "topol.top", "-o", "md.tpr"),
            _command(project_dir, "mdrun", "-deffnm", "md"),
        ),
    )


_BUILDERS = {
    StageName.PREPARATION: build_prepare_stage,
    StageName.MINIMIZATION: build_minimize_stage,
    StageName.NVT: build_nvt_stage,
    StageName.NPT: build_npt_stage,
    StageName.PRODUCTION: build_production_stage,
}


def build_pipeline(
    project_dir: str | Path,
    stages: Sequence[StageName] | None = None,
    *,
    resume_from: StageName | None = None,
    resume_from_stage: StageName | None = None,
) -> tuple[PipelineStage, ...]:
    """Build stages in canonical dependency order.

    ``stages`` is a selection (not an ordering instruction).  When resuming,
    the canonical list is sliced at the requested checkpoint before applying
    that selection, so a production run can never precede NPT.
    """

    if resume_from is not None and resume_from_stage is not None:
        raise ValueError("specify only one resume stage")
    selected = set(StageName if stages is None else stages)
    unknown = selected - set(StageName)
    if unknown:
        raise ValueError(f"unknown pipeline stage(s): {sorted(unknown)}")
    start = resume_from if resume_from is not None else resume_from_stage
    canonical = tuple(StageName)
    if start is not None:
        try:
            canonical = canonical[canonical.index(StageName(start)):]
        except (ValueError, TypeError) as exc:
            raise ValueError(f"unknown resume stage: {start!r}") from exc
    return tuple(_BUILDERS[name](project_dir) for name in canonical if name in selected)


__all__ = [
    "PipelineCommand", "PipelineStage", "build_prepare_stage",
    "build_preparation_stage", "build_minimize_stage",
    "build_minimization_stage", "build_nvt_stage", "build_npt_stage",
    "build_production_stage", "build_pipeline",
]
