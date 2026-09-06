# Offline CHARMM36m package

MolDynStudio does not download force fields while preparing a simulation.

To enable CHARMM36m, choose **Download CHARMM36m** in **MD Setup**. This explicit
action retrieves the official GROMACS package directly from the MacKerell
Laboratory. Alternatively, obtain and **Import package…** once:

- File: `charmm36-feb2026_cgenff-5.0.ff.tgz`
- SHA-256: `c2b0c70e21cced150528ee25d205e5bd3367a5e55e85c2ff0403911980528151`
- Official page: <https://mackerell.umaryland.edu/charmm_ff.shtml>

The GUI validates the checksum, safely extracts the package into the
per-user MolDynStudio data directory, and copies it into each CHARMM project.
No Internet connection is used during simulation after installation. Downloads
have a size limit and timeout; failures never install unverified files. Retry
or import a local copy when the upstream server is unavailable.

The force-field archive is intentionally not included in the public source
distribution: the upstream CHARMM source repository has an MIT license, but
redistribution terms covering this converted GROMACS archive were not confirmed.
This is not a claim that CHARMM parameters are proprietary. See
`../licenses/THIRD_PARTY_NOTICES.md` for provenance and scope. Distributions
with separate authorization may place
the official archive in this directory; MolDynStudio will detect and install
it locally without changing the application code.
