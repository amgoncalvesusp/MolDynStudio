"""Generate GROMACS .mdp files from immutable GUI parameters."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict


@dataclass(frozen=True)
class MDParameters:
    """Simulation parameters shared by generated MDP stages."""

    duration_ns: float = 100.0
    timestep_fs: float = 2.0
    temperature_k: float = 300.0
    pressure_bar: float = 1.0
    save_every_ps: float = 10.0
    force_field: str = "AMBER99SB-ILDN"
    water_model: str = "TIP3P"
    box_type: str = "Dodecahedron"
    box_padding_nm: float = 1.2
    ion_concentration_m: float = 0.15

    def normalized(self) -> "MDParameters":
        """Return a clamped copy suitable for file generation."""

        return replace(
            self,
            duration_ns=max(0.001, float(self.duration_ns)),
            timestep_fs=max(0.001, float(self.timestep_fs)),
            temperature_k=max(1.0, float(self.temperature_k)),
            pressure_bar=max(0.001, float(self.pressure_bar)),
            save_every_ps=max(0.001, float(self.save_every_ps)),
            box_padding_nm=max(0.1, float(self.box_padding_nm)),
            ion_concentration_m=max(0.0, float(self.ion_concentration_m)),
        )


def _steps_for(params: MDParameters) -> tuple[int, int]:
    normalized = params.normalized()
    total_ps = normalized.duration_ns * 1000.0
    step_ps = normalized.timestep_fs / 1000.0
    nsteps = max(1, int(round(total_ps / step_ps)))
    nstxout = max(1, int(round(normalized.save_every_ps / step_ps)))
    return nsteps, nstxout


def generate_minimization_mdp(params: MDParameters) -> str:
    params = params.normalized()
    return f"""; MolDynStudio energy minimization
integrator              = steep
emtol                   = 1000.0
emstep                  = 0.01
nsteps                  = 50000
cutoff-scheme           = Verlet
nstlist                 = 20
rvdw                    = 1.0
rcoulomb                = 1.0
coulombtype             = PME
pbc                     = xyz
; Force field: {params.force_field}
; Water model: {params.water_model}
"""


def generate_nvt_mdp(params: MDParameters) -> str:
    params = params.normalized()
    return f"""; MolDynStudio NVT equilibration
define                  = -DPOSRES
integrator              = md
nsteps                  = 50000
dt                      = {params.timestep_fs / 1000.0:.4f}
nstxout-compressed      = 500
nstenergy               = 500
nstlog                  = 500
continuation            = no
constraint_algorithm    = lincs
constraints             = h-bonds
cutoff-scheme           = Verlet
nstlist                 = 20
rvdw                    = 1.0
rcoulomb                = 1.0
coulombtype             = PME
tcoupl                  = V-rescale
tc-grps                 = Protein Non-Protein
tau_t                   = 0.1 0.1
ref_t                   = {params.temperature_k:.1f} {params.temperature_k:.1f}
pcoupl                  = no
pbc                     = xyz
gen_vel                 = yes
gen_temp                = {params.temperature_k:.1f}
gen_seed                = -1
"""


def generate_npt_mdp(params: MDParameters) -> str:
    params = params.normalized()
    return f"""; MolDynStudio NPT equilibration
define                  = -DPOSRES
integrator              = md
nsteps                  = 50000
dt                      = {params.timestep_fs / 1000.0:.4f}
nstxout-compressed      = 500
nstenergy               = 500
nstlog                  = 500
continuation            = yes
constraint_algorithm    = lincs
constraints             = h-bonds
cutoff-scheme           = Verlet
nstlist                 = 20
rvdw                    = 1.0
rcoulomb                = 1.0
coulombtype             = PME
tcoupl                  = V-rescale
tc-grps                 = Protein Non-Protein
tau_t                   = 0.1 0.1
ref_t                   = {params.temperature_k:.1f} {params.temperature_k:.1f}
pcoupl                  = Parrinello-Rahman
pcoupltype              = isotropic
tau_p                   = 2.0
ref_p                   = {params.pressure_bar:.3f}
compressibility         = 4.5e-5
pbc                     = xyz
gen_vel                 = no
"""


def generate_production_mdp(params: MDParameters) -> str:
    params = params.normalized()
    nsteps, nstxout = _steps_for(params)
    return f"""; MolDynStudio production MD
integrator              = md
nsteps                  = {nsteps}
dt                      = {params.timestep_fs / 1000.0:.4f}
nstxout-compressed      = {nstxout}
nstenergy               = {nstxout}
nstlog                  = {nstxout}
continuation            = yes
constraint_algorithm    = lincs
constraints             = h-bonds
cutoff-scheme           = Verlet
nstlist                 = 20
rvdw                    = 1.0
rcoulomb                = 1.0
coulombtype             = PME
tcoupl                  = V-rescale
tc-grps                 = Protein Non-Protein
tau_t                   = 0.1 0.1
ref_t                   = {params.temperature_k:.1f} {params.temperature_k:.1f}
pcoupl                  = Parrinello-Rahman
pcoupltype              = isotropic
tau_p                   = 2.0
ref_p                   = {params.pressure_bar:.3f}
compressibility         = 4.5e-5
pbc                     = xyz
gen_vel                 = no
"""


def generate_all_mdp(params: MDParameters) -> Dict[str, str]:
    """Return all standard MolDynStudio MDP files."""

    return {
        "em.mdp": generate_minimization_mdp(params),
        "nvt.mdp": generate_nvt_mdp(params),
        "npt.mdp": generate_npt_mdp(params),
        "md.mdp": generate_production_mdp(params),
    }

