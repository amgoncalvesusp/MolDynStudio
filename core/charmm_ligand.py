"""Prepare explicit ligand poses with SwissParam MATCH and CHARMM parameters."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from io import StringIO
import json
import math
from pathlib import Path
from typing import Callable
import uuid

from core.charmm_conversion import convert_charmm_ligand
from core.swissparam_client import parameterize_match
from utils.topology_builder import LigandTopologyArtifacts


def _molecule(path: Path):
    from parmed.formats import Mol2File
    from parmed.periodic_table import AtomicNum

    payload = path.read_bytes()
    if len(payload) > 5 * 1024 * 1024 or payload.count(b"@<TRIPOS>MOLECULE") != 1:
        raise ValueError("Supply one explicit hydrogenated MOL2 ligand, at most 5 MiB.")
    molecule = Mol2File.parse(StringIO(payload.decode("utf-8")), structure=True)
    names = tuple(atom.name.upper() for atom in molecule.atoms)
    if len(molecule.residues) != 1 or not names or len(names) > 1000 or len(set(names)) != len(names):
        raise ValueError("Each ligand pose must have one residue and unique atom names (1–1000 atoms).")
    if any(not math.isfinite(float(value)) for atom in molecule.atoms for value in (atom.xx, atom.xy, atom.xz)):
        raise ValueError("Ligand coordinates must be finite.")
    elements = tuple(sorted((atom.name.upper(), AtomicNum.get(str(atom.type).split(".", 1)[0], 0))
                            for atom in molecule.atoms))
    if any(number == 0 for _name, number in elements):
        raise ValueError("Every MOL2 atom must have an explicit supported Tripos element type.")
    bonds = tuple(sorted((*sorted((bond.atom1.name.upper(), bond.atom2.name.upper())), float(bond.order))
                         for bond in molecule.bonds))
    return molecule, (elements, bonds), hashlib.sha256(payload).hexdigest()


def _one_file(package: Path, suffix: str) -> Path:
    matches = tuple(path for path in package.rglob("*") if path.is_file() and path.suffix.lower() == suffix)
    if len(matches) != 1:
        raise ValueError(f"MATCH package must contain exactly one {suffix} file.")
    return matches[0]


def _snapshot_parameters(package: Path, files: tuple[Path, ...], run: Path, expected_input: str):
    provenance = (package / "provenance.json").read_bytes()
    metadata = json.loads(provenance)
    if not isinstance(metadata, dict) or metadata.get("input_sha256") != expected_input:
        raise ValueError("MATCH provenance input hash does not match the submitted ligand.")
    declared = metadata.get("output_sha256")
    if not isinstance(declared, dict):
        raise ValueError("MATCH provenance has no verifiable output hashes.")
    payloads = tuple((path, path.read_bytes()) for path in files)
    if any(hashlib.sha256(payload).hexdigest() != declared.get(path.relative_to(package).as_posix())
           for path, payload in payloads):
        raise ValueError("MATCH package integrity changed after retrieval.")
    directory = run / "source_parameters"
    directory.mkdir()
    copies = tuple(directory / path.name for path in files)
    for destination, (_source, payload) in zip(copies, payloads):
        destination.write_bytes(payload)
    copied_provenance = directory / "provenance.json"
    copied_provenance.write_bytes(provenance)
    hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (*copies, copied_provenance)}
    return copies, copied_provenance, hashes


def _write_pose_gro(path: Path, molecules: tuple, atom_names: tuple[str, ...], residue_name: str) -> None:
    count = len(molecules) * len(atom_names)
    if count > 99999 or len(residue_name) > 5:
        raise ValueError("Ligand copies exceed GRO atom/residue representation limits.")
    coordinates = tuple(tuple(float(value) / 10 for value in (atom.xx, atom.xy, atom.xz))
                        for molecule in molecules for atom in molecule.atoms)
    box = tuple(max(max(row[axis] for row in coordinates) - min(row[axis] for row in coordinates) + 2.0, 2.0)
                for axis in range(3))
    rows = ["SwissParam MATCH ligand poses; original input coordinates", str(count)]
    for residue_id, molecule in enumerate(molecules, start=1):
        by_name = {atom.name.upper(): atom for atom in molecule.atoms}
        for atom_index, name in enumerate(atom_names, start=1):
            atom = by_name[name]
            serial = (residue_id - 1) * len(atom_names) + atom_index
            xyz = "".join(f"{value / 10:8.3f}" for value in (atom.xx, atom.xy, atom.xz))
            if len(xyz) != 24:
                raise ValueError("Ligand coordinates exceed GRO fixed-width limits.")
            rows.append(f"{residue_id:5d}{residue_name:<5s}{name:>5s}{serial:5d}{xyz}")
    rows.append(" ".join(f"{value:.5f}" for value in box))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")


def _write_manifest(path: Path, values: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(values, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def prepare_charmm_ligand(mol2_path: str | Path, project_dir: str | Path, *, net_charge: int,
                          allow_upload: bool, on_log: Callable[[str], None] | None = None,
                          ligand_copies: tuple[str, ...] = ()) -> LigandTopologyArtifacts:
    """Parameterize one ligand graph and retain each explicit bound pose.

    Additional copies must use exactly the same atom names, elements and bond
    orders. Any service-induced change to protonation/identity is rejected.
    """
    if isinstance(net_charge, bool) or not isinstance(net_charge, int) or abs(net_charge) > 20:
        raise ValueError("Ligand net charge must be an integer between -20 and 20.")
    if not isinstance(allow_upload, bool):
        raise ValueError("SwissParam upload permission must be an explicit boolean.")
    if not isinstance(ligand_copies, (tuple, list)) or len(ligand_copies) > 99:
        raise ValueError("Provide at most 99 additional ligand MOL2 poses.")
    paths = tuple(Path(value).expanduser().resolve() for value in (mol2_path, *ligand_copies))
    inputs = tuple(_molecule(path) for path in paths)
    reference_signature = inputs[0][1]
    if any(signature != reference_signature for _molecule_value, signature, _digest in inputs[1:]):
        raise ValueError("Ligand copies must have identical atom identities, elements and bond orders.")
    project = Path(project_dir).expanduser().resolve()
    run = project / "ligand" / f"charmm_{uuid.uuid4().hex}"
    run.mkdir(parents=True, exist_ok=False)
    manifest_path = run / "charmm_ligand_manifest.json"
    base = {"schema_version": 1, "method": "SwissParam MATCH (CGenFF-derived)", "status": "running",
            "created_at": datetime.now(timezone.utc).isoformat(), "requested_net_charge": net_charge,
            "molecule_count": len(inputs),
            "inputs": [{"path": str(path), "sha256": data[2]} for path, data in zip(paths, inputs)]}
    _write_manifest(manifest_path, base)
    try:
        package = parameterize_match(paths[0], project / "ligand" / "swissparam_cache",
                                     allow_upload=allow_upload, on_log=on_log)
        for path, data in zip(paths, inputs):
            if hashlib.sha256(path.read_bytes()).hexdigest() != data[2]:
                raise ValueError("A ligand input changed during MATCH parameterization; no result was adopted.")
        rtf = _one_file(package, ".rtf")
        matched_mol2, parameter_file = rtf.with_suffix(".mol2"), rtf.with_suffix(".par")
        if not matched_mol2.is_file() or not parameter_file.is_file():
            raise ValueError("MATCH RTF, PAR and MOL2 must have the same basename and directory.")
        copied, provenance, snapshot_hashes = _snapshot_parameters(
            package, (rtf, parameter_file, matched_mol2), run, inputs[0][2]
        )
        rtf, parameter_file, matched_mol2 = copied
        matched, matched_signature, _digest = _molecule(matched_mol2)
        if matched_signature != reference_signature:
            raise ValueError("MATCH changed ligand chemistry, protonation, atom identities or bond orders. "
                             "Supply an explicit hydrogenated MOL2 with unique, stable atom names.")
        result = convert_charmm_ligand(rtf, parameter_file,
                                      matched_mol2, run / "converted", net_charge=net_charge)
        # The server may reorient coordinates. Reuse atom-identity-checked
        # original poses rather than replacing the binding pose with that result.
        pose_gro = run / "ligand.gro"
        _write_pose_gro(pose_gro, tuple(data[0] for data in inputs), result.atom_names,
                        matched.residues[0].name)
        outputs = (pose_gro, result.ligand_itp, result.parameters_itp, result.psf_path)
        if any(hashlib.sha256(path.read_bytes()).hexdigest() != digest for path, digest in snapshot_hashes.items()):
            raise ValueError("A snapshotted CHARMM conversion input changed; no result was adopted.")
        if any(hashlib.sha256(path.read_bytes()).hexdigest() != data[2] for path, data in zip(paths, inputs)):
            raise ValueError("A ligand input changed during conversion; no result was adopted.")
        summary = {**base, "status": "completed", "net_charge_per_copy": result.net_charge,
                   "atom_order": result.atom_names, "term_counts_per_copy": dict(result.term_counts),
                   "coordinate_source": "original input MOL2 poses, mapped by unique atom name",
                   "nonbonded_scope": "Namespaced MATCH atom types; parent CHARMM Lorentz-Berthelot mixing. "
                                      "No unprovided intermolecular NBFIX parameters are inferred.",
                   "server_provenance": str(provenance),
                   "server_provenance_sha256": hashlib.sha256(provenance.read_bytes()).hexdigest(),
                   "conversion_input_sha256": {str(path.relative_to(run)): digest
                                               for path, digest in snapshot_hashes.items()},
                   "outputs": {str(path.relative_to(run)): hashlib.sha256(path.read_bytes()).hexdigest()
                               for path in outputs}}
        _write_manifest(manifest_path, summary)
        if on_log:
            on_log(f"CHARMM ligand ready: {len(result.atom_names)} atoms per copy, {len(inputs)} copies; "
                   f"charge {result.net_charge:.6f} per copy. Original poses retained.")
        return LigandTopologyArtifacts(pose_gro, result.ligand_itp, None,
                                       parameter_includes=(result.parameters_itp,),
                                       provenance_path=manifest_path, molecule_count=len(inputs))
    except Exception as exc:
        _write_manifest(manifest_path, {**base, "status": "failed", "error": str(exc)})
        raise
