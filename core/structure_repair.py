"""Reviewable, transactional structure-repair plans with verified provenance."""
from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Callable
from uuid import uuid4

from core.sequence_mapping import ONE_TO_THREE
from core.structure_audit import HEAVY_ATOMS, AtomRecord, StructureAudit, audit_structure, file_sha256
from utils.structure_validation import CovalentLigandError, IGNORED_HETERO_RESIDUES, ensure_noncovalent_complex


@dataclass(frozen=True)
class RepairChoices:
    model_id: int | None = None
    chain_ids: tuple[str, ...] = ()
    remove_heterogens: bool = False
    normalize_modres: tuple[str, ...] = ()
    delete_atom_lines: tuple[int, ...] = ()
    selected_altlocs: tuple[tuple[str, str], ...] = ()
    remove_exact_duplicates: bool = True
    resolve_altlocs: bool = True
    fill_gap_ids: tuple[str, ...] = ()
    add_missing_heavy_atoms: bool = False


@dataclass(frozen=True)
class RepairPlan:
    audit: StructureAudit
    choices: RepairChoices
    changes: tuple[str, ...]
    blocked_reasons: tuple[str, ...]

    @property
    def can_apply(self) -> bool:
        return not self.blocked_reasons


@dataclass(frozen=True)
class RepairResult:
    directory: Path
    candidate_path: Path
    report_path: Path
    source_path: Path
    source_sha256: str
    candidate_sha256: str
    ready: bool
    blocked_reasons: tuple[str, ...]
    md_requires_parameters: bool = False
    report_sha256: str = ""

    @property
    def coordinate_repair_ready(self) -> bool:
        return self.ready


def build_repair_plan(audit: StructureAudit, choices: RepairChoices) -> RepairPlan:
    if not isinstance(choices, RepairChoices):
        raise TypeError("Repair choices must be explicit RepairChoices.")
    blocks = list(audit.blocked_reasons)
    changes: list[str] = []
    if choices.model_id is None and len(audit.model_ids) > 1:
        blocks.append("Select exactly one coordinate model before repair.")
    if choices.model_id is not None and choices.model_id not in audit.model_ids:
        blocks.append("Selected coordinate model is absent.")
    known_chains = {atom.identity.chain_id for atom in audit.atoms}
    if set(choices.chain_ids) - known_chains:
        blocks.append("Selected chain identifiers are absent.")
    known_lines = {atom.line_number for atom in audit.atoms}
    if any(type(number) is not int or number <= 0 or number not in known_lines for number in choices.delete_atom_lines):
        blocks.append("Atom deletions require positive, existing atom line numbers.")
    modifications = {item.residue_id: item for item in audit.modified_residues}
    if set(choices.normalize_modres) - modifications.keys():
        blocks.append("Selected MODRES conversion is absent from the audit.")
    for residue_id in choices.normalize_modres:
        item = modifications.get(residue_id)
        if item is not None and item.parent_residue_name not in HEAVY_ATOMS:
            blocks.append(f"No validated standard parent template for {residue_id}.")
    alternatives = {item.residue_id: item for item in audit.altloc_groups}
    if len(dict(choices.selected_altlocs)) != len(choices.selected_altlocs):
        blocks.append("Only one conformer may be chosen per residue.")
    for residue_id, label in choices.selected_altlocs:
        if residue_id not in alternatives or label not in alternatives[residue_id].labels:
            blocks.append("Selected alternate conformer is absent from the audit.")
    gaps = {gap.gap_id: gap for gap in audit.sequence_gaps}
    if set(choices.fill_gap_ids) - gaps.keys():
        blocks.append("Selected sequence gap is absent from the audit.")
    selected_gap_keys = []
    for mapping in audit.sequence_mappings:
        selected = [gap for gap in mapping.gaps if gap.gap_id in choices.fill_gap_ids]
        if selected:
            blocks.extend(mapping.blocked_reasons)
        for gap in selected:
            selected_gap_keys.append((gap.model_id, gap.chain_id, gap.insert_before))
            if choices.model_id is not None and gap.model_id != choices.model_id:
                blocks.append("Selected sequence gap belongs to an excluded model.")
            if choices.chain_ids and gap.chain_id not in choices.chain_ids:
                blocks.append("Selected sequence gap belongs to an excluded chain.")
            changes.append(f"Model explicitly selected {gap.label}; inferred coordinates require review.")
    if len(set(selected_gap_keys)) != len(selected_gap_keys):
        blocks.append("FASTA and SEQRES selections describe the same insertion site; select one reference only.")
    retained = _retained_atoms(audit, choices)
    if len({atom.serial for atom in retained}) != len(retained):
        blocks.append("Duplicate retained atom serial numbers make connectivity ambiguous.")
    surviving_lines = {atom.line_number for atom in retained}
    retained_residues = {atom.identity.residue_id for atom in retained}
    for gap_id in choices.fill_gap_ids:
        gap = gaps.get(gap_id)
        if gap and any(anchor and anchor not in retained_residues for anchor in (gap.left_residue_id, gap.right_residue_id)):
            blocks.append("Selected gap anchors were removed; repeat sequence mapping for the retained structure.")
    if not retained:
        blocks.append("Repair choices would remove every atom.")
    for collision in audit.collisions:
        if collision.first_line in surviving_lines and collision.second_line in surviving_lines:
            blocks.append(f"Distinct atoms at lines {collision.first_line}/{collision.second_line} are {collision.distance_angstrom:.3f} Å apart; explicit resolution required.")
    removed = len(audit.atoms) - len(retained)
    if removed:
        changes.append(f"Remove {removed} coordinate records through the explicitly listed filters.")
    if choices.resolve_altlocs and audit.altloc_groups:
        changes.append("Select one coherent alternate conformation per residue; blank shared atoms are retained.")
    if choices.remove_exact_duplicates:
        changes.append(f"Remove {len(audit.exact_duplicate_lines)} exact repeated coordinate records.")
    if choices.remove_heterogens:
        changes.append("Remove non-polymer heterogens, including waters and ligands (explicit apo/export choice).")
    for residue_id in choices.normalize_modres:
        if residue_id in modifications:
            item = modifications[residue_id]
            changes.append(f"Convert {residue_id} to {item.parent_residue_name}; remove only non-parent atoms: {', '.join(item.removable_atom_names) or 'none'}.")
    if choices.add_missing_heavy_atoms:
        changes.append("Reconstruct missing heavy atoms using the selected structural backend; preserve observed coordinates.")
    return RepairPlan(audit, choices, tuple(changes), tuple(dict.fromkeys(blocks)))


