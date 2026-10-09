"""Principal component analysis using MDAnalysis."""

from __future__ import annotations

from analysis.base import AnalysisBase, AnalysisResult
from analysis.trajectory import managed_universe, atom_metadata


class PCAAnalysis(AnalysisBase):
    name = "PCA"
    tool = "mdanalysis"
    tooltip = "Dominant collective motions from trajectory covariance."

    def run(self, topology: str, trajectory: str, selection: str = "backbone", n_components: int = 3) -> AnalysisResult:
        from MDAnalysis.analysis import pca
        import numpy as np

        with managed_universe(topology, trajectory, selection) as (universe, atoms):
            if len(atoms) < 3 or len(universe.trajectory) < 2:
                raise ValueError("PCA requires at least three atoms and two trajectory frames.")
            if not 1 <= n_components <= min(3 * len(atoms), len(universe.trajectory) - 1):
                raise ValueError("PCA components exceed the available trajectory rank.")
            if (3 * len(atoms)) ** 2 * 8 > 512 * 1024**2:
                raise ValueError("PCA covariance exceeds 512 MiB; select fewer atoms, for example protein and name CA.")
            # Apply the same fit during covariance estimation and projection.
            from MDAnalysis.transformations import fit_rot_trans
            with managed_universe(topology, trajectory, selection) as (reference, reference_atoms):
                universe.trajectory.add_transformations(fit_rot_trans(atoms, reference_atoms))
                analysis = pca.PCA(universe, select=selection, align=False, n_components=n_components).run()
                projection = analysis.transform(atoms, n_components=n_components)
                variance = np.real_if_close(analysis.results.variance)
                cumulative = np.real_if_close(analysis.results.cumulated_variance)
                if np.iscomplexobj(variance) or not np.isfinite(variance).all() or not np.isfinite(cumulative).all():
                    raise ValueError("PCA returned undefined variance; the selected trajectory may have no internal motion.")
                rows = [(ts.frame, float(ts.time), *map(float, values)) for ts, values in zip(universe.trajectory, projection)]
                return AnalysisResult(self.name, rows, "PCA of coordinates aligned to frame 0; projections in angstrom.",
                                      ("frame", "time_ps", *(f"pc{i + 1}_angstrom" for i in range(n_components))),
                                      {**atom_metadata(atoms), "alignment": "fit_rot_trans", "reference_frame": 0,
                                       "variance_angstrom2": variance.tolist(),
                                       "cumulative_variance": cumulative.tolist()})

