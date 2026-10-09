# Workflow audit and validation record

Last updated: 9 October 2026. Scope: the accompanying MolDynStudio v1.2.0 source revision, based on commit `35a5e67`. The general workflow baseline was recorded on 7 October; the structure-repair and CHARMM/MATCH addenda record subsequent work separately. This record describes local validation, not a published binary release.

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

## Limits relevant to publication and use

The short synthetic simulations and the complete 6D15 execution check verify execution and file interoperability. They do not validate biological conclusions, equilibrium sampling, binding affinity, force-field suitability for arbitrary chemistry, or long-run performance. A publication still needs openly described test systems, independent replicates where applicable, uncertainty analysis, and comparisons against established workflows.

Periodic-boundary preparation is explicitly acknowledged by the user; MolDynStudio does not verify it automatically. MDTraj-based analyses hold the selected trajectory in memory. Python cancellation takes effect between analyses; external QC/MM-PBSA/CPPTRAJ tasks lack an interactive cancellation path, and session guards keep those jobs alive until completion.

DockLens export represents one frame, not interaction occupancy over a trajectory. PDB does not preserve bond orders or force-field partial charges. DockLens performs its own chemical perception, and the user must select the molecular-dynamics evidence context. Returned DockLens results are retained externally and are not automatically imported into MolDynStudio.

MM-PBSA and CPPTRAJ command construction, execution wiring, validation, and failure handling were tested. A full external MM-PBSA/CPPTRAJ calculation on a validated receptor–ligand dataset was not performed. Advanced pages labeled as previews remain configuration previews. Moved project folders still require valid referenced paths.

Packaging declarations include the new scientific modules. The full Windows executable and fresh Linux installation passed the runtime checks described above; the Windows executable also passed the synthetic repair-worker check. Release publication and exact distributed-asset identity are documented separately in release notes and checksums. Refer to the [user manual](USER_MANUAL.md) for the source workflow, structure-repair procedure, and methods references.
