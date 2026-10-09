# Sequence-guided structure repair

Implementation plan, 7 October 2026.

Scope clarified by the user on 8 October 2026: use the official deposited files
and preserve the complete covalent complexes. Retain C6S/BJI, their serine
linkages, and BHD214. No apo conversion is authorized for the validation cases.

## Workflow

1. Inspect the original PDB before a molecular parser can discard alternate conformations or duplicate records.
2. Associate a FASTA record with a protein chain, or use the deposited SEQRES sequence. Align observed residue identities rather than infer missing residues from numbering.
3. Review a concrete plan: retained chains/conformers, exact duplicates, explicitly selected atom deletions, approved MODRES conversions, and selected missing-residue intervals.
4. Generate a separate candidate. Use PDBFixer/OpenMM only to construct approved missing residues and heavy atoms. Hydrogens, solvent, ions, and simulation topology remain the responsibility of the existing GROMACS preparation workflow.
5. Inspect candidate QC and provenance, then explicitly adopt it as the structure input. Revalidate its report and hashes immediately before system preparation. Coordinate-repair readiness is separate from force-field readiness.
6. Check force-field coverage for the complete covalent adducts and modified residues. Run minimization, NVT, NPT, and short production dynamics only when the required parameters and topology are available and validated. Preserve explicit blocked status if they are missing, rather than removing the ligands or assigning unrelated atom types.

## Scientific constraints

An alternate conformation is not an erroneous extra atom. Select conformers consistently per residue, keeping shared atoms. A close contact between different atoms is not sufficient reason to delete either atom; deletion requires an explicit choice. Exact duplicate records are a separate diagnosis.

FASTA gives sequence identity, not experimental coordinates. Ambiguous alignments and mismatches must be exposed. Numeric discontinuities, insertion codes, signal peptides, tags, and residues outside the experimental construct do not justify automatic rebuilding. Terminal extensions are disabled by default. Approved reconstructions remain labeled as modeled coordinates.

MODRES information can identify a parent amino acid, but it does not authorize chemical conversion. In particular, converting BHD to ASP removes a chemical substituent and must be explicit. Protein-only derivatives of inhibitor complexes are separate apo models, not repaired copies of the original covalent complex. The existing rejection of unsupported covalent ligand parameterization remains active.

## Architecture and evidence

- `core/structure_audit.py`: raw PDB diagnosis and stable source identities.
- `core/sequence_mapping.py`: validated FASTA and sequence mapping.
- `core/structure_repair.py`: immutable choices/plans, transactional candidate creation, source and output hashes, and adoption verification.
- `core/pdbfixer_backend.py`: controlled local reconstruction with a recorded seed and backend versions.
- `windows/structure_repair_dialog.py`: inspect, plan, generate, review, and adopt.
- `tabs/md_setup_tab.py` and `core/preparation_orchestrator.py`: guarded handoff and provenance in the simulation manifest.

Each repair retains source PDB/FASTA, the audit, choices, plan, candidate, residue/atom mappings, removed and modeled identities, backend metadata, and QC. No in-place edits or replacement of a previous result directory are permitted. An invalid, failed, or changed candidate cannot be adopted.

## Example diagnosis

The official 5T66 entry contains two protein chains, alternate conformations, and missing heavy atoms. It has no internal sequence gap in the observed chains. Its publication states that the expressed CTX-M-15 construct starts at residue 29; rebuilding the unexpressed signal region would change the system. GLN29 is an optional unobserved terminal residue, while the partially observed THR30 requires heavy-atom completion. [5T66 entry](https://www.rcsb.org/structure/5T66), [experimental methods](https://pmc.ncbi.nlm.nih.gov/articles/PMC5365654/).

The official 1ERM entry contains a complete observed protein sequence, including BHD214 as a modified polymer residue with ASP as its parent. It does not contain an internal missing stretch simply because its author numbering starts at 26. Its BJI inhibitor is covalently attached. BHD214 and its OB atom must remain in the requested complete-complex validation; an explicit parent-conversion option belongs only to a separately selected derivative workflow. [1ERM entry](https://www.rcsb.org/structure/1ERM).

Regression tests also use deliberately incomplete synthetic structures to exercise genuine sequence-gap reconstruction without pretending those gaps occur in the deposited 1ERM structure.

## Acceptance checks

Tests cover consistent altloc selection, true duplicates, distinct close atoms, MODRES conversion, FASTA ambiguity/mismatches, author-number gaps, insertion codes, stale hashes, backend failure, invalid successful output, GUI job lifetime, explicit adoption, and preparation handoff. Real examples must produce readable topology/trajectory outputs and finite values without indiscriminately accepting GROMACS warnings. Short dynamics verifies execution and initial numerical behavior; it does not validate the biological conformation of a reconstructed region.

API references: [PDBFixer manual](https://github.com/openmm/pdbfixer/blob/master/Manual.html), [Biopython pairwise alignment](https://biopython.org/docs/latest/Tutorial/chapter_pairwise.html).
