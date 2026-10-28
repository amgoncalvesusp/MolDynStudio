# MolDynStudio v1.3.0

This release connects preparation, execution, inspection, and measured analysis
without replacing the existing GROMACS workflow or adding a second renderer.

## Changes

- MD Setup passes its protocol choices to MD Run. A preparation review shows the
  actual MDP files and flags pending setup changes before execution or resume.
- One dockable/floating 3D panel inspects structures and trajectories from Setup
  and Analysis, with frame navigation, playback, saved view state, and only
  topology-provided bonds. Readers are released before project writes.
- Results Viewer connects plotted points and read-only measured rows to exact
  source frames. Adjacent analysis manifests, output/source hashes, frame indices,
  and recorded times are checked before navigation.
- RMSF points identify their original atom and highlight the unique source
  residue. Repeated residue numbers in different segments remain distinct.
  Highlighting does not alter receptor/ligand export selections.
- Failed navigation restores the reader cursor. Overlapping plotted points
  require an explicit row choice; busy operations report rejected requests.
- Windows XTC/TRR readers use a verified ASCII short-path alias when available
  for Unicode paths. Original paths, hashes, and export provenance are retained.
- CHARMM installation recovers from Python HTTPS timeout/reset failures through
  system curl 8.4.0+ using HTTP/1.1. Certificate verification, HTTPS redirects,
  the shared deadline, byte limit, and exact archive/tree checks stay enabled.
  Other download or validation failures remain explicit.

## Dependency security

Both GUI requirement sets now require Pillow 13.0.0+, filelock 4.2.1+,
certifi 2026.10.19+, and setuptools 84.1.0+. These floors address advisories
found during release preparation. The upgraded Windows and Linux validation
environments passed dependency consistency and advisory checks with no known
vulnerabilities. Exact distributed versions appear in the Windows runtime
license/source manifest; Linux resolves compatible versions during installation.

## Validation before publication

- Windows: 801 tests and 249 subtests passed; two opt-in engine tests skipped.
  Configured statement coverage: 85.88%; six runtime checks passed.
- Linux/WSL2: 797 tests and 242 subtests passed; six tests skipped (two opt-in
  engine checks and four Windows-specific checks). Statement coverage: 85.58%;
  six runtime checks passed.
- Local discovery included six existing project verification cases outside the
  published source. The clean release checkout therefore contains 797 cases.
- The separate four-test real GROMACS 2025.4 module passed, including a fresh
  official CHARMM download, preparation, and minimization. The final public
  downloader also passed with the curl version guard on Windows and Linux.
- Real 6D15 analyses generated 11 RMSD points, 270 C-alpha RMSF points, and 11
  radius-of-gyration points. GUI journeys selected frame 10 at 2 ps, highlighted
  CYS46 by source residue index 45, and preserved same-frame export coordinates
  and selections for 4,017 protein atoms and 58 ligand atoms. Source hashes stayed
  unchanged. Windows used offscreen Qt; Linux also used native WSLg/xcb.

The GitHub release notes record the exact build revision and CI run. Versioned
installers, corresponding sources, provenance, and `SHA256SUMS-v1.3.0.txt`
identify the distributed files.

## Scope and limits

External XVG/CSV files remain readable; 3D links require a verified completed
result and its adjacent `analysis_manifest.json`. Absolute manifest paths must
resolve locally; no cross-OS path remapping is performed. RMSF remains per-atom
after alignment, with no residue averaging. Export still requires explicit PBC
review and does not add highlighted atoms to an export selection.

Short execution and interoperability checks do not establish equilibrium,
affinity, interaction occupancy, or general force-field suitability. GPU and
long-run MD performance were not benchmarked. GROMACS remains an external
native/WSL backend. Existing advanced pages labeled as previews remain previews.

See the [user manual](USER_MANUAL.md), [validation history](VALIDATION_REPORT.md),
and [rebuilding instructions](../licenses/REBUILD.md).
