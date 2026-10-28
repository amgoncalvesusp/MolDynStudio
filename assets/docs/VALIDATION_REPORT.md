# Workflow audit and validation record

Last updated: 27 October 2026. The 7–9 October results below describe the MolDynStudio v1.2.0 source scope based on commit `35a5e67`, with subsequent structure-repair and CHARMM/MATCH addenda. Separate sections record the later MD Setup → MD Run review and integrated 3D viewer candidates; earlier counts and packaged-runtime results do not validate either later scope. This record describes local validation, not a published binary release.

## Decisions made before implementation

The audit selected five connected improvements: execute real trajectory analyses in background jobs; preserve source identity and methods in result manifests; inspect and export a receptor–ligand pair from one trajectory frame to DockLens; export a compact evidence package; and make the GUI accurately distinguish executable workflows from configuration previews. These additions reuse the existing preparation, four-stage GROMACS pipeline, WSL bridge, settings, and QC infrastructure.

The intended contribution is traceable movement from simulation to analysis and interaction inspection. A claim of scientific or software novelty still requires a defined comparison with existing tools and reproducible benchmarks. Interface changes alone do not establish novelty.

## Implemented connections and corrections

| Area | Result |
| --- | --- |
| Preparation → simulation | ACPYPE explicitly uses GAFF2 with supported charge methods. Duplicate preparation is blocked. Required pipeline stages remain enabled. |
| CHARMM36m → noncovalent ligand | Explicit-hydrogen MOL2 review, consented SwissParam MATCH submission or verified cache reuse, local ParmEd conversion, retained bound-copy coordinates, explicit exclusions, and parameterization provenance. Covalent adducts remain unsupported. |
| Structural review → preparation | Raw PDB inspection, explicit sequence-to-chain mapping, reviewed repair choices, separate candidates, observed/modeled atom provenance, hash-verified adoption, and revalidation before preparation. The approved report digest persists through `.mds` save/reopen and is checked before preparation. Coordinate readiness remains separate from parameter readiness. |
| Pipeline lifecycle | Rerunning an upstream stage invalidates downstream stage states and stale QC. Production completion forwards actual topology, trajectory, energy, and backend settings to Analysis. |
| Sessions | Atomic project saves, prevalidated restores, reset of old results/settings, and guards against replacing or closing a session during active work. |
| Python analysis | Nine working backends, background execution, unique result directories, normalized CSV units, explicit failures, source/output hashes, selections, mappings, timings, and software versions. |
| Scientific identity | RMSF and PCA use consistent fitting. TPR conversion preserves atom order, source bonds, long chain identities, and separate copies distinguished by molecule number. Ramachandran results match residue identity. Hydrogen bonds require suitable bonds/charges rather than an atom-name guess. |
| GROMACS QC | Shared executable/environment, fresh outputs, numeric validation, recognized unit normalization, separate residue axes, and explicit partial coverage when an optional energy term is absent. Unexpected command failures remain failures. |
| Frame → DockLens | Real frame navigation and paired receptor/ligand export, source indices, chain mapping, connectivity, hashes, and the existing `docklens-launch-v1` manifest contract. Launch uses `--manifest`. |
| CPPTRAJ / MM-PBSA | Background execution, shared settings, explicit working directories, corrected PB input syntax, retained execution records, and busy-state handling. |
| Evidence export | Unique ZIP with recognized text artifacts and SHA-256 records for binary/large files. Recorded failures remain visible. Linked external directories are excluded; OneDrive cloud placeholders remain usable. |
| Native interface | Preserved light/orange visual identity, clearer task controls, visible focus, scrollable layouts, collapsible guidance/logs, useful empty states, and removal of simulated results and fake execution controls. |
| Language and help | English labels and supported preferences reconciled with actual behavior. English manual covers each workflow and preview, including structure repair and CHARMM/MATCH, with 27 scientific references and explicit interpretation limits. |

DockLens was exercised against the sibling project's real manifest reader, paired analysis entry point, and result writer using a synthetic frame pair: one pose, two receptor atoms, two ligand atoms, and zero input-QC errors. No sibling-project code changes were needed. This checks interoperability, not chemical accuracy across ligand classes.

StructLens was also inspected. Its `structlens compare reference.pdb target.pdb --json ... --csv ...` CLI can support a future frame-to-frame receptor comparison. Its GUI lacks an equivalent automatic file-opening contract, so no nominal button was added. SMILES2Select/SMILES2Docking compatibility was not established in this audit.

## General workflow verification baseline — 7 October 2026

These confirmed counts precede the structure-repair extension. They must not be read as final suite or coverage results for every subsequent change.

| Check | Observed result |
| --- | --- |
| Windows full pytest suite, Python 3.12.12 | 385 passed, 2 skipped, 108 subtests passed. The two opt-in Linux engine tests were skipped here. |
| Linux/WSL full pytest suite, Python 3.11.15 | 383 passed, 4 skipped, 103 subtests passed. Skips: two Windows launcher tests and two opt-in engine tests. Test dependencies were installed in a temporary environment; the scientific environment was preserved. |
| Existing configured coverage scope | 89%, 4,770 statements. `.coveragerc` excludes several GUI and analysis files; this is not whole-application coverage. |
| Separate complete `analysis/` measurement | 89%, 942 statements, with no analysis-module omissions; 47 focused tests passed. |
| GROMACS 2025.4 opt-in suite on Linux | Four tests passed: two contracts plus real argon minimization/TPR reading and real CHARMM36m download, preparation, and minimization. |
| Real Windows → WSL QC worker | Nine validated XVG outputs from a synthetic GLY-GLY/TIP3P system after 20 NPT steps: eight time series with 21 rows each and RMSF for two residues. |
| Native Python analysis of that GROMACS TPR/XTC | RMSD, RMSF, radius of gyration, SASA, hydrogen bonds, PCA, and DSSP completed with an analysis manifest. Zero hydrogen-bond events were retained as an empty valid event table. |
| DockLens protocol | Real sibling reader/analysis/result writer accepted the paired frame. Export and Qt lifecycle regressions passed. |
| Static checks | Ruff, Python compilation, and `git diff --check` passed. Dependency audit of `requirements.txt` reported no known vulnerabilities at the time checked. |
| Visual review | Offscreen Qt rendering inspected at 1440×900 and 1024×768, plus the frame inspector. This does not constitute physical-display or assistive-technology testing. |
| Independent reviews | Code, Python, scientific, and security reviews informed fixes; final code review found no remaining concrete defect in its reviewed scope. |

