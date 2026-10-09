"""RMSF analysis using MDAnalysis."""

from __future__ import annotations

from analysis.base import AnalysisBase, AnalysisResult
from analysis.trajectory import managed_universe, atom_metadata


class RMSFAnalysis(AnalysisBase):
    name = "RMSF"
    tool = "mdanalysis"
    tooltip = "Per-atom or per-residue positional fluctuation."

    def run(self, topology: str, trajectory: str, selection: str = "protein and name CA") -> AnalysisResult:
        from MDAnalysis.analysis import rms
        from MDAnalysis.transformations import fit_rot_trans

        with managed_universe(topology, trajectory, selection) as (universe, atoms):
            with managed_universe(topology, trajectory, selection) as (reference, reference_atoms):
                if len(atoms) < 3:
                    raise ValueError("RMSF alignment requires at least three selected atoms.")
                universe.trajectory.add_transformations(fit_rot_trans(atoms, reference_atoms))
                analysis = rms.RMSF(atoms)
                analysis.run()
                return AnalysisResult(self.name, analysis.results.rmsf, "Per-atom RMSF after fitting selected atoms to frame 0; no residue averaging.",
                                      ("atom_index", "residue_id", "rmsf_angstrom"),
                                      {**atom_metadata(atoms), "reference_frame": 0, "alignment": "fit_rot_trans"})
