"""Central rich-text tooltips for MolDynStudio controls."""

from __future__ import annotations

from typing import Mapping


TOOLTIPS: Mapping[str, str] = {
    "force_field": (
        "<b>Force Field</b><br>"
        "Defines the energy function for the MD simulation.<br>"
        "<ul>"
        "<li><b>AMBER99SB-ILDN</b>: strong default for proteins.</li>"
        "<li><b>CHARMM36m</b>: useful for membrane proteins and broad biomolecular systems.</li>"
        "<li><b>OPLS-AA</b>: often used for organic molecules.</li>"
        "</ul>"
    ),
    "water_model": (
        "<b>Water Model</b><br>"
        "<ul>"
        "<li><b>TIP3P</b>: fast, common, and compatible with AMBER-style workflows.</li>"
        "<li><b>SPC/E</b>: improved diffusion behavior.</li>"
        "<li><b>TIP4P-Ew</b>: more accurate but slower.</li>"
        "</ul>"
    ),
    "box_padding": (
        "<b>Box padding</b><br>"
        "Distance from solute surface to box edge. 1.0-1.2 nm is a common starting range."
    ),
    "ion_concentration": (
        "<b>Ion concentration</b><br>"
        "Salt concentration used during neutralization/ion addition. 0.15 M NaCl is physiological."
    ),
    "parameterization": (
        "<b>Ligand parameterization</b><br>"
        "ACPYPE/GAFF2 are common AMBER-family choices; CGenFF is better aligned with CHARMM workflows."
    ),
    "mm_gbsa": (
        "<b>MM-GBSA</b><br>"
        "Estimates protein-ligand binding free energy using molecular mechanics and generalized Born solvation."
    ),
    "mm_pbsa": (
        "<b>MM-PBSA</b><br>"
        "Uses Poisson-Boltzmann implicit solvent. It is often slower and can be useful for charged systems."
    ),
    "cpptraj": (
        "<b>CPPTRAJ</b><br>"
        "AmberTools trajectory analysis engine. Scripts run through the configured conda environment."
    ),
    "duration": "<b>Production duration</b><br>Total production simulation time in nanoseconds.",
    "timestep": "<b>Timestep</b><br>2 fs is standard with constrained bonds to hydrogens.",
    "save_every": "<b>Save interval</b><br>Trajectory and energy output cadence. Smaller values create larger files.",
}


def tooltip(key: str) -> str:
    return TOOLTIPS.get(key, "")

