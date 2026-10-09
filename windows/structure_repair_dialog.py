"""Review structural evidence and explicitly adopt a separately written candidate."""

from __future__ import annotations

from functools import partial
import json
from pathlib import Path
import re
from typing import Callable

from PyQt5.QtCore import Qt, QThread, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QScrollArea, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from core.structure_audit import audit_structure
from core.pdbfixer_backend import backend_available
from core.structure_repair import (
    RepairChoices, apply_repair_plan, build_repair_plan, verify_repair_result,
)
from tabs.base import PathSelector


class StructureTask(QThread):
    """Keep work results alive until QThread.finished is delivered to the UI."""

    def __init__(self, operation: Callable, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.result = None
        self.error = ""

    def run(self) -> None:
        try:
            self.result = self.operation()
        except Exception as exc:
            self.error = str(exc) or type(exc).__name__


class StructureRepairDialog(QDialog):
    repair_adopted = pyqtSignal(str, str, str)

    def __init__(self, source_path: str = "", output_root: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Inspect / repair structure")
        self.resize(1050, 810)
        self.operation_blocked: Callable[[], bool] = lambda: False
        self._active = False
        self._worker = None
        self._revision = 0
        self._syncing = False
        self._audit = None
        self._plan = None
        self._candidate = None
        self._mapping_pending = False
        self._editors = []
        self.altloc_choices = {}
        layout = QVBoxLayout(self)
        intro = QLabel("Inspect the original, review a repair plan, and verify a separate candidate. All chains and heterogens are retained unless you explicitly choose otherwise. Coordinate repair does not supply a force-field topology.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self._build_inspect(source_path, output_root)
        self._build_plan()
        self._build_candidate()
        self._build_adoption()
        self.status = QLabel("1. Select a PDB structure and inspect it. The original file is preserved.")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._wire_changes()
        self._refresh_actions()

    def _tab(self, title: str):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        page = QWidget()
        layout = QVBoxLayout(page)
        scroll.setWidget(page)
        self.tabs.addTab(scroll, title)
        return layout

    def _build_inspect(self, source_path: str, output_root: str):
        layout = self._tab("1 Inspect")
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.source = PathSelector("Original PDB", "PDB structures (*.pdb);;All Files (*)")
        self.source.setText(source_path)
        self.fasta = PathSelector("Optional reference FASTA", "FASTA (*.fasta *.fa *.faa *.fas);;All Files (*)")
        self.fasta_chain = QComboBox()
        self.fasta_chain.addItem("Select chain after inspection", None)
        self.fasta_record = QComboBox()
        self.fasta_record.addItem("Select FASTA record after inspection", None)
        self.output = PathSelector("Candidate output folder", directory=True)
        self.output.setText(output_root or str(Path(source_path).parent / "structure_repairs"))
        for label, field in (("Original PDB", self.source), ("Optional FASTA", self.fasta),
                             ("FASTA chain", self.fasta_chain), ("FASTA record", self.fasta_record),
                             ("Output folder", self.output)):
            form.addRow(label, field)
            self._editors.append(field)
        layout.addLayout(form)
        note = QLabel("FASTA mapping is explicit: inspect once to list chains and records, select a record and its chain, then inspect again. No sequence gap is modeled without an approved mapping and a selected gap.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.inspect_button = QPushButton("Inspect structure")
        self.inspect_button.setObjectName("PrimaryAction")
        self.inspect_button.clicked.connect(self.inspect_structure)
        layout.addWidget(self.inspect_button)
        self.audit_report = self._text("Audit findings will appear here: alternate locations, duplicate atoms, close overlaps, sequence gaps, missing heavy atoms, and modified residues.")
        layout.addWidget(self.audit_report, 1)

    def _build_plan(self):
        layout = self._tab("2 Plan")
        form = QFormLayout()
        self.model = QComboBox()
        self.model.addItem("Keep source model selection", None)
        form.addRow("Model", self.model)
        self.chains = QListWidget()
        self.chains.setMaximumHeight(115)
        form.addRow("Retained chains", self.chains)
        layout.addLayout(form)
        self.resolve_altlocs = QCheckBox("Resolve alternate locations by atom completeness, then occupancy")
        self.resolve_altlocs.setChecked(True)
        self.remove_duplicates = QCheckBox("Remove exact duplicate atom records")
        self.remove_duplicates.setChecked(True)
        self.remove_heterogens = QCheckBox("Alternative apo preparation: remove heterogens and export protein only")
        self.missing_heavy = QCheckBox("Build missing heavy atoms using the optional local PDBFixer backend")
        for field in (self.resolve_altlocs, self.remove_duplicates, self.remove_heterogens, self.missing_heavy):
            layout.addWidget(field)
        backend = QLabel(
            "Local modeling backend: available. Added coordinates will be identified separately from observed atoms."
            if backend_available() else
            "Local modeling backend: unavailable. Building gaps or missing heavy atoms requires PDBFixer and OpenMM in this application's Python environment. Conservative edits remain available."
        )
        backend.setWordWrap(True)
        layout.addWidget(backend)
        caution = QLabel("Protein-only export removes ligands, ions, and other heterogens. Parent-residue conversion changes chemical identity. Both remain off unless selected; a full covalent complex requires a custom topology before MD.")
        caution.setWordWrap(True)
        layout.addWidget(caution)
        self.altloc_group = QGroupBox("Alternate conformers")
        self.altloc_form = QFormLayout(self.altloc_group)
        layout.addWidget(self.altloc_group)
        lists = QHBoxLayout()
        self.modres = self._checklist_group(lists, "Optional MODRES parent conversions")
        self.gaps = self._checklist_group(lists, "Approved sequence gaps (none selected)")
        layout.addLayout(lists)
        deletion_form = QFormLayout()
        self.delete_lines = QLineEdit()
        self.delete_lines.setPlaceholderText("Explicit atom line IDs, e.g. 120, 135; no ranges")
        self.delete_lines.setAccessibleName("Atom line IDs explicitly approved for deletion")
        deletion_form.addRow("Delete atom lines", self.delete_lines)
        layout.addLayout(deletion_form)
        self.review_button = QPushButton("Review repair plan")
        self.review_button.clicked.connect(self.review_plan)
        layout.addWidget(self.review_button)
        self.plan_report = self._text("Inspect a structure before reviewing repair choices. Overlap findings never trigger automatic atom deletion.")
        layout.addWidget(self.plan_report, 1)
        self._editors.extend((self.model, self.chains, self.resolve_altlocs, self.remove_duplicates,
                              self.remove_heterogens, self.missing_heavy, self.modres, self.gaps, self.delete_lines))

    def _build_candidate(self):
        layout = self._tab("3 Candidate")
        note = QLabel("Generate a new file from the reviewed plan. Modeled coordinates are estimates, not experimental observations. Inspect the candidate's QC and change report before adoption.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.generate_button = QPushButton("Generate candidate from reviewed plan")
        self.generate_button.setObjectName("PrimaryAction")
        self.generate_button.clicked.connect(self.generate_candidate)
        layout.addWidget(self.generate_button)
        self.candidate_report = self._text("No candidate generated.")
        layout.addWidget(self.candidate_report, 1)
        self.open_report_button = QPushButton("Open full repair report")
        self.open_report_button.clicked.connect(self.open_full_report)
        layout.addWidget(self.open_report_button)

    def _build_adoption(self):
        layout = self._tab("4 Adopt")
        self.adoption_summary = QLabel("A verified candidate is required. Adoption changes the Protein input in MD Setup; it does not run topology generation or MD.")
        self.adoption_summary.setWordWrap(True)
        self.adoption_summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.adoption_summary)
        self.adopt_button = QPushButton("Use repaired structure")
        self.adopt_button.setObjectName("PrimaryAction")
        self.adopt_button.clicked.connect(self.adopt_candidate)
        layout.addWidget(self.adopt_button)
        layout.addStretch(1)

    @staticmethod
    def _text(initial: str):
        widget = QTextEdit()
        widget.setReadOnly(True)
        widget.setPlainText(initial)
        widget.setMinimumHeight(130)
        return widget

    @staticmethod
    def _checklist_group(layout, title):
        group = QGroupBox(title)
        inner = QVBoxLayout(group)
        widget = QListWidget()
        widget.setMinimumHeight(115)
        inner.addWidget(widget)
        layout.addWidget(group)
        return widget

    def _wire_changes(self):
        self.source.line_edit.textChanged.connect(self._source_changed)
        self.fasta.line_edit.textChanged.connect(self._fasta_changed)
        self.output.line_edit.textChanged.connect(self._invalidate_plan)
        self.fasta_chain.currentIndexChanged.connect(self._invalidate_audit)
        self.fasta_record.currentIndexChanged.connect(self._invalidate_audit)
        self.model.currentIndexChanged.connect(self._invalidate_plan)
        for field in (self.chains, self.modres, self.gaps):
            field.itemChanged.connect(self._invalidate_plan)
        for field in (self.resolve_altlocs, self.remove_duplicates, self.remove_heterogens, self.missing_heavy):
            field.toggled.connect(self._invalidate_plan)
        self.delete_lines.textChanged.connect(self._invalidate_plan)

    def _source_changed(self):
        self._syncing = True
        self.fasta_chain.clear()
        self.fasta_chain.addItem("Select chain after inspection", None)
        self.delete_lines.clear()
        self.remove_heterogens.setChecked(False)
        self.missing_heavy.setChecked(False)
        self.resolve_altlocs.setChecked(True)
        self.remove_duplicates.setChecked(True)
        self._syncing = False
        self._invalidate_audit()

    def _fasta_changed(self):
        self._syncing = True
        self.fasta_record.clear()
        self.fasta_record.addItem("Select FASTA record after inspection", None)
        self._syncing = False
        self._invalidate_audit()

    def _invalidate_audit(self):
        if self._syncing:
            return
        self._audit = None
        self._invalidate_plan()
        self.status.setText("Inputs or FASTA mapping changed. Inspect again before reviewing a plan.")

    def _invalidate_plan(self):
        if self._syncing:
            return
        self._revision += 1
        self._plan = None
        self._candidate = None
        self.candidate_report.setPlainText("Candidate invalidated by changed inputs or repair choices.")
        self.adoption_summary.setText("Review the plan and generate a new candidate before adoption.")
        self._refresh_actions()

    def _refresh_actions(self):
        idle = not self.is_busy()
        self.inspect_button.setEnabled(idle)
        self.review_button.setEnabled(idle and self._audit is not None and not self._mapping_pending)
        self.generate_button.setEnabled(idle and self._plan is not None and self._plan.can_apply)
        self.adopt_button.setEnabled(idle and self._candidate is not None and self._candidate.ready)
        self.open_report_button.setEnabled(idle and self._candidate is not None)
        for widget in (*self._editors, *self.altloc_choices.values()):
            widget.setEnabled(idle)
        for widget in self.altloc_choices.values():
            widget.setEnabled(idle and self.resolve_altlocs.isChecked())

    def is_busy(self) -> bool:
        return self._active or bool(self._worker and self._worker.isRunning())

    def _start(self, description: str, operation: Callable, completed: Callable):
        if self.is_busy() or self.operation_blocked():
            self.status.setText("Wait for the current project operation to finish.")
            return
        self._active = True
        self.status.setText(description)
        worker = StructureTask(operation, self)
        self._worker = worker
        worker.finished.connect(partial(self._finished, worker, self._revision, completed))
        self._refresh_actions()
        try:
            worker.start()
        except Exception as exc:
            self._active = False
            self._worker = None
            self.status.setText(f"Could not start structure task: {exc}")
            self._refresh_actions()

    def _finished(self, worker, revision, completed):
        self._active = False
        if revision != self._revision:
            self.status.setText("Inputs changed while processing. Results discarded; inspect again.")
        elif worker.error:
            self._candidate = None
            self.status.setText(f"Structure task failed: {worker.error}")
        else:
            try:
                completed(worker.result)
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                self._candidate = None
                self.status.setText(f"Could not display structure results: {exc}")
        self._refresh_actions()

    def inspect_structure(self):
        if self.is_busy() or self.operation_blocked():
            self.status.setText("Wait for the current project operation to finish.")
            return
        self._audit = self._plan = self._candidate = None
        source, fasta = self.source.text(), self.fasta.text()
        chain, record = self.fasta_chain.currentData(), self.fasta_record.currentData()
        self._start("Inspecting the original structure…", partial(self._inspect, source, fasta, chain, record), self._show_audit)

    @staticmethod
    def _inspect(source, fasta, chain, record):
        from core.sequence_mapping import parse_fasta
        records = parse_fasta(fasta) if fasta else ()
        mapped = bool(fasta and chain is not None and record is not None)
        audit = audit_structure(source, fasta_path=fasta if mapped else None,
                                chain_id=chain if mapped else None, fasta_record_id=record if mapped else None)
        return audit, records, bool(fasta and not mapped), StructureRepairDialog._describe_audit(audit)

    def choices(self):
        tokens = re.split(r"[\s,]+", self.delete_lines.text().strip()) if self.delete_lines.text().strip() else ()
        if any(not token.isdigit() or int(token) < 1 for token in tokens):
            raise ValueError("Atom deletions require positive line IDs separated by commas or spaces; ranges are not accepted.")
        chains = self._checked(self.chains)
        if self.chains.count() and not chains:
            raise ValueError("Select at least one chain to retain in the candidate.")
        altlocs = tuple((key, combo.currentData()) for key, combo in self.altloc_choices.items() if combo.currentData())
        return RepairChoices(
            model_id=self.model.currentData(), chain_ids=chains,
            remove_heterogens=self.remove_heterogens.isChecked(), normalize_modres=self._checked(self.modres),
            delete_atom_lines=tuple(sorted({int(token) for token in tokens})),
            selected_altlocs=altlocs if self.resolve_altlocs.isChecked() else (),
            remove_exact_duplicates=self.remove_duplicates.isChecked(), resolve_altlocs=self.resolve_altlocs.isChecked(),
            fill_gap_ids=self._checked(self.gaps), add_missing_heavy_atoms=self.missing_heavy.isChecked(),
        )

    @staticmethod
    def _checked(widget):
        return tuple(widget.item(i).data(Qt.UserRole) for i in range(widget.count()) if widget.item(i).checkState() == Qt.Checked)

    @staticmethod
    def _add_choice(widget, identifier, label, checked=False):
        item = QListWidgetItem(label, widget)
        item.setData(Qt.UserRole, identifier)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        item.setToolTip(label)

    def _show_audit(self, result):
        audit, records, mapping_pending, summary = result
        self._syncing = True
        try:
            self._audit = audit
            self._mapping_pending = mapping_pending
            self._plan = self._candidate = None
            self._populate_choices(audit, records)
            self.audit_report.setPlainText(summary)
        finally:
            self._syncing = False
        self.status.setText("Select a FASTA record and its chain, then inspect again." if mapping_pending else "Inspection complete. Review repair choices in 2 Plan; no files have been changed.")
        if not mapping_pending:
            self.tabs.setCurrentIndex(1)

    def _populate_choices(self, audit, records):
        previous_chain, previous_record = self.fasta_chain.currentData(), self.fasta_record.currentData()
        self.fasta_chain.clear()
        self.fasta_chain.addItem("Select a chain for this FASTA record", None)
        self.chains.clear()
        for chain in audit.chain_ids:
            self.fasta_chain.addItem(chain or "(blank chain)", chain)
        for chain in dict.fromkeys(atom.identity.chain_id for atom in audit.atoms):
            self._add_choice(self.chains, chain, chain or "(blank chain)", True)
        self.fasta_record.clear()
        self.fasta_record.addItem("Select a FASTA record", None)
        for record in records:
            self.fasta_record.addItem(f"{record.record_id} ({len(record.sequence)} residues)", record.record_id)
        self.fasta_chain.setCurrentIndex(max(0, self.fasta_chain.findData(previous_chain)))
        self.fasta_record.setCurrentIndex(max(0, self.fasta_record.findData(previous_record)))
        self.model.clear()
        self.model.addItem("Select a model if the source contains more than one", None)
        for model in audit.model_ids:
            self.model.addItem(str(model), model)
        self.modres.clear()
        for item in audit.modified_residues:
            extra = f"; removes {', '.join(item.removable_atom_names)}" if item.removable_atom_names else ""
            self._add_choice(self.modres, item.residue_id, f"{self._residue_label(item.residue_id)} → {item.parent_residue_name}{extra}")
        self.gaps.clear()
        for gap in audit.sequence_gaps:
            self._add_choice(self.gaps, gap.gap_id, gap.label)
        while self.altloc_form.rowCount():
            self.altloc_form.removeRow(0)
        self.altloc_choices = {}
        for group in audit.altloc_groups:
            combo = QComboBox()
            combo.addItem(f"Recommended: {group.recommended}", "")
            for label in group.labels:
                combo.addItem(label, label)
            combo.currentIndexChanged.connect(self._invalidate_plan)
            self.altloc_choices[group.residue_id] = combo
            self.altloc_form.addRow(self._residue_label(group.residue_id), combo)

    @staticmethod
    def _describe_audit(audit):
        label = StructureRepairDialog._residue_label
        sections = [
            ("Alternate conformers", [f"{label(item.residue_id)}: labels {', '.join(item.labels)}; recommended {item.recommended}" for item in audit.altloc_groups]),
            ("Exact duplicate atom lines", [str(line) for line in audit.exact_duplicate_lines]),
            ("Close overlaps (review atom line IDs; no automatic deletion)", [f"Lines {item.first_line} / {item.second_line}: {item.distance_angstrom:.3f} Å" for item in audit.collisions]),
            ("MODRES (preserved unless conversion is selected)", [f"{label(item.residue_id)} → parent {item.parent_residue_name}; extra atoms: {', '.join(item.removable_atom_names) or 'none'}" for item in audit.modified_residues]),
            ("Missing heavy atoms", [f"{label(item.residue_id)}: {', '.join(item.atom_names)}" for item in audit.missing_heavy_atoms]),
            ("Sequence gaps (not inferred from residue numbering)", [item.label for item in audit.sequence_gaps]),
            ("Warnings", list(audit.warnings)), ("Blocked findings", list(audit.blocked_reasons)),
        ]
        lines = ["Original structure audit — no changes applied", str(audit.source_path),
                 f"SHA-256: {audit.source_sha256}", f"Atom records: {len(audit.atoms)} | Residues: {len(audit.residues)}",
                 f"Models: {', '.join(map(str, audit.model_ids))}",
                 f"Polymer chains: {', '.join(chain or '(blank)' for chain in audit.chain_ids)}"]
        for title, findings in sections:
            lines.extend(["", f"{title} ({len(findings)})", *(findings or ["None found."])])
        for mapping in audit.sequence_mappings:
            lines.extend(["", f"Sequence mapping: {mapping.source} / {mapping.reference_id} → chain {mapping.chain_id or '(blank)'}",
                          f"Ambiguous: {'yes' if mapping.ambiguous else 'no'}", *mapping.mismatches, *mapping.blocked_reasons])
        return "\n".join(lines)

    @staticmethod
    def _residue_label(identifier):
        model, chain, number, insertion, name = json.loads(identifier)
        return f"{name} {chain or '(blank)'}:{number}{insertion} (model {model})"

    def review_plan(self):
        if self._mapping_pending:
            self.status.setText("Select the FASTA record and its chain, then inspect again; or clear the optional FASTA input.")
            return
        if self.is_busy() or self.operation_blocked() or self._audit is None:
            self.status.setText("Inspect the structure after active tasks have finished.")
            return
        try:
            self._plan = build_repair_plan(self._audit, self.choices())
        except (OSError, ValueError) as exc:
            self._plan = None
            self.status.setText(f"Repair plan needs attention: {exc}")
        else:
            lines = ["Reviewed changes:", *(f"• {change}" for change in self._plan.changes)]
            if self._plan.blocked_reasons:
                lines.extend(["", "Blocked:", *self._plan.blocked_reasons])
            self.plan_report.setPlainText("\n".join(lines))
            self.status.setText("Plan reviewed. Generate a separate candidate in 3 Candidate." if self._plan.can_apply else "Plan blocked. Review the listed issues and inspect again if inputs change.")
        self._refresh_actions()

    def generate_candidate(self):
        if self._plan is None or not self._plan.can_apply:
            self.status.setText("Review an applicable repair plan first.")
            return
        plan, output = self._plan, self.output.text()
        if not output:
            self.status.setText("Select an output folder for the new candidate.")
            return
        self._start("Generating and checking a separate candidate…", partial(self._generate, plan, output), self._show_candidate)

    @staticmethod
    def _generate(plan, output):
        result = apply_repair_plan(plan, output)
        if result.ready:
            verify_repair_result(result)
        report = json.loads(Path(result.report_path).read_text(encoding="utf-8"))
        return result, StructureRepairDialog._describe_candidate(report)

    @staticmethod
    def _describe_candidate(report):
        mapping = report.get("atom_mapping", ())
        observed = sum(item.get("origin") == "observed" for item in mapping)
        modeled = tuple(item for item in mapping if item.get("origin") == "modeled")
        lines = [f"Observed atoms retained: {observed}", f"Modeled atoms added: {len(modeled)}",
                 f"Covalent links retained: {len(report.get('remaining_covalent_links', ()))}",
                 f"Nonstandard components retained: {len(report.get('special_residues', ()))}", "", "Reviewed changes:",
                 *report.get("changes", ()), "", "QC / blocking findings:",
                 *(report.get("blocked_reasons") or ["No coordinate repair blockers found."])]
        if modeled:
            lines.extend(["", "Modeled atom identities (first 50; full list in report):"])
            for item in modeled[:50]:
                atom = item.get("output_identity", {})
                lines.append(f"{atom.get('chain_id', '')}:{atom.get('residue_number', '')}{atom.get('insertion_code', '')} {atom.get('residue_name', '')} {atom.get('atom_name', '')}")
        backend = report.get("backend") or {}
        if isinstance(backend, dict):
            lines.extend(["", "Backend:", json.dumps(backend.get("backend_versions", {}), sort_keys=True),
                          *backend.get("warnings", ())])
        lines.extend(["", *report.get("scientific_limitations", ())])
        return "\n".join(lines)

    def _show_candidate(self, payload):
        result, report = payload
        self._candidate = result
        summary = [f"Structural readiness: {'ready for review' if result.ready else 'blocked'}",
                   f"Candidate: {result.candidate_path}", f"Report: {result.report_path}",
                   f"Candidate SHA-256: {result.candidate_sha256}", *result.blocked_reasons]
        self.candidate_report.setPlainText("\n".join(summary) + "\n\nObserved / modeled changes and QC:\n" + report)
        parameters = ("Nonstandard components retained: validate suitable parameters before MD."
                      if result.md_requires_parameters else "Force-field and topology validation is still required during MD preparation.")
        self.adoption_summary.setText("\n".join(summary) + "\n\n" + parameters + " Structural readiness does not establish parameter readiness.")
        self.status.setText("Candidate generated. Inspect the change report, then explicitly adopt in 4 Adopt." if result.ready else "Candidate generated but blocked. Review its QC report; adoption remains disabled.")

    def open_full_report(self):
        if self._candidate is None:
            return
        path = Path(self._candidate.report_path)
        if not path.is_file() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve()))):
            self.status.setText(f"Could not open the report. Inspect this file manually: {path}")

    def adopt_candidate(self):
        candidate = self._candidate
        if candidate is None or not candidate.ready:
            self.status.setText("Generate a structurally ready candidate before adoption.")
            return
        def verify():
            verify_repair_result(candidate)
            return candidate
        self._start("Rechecking candidate and report hashes before adoption…", verify, self._finish_adoption)

    def _finish_adoption(self, candidate):
        self.repair_adopted.emit(
            str(candidate.candidate_path), str(candidate.report_path), candidate.report_sha256,
        )
        self.status.setText("Repaired structure adopted in MD Setup. The original remains unchanged.")
        self.accept()

    def reset_session(self) -> bool:
        if self.is_busy():
            return False
        self._invalidate_audit()
        self.close()
        return True

    def reject(self):
        if self.is_busy():
            self.status.setText("Wait for the structure task to finish before closing this dialog.")
            return
        super().reject()

    def closeEvent(self, event):
        if self.is_busy():
            self.status.setText("Wait for the structure task to finish before closing this dialog.")
            event.ignore()
        else:
            event.accept()
