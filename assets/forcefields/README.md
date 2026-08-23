# Offline CHARMM36m package

MolDynStudio does not download force fields while preparing a simulation.

To enable CHARMM36m, obtain the official GROMACS package from the MacKerell
Laboratory and import it once from the **MD Setup** screen:

- File: `charmm36-feb2026_cgenff-5.0.ff.tgz`
- SHA-256: `c2b0c70e21cced150528ee25d205e5bd3367a5e55e85c2ff0403911980528151`
- Official page: <https://mackerell.umaryland.edu/charmm_ff.shtml>

The GUI validates the checksum, safely extracts the package into the
per-user MolDynStudio data directory, and copies it into each CHARMM project.
No Internet connection is used after import.

The force-field archive is intentionally not included in the public source
distribution because its official download does not state a third-party
redistribution license. Distributions with separate authorization may place
the official archive in this directory; MolDynStudio will detect and install
it locally without changing the application code.
