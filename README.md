# MolDynStudio v1.3.0

Copyright (C) 2026 Adriano Marques Gonçalves.
MolDynStudio is free software under the GNU General Public License version 3
(GPL-3.0-only). You may redistribute and modify it under those terms. It is
provided WITHOUT ANY WARRANTY, including MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See [LICENSE](LICENSE). Third-party components retain their
own licenses; see [third-party notices](assets/licenses/THIRD_PARTY_NOTICES.md).

Inventor: Adriano Marques Gonçalves (UNIARA)

MolDynStudio is a PyQt5 desktop application for preparing systems, running a real
four-stage GROMACS molecular-dynamics pipeline, monitoring its logs and
thermodynamic data, and reviewing post-MD analysis outputs. The MD Run page
executes the selected GROMACS build; it is not a simulated progress demo.

Version 1.1.0 completes the real MD execution path that was previously exposed
as a preview/mock flow. A trajectory is only available when the selected
GROMACS commands complete successfully and produce the corresponding output
files; opening an older `.mds` file does not imply that it contains a trajectory.

## Supported MD workflows

MD Setup and MD Run support these workflows:

- **Protein-only:** prepare and run a protein system through minimization, NVT,
  NPT, and production MD.
- **Protein + separate non-covalent ligand:** use an AMBER-family protein force
  field with the built-in ACPYPE/GAFF2 ligand parameterization path.
- **CHARMM36m + non-covalent ligand:** submit a reviewed MOL2 with explicit
  hydrogens to SwissParam MATCH, then convert the supported CHARMM parameters
  locally with ParmEd. New submissions require explicit ligand-upload consent;
  verified cached results can be reused offline.
- **Multiple bound copies from a complex PDB:** use the CHARMM ligand route to
  retain every matching ligand residue with its own heavy-atom coordinates.
  The MOL2 supplies reviewed chemistry; exclusions of other compounds are explicit.

