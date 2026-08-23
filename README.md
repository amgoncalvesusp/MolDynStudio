# MolDynStudio v1.0.2

Inventor: Adriano Marques Gonçalves (UNIARA)

MolDynStudio is a PyQt5 desktop application for molecular dynamics setup, execution monitoring, and post-MD analysis with GROMACS-oriented workflows.

## Downloads

Installers are published on the GitHub Releases page:

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

## Offline CHARMM36m

CHARMM36m system preparation never downloads force-field files at runtime.
From **MD Setup**, select **Import package…** once and choose the
official `charmm36-feb2026_cgenff-5.0.ff.tgz` archive. MolDynStudio verifies
its SHA-256 checksum and stores it in the local application cache. Subsequent
simulations run without Internet access.

The current ligand workflow intentionally supports non-covalent ligands only.
Automatic ACPYPE/GAFF2 parameterization is restricted to AMBER-family protein
force fields. Covalent protein-ligand structures are detected and rejected
before topology generation. Automatic generation of arbitrary CGenFF ligand
parameters is not bundled.

See `assets/forcefields/README.md` for the official package name, checksum,
and redistribution note.
