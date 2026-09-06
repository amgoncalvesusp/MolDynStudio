# MolDynStudio v1.1.1

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

Covalent protein–ligand structures and arbitrary automatic CGenFF ligand
generation are unsupported. CHARMM36m remains available for systems whose
residues are already provided by the locally imported force-field package; it
does not add automatic CGenFF ligand generation.

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

- Windows: `MolDynStudio-windows-x86_64.zip`
- Linux: `MolDynStudio-linux-x86_64.run`

Use `SHA256SUMS.txt` from the release assets to verify downloads.

## Development

```bash
pip install -r requirements.txt
python main.py
python -m unittest discover -s tests
```

See `README_GROMACS_Analysis_Studio_v11.md` for setup and release-build details.

## Integrated CHARMM36m

CHARMM36m system preparation never downloads force-field files at runtime.
From **MD Setup**, select **Download CHARMM36m** once. The application fetches
the official package directly from the MacKerell Laboratory over HTTPS.
Alternatively, use **Import package…** with the official
`charmm36-feb2026_cgenff-5.0.ff.tgz` archive. MolDynStudio verifies
its SHA-256 checksum and stores it in the local application cache. Subsequent
simulations run without Internet access.

Version 1.1.1 also enforces CHARMM force-switch/cutoff settings when generating
MDPs through the backend, blocks incompatible automatic GAFF2 ligand assignment,
rejects invalid/empty MD outputs, and preserves Linux force-field caches on upgrade.

The current ligand workflow intentionally supports non-covalent ligands only.
Automatic ACPYPE/GAFF2 parameterization is restricted to AMBER-family protein
force fields. Covalent protein-ligand structures are detected and rejected
before topology generation. Automatic generation of arbitrary CGenFF ligand
parameters is not bundled.

See `assets/forcefields/README.md` for the official package name, checksum,
and redistribution note.

Third-party terms and scientific citations: `assets/licenses/THIRD_PARTY_NOTICES.md`.