Covalent protein–ligand adducts and general metal-coordination parameterization
remain unsupported. The MATCH route accepts a restricted isolated-ligand
topology, checks chemical identity and expected charge, and records its
provenance; it is not a general importer for arbitrary CGenFF patches. See the
[English user manual](assets/docs/USER_MANUAL.md#45-prepare-a-noncovalent-ligand-with-charmm36m-and-match)
for the complete workflow and the 6D15 example.

Before execution, **Review MD run** opens the prepared settings and all four
actual MDP files. Recorded MD Setup edits remain pending until restored or
prepared again, blocking **Run All** and **Resume**. Older projects explicitly
report an unrecorded setup comparison. **Check environment** probes the selected
GROMACS build in the background; compiled GPU support does not establish actual
GPU use. See the [run procedure](assets/docs/USER_MANUAL.md#51-execute-the-four-stages)
and [candidate validation status](assets/docs/VALIDATION_REPORT.md#md-setup--md-run-review--27-october-2026).

## Integrated 3D inspection

Open **View > 3D structures and trajectories** for the project's dockable native
viewer, initially below the workflow and detachable as a floating panel.
**Inspect structure in 3D** on MD Setup and **Open Trajectory Viewer** on Analysis
reuse that panel. Load a readable PDB/GRO or compatible TPR alone to inspect one
structure, or add a matching trajectory for frame navigation, playback, and
reader-reported time. Structure-only inspection has no simulation time and
cannot export a molecular-dynamics frame to DockLens.

The viewer reads frames from disk through MDAnalysis and uses the existing
Matplotlib/Qt dependencies. It draws selected atoms and available source bonds
without inferring connectivity. Large selections are reduced to about 6,000
displayed atoms; export retains the full validated selections. Production
completion offers the output paths; choose **Load 3D view** to open the reader.
Saved sessions restore paths, selections, frame index, and panel visibility,
then require that same explicit load and a fresh periodic-boundary review.
See the [3D procedure](assets/docs/USER_MANUAL.md#91-open-and-navigate-the-integrated-3d-view)
and [separate source validation status](assets/docs/VALIDATION_REPORT.md#integrated-3d-viewer--27-october-2026).

In **Results Viewer > QC Dashboard**, click a measured plot point or choose a
source file and row under **Measured points**, then **Show selected point in
3D**. Generated analysis CSV files with their adjacent `analysis_manifest.json`
link to exact source frames and times; RMSF points highlight the atom's source
residue without changing export selections. Source and output hashes are
checked before inspection. External series without this provenance remain
readable, with an explicit explanation when 3D linking is unavailable.
See the [linked-analysis procedure](assets/docs/USER_MANUAL.md#84-inspect-a-measured-point-in-3d)
and [third-delivery checks](assets/docs/VALIDATION_REPORT.md#analysis-points-linked-to-3d--27-october-2026).

## Restarting an interrupted run

For a production run with a valid `md.cpt` checkpoint, select **Resume
Production checkpoint** on MD Run. MolDynStudio resumes GROMACS with `-cpi`
and the existing `md.tpr`, `md.log`, and `md.edr` inputs. Keep the output files
unchanged between interruption and resume so GROMACS can append continuously;
do not replace, truncate, or rename them.

## CPU and GPU execution

The **GPU acceleration** setting is evaluated against the capabilities reported
by the selected GROMACS executable. **Auto** follows that build's available
backend and otherwise permits the build's normal CPU path. Installing a GPU
driver alone does not guarantee that the selected GROMACS build has GPU
support; use the setup diagnostics and select a GPU-enabled build when GPU
execution is required.

## Project artifacts

Each prepared project keeps its manifest, inputs, MDP files, and stage outputs
together:

```text
project/
  moldynstudio_run.json
  system.gro
  topol.top
  em.mdp
  nvt.mdp
  npt.mdp
  md.mdp
  em.*
  nvt.*
  npt.*
  md.*
```

## Downloads

For a published release, download the matching installers from the GitHub
Releases page:

- Windows: `MolDynStudio-windows-x86_64-v1.3.0.zip`
- Linux: `MolDynStudio-linux-x86_64-v1.3.0.run`

Use `SHA256SUMS-v1.3.0.txt` from the release assets to verify downloads.
See the [v1.3.0 release notes](assets/docs/RELEASE_NOTES_v1.3.0.md) for changes,
validation, and remaining scientific and platform limits.

## Development

```bash
pip install -r requirements.txt
python main.py
python -m unittest discover -s tests
```

For the full regression suite (including scientific checks written as pytest
functions), run `python -m pytest -q`. The GUI environment now includes
MDAnalysis and MDTraj for local analysis and trajectory frame inspection.
Python 3.11 or newer is required. The default scientific environment pins
GROMACS 2025.4 to keep generated TPR files compatible with MDAnalysis 2.10.
GROMACS 2026 TPR files require a newer compatible reader or an explicitly
converted, matching PDB/GRO structure for coordinate analyses. Those structures
do not replace charge-aware topology for hydrogen-bond analysis.

## Workflow guide and research evidence

See the [English user manual](assets/docs/USER_MANUAL.md) for installation,
system preparation, simulation, analysis, and the supported scientific methods.
It includes verified literature references and explains the limits of each result.
The [validation record](assets/docs/VALIDATION_REPORT.md) documents the audit,
implemented integrations, measured test coverage, and remaining validation limits.

The revised workflow executes selected trajectory analyses in background jobs,
writes measured CSV tables with a provenance manifest, and hands completed
production files to Analysis. The frame inspector exports a receptor and ligand
from the same trajectory frame and opens that pair in a compatible DockLens
installation. Review periodic boundaries and chemical typing before interpreting
contacts; a single frame does not measure interaction occupancy.

Reports & Export creates a ZIP evidence snapshot with SHA-256 records and the
recorded stage states. Large/binary simulation files are hashed but not copied;
retain their originals. Advanced configuration pages that do not execute a
backend are explicitly marked as previews. QC summaries are heuristic stability
checks, not demonstrations of convergence or binding affinity.

See `README_GROMACS_Analysis_Studio_v11.md` for setup and release-build details.

## Integrated CHARMM36m

CHARMM36m system preparation never downloads force-field files at runtime.
From **MD Setup**, select **Download CHARMM36m** once. The application fetches
the official package directly from the MacKerell Laboratory over HTTPS.
Alternatively, use **Import package…** with the official
`charmm36-feb2026_cgenff-5.0.ff.tgz` archive. MolDynStudio verifies
its SHA-256 checksum and stores it in the local application cache. The protein
force field is then available offline. New MATCH ligand parameters require a
separate consented SwissParam request unless a verified matching cache exists.

Version 1.1.1 also enforces CHARMM force-switch/cutoff settings when generating
MDPs through the backend, blocks incompatible automatic GAFF2 ligand assignment,
rejects invalid/empty MD outputs, and preserves Linux force-field caches on upgrade.

The current ligand workflow intentionally supports non-covalent ligands only.
Automatic ACPYPE/GAFF2 parameterization is restricted to AMBER-family protein
force fields. CHARMM36m uses SwissParam MATCH (CGenFF-derived) with local
ParmEd 4.3.1 conversion. The expected net charge, explicit hydrogen pattern,
atom identities, and connectivity must agree with the reviewed MOL2. Only the
ligand is uploaded; protein files remain local. Covalent protein-ligand
structures are detected and rejected before topology generation.

MATCH-derived ligand parameters are not claimed equivalent to a licensed-server
CGenFF 5.0 assignment. Ligand types are given unique names and combined with
the protein through CHARMM/Lorentz–Berthelot mixing; missing intermolecular
NBFIX corrections are not inferred. A short execution test does not validate
these cross interactions or quantitative binding predictions.

See `assets/forcefields/README.md` for the official package name, checksum,
and redistribution note.

Third-party terms and scientific citations: `assets/licenses/THIRD_PARTY_NOTICES.md`.