The full suites emit upstream fixture-format and deprecation warnings. A PCA complex-dtype warning was investigated on rigid and flexible fixtures and on the real GROMACS trajectory: retained components and projections had zero imaginary part, casting changed projections by 0 Å, and the largest eigenvalue difference against `numpy.linalg.eigh` was 2.08 × 10⁻¹⁷ Å². No numerical error was observed in those cases.

Run the main verification from the repository with dependencies installed:

```bash
python -m pip install -r requirements.txt -r requirements-build.txt
python -m coverage run -m pytest -q
python -m coverage report
python -m ruff check . --exclude dist --exclude build/MolDynStudio --exclude .worktrees
python -m compileall -q analysis core tabs utils windows build
```

For headless Qt, set `QT_QPA_PLATFORM=offscreen` and `QTWEBENGINE_DISABLE_SANDBOX=1`. On Linux, enable the real engine suite with `MOLDYNSTUDIO_REAL_GROMACS_SMOKE=1` and set `MOLDYNSTUDIO_GROMACS_EXECUTABLE` to the selected executable, then run `python -m unittest tests.test_real_gromacs_smoke -v`.

CI now runs pytest so function-based scientific tests are included. New scientific environments pin GROMACS 2025.4 because MDAnalysis 2.10 reads that TPR format; it does not read GROMACS 2026 TPR files. Existing simulation environments are not automatically downgraded. Python 3.11 is the minimum supported GUI version.

## Structure-repair addendum — 8 October 2026

The requested validation scope preserves the complete deposited complexes: both protein chains and C6S copies in the 5T66 asymmetric unit, and the BJI adduct plus BHD214 in 1ERM. Retaining both 5T66 chains for this validation does not identify them as a biological dimer. No apo conversion, BHD-to-ASP conversion, or automatic deletion of distinct overlapping atoms forms part of these cases. The [implementation plan](STRUCTURE_REPAIR_PLAN.md) describes the decisions and acceptance criteria; it is not itself a record that every criterion passed.

The interface now exposes inspection, explicit FASTA record/chain association, editable conformer and atom-line choices, plan review, separate candidate generation, QC review, and explicit adoption. Sequence gaps require approved mapping and individual selection; terminal intervals start unselected. Alternate locations are resolved consistently within each residue, retaining shared atoms. Exact duplicate records and close contacts between distinct atoms are reported separately. A close contact alone does not authorize deletion.

The native PDBFixer/OpenMM worker runs outside the GUI process. It preserves observed coordinates, constructs approved missing standard atoms or residues, and retains requests, responses, and engine logs. Unknown components remain fixed context without implied force-field coverage. Adoption and preparation recheck source/candidate hashes, provenance artifacts, and the approved report digest; modifying an input invalidates the previous verification. Older repair sessions without that digest require review and adoption again before preparation.

### Confirmed reconstruction evidence

Official PDB, mmCIF, FASTA, and entry/entity records were downloaded from RCSB PDB and recorded with source URLs and SHA-256 checksums. The checks below used PDBFixer 1.12.0, OpenMM 8.6.1, the CPU platform, and seed 2026. Counts were read from the resulting backend evidence files, rather than inferred from interface state.

The local evidence directory `project/structure_repair_validation_20261008` contains PDB/mmCIF/FASTA inputs, the download manifest, reviewed plans, candidates, repair reports, and reconstruction logs. These raw files are not included in the public source distribution; their confirmed results and input digests are summarized here. The local `core_validation_summary.json` records both candidates as coordinate-ready, with no coordinate blockers, and `md_requires_parameters: true`. It records two retained covalent links for 5T66 and one for 1ERM.

| Input and retained chemistry | Observed atoms preserved | Modeled heavy atoms added | Residues added | Maximum observed-coordinate displacement |
| --- | ---: | ---: | ---: | ---: |
| 5T66, both chains and C6S adducts, residue-consistent conformer A | 4,404 | 94 | 0 | 0 Å |
| 1ERM, BJI adduct and BHD214 including OB | 2,183 | 0 | 0 | 0 Å |

The source PDB digests identify the precise inputs to these checks:

```text
5T66.pdb  278d88871ac47012cb08b7c77f9125eb99682f5c6a71da2c1fcd0e26bb0bd084
1ERM.pdb  66e104714def54f9d3d76b242d15eb9bd5b021ea26c29ce763ce5eab118f8c82
```

These are coordinate-reconstruction results, not successful full-complex GROMACS runs. The retained-atom comparison uses the structure after the explicitly selected conformers have been resolved. An atom count before alternate-location selection can therefore differ from the retained count without indicating arbitrary atom loss.