def apply_repair_plan(plan: RepairPlan, output_root: str | Path, backend=None, on_progress=None) -> RepairResult:
    if not plan.can_apply:
        raise ValueError("Repair plan is blocked: " + "; ".join(plan.blocked_reasons))
    _verify_sources(plan.audit)
    root = Path(output_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    final = root / f"structure_repair_{uuid4().hex}"
    staging = Path(tempfile.mkdtemp(prefix=".structure_repair_", dir=root))
    progress: Callable[[str], None] = on_progress or (lambda _message: None)
    try:
        progress("Preserving source files and writing the reviewed plan.")
        shutil.copy2(plan.audit.source_path, staging / "source.pdb")
        if file_sha256(staging / "source.pdb") != plan.audit.source_sha256:
            raise ValueError("Source structure changed while preserving its original copy.")
        if plan.audit.fasta_path:
            shutil.copy2(plan.audit.fasta_path, staging / "source.fasta")
            if file_sha256(staging / "source.fasta") != plan.audit.fasta_sha256:
                raise ValueError("FASTA changed while preserving its original copy.")
        _write_json(staging / "audit.json", plan.audit)
        _write_json(staging / "plan.json", plan)
        sanitized = staging / "selected.pdb"
        _write_selected(plan, sanitized)
        candidate = staging / "candidate.pdb"
        backend_report = None
        errors: list[str] = []
        if plan.choices.add_missing_heavy_atoms or plan.choices.fill_gap_ids:
            try:
                progress("Reconstructing only approved missing atoms and sequence segments.")
                backend_report = _run_backend(plan, sanitized, candidate, backend, progress)
            except Exception as exc:
                errors.append(f"Structural modeling failed: {exc}")
                shutil.copy2(sanitized, candidate)
        else:
            shutil.copy2(sanitized, candidate)
        _verify_sources(plan.audit)
        candidate_audit = audit_structure(candidate)
        errors.extend(_candidate_errors(candidate_audit))
        atom_mapping, mapping_errors = _atom_mapping(plan, candidate_audit)
        errors.extend(mapping_errors)
        covalent = tuple(asdict(link) for link in candidate_audit.covalent_links)
        special = tuple(residue.residue_id for residue in candidate_audit.residues
                        if residue.identity.residue_name not in HEAVY_ATOMS
                        and residue.identity.residue_name not in IGNORED_HETERO_RESIDUES)
        requires_parameters = bool(covalent or special)
        # Reuse the preparation gate, but coordinate adoption itself supports complete covalent complexes.
        try:
            ensure_noncovalent_complex(candidate)
        except CovalentLigandError:
            requires_parameters = True
        candidate_hash = file_sha256(candidate)
        errors = list(dict.fromkeys(errors))
        report = {
            "schema_version": "1.0", "coordinate_repair_ready": not errors,
            "ready": not errors, "md_requires_parameters": requires_parameters,
            "source_path": str(plan.audit.source_path), "source_sha256": plan.audit.source_sha256,
            "fasta_path": str(plan.audit.fasta_path) if plan.audit.fasta_path else None,
            "fasta_sha256": plan.audit.fasta_sha256,
            "candidate_path": str(final / "candidate.pdb"), "candidate_sha256": candidate_hash,
            "blocked_reasons": errors, "remaining_covalent_links": covalent,
            "special_residues": special, "changes": plan.changes,
            "atom_mapping": atom_mapping, "backend": _rebase_backend_report(backend_report, staging, final),
            "artifact_sha256": {name: file_sha256(staging / name) for name in ("source.pdb", "audit.json", "plan.json")},
            "scientific_limitations": ["Reconstructed coordinates are modeled, not experimental observations.",
                                       "Coordinate readiness does not establish force-field parameterization or scientific validity."],
        }
        if plan.audit.fasta_path:
            report["artifact_sha256"]["source.fasta"] = file_sha256(staging / "source.fasta")
        _write_json(staging / "repair_report.json", report)
        report_hash = file_sha256(staging / "repair_report.json")
        os.replace(staging, final)
        progress("Candidate and provenance saved. Review coordinate and parameterization status before adoption.")
        return RepairResult(final, final / "candidate.pdb", final / "repair_report.json", plan.audit.source_path,
                            plan.audit.source_sha256, candidate_hash, not errors, tuple(errors), requires_parameters, report_hash)
    except BaseException:
        if staging.is_dir() and staging.resolve().parent == root:
            shutil.rmtree(staging)
        raise


def verify_repair_result(result: RepairResult) -> None:
    if not result.report_sha256 or file_sha256(result.report_path) != result.report_sha256:
        raise ValueError("Repair report changed after the candidate was generated.")
    metadata = verify_repair_report(result.report_path, result.candidate_path)
    if metadata["report_sha256"] != result.report_sha256:
        raise ValueError("Repair report changed during candidate verification.")
    if metadata["source_sha256"] != result.source_sha256 or metadata["candidate_sha256"] != result.candidate_sha256:
        raise ValueError("Repair result hashes changed after the candidate was created.")


def verify_repair_report(report_path: str | Path, candidate_path: str | Path) -> dict:
    report_file, candidate = Path(report_path).resolve(), Path(candidate_path).resolve()
    report_bytes = report_file.read_bytes()
    report_hash = hashlib.sha256(report_bytes).hexdigest()
    data = json.loads(report_bytes)
    if not isinstance(data, dict) or data.get("schema_version") != "1.0":
        raise ValueError("Unsupported structural repair report.")
    if data.get("coordinate_repair_ready") is not True or data.get("blocked_reasons"):
        raise ValueError("Repair candidate is not coordinate-ready.")
    if Path(data.get("candidate_path", "")).resolve() != candidate:
        raise ValueError("Repair report belongs to a different candidate.")
    source = Path(data["source_path"])
    for path, expected, label in ((candidate, data["candidate_sha256"], "Candidate"),
                                   (source, data["source_sha256"], "Original source")):
        if file_sha256(path) != expected:
            raise ValueError(f"{label} changed after structure repair.")
    if data.get("fasta_path") and file_sha256(data["fasta_path"]) != data["fasta_sha256"]:
        raise ValueError("FASTA source changed after structure repair.")
    hashes = data.get("artifact_sha256", {})
    if not isinstance(hashes, dict) or not {"source.pdb", "audit.json", "plan.json"}.issubset(hashes):
        raise ValueError("Repair provenance is incomplete.")
    for name, expected in hashes.items():
        if name not in {"source.pdb", "source.fasta", "audit.json", "plan.json"}:
            raise ValueError("Unexpected provenance artifact path.")
        if file_sha256(report_file.parent / name) != expected:
            raise ValueError(f"Repair provenance changed: {name}.")
    if hashes["source.pdb"] != data["source_sha256"]:
        raise ValueError("Archived source does not match the audited original.")
    errors = _candidate_errors(audit_structure(candidate))
    if errors:
        raise ValueError("Candidate validation failed: " + "; ".join(errors))
    if file_sha256(report_file) != report_hash:
        raise ValueError("Repair report changed during candidate verification.")
    return {"report_path": str(report_file), "report_sha256": report_hash,
            "source_path": str(source), "source_sha256": data["source_sha256"],
            "candidate_path": str(candidate), "candidate_sha256": data["candidate_sha256"],
            "coordinate_repair_ready": True, "md_requires_parameters": bool(data.get("md_requires_parameters")),
            "remaining_covalent_links": data.get("remaining_covalent_links", []),
            "special_residues": data.get("special_residues", [])}


def _write_json(path: Path, value) -> None:
    def encode(item):
        if is_dataclass(item):
            return asdict(item)
        if isinstance(item, Path):
            return str(item)
        raise TypeError(f"Unsupported report value: {type(item).__name__}")
    path.write_text(json.dumps(value, default=encode, indent=2, allow_nan=False), encoding="utf-8")


def _rebase_backend_report(value, staging: Path, final: Path):
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        return {key: _rebase_backend_report(item, staging, final) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_rebase_backend_report(item, staging, final) for item in value]
    if isinstance(value, (str, Path)) and str(value).startswith(str(staging)):
        return str(final) + str(value)[len(str(staging)):]
    return value


def _verify_sources(audit: StructureAudit) -> None:
    if file_sha256(audit.source_path) != audit.source_sha256:
        raise ValueError("Source structure changed after audit; run the audit again.")
    if audit.fasta_path and file_sha256(audit.fasta_path) != audit.fasta_sha256:
        raise ValueError("FASTA source changed after audit; run the audit again.")


def _retained_atoms(audit: StructureAudit, choices: RepairChoices) -> tuple[AtomRecord, ...]:
    model = choices.model_id if choices.model_id is not None else next(iter(audit.model_ids), 1)
    polymers = {residue.residue_id for residue in audit.residues if residue.polymer}
    mods = {item.residue_id: item for item in audit.modified_residues}
    selected = {item.residue_id: item.recommended for item in audit.altloc_groups}
    selected.update(dict(choices.selected_altlocs))
    deleted = set(choices.delete_atom_lines)
    if choices.remove_exact_duplicates:
        deleted.update(audit.exact_duplicate_lines)
    result = []
    for atom in audit.atoms:
        identity = atom.identity
        if identity.model_id != model or choices.chain_ids and identity.chain_id not in choices.chain_ids:
            continue
        if atom.line_number in deleted or choices.remove_heterogens and identity.residue_id not in polymers:
            continue
        if choices.resolve_altlocs and identity.altloc and selected.get(identity.residue_id) != identity.altloc:
            continue
        if identity.residue_id in choices.normalize_modres:
            modification = mods.get(identity.residue_id)
            if modification and identity.atom_name in modification.removable_atom_names:
                continue
        result.append(atom)
    return tuple(result)


def _write_selected(plan: RepairPlan, target: Path) -> None:
    retained = _retained_atoms(plan.audit, plan.choices)
    lookup = {atom.line_number: atom for atom in retained}
    serials = {atom.serial for atom in retained}
    residue_keys = {(atom.identity.chain_id, atom.identity.residue_number, atom.identity.insertion_code, atom.identity.residue_name) for atom in retained}
    modifications = {item.residue_id: item for item in plan.audit.modified_residues}
    chains = {atom.identity.chain_id for atom in retained}
    def atom_key(atom):
        identity = atom.identity
        return (identity.chain_id, identity.residue_number, identity.insertion_code,
                identity.residue_name, identity.atom_name, identity.altloc)
    retained_keys = {atom_key(atom) for atom in retained}
    selected_model = retained[0].identity.model_id
    original_keys = {atom_key(atom) for atom in plan.audit.atoms if atom.identity.model_id == selected_model}
    original_residue_keys = {key[:4] for key in original_keys}
    def has_link_endpoint(end, keys):
        return end in keys if end[5] else any(key[:5] == end[:5] for key in keys)
    original_serials = {atom.serial for atom in plan.audit.atoms if atom.identity.model_id == selected_model}
    parent_names = {atom_key(atom)[:4]: modifications[atom.identity.residue_id].parent_residue_name
                    for atom in retained if atom.identity.residue_id in plan.choices.normalize_modres}
    output = []
    segment_has_atoms = False
    active_model = None
    for number, line in enumerate(plan.audit.source_path.read_text(encoding="utf-8-sig").splitlines(), 1):
        record = line[:6].strip()
        if record == "MODEL":
            active_model = int(line[10:14])
            continue
        if record == "ENDMDL":
            active_model = None
            continue
        if active_model is not None and active_model != selected_model:
            continue
        if record in {"ATOM", "HETATM"}:
            atom = lookup.get(number)
            if atom is None:
                continue
            if plan.choices.resolve_altlocs:
                line = line[:16] + " " + line[17:]
            if atom.identity.residue_id in plan.choices.normalize_modres:
                line = "ATOM  " + line[6:17] + modifications[atom.identity.residue_id].parent_residue_name.rjust(3) + line[20:]
            output.append(line)
            segment_has_atoms = True
        elif record == "TER":
            if segment_has_atoms:
                output.append("TER")
                segment_has_atoms = False
        elif record == "CONECT":
            try:
                numbers = [int(line[start:start + 5]) for start in range(6, len(line), 5) if line[start:start + 5].strip()]
            except ValueError:
                raise ValueError(f"Invalid fixed-column CONECT record at line {number}.") from None
            if numbers and (numbers[0] in serials or numbers[0] not in original_serials):
                bonded = [value for value in numbers[1:] if value in serials or value not in original_serials]
                if bonded:
                    output.append("CONECT" + "".join(f"{value:5d}" for value in (numbers[0], *bonded)))
        elif record == "LINK":
            ends = ((line[21:22].strip(), line[22:26].strip(), line[26:27].strip(), line[17:20].strip(), line[12:16].strip(), line[16:17].strip()),
                    (line[51:52].strip(), line[52:56].strip(), line[56:57].strip(), line[47:50].strip(), line[42:46].strip(), line[46:47].strip()))
            if any((end[:4] in original_residue_keys and end[:4] not in residue_keys)
                   or (has_link_endpoint(end, original_keys) and not has_link_endpoint(end, retained_keys)) for end in ends):
                continue
            for end, start in zip(ends, (17, 47)):
                if end[:4] in parent_names:
                    line = line[:start] + parent_names[end[:4]].rjust(3) + line[start + 3:]
                if plan.choices.resolve_altlocs and has_link_endpoint(end, retained_keys):
                    line = line[:start - 1] + " " + line[start:]
            output.append(line)
        elif record == "SSBOND":
            ends = ((line[15:16].strip(), line[17:21].strip(), line[21:22].strip()),
                    (line[29:30].strip(), line[31:35].strip(), line[35:36].strip()))
            if not any(any(key[:3] == end for key in original_residue_keys)
                       and not any(key[:3] == end for key in residue_keys) for end in ends):
                output.append(line)
        elif record == "MODRES":
            key = (line[16:17].strip(), line[18:22].strip(), line[22:23].strip(), line[12:15].strip())
            if key in residue_keys and not any(atom.identity.residue_id in plan.choices.normalize_modres and
                                             (atom.identity.chain_id, atom.identity.residue_number, atom.identity.insertion_code, atom.identity.residue_name) == key for atom in retained):
                output.append(line)
        elif record == "SEQRES":
            if line[11:12].strip() in chains:
                output.append(line)
        elif record not in {"MODEL", "ENDMDL", "END", "MASTER", "TER", "ANISOU", "SIGATM", "SIGUIJ"}:
            output.append(line)
    if segment_has_atoms:
        output.append("TER")
    output.append("END")
    target.write_text("\n".join(output) + "\n", encoding="utf-8")


def _run_backend(plan: RepairPlan, selected: Path, candidate: Path, backend, progress):
    if backend is None:
        from core.pdbfixer_backend import build_missing_structure
        backend = build_missing_structure
    function = getattr(backend, "build_missing_structure", backend)
    selected_gaps = [gap for gap in plan.audit.sequence_gaps if gap.gap_id in plan.choices.fill_gap_ids]
    retained_ids = {atom.identity.residue_id for atom in _retained_atoms(plan.audit, plan.choices)}
    def retained_slot(gap):
        original_chain = [residue.residue_id for residue in plan.audit.residues if residue.polymer
                          and residue.identity.model_id == gap.model_id and residue.identity.chain_id == gap.chain_id]
        return sum(residue_id in retained_ids for residue_id in original_chain[:gap.insert_before])
    gaps = tuple({"gap_id": gap.gap_id, "chain_id": gap.chain_id, "insert_before": retained_slot(gap),
                  "resnames": tuple(ONE_TO_THREE[letter] for letter in gap.sequence),
                  "source": gap.source, "reference_start": gap.start_index, "reference_end": gap.end_index}
                 for gap in selected_gaps)
    return function(selected, candidate, missing_residues=gaps,
                    add_heavy_atoms=plan.choices.add_missing_heavy_atoms, seed=2026, on_progress=progress)


def _candidate_errors(audit: StructureAudit) -> list[str]:
    errors = list(audit.blocked_reasons)
    if len(audit.model_ids) != 1:
        errors.append("Candidate must contain one coordinate model.")
    if audit.altloc_groups:
        errors.append("Unresolved alternate conformations remain.")
    if audit.exact_duplicate_lines:
        errors.append("Exact duplicate atom records remain.")
    if audit.collisions:
        errors.append(f"Candidate contains {len(audit.collisions)} distinct atom pairs closer than 0.5 Å.")
    if audit.missing_heavy_atoms:
        errors.append(f"Candidate contains {len(audit.missing_heavy_atoms)} residues with missing standard heavy atoms.")
    identities = [atom.identity for atom in audit.atoms]
    if len(set(identities)) != len(identities):
        errors.append("Conflicting repeated atom identities remain.")
    serials = [atom.serial for atom in audit.atoms]
    if len(set(serials)) != len(serials):
        errors.append("Duplicate atom serial numbers make connectivity ambiguous.")
    errors.extend(_connectivity_errors(audit))
    return errors


def _connectivity_errors(audit: StructureAudit) -> list[str]:
    serials = {atom.serial for atom in audit.atoms}
    keys = {(a.identity.chain_id, a.identity.residue_number, a.identity.insertion_code,
             a.identity.residue_name, a.identity.atom_name, a.identity.altloc) for a in audit.atoms}
    errors = []
    for line in audit.source_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("CONECT"):
            try:
                values = [int(line[start:start + 5]) for start in range(6, len(line), 5) if line[start:start + 5].strip()]
            except ValueError:
                errors.append("Malformed CONECT record remains.")
                continue
            if len(values) < 2 or not set(values).issubset(serials):
                errors.append("Dangling CONECT atom reference remains.")
        elif line.startswith("LINK"):
            ends = ((line[21:22].strip(), line[22:26].strip(), line[26:27].strip(), line[17:20].strip(), line[12:16].strip(), line[16:17].strip()),
                    (line[51:52].strip(), line[52:56].strip(), line[56:57].strip(), line[47:50].strip(), line[42:46].strip(), line[46:47].strip()))
            if any(end not in keys for end in ends):
                errors.append("Dangling LINK atom reference remains.")
        elif line.startswith("SSBOND"):
            ends = ((line[15:16].strip(), line[17:21].strip(), line[21:22].strip()),
                    (line[29:30].strip(), line[31:35].strip(), line[35:36].strip()))
            if any(not any(key[:3] == end and key[4] == "SG" for key in keys) for end in ends):
                errors.append("Dangling SSBOND sulfur reference remains.")
    return errors


def _atom_mapping(plan: RepairPlan, candidate: StructureAudit) -> tuple[list[dict], list[str]]:
    retained = _retained_atoms(plan.audit, plan.choices)
    lookup = {(atom.identity.chain_id, atom.identity.residue_number, atom.identity.insertion_code,
               atom.identity.residue_name, atom.identity.atom_name): atom for atom in candidate.atoms}
    modifications = {item.residue_id: item.parent_residue_name for item in plan.audit.modified_residues}
    mapping, errors, observed_keys = [], [], set()
    for atom in retained:
        identity = atom.identity
        name = modifications[identity.residue_id] if identity.residue_id in plan.choices.normalize_modres else identity.residue_name
        key = (identity.chain_id, identity.residue_number, identity.insertion_code, name, identity.atom_name)
        output = lookup.get(key)
        observed_keys.add(key)
        if output is None:
            errors.append(f"Observed atom disappeared during repair: line {atom.line_number}.")
            continue
        if any(abs(a - b) > 1e-6 for a, b in zip(atom.coordinates, output.coordinates)):
            errors.append(f"Observed atom moved during repair: line {atom.line_number}.")
        mapping.append({"origin": "observed", "source_line": atom.line_number,
                        "source_identity": asdict(identity), "output_identity": asdict(output.identity)})
    for key, atom in lookup.items():
        if key not in observed_keys:
            mapping.append({"origin": "modeled", "source_line": None, "source_identity": None,
                            "output_identity": asdict(atom.identity)})
    return mapping, errors
