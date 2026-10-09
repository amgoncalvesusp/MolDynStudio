"""Secondary structure analysis."""

from __future__ import annotations

from analysis.base import AnalysisBase, AnalysisResult
from analysis.trajectory import load_mdtraj


class DSSPAnalysis(AnalysisBase):
    name = "DSSP"
    tool = "mdtraj"
    tooltip = "Secondary structure assignment through MDTraj DSSP."

    def run(self, topology: str, trajectory: str, simplified: bool = True, selection: str = "protein") -> AnalysisResult:
        import mdtraj as md

        traj, metadata = load_mdtraj(topology, trajectory, selection)
        assignments = md.compute_dssp(traj, simplified=simplified)
        rows = [(frame, float(traj.time[frame]), residue, str(code))
                for frame, codes in enumerate(assignments) for residue, code in enumerate(codes)]
        return AnalysisResult(self.name, rows, "Simplified DSSP: H helix, E strand, C coil; NA lacks backbone atoms.",
                              ("frame", "time_ps", "residue_index", "secondary_structure"),
                              {**metadata, "residue_labels": [str(r) for r in traj.topology.residues]})