For [5T66](https://www.rcsb.org/structure/5T66), the audit found no internal sequence gap. The experimental construct starts at residue 29, so residues 1–28 were not modeled; GLN29 was also left unbuilt as an optional unobserved terminal residue. Heavy-atom completion included the partially observed THR30. The construct boundary is documented in [Cahill et al., 2017](https://doi.org/10.1128/AAC.02260-16).

For [1ERM](https://www.rcsb.org/structure/1ERM), the observed protein sequence was complete. Numbering from residue 26 is not evidence of an internal missing segment. BHD214 was retained as a modified polymer residue rather than converted to its ASP parent. Synthetic incomplete structures separately exercise genuine gap-reconstruction behavior; they do not establish that the deposited 1ERM contains those gaps.

Additional focused checks confirmed during this extension are listed separately from the earlier full-suite baseline:

| Check | Confirmed result |
| --- | --- |
| Complete-complex GUI workflow | Both real cases proceeded through candidate review and explicit adoption without losing the required complex components. |
| Qt repair checks | 18 tests passed. |
| Isolated reconstruction backend | 13 tests passed; dedicated backend coverage was 87%. |
| Covalent preparation gate | Dedicated measured coverage was 96%. |
| Linux focused checks | 28 tests passed in the stable Linux environment. |

Review-driven corrections to MODEL-record handling, alternate-conformer selection, dangling connectivity, and approved-report hash persistence have been implemented. The combined suites then produced the following results:

| Final combined check — 8 October 2026 | Confirmed result |
| --- | --- |
| Windows full pytest suite | 482 passed, 2 skipped, 114 subtests passed. |
| Linux/WSL full pytest suite | 480 passed, 4 skipped, 109 subtests passed. |
| Configured coverage scope | 86%, 6,003 statements. This uses the repository's configured omissions and is not whole-application coverage. |
| Repository Ruff checks | Passed. |
| Installed dependency consistency (`pip check`) | Passed. |

Independent code and Python reviews approved the corrected scope without remaining high- or medium-priority findings. A final regression also confirmed that changing a report during candidate verification prevents adoption. Passing automated suites does not imply that all scientific workflows or a full installer have been validated.

### Frozen-worker probe

A frozen executable probe successfully exercised the public reconstruction backend and its isolated worker launched from the same executable. On a synthetic case, it added one oxygen atom while preserving nine observed atoms, including a boron-containing BJI component, LINK/CONECT connectivity, and a maximum observed-coordinate displacement of 0 Å. This checks packaged worker execution and preservation behavior; the fixture is not the complete 1ERM system.

The earlier native-runtime crash was traced to the older MSVC 14.26 runtime loaded with Qt. The packaging correction selects the compatible 14.50 runtime family, and the corrected probe passed. This was an executable probe, not a rebuilt and validated full installer or a published release.

### Outstanding validation and parameterization

The full covalent complexes have not been run through minimization, NVT, NPT, or production MD. Validated, complete topologies for the boronate–serine adducts and retained BHD chemistry are missing. This remains a scientific workflow blocker after coordinate reconstruction. No unrelated atom types were substituted, and no ligand was removed to obtain an apparently successful run.

The actual preparation preflight was exercised on both verified candidates. It rejected the unsupported covalent topology before issuing any external command; the recorded stage states remain failed/not ready. The local `covalent_preflight_checks.json` includes the exact error, candidate/report digests, and manifest paths. The local `frozen_worker_smoke.json` separately records the successful synthetic packaging probe. Both belong to the evidence directory identified above.

Relevant parameterization literature was verified: [Chiacchio et al., 2023](https://doi.org/10.3390/molecules28062866), [Lence and González-Bello, 2021](https://doi.org/10.3389/fmicb.2021.721826), and [Tafi et al., 2005](https://doi.org/10.1016/j.ejmech.2005.06.011). These studies inform a future parameter review; they do not prove that a compatible, validated topology for each complete complex is installed in MolDynStudio. The 2005 paper concerns boronate force-field implementation and is distinct from the original 1ERM crystallographic publication.

The combined suite counts and executable-probe result above describe the 8 October structure-repair scope. They do not include every subsequent CHARMM/MATCH change. Full installer validation and MD of the covalent complexes remain outside the completed evidence; no new release is claimed.

## CHARMM/MATCH and 6D15 addendum — 9 October 2026

The new ligand route combines the explicitly installed official CHARMM36m February 2026 package with SwissParam MATCH parameters for a reviewed noncovalent ligand. The ligand is submitted as one connected MOL2 molecule with explicit hydrogens; chemistry review and permission to upload are separate decisions. Only the ligand is sent to the public service. A matching cache is checked against source and output hashes before reuse. ParmEd 4.3.1 converts the supported isolated-ligand topology locally. Unsupported force-field features are rejected rather than omitted.

The protein base is the February 2026 CHARMM36m/CGenFF 5.0 archive, while ligand parameters come from the MATCH service response. Returned ligand atom types are given unique names and combined using CHARMM/Lorentz–Berthelot cross interactions. No unprovided intermolecular NBFIX correction is inferred. This preserves the supplied potential within the supported conversion contract; it does not establish equivalence to a new licensed-server CGenFF 5.0 assignment. Phosphonate–sodium and other ligand cross interactions require scientific validation before publication claims about quantitative affinity.

The route can also derive all copies of a named ligand from a complex PDB. The MOL2 defines chemistry and atom names; each deposited residue supplies its own heavy-atom coordinates. Exclusions of other compounds are recorded explicitly, and original files remain unchanged. This route does not change the blocked status of the covalent 5T66 and 1ERM systems.

### Confirmed input and chemical-identity evidence

Official inputs and raw evidence are retained locally in `project/charmm36_6d15_validation_20261008`; they are not included in the public source distribution. Its `download_manifest.json`, `SCIENTIFIC_AUDIT.md`, and `coordinate_repair_result.json` identify the downloaded and derived files. The original 6D15 PDB has SHA-256 `67c8ab61b56d45211e667ea4730569d0757fd2bbad5ad4def97dac62818c27bc`; the reviewed candidate has SHA-256 `c9a4b255b8085f204eead1ea4fddddf57212bae98fff48e164db523f0120aec1`. The tables below provide the corresponding public summary without machine-specific paths.

| Check | Observed result |
| --- | --- |
| Deposited sequence | 290 construct residues, with 270 observed protein residues. No internal sequence gap or missing standard heavy atom was found. N-terminal expression-tag residues and unobserved terminal GLN295 were not reconstructed. |
| Coordinate selection | 2,365 original atom records became 2,327 after removing 38 alternate A records; conformer B was retained at LYS111, GLN191, ARG266, and GLU288. Maximum observed-coordinate displacement was 0 Å. |
| Repaired composition | 2,016 protein heavy atoms, TWB A301 and A302 with 18 heavy atoms each, six glycerol heavy atoms, and 269 waters. The deposited disulfide remained. No protein–TWB covalent bond or metal cofactor was deposited. |
| Planned simulation composition | Both TWB copies are retained; GOL is an explicit exclusion during derived-input preparation. The original PDB and repaired candidate retain GOL. The two TWB occupancies do not establish physiological stoichiometry. |
| Reviewed ligand model | Dianionic C12H11O5P: 29 atoms, comprising 18 heavy atoms and 11 hydrogens, with 30 bonds and expected integer charge −2 per molecule. |
| MOL2-based MATCH response | Atom names, elements, named connectivity, MOL2 bond orders, and coordinates were preserved. The RTF declares −2.000010 and its atomic charges sum to the same value within floating-point precision. Service rounding was retained without manual charge adjustment. |
| Rejected alternative | An earlier SMILES-based submission changed the requested protonation state. Its returned parameters were not accepted for this model. |

The accepted local `swissparam_twb_mol2_minus2/chemical_validation.json` records `chemical_identity_accepted: true` and `ready_for_md: false`; conversion and complete-system assembly remained separate checks at that stage. The source MOL2 digest is `df7c2767e31dc0c0c3973713944cdeb685885547bfb2f3fe1cf5189646f6517b`. The report also records why the returned `tautomers.smi` was not used as protonation evidence.

The −2 state is a simulation hypothesis for pH 7.4 informed by a microspecies prediction. It is not a measured bound-state charge. The TWB dictionary entry represents the neutral acid, and [Pemberton et al., 2019](https://doi.org/10.1021/acs.jmedchem.9b00728) report KPC-2 inhibition assays at pH 7.0; neither establishes the crystal pH or a unique simulation protonation state. The current 6D15 deposition and the paper's discussion of second-copy density must also be distinguished when interpreting the retained model.

### Execution and validation status

A synthetic conversion check comparing energies and forces passed. The real TWB conversion also passed a comparison at identical GRO coordinates, using independent OpenMM CHARMM and GROMACS readers plus an actual GROMACS 2025.4 single-point calculation. The local `swissparam_twb_mol2_minus2/conversion_validated/energy_parity.json` records:

| Real TWB conversion check | Observed value |
| --- | ---: |
| Native CHARMM representation, OpenMM potential energy | 588.3779501546522 kJ/mol |
| Converted GROMACS representation, OpenMM potential energy | 588.3779500014172 kJ/mol |
| GROMACS 2025.4 single-point potential energy | 588.377991 kJ/mol |
| Absolute GROMACS–native-CHARMM energy difference | 4.0845 × 10⁻⁵ kJ/mol |
| Maximum absolute force-component difference between OpenMM readers | 2.1434 × 10⁻⁵ kJ mol⁻¹ nm⁻¹ |
| Retained potential terms | 29 atoms, 30 bonds, 52 angles, 67 dihedrals, 1 improper, 0 Urey–Bradley terms |

This confirms numerical preservation of the tested isolated-ligand potential at the stated coordinates. It does not validate the force-field model against experiment or establish the accuracy of ligand–protein/ion cross interactions.

The separate crystal-water check retained 269 observed oxygens with zero displacement before output rounding and added 538 modeled hydrogens. Standard GRO precision introduced at most 0.005 Å coordinate rounding. Protein `pdb2gmx` completed with exit code zero. The local `crystal_water_validation/result.json` limits that evidence to water reconstruction and protein topology generation; it does not report full preparation or MD.

The duplicate SOL molecule-row problem and topology line-ending handling that blocked `genion` have been corrected. Generated topology text uses LF line endings for the native GROMACS tools, and water molecule counts are reconciled during assembly. TPR and checkpoint checks now use the run manifest's GROMACS executable and Conda environment, with Windows-to-WSL path translation. The prepared TPR identifies the two TWB copies as `LIG`, with 29 atoms per copy; downstream selections must use these prepared names.

The complete 6D15 execution passed with GROMACS `2025.4-conda_forge`. It started from fresh preparation, without reusing a completed preparation, and took 247.438 seconds on the validation machine. The local `application_smoke/validation_summary.json` records preparation, minimization, NVT, NPT, and production as completed. Minimization reached a maximum force of 938.61133 kJ mol⁻¹ nm⁻¹, below the requested 1,000 kJ mol⁻¹ nm⁻¹ tolerance, after 1,028 steps. Each subsequent dynamics stage ran for 2 ps with a 1 fs timestep.

Stage QC now treats minimization separately from dynamics: it records energy change, checks numeric finiteness, and extracts force-tolerance evidence from explicit GROMACS log statements. Temperature, pressure, and density equilibrium criteria are not applied to minimization. Earlier QC results remain part of the original run evidence; a separately hashed reassessment records interpretation under the corrected rules without replacing that history.

Ion-placement preprocessing now uses cut-off electrostatics solely for the TPR consumed by `genion`; that TPR is never executed as MD. The generated minimization, NVT, NPT, and production protocols retain PME.

Restrained NPT now uses `pcoupl = C-rescale` and `refcoord-scaling = com`; unrestrained production retains Parrinello-Rahman coupling. This corrects the preprocessing issue involving absolute restraint-reference coordinates without bypassing warnings through `-maxwarn`. C-rescale follows [Bernetti and Bussi, 2020](https://doi.org/10.1063/5.0020514). The [GROMACS documentation](https://manual.gromacs.org/documentation/2025.4/user-guide/mdp-options.html#mdp-refcoord-scaling) explains that COM scaling uses one reference center across restrained molecules and may leave pressure and virial ill defined. No quantitative pressure or ensemble claim is made from restrained equilibration.

The GUI now exposes **NVT steps**, **NPT steps**, and **Short pipeline test: 2 ps per MD stage**. The preset requests minimization followed by 2 ps NVT, 2 ps NPT, and 2 ps production at a 1 fs timestep; production output is requested every 0.2 ps, with 0.5 ps NVT/NPT output. Such a short trajectory can test execution and file handoff. It cannot establish equilibration, stable binding, sampling adequacy, or physical parameter accuracy.

Methods are attributed to [SwissParam 2023](https://doi.org/10.1021/acs.jcim.3c01053), [MATCH](https://doi.org/10.1002/jcc.21963), and the [citation requested by ParmEd](https://doi.org/10.1007/s10822-016-9977-1). The [user manual](USER_MANUAL.md#45-prepare-a-noncovalent-ligand-with-charmm36m-and-match) describes the complete user procedure, consent/cache behavior, and scientific limits.

### Trajectory integrity and downstream execution

The final prepared system contained 46,679 atoms: 4,017 protein atoms across 270 residues, two 29-atom `LIG` molecules, 14,171 `SOL` waters, 48 `SOD` ions, and 43 `CLA` ions. Each ligand retained 11 hydrogens, the reviewed named bond graph, and a charge of approximately −2.000010. The production XTC contained 11 frames from 0 to 2 ps.

The raw trajectory represented part of the protein across a periodic boundary. The separate audit identified this before analysis, with normal minimum-image bond distances. A derived trajectory was generated using `gmx trjconv -pbc mol -center`, selecting Protein for centering and System for output. The original files were preserved; command, group choices, and hashes were recorded. In all 11 derived frames, solute bonds were whole and direct ligand–protein minimum distances agreed with their periodic-image counterparts within 0.1 Å. This was an explicit external verification, not an automatic guarantee provided by the GUI checkbox.

The original production TPR SHA-256 is `d2d5589e0422fe1b8153dfedf5f1ac4e54e96d4cb8946f2dbcba7282ce5f931c`; the raw XTC digest is `61b14fc36acee55d8994c0040f41362cce5cdba9df4ea0873325ec4b8ccfa01e`. The derived whole/centered XTC digest is `32acab8fc37ef8c5de5697270be731674c279c6c708d1d78fc33892c1a1e6014`. The local post-MD verification record retains the source/output mapping and confirms that source files were unchanged.

All nine implemented trajectory analyses completed with no failed analyses: RMSD, RMSF, radius of gyration, SASA, hydrogen bonds, DSSP, contact map, Ramachandran angles, and PCA. The first eight used `protein` as the primary selection, with `resname LIG` as the hydrogen-bond partner; PCA used `protein and name CA`. The manifests record MDAnalysis 2.10.0, MDTraj 1.11.2, NumPy 2.5.3, input hashes, selections, and output files.

PCA emitted a complex-to-real conversion warning. A separate diagnostic measured exactly zero imaginary contribution in the three retained components, their variances, and their projections; repeating the calculation differed from the CSV by at most 1.23 × 10⁻¹⁴ Å. The warning reflected the array data type in this case, without a discarded imaginary contribution to those outputs. This numerical check does not extend the sampling represented by the 11-frame trajectory.

Two receptor–ligand pairs were exported for DockLens from frame index 10, at 2 ps, one pair for each complete ligand copy. Both exports include their own launch manifest and provenance. DockLens itself was not executed for this 6D15 check, so no interaction-engine result is claimed.

The QC parser excludes final averages and RMS summary tables from the time series. Final potential-energy values from the four stage logs agreed with independent EDR extraction within 0.5 kJ/mol, the tolerance used for log-printing precision. The corrected stage-QC reassessment passed minimization, NPT, and production; it retained an NVT warning for a logged pressure minimum of −4,185.21 bar. Original QC records were preserved alongside the reassessment and its code hash. These outcomes describe numeric checks, not equilibrium. Separate `gmx energy` extraction from EDR files completed successfully and retained the energy, temperature, and pressure series for review.

### Combined source verification

The final source suites for the CHARMM/MATCH extension produced the following results. These supersede the earlier totals for the current source scope while preserving the dated baselines above.

| Check — 9 October 2026 | Confirmed result |
| --- | --- |
| Windows full pytest suite | 658 passed, 2 skipped, 175 subtests passed in 41.42 seconds. |
| Linux/WSL full pytest suite | 656 passed, 4 skipped, 170 subtests passed in 26.27 seconds. |
| Windows configured statement coverage | 86.33468% of 7,691 statements. |
| Linux/WSL configured statement coverage | 86.21766% of the same 7,691-statement scope. |
| Ruff and Python compilation checks | Passed. |
| Installed dependency consistency (`pip check`) | Passed. |
| Dependency advisory scan (`pip-audit`) | No known vulnerabilities reported for the scanned installed dependencies. |
| Independent code and security reviews | Approved. |

Coverage uses the repository's configured omissions and is not whole-application coverage. Advisory results describe the database and installed dependencies at the time of the scan; they do not guarantee the absence of unknown vulnerabilities.

### Packaged-runtime checks

The full Windows executable passed all six runtime self-checks. Its isolated repair worker also passed the synthetic reconstruction fixture: one oxygen was added, nine observed atoms remained at exactly their input positions, and the boron-containing BJI component and LINK/CONECT records were retained. This verifies that worker's execution and preservation behavior in the full executable. It does not demonstrate reconstruction of a covalent force-field topology or MD of complete 5T66 or 1ERM.

A fresh Linux installation passed the same six runtime self-checks. These checks cover the bundled scientific libraries and GUI construction; they do not execute the external GROMACS workflow. They identify the diagnostic bundles tested during development. For a distributed build, use its release notes, `SHA256SUMS-v1.2.0.txt`, and `RELEASE-PROVENANCE-v1.2.0.json` to identify the exact assets and their verification results; this source record does not assert publication of a release.

## MD Setup → MD Run review — 27 October 2026

Status: source candidate verified on Windows and Ubuntu 24.04 under WSL2. The dated results above remain historical evidence for their stated scopes. These results identify the local source changes on top of `743a9bc8fe4a3ec861cba89379bd6a8f686c269c`, not a new installer or release.

### Implemented source behavior

Successful preparation adds a `preparation_review` record and canonical `setup_snapshot` to the existing run manifest. MD Run compares the prepared choices with the editable MD Setup draft; a mismatch blocks both Run All and Resume until the choices are restored or preparation is repeated. Older projects explicitly report an unrecorded comparison instead of being presented as matching. Their existing prerequisite checks and readable MDPs remain available.

The read-only review reads the actual `em.mdp`, `nvt.mdp`, `npt.mdp`, and `md.mdp`, reports missing/unreadable files, and distinguishes the 2 ps execution test from an equilibrated protocol. The environment check uses a background worker with the recorded executable and Conda environment through the existing Windows/WSL or native Linux route. Reported GPU support describes the build; actual hardware use remains an `mdrun` execution result. Setup exposes essential duration/temperature/pressure controls and a collapsible Advanced protocol parameters group, plus Review MD run. Saved sessions preserve setup choices and recompute the comparison on restore. Failure guidance links the existing MD Setup, Settings, and Open project / logs actions.

Source locations: `core/run_review.py`, `core/preparation_orchestrator.py`, `windows/run_review_panel.py`, `windows/setup_protocol_controls.py`, `tabs/md_setup_tab.py`, `tabs/md_run_tab.py`, and `gromacs_analysis_studio_v11.py`. The [user procedure](USER_MANUAL.md#51-execute-the-four-stages) describes the controls and scientific limits.

### Completed candidate checks

Full regression runs preceded final typing-only adjustments; the final source then passed the same 110 targeted tests and nine subtests on both platforms. Test-only and platform-specific skips remain explicit. Evidence is retained outside the repository in `C:/Users/adria/Documents/Codex/2026-10-26/p/work/moldyn-validation/`.

| Check | Candidate status / evidence |
| --- | --- |
| Windows regression suite | 707 passed, two skipped, 175 subtests; Python 3.12.12. `windows/full-suite-final.log`. |
| Linux/WSL regression suite | 705 passed, four skipped, 170 subtests; isolated Python 3.11.15. `linux/final-pytest-coverage.log`. |
| Configured statement coverage on both platforms | Windows 85.19%; Linux 85.07%; existing 80% threshold and exclusions unchanged. `windows/coverage-full.json`, `linux/final-coverage.json`. |
| Review/snapshot, pending gates, legacy manifests, session restore, and failure guidance | Final targeted suite: 110 passed and nine subtests on each platform. Separate measurement of the three new production modules, without the legacy GUI omissions: 92.97% combined statement/branch coverage (core review 96%, run review panel 89%, protocol controls 100%). `windows/coverage-features.json`, `linux/post-review-targeted-pytest.log`. |
| Environment worker lifecycle and Windows/WSL/native routing | Busy-operation guards, same-directory repreparation invalidation, failure recovery, and final source checks passed. Six runtime self-check probes passed on each platform. Compiled GPU support is not a hardware-use measurement; execution tests used CPU. |
| Ruff, compilation, dependency consistency, typing, and code review | Ruff's repository fatal-error checks and full checks for the new modules/tests passed; changed Python files compiled. `pip check` passed for both test environments. Mypy passed for the three new production modules. General and Python code reviews approved with no remaining findings. No new dependency advisory scan was run. |
| Qt Setup → review → Run execution smoke | Actual Windows Qt controls prepared synthetic GLY-GLY through WSL/GROMACS 2025.4, enforced pending/undo, restored a session, and completed all four stages: 13 recorded commands, 2,232 atoms, 11 frames over 2 ps. The NVT QC warning remained recorded; the input PDB hash was unchanged. `windows/windows-wsl-setup-run.json`. |
| Actual interruption and checkpoint continuation | A separate 50 ps production was stopped through Qt controls, saved a 55,032-byte checkpoint, and resumed to completion with `-cpi md.cpt`. The log confirms append; the final trajectory contains 251 strictly increasing frames from 0 to 50 ps. Input PDB unchanged. `windows/windows-wsl-checkpoint-verified.json`. |
| Linux real-engine smoke | Four tests passed with GROMACS 2025.4, including argon preprocessing/minimization/TPR reading and CHARMM36m preparation/minimization. Direct WSL HTTPS timed out; the CHARMM smoke used the official archive downloaded on Windows and checked against the pinned SHA-256, replacing only the download response. Engine and command execution were real. `linux/linux-final-state.json`. |

The checkpoint harness initially demanded an explicit `-append` flag after the physical continuation had completed. The selected GROMACS help reports append as the default. That assertion was corrected, and the existing log and trajectory were independently checked without rerunning MD; the original harness report is retained beside the verified report. Windows Qt screenshots were inspected with Segoe UI loaded in the offscreen harness; Linux GUI checks were headless. GPU execution and a Linux desktop visual review were not performed.

No candidate installer or release claim follows from these source changes. The earlier 6D15 run remains a 2 ps operational check and cannot establish equilibration, sampling adequacy, binding stability, or parameter accuracy.

### QwikMD reference provenance and limits

The handoff design used static workflow observations from the [official QwikMD home](https://www.ks.uiuc.edu/Research/qwikmd/) and [manual](https://www.ks.uiuc.edu/Research/vmd/plugins/qwikmd/), retrieved on 27 October 2026, plus the official [Windows beta ZIP](https://www.ks.uiuc.edu/Research/qwikmd/qwikmd_beta_windows.zip). That ZIP is 220,160 bytes, has server Last-Modified 11 October 2019, and SHA-256 `64439fe716d46bd5109b27f084c6dc0666edb34464ab8d25142c04f2bfcb5707`. Its package declares QwikMD 1.2; this does not establish the newest plugin version.

REA 6.8.0 recorded the archive inventory as `ev_82f0cb923275c084987943c16b824a24e0467070d5c767a2512b0b1a4819382f` and inspection as `ev_f4cff045bbd32260d03878f263ba669fd1e72aa147b9c38b497588635fca8cdb`. Inventory covered 16 regular members and one directory. Eight selected text resources were checked against member sizes and hashes. The external task-workspace directory `work/moldyn-qwikmd-investigation/` retains `FINDINGS.md`, `provenance.json`, `resource-hashes.json`, and the two-record `rea-evidence.json`; the evidence JSON SHA-256 is `68e61c81f39f1fa695ed84c3ced963fb507a0a7f3babceb8a079d5da8ad61b32`. Those local audit files are outside this repository.

The resulting design inference was to separate preparation, review, and explicit start; keep exact prepared artifacts together; and require valid stage outputs before continuation. No downloaded Tcl, VMD, or NAMD was executed or installed, and no Tcl source or NAMD parameters were transferred into MolDynStudio. This historical Windows beta inspection does not establish current QwikMD runtime behavior, cross-engine parameter equivalence, or scientific suitability. REA inventory covers ZIP central-directory entries; static inspection is not a runtime test.

## Integrated 3D viewer — 27 October 2026

Status: **Source candidate verified on Windows and Linux/WSL2**. This is a separate source delivery after the MD Setup → MD Run review above. Its earlier results remain scoped to that delivery; the viewer checks below were performed separately. No new installer or release is claimed.

### Implemented source behavior

The project owns one native Qt dock, initially placed at the bottom and also usable at the right or as a floating panel. **View > 3D structures and trajectories**, **MD Setup > Inspect structure in 3D**, and **Analysis > Open Trajectory Viewer** share it. It reuses the existing MDAnalysis and Matplotlib/Qt dependencies. A readable structure without a trajectory supplies one frame and no simulation time; MD-frame export and playback remain disabled. A matching trajectory uses a disk-backed reader with zero-based frame navigation, Previous/Next, slider, Play/Pause, and reader time. Camera orientation and limits persist across frames, with an explicit refit control.

The display supports MDAnalysis selections for available chain, residue, and ligand identities. It draws only source-topology bonds, without inferring connectivity. Selections are reduced to about 6,000 atoms for drawing; the existing exporter retains the complete validated receptor/ligand selections. Original coordinates and source files remain unchanged. File/reader/selection failures are reported explicitly. Structure inspection does not certify chemistry, parameter readiness, or periodic imaging.

Viewer work participates in project busy and close guards. Preparation and MD execution close an idle reader first to release handles, including Windows file locks. Production completion offers source paths without opening a reader. Sessions store paths, selections, requested frame, and panel visibility; restoration requires **Load 3D view** and does not restore periodic-boundary consent or an exported pair. Existing DockLens export still requires a real trajectory, applied disjoint selections, complete ligand connectivity where available, explicit elements, and fresh periodic-image review.

Source locations: `core/trajectory_frames.py`, `windows/trajectory_viewer.py`, `windows/project_viewer.py`, `gromacs_analysis_studio_v11.py`, `tabs/analysis_tab.py`, `tabs/md_setup_tab.py`, and `tabs/md_run_tab.py`. See the [3D inspection procedure](USER_MANUAL.md#91-open-and-navigate-the-integrated-3d-view).

### Completed candidate checks

The task evidence directory is `C:/Users/adria/Documents/Codex/2026-10-26/p/work/moldyn-viewer-validation/`. Both regression runs used default repository discovery, including the six existing scientific verification tests under `project/`; both collected the same 747 cases. Counts from the preceding delivery are not carried forward.

| Check | Candidate status / evidence |
| --- | --- |
| Windows full regression suite and configured coverage | 745 passed, two opt-in engine tests skipped, 187 subtests. Existing statement-coverage configuration: 85.31%. `windows/full-suite-final.log`, `windows/coverage-final.json`. |
| Linux/WSL full regression suite and configured coverage | 741 passed, six skipped, 180 subtests. Four additional skips are Windows launcher/Unicode checks; two are opt-in engine tests. Statement coverage: 85.00%. The 125 source/test/helper hashes were identical before/after the run. `linux/linux-validation-summary.json`, `linux/final-full-pytest.log`, `linux/final-full-coverage.json`. |
| Structure-only loading, explicit read failures, source bonds, full-selection export | Windows targeted suite: 69 passed and 24 subtests. Explicit combined statement/branch coverage of core/frame reader, dock, and viewer: Windows 88.99%; Linux 86.63%. These measurements include GUI modules omitted by the old global coverage configuration. `windows/viewer-coverage.json`, `linux/final-viewer-coverage.json`. |
| Shared dock routes, session restore, source handoff, playback, camera, and lifecycle guards | Qt journeys passed: shared Setup/Analysis dock, structure-only mode, navigation, playback/pause, restore of frame and selections, source replacement, close guards, and release before project writes. Failures invalidate PBC consent and cached exports. Zero-size hidden QC canvas regression was reproduced and fixed without changing QC data. |
| Windows/WSL production handoff and source preservation | Actual Qt controls loaded a structure, released its reader before preparation, loaded the prepared GRO, released that reader before execution, and completed minimization/NVT/NPT/production via GROMACS 2025.4. The offered TPR/XTC then opened in 3D, with 11 frames, frame 10 at 2 ps, 16 known protein bonds, restored frame state, and unchanged source PDB. The project path contained spaces and accents. `windows/real-setup-run-viewer.json`. |
| Screenshots and visual inspection | Existing centered 6D15 TPR/XTC copies were inspected on both platforms at frames 0/5/10 and times 0/1/2 ps: backbone 1,079 atoms/1,078 bonds; ligand 58 atoms/60 bonds. Source hashes were preserved. Windows screenshots used native Matplotlib/Qt offscreen with Segoe UI; Linux WSLg/xcb rendered initially, and the final New/Open journey passed offscreen after display sockets became unavailable. Final integrated and standalone screenshots were inspected. `windows/6d15-real-render.json`, `linux/offscreen-final/integrated-linux-smoke.json`. |
| Lint, typing, compilation, dependency consistency, and code review | Repository fatal-error Ruff checks, stricter new-code checks including E402, compilation, dependency consistency, and general/Python reviews passed. Mypy passed for the viewer and dock; old reader typing diagnostics were not represented as a clean full-project type check. No new dependency advisory scan was run. Runtime self-checks passed six probes and retained all 15 navigation pages on each platform. |

Real-file tests found a Windows XDR-reader limitation for non-ASCII XTC/TRR paths. The opener now uses a Windows ASCII short-path alias only when the alias identifies the same file; stored paths, source checks, and provenance retain the originals. The reader does not copy trajectories. If the filesystem supplies no suitable 8.3 alias, the error asks for ASCII-only paths. The initial failed smoke and the successful repeated workflow are retained separately. A second real-GUI issue, `tight_layout()` on a zero-height hidden QC figure after docking, was reproduced on both platforms and fixed with a positive-size guard.

Eleven unchanged modules/tests from the first delivery were checked against their previously recorded hashes. GROMACS protocol generation, QC calculations, DockLens export contracts, and first-delivery review behavior were preserved. The short MD runs are operational checks; neither the visualization nor the 2 ps trajectory establishes equilibration or scientific suitability.

## Analysis points linked to 3D — 27 October 2026

Status: **Source candidate verified on Windows and Linux/WSL2**. This third source delivery connects the existing QC plots and measured tables to the shared native 3D panel. It adds no renderer, scientific backend, or dependency. Earlier delivery results above retain their original scopes. No new installer or release is claimed.

### Implemented source behavior

**Results Viewer > QC Dashboard** retains its summary table and adds a source selector, **Plot** / **Measured points** tabs, and **Show selected point in 3D**. A plot pick, measured-row double-click, or explicit button activation sends the exact source file and zero-based numeric row. Overlapping points or series require an explicit source/row choice. The plotted marker and measured-row selection refer to that observation, without resolving repeated residue numbers by proximity.

The linked CSV must have an adjacent supported `analysis_manifest.json`, an exact completed result entry, an unchanged output hash/shape, and matching topology/trajectory fingerprints. A completed result from a partial analysis job is usable only when the manifest reports unchanged sources. The existing frame worker performs source hashing and reading. Time-series navigation uses the saved frame index and verifies the reader time against `time_ns`; no regular frame spacing or zero time origin is assumed. RMSF uses the saved atom index and atom/residue/segment metadata, validates them against the topology, and highlights the unique source residue at the current frame. This is a visual overlay of the existing per-atom RMSF result, not residue averaging or interaction analysis.

Existing receptor/ligand selections and camera remain intact when sources match. The highlight is drawn in pink with a white outline above the molecular context so it remains visible. Ordinary frame navigation clears it. Export retains the existing complete, same-frame selections and periodic-boundary consent requirement; highlight atoms are not added to an exported selection. Read failures invalidate exports/consent and restore the prior reader cursor; a restoration failure explicitly requires reload. A second request during active work reports that its point was not opened. New/opened sessions clear the previous dashboard context.

External XVG/CSV series remain readable without a verified link and display the reason for unavailable 3D navigation. Changed plotted data require reimport; changed source inputs require regenerated analysis. Absolute manifest paths must resolve on the current operating system; this delivery adds no automatic relocation or cross-OS path remapping.

Source locations: `analysis/result_links.py`, `core/trajectory_frames.py`, `tabs/qc_dashboard_tab.py`, `windows/trajectory_viewer.py`, `windows/project_viewer.py`, and `gromacs_analysis_studio_v11.py`. See the [linked-analysis procedure](USER_MANUAL.md#84-inspect-a-measured-point-in-3d).

### Completed candidate checks

Evidence is stored separately under `C:/Users/adria/Documents/Codex/2026-10-26/p/work/moldyn-analysis-validation/`.

| Check | Candidate status / evidence |
| --- | --- |
| Windows full regression suite and configured coverage | Default repository discovery collected 789 cases, including the six existing scientific verification tests under `project/`: 787 passed, two opt-in engine tests skipped, and 228 subtests. Existing statement-coverage configuration: 85.81%. `windows/full-suite-final.log`, `windows/full-coverage.json`, `windows/windows-validation-summary.json`. |
| Linux/WSL2 full regression suite and configured coverage | The same default scope collected 789 cases: 783 passed, six skipped, and 221 subtests. Four additional skips are Windows launcher/Unicode checks. Existing statement coverage: 85.51%; explicit combined statement/branch coverage of result linking, frame reader, dashboard, dock, and viewer: 88.74%. Source hashes were identical before/after validation. `linux/final-validation-status.json`, `linux/final-full-pytest.log`, `linux/final-full-coverage.json`, `linux/final-viewer-coverage.json`. |
| Exact row/source identity and failure handling | Unit/integration checks cover missing or malformed manifests, changed CSV/source hashes, changed row mappings, non-finite values, exact frame/time validation with nonzero/nonuniform times, repeated residue IDs in different segments, cursor rollback, overlapping plot points/series, and explicit rejection while busy. Focused checks include prior viewer/export behavior. |
| Real Windows and Linux/WSL2 analyses and GUI | Existing centered 6D15 copies produced 11 RMSD points, 270 C-alpha RMSF points, and 11 radius-of-gyration points. The Windows Qt Analysis worker generated these outputs and handed them to the dashboard. Windows offscreen/Segoe UI and Linux native WSLg/xcb journeys selected RMSD row 10 to open frame 10 at 2 ps. RMSF row 45 identified atom 673 CA in CYS46, source residue index 45/segment 0, and highlighted its 10 atoms without changing export selections. The pink/white highlight was visually inspected. `windows/fixture-generation.json`, `linux/fixture-generation.json`, `windows/offscreen-gui/analysis-gui-smoke.json`, `linux/xcb-gui/analysis-gui-smoke.json`. |
| Same-frame export and source preservation | Both real-file GUI journeys exported 4,017 protein and 58 ligand atoms from frame 10 at 2 ps. Exported coordinates matched the same source frame and original selection mappings; source hashes remained unchanged. The journeys checked ordinary-navigation overlay clearing, readable unlinked external data, invalid-source-hash rejection, and New-session clearing. `windows/offscreen-gui/analysis-gui-smoke.json`, `linux/xcb-gui/analysis-gui-smoke.json`. |
| Explicit GUI/core coverage and final review | The same focused 111-case scope passed 111 tests/65 subtests on Windows and 107 tests/58 subtests on Linux, with four Windows-only cases skipped there. Combined statement/branch coverage including the GUI modules excluded by the old global configuration: Windows 90.11%, Linux 88.74%. The scope includes the zero-height hidden-QC-canvas regression. General/Python review approved the final changes without outstanding findings. `windows/feature-coverage.json`, `linux/final-viewer-coverage.json`. |
| Runtime checks | All six existing runtime probes passed on each platform. The source GUI retained the existing native dependencies and 15 navigation pages. |

These short stored trajectories test file identity, UI navigation, and export interoperability. They do not establish long-run performance, equilibration, interaction occupancy, or biological conclusions.

## v1.3.0 release preparation — 27 October 2026

The three delivery scopes above are included in v1.3.0. Release preparation
raised both GUI requirement sets to Pillow 13.0.0+, filelock 4.2.1+,
certifi 2026.10.19+, and setuptools 84.1.0+ after finding advisories in the
previously installed versions. The upgraded Windows and Linux environments
passed dependency consistency checks and advisory audits with no known
vulnerabilities. The Linux audit covered all 50 installed distributions with no
omissions; its bootstrap pip was also upgraded to 26.2.2.

Post-upgrade full suites again passed 787 tests/228 subtests on Windows and
783 tests/221 subtests on Linux, with the same two/six skips and 85.81%/85.51%
configured statement coverage. Both runtime checks passed all six probes. The
Linux native WSLg/xcb 6D15 journey again preserved frame navigation, residue
highlighting, selections, source hashes, and exported coordinates. Ruff passed
on the publishable Python files using the CI-pinned version 0.14.5.

These local suites include six existing checks under the untracked project-data
directory. Those data and checks are not part of the published application;
clean-checkout CI collects 783 application cases. Build- and publication-specific
results belong to the GitHub release notes and versioned provenance/checksum
assets, rather than the earlier historical bundle checks in this document.

## CHARMM download compatibility — 28 October 2026

Release CI exposed HTTPS handshake timeouts in the official CHARMM download
through Python's transport. Both platform application/packaging jobs passed;
the separate real-GROMACS job correctly failed at that external download.
The downloader now handles only timeout/reset failures with an optional system
curl 8.4.0+ HTTP/1.1 fallback. TLS certificate verification and HTTPS-only
redirects remain required; the original deadline, size limit, SHA-256, and
force-field-tree validation remain enforced. Older or malformed curl clients
are rejected before transfer. Certificate, HTTP, and validation errors do not
trigger a fallback.

The focused force-field/setup suite passed 50 tests and 21 subtests. Both
modified download functions reached 100% statement and branch coverage.
Independent general/security/Python review approved the final controls.
Windows' public download API installed the exact supported archive after a
transient Python connection failure. Linux also passed the four-test opt-in
GROMACS 2025.4 module, including fresh CHARMM download, preparation, minimization,
and the separate tiny-system grompp/mdrun test. No force-field archive is
redistributed in the application or release sources.

Final post-compatibility suites collected 803 local cases: Windows passed
801 tests/249 subtests with two skips and 85.88% statement coverage; Linux passed
797 tests/242 subtests with six skips and 85.58% coverage. The published
application scope contains 797 cases after excluding the six project-data
checks. Both platforms passed all six runtime probes. Fresh public downloads
with the curl version guard passed on Windows and Linux; Linux source hashes
were unchanged during the final suite and download validation.

## Limits relevant to publication and use

The short synthetic simulations and the complete 6D15 execution check verify execution and file interoperability. They do not validate biological conclusions, equilibrium sampling, binding affinity, force-field suitability for arbitrary chemistry, or long-run performance. A publication still needs openly described test systems, independent replicates where applicable, uncertainty analysis, and comparisons against established workflows.

Periodic-boundary preparation is explicitly acknowledged by the user; MolDynStudio does not verify it automatically. MDTraj-based analyses hold the selected trajectory in memory. Python cancellation takes effect between analyses; external QC/MM-PBSA/CPPTRAJ tasks lack an interactive cancellation path, and session guards keep those jobs alive until completion.

DockLens export represents one frame, not interaction occupancy over a trajectory. PDB does not preserve bond orders or force-field partial charges. DockLens performs its own chemical perception, and the user must select the molecular-dynamics evidence context. Returned DockLens results are retained externally and are not automatically imported into MolDynStudio.

MM-PBSA and CPPTRAJ command construction, execution wiring, validation, and failure handling were tested. A full external MM-PBSA/CPPTRAJ calculation on a validated receptor–ligand dataset was not performed. Advanced pages labeled as previews remain configuration previews. Moved project folders still require valid referenced paths.

Packaging declarations include the new scientific modules. The full Windows executable and fresh Linux installation passed the runtime checks described above; the Windows executable also passed the synthetic repair-worker check. Release publication and exact distributed-asset identity are documented separately in release notes and checksums. Refer to the [user manual](USER_MANUAL.md) for the source workflow, structure-repair procedure, and methods references.
