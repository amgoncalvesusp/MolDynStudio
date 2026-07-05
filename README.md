# MolDynStudio v1.0.1

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
