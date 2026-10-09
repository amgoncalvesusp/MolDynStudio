"""MD setup tab for system preparation and MDP generation."""

from __future__ import annotations

from functools import partial
from http.client import HTTPException
from pathlib import Path
from typing import Callable

from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QSpinBox,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtCore import QEvent, QThread, Qt, pyqtSignal

from core.forcefield_manager import (
    CHARMM36_ARCHIVE_NAME,
    cached_force_field_dir,
    download_charmm36_force_field,
    install_charmm36_archive,
    is_valid_force_field,
)
from core.preparation_orchestrator import PreparationRequest, PreparationWorker
from core.run_manifest import manifest_path
from core.settings import SettingsStore
from core.system_prep import SystemPrepParams
from tabs.base import MolDynBasePage, PathSelector
from utils.file_validators import validate_md_inputs
from utils.mdp_generator import MDParameters, generate_all_mdp
from utils.tooltips import tooltip
from windows.mdp_editor import MDPEditor
from windows.ligand_setup_controls import build_charmm_controls, charmm_request_options, reset_ligand_consent
from windows.structure_repair_dialog import StructureRepairDialog


class ForceFieldImportWorker(QThread):
    """Validate and install a force-field archive without blocking the UI."""

    done = pyqtSignal(bool, str)

    def __init__(self, archive: str | None, parent=None):
        super().__init__(parent)
        self.archive = archive

    def run(self) -> None:
        try:
            installed = (
                download_charmm36_force_field()
                if self.archive is None else install_charmm36_archive(self.archive)
            )
        except (OSError, ValueError, HTTPException) as exc:
            self.done.emit(False, str(exc))
            return
        self.done.emit(True, str(installed))


class MDSetupTab(MolDynBasePage):
    project_prepared = pyqtSignal(str, str)

    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self.settings = settings or SettingsStore()
        self._preparation_active = False
        self._structure_repair_dialog = None
        self.operation_blocked: Callable[[], bool] = lambda: False
        outer = QVBoxLayout(self)
        title = QLabel("MD Setup")
        title.setObjectName("PageTitle")
        outer.addWidget(title)
        splitter = QSplitter(Qt.Horizontal)
        outer.addWidget(splitter, 1)

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setMinimumWidth(420)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_scroll.setWidget(left)
        splitter.addWidget(left_scroll)

        right = QWidget()
        right.setMinimumWidth(240)
        right_layout = QVBoxLayout(right)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        splitter.setChildrenCollapsible(False)
        splitter.setSizes([650, 330])

        self._build_system_inputs(left_layout)
        self._build_parameterization(left_layout)
        self._build_steps(left_layout)
        self._build_production(left_layout)
        left_layout.addStretch(1)
        self._refresh_charmm_status()

        file_group = QGroupBox("Project File Tree")
        file_layout = QVBoxLayout(file_group)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        file_layout.addWidget(self.tree)
        right_layout.addWidget(file_group, 1)

        preview_group = QGroupBox("MDP Preview")
        preview_layout = QVBoxLayout(preview_group)
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMinimumHeight(260)
        preview_layout.addWidget(self.preview)
        right_layout.addWidget(preview_group, 2)

        for widget in self.fields.values():
            if hasattr(widget, "valueChanged"):
                widget.valueChanged.connect(self.refresh_preview)  # type: ignore[attr-defined]
            if hasattr(widget, "currentTextChanged"):
                widget.currentTextChanged.connect(self.refresh_preview)  # type: ignore[attr-defined]
        self.refresh_preview()

    def _build_system_inputs(self, parent: QVBoxLayout) -> None:
        group = QGroupBox("System Input")
        form = QFormLayout(group)
        self.fields["project_dir"] = PathSelector("Project directory", directory=True)
        form.addRow("Project folder", self.fields["project_dir"])

        self.fields["protein"] = PathSelector("Protein PDB/GRO", "Structures (*.pdb *.gro);;All Files (*)")
        form.addRow("Protein (PDB/GRO)", self.fields["protein"])
        self.inspect_structure_button = QPushButton("Inspect / repair structure")
        self.inspect_structure_button.clicked.connect(self.open_structure_repair)
        form.addRow("Structure quality", self.inspect_structure_button)
        self.fields["structure_repair_report"] = QLineEdit()
        self.fields["structure_repair_report"].setReadOnly(True)
        self.fields["structure_repair_report"].setPlaceholderText("No repaired candidate adopted")
        self.fields["structure_repair_report"].setAccessibleName("Verified structure repair report")
        form.addRow("Repair provenance", self.fields["structure_repair_report"])
        self.fields["structure_repair_report_sha256"] = QLineEdit(self)
        self.fields["structure_repair_report_sha256"].setReadOnly(True)
        self.fields["structure_repair_report_sha256"].hide()
        self.fields["structure_repair_report"].textChanged.connect(
            self.fields["structure_repair_report_sha256"].clear
        )
        self.fields["protein"].line_edit.textChanged.connect(
            self._clear_structure_repair_provenance
        )

        self.fields["ligand"] = PathSelector("Ligand MOL2/SDF", "Ligands (*.mol2 *.sdf *.pdb);;All Files (*)")
        form.addRow("Ligand (MOL2/SDF)", self.fields["ligand"])

        self.fields["force_field"] = QComboBox()
        self.fields["force_field"].addItems(["AMBER99SB-ILDN", "CHARMM36m", "OPLS-AA", "AMBER14SB"])
        self.fields["force_field"].setToolTip(tooltip("force_field"))
        form.addRow("Force Field", self.fields["force_field"])

        charmm_package = QWidget()
        charmm_layout = QVBoxLayout(charmm_package)
        charmm_layout.setContentsMargins(0, 0, 0, 0)
        self.charmm_status = QLabel()
        self.charmm_status.setWordWrap(True)
        charmm_layout.addWidget(self.charmm_status, 1)
        self.charmm_download_button = QPushButton("Download CHARMM36m")
        self.charmm_download_button.setToolTip(
            "Download the official MacKerell package once over HTTPS; "
            "verify SHA-256 and install for offline use. No simulation files are uploaded."
        )
        self.charmm_download_button.clicked.connect(
            lambda: self._start_charmm_install(None)
        )
        charmm_layout.addWidget(self.charmm_download_button)
        self.charmm_import_button = QPushButton("Import package…")
        self.charmm_import_button.clicked.connect(self._import_charmm_package)
        charmm_layout.addWidget(self.charmm_import_button)
        form.addRow("CHARMM36m package", charmm_package)

        self.fields["water_model"] = QComboBox()
        self.fields["water_model"].addItems(["TIP3P", "SPC/E", "TIP4P-Ew"])
        self.fields["water_model"].setToolTip(tooltip("water_model"))
        form.addRow("Water Model", self.fields["water_model"])

        self.fields["box_type"] = QComboBox()
        self.fields["box_type"].addItems(["Dodecahedron", "Cubic", "Triclinic", "Octahedron"])
        form.addRow("Box Type", self.fields["box_type"])

        self.fields["box_padding"] = QDoubleSpinBox()
        self.fields["box_padding"].setRange(0.1, 10.0)
        self.fields["box_padding"].setSingleStep(0.1)
        self.fields["box_padding"].setValue(1.2)
        self.fields["box_padding"].setToolTip(tooltip("box_padding"))
        form.addRow("Box padding (nm)", self.fields["box_padding"])

        self.fields["ion_concentration"] = QDoubleSpinBox()
        self.fields["ion_concentration"].setRange(0.0, 5.0)
        self.fields["ion_concentration"].setDecimals(3)
        self.fields["ion_concentration"].setValue(0.150)
        self.fields["ion_concentration"].setToolTip(tooltip("ion_concentration"))
        form.addRow("Ion concentration (M)", self.fields["ion_concentration"])
        parent.addWidget(group)

    def _build_parameterization(self, parent: QVBoxLayout) -> None:
        group = QGroupBox("Ligand Parameterization")
        layout = QVBoxLayout(group)
        row = QHBoxLayout()
        self.acpype = QLabel("ACPYPE / GAFF2 (AMBER-family)")
        self.acpype.setAccessibleName("Selected ligand parameterization method")
        self.acpype.setToolTip(tooltip("parameterization"))
        row.addWidget(self.acpype)
        row.addStretch(1)
        layout.addLayout(row)
        self.ligand_compatibility = QLabel()
        self.ligand_compatibility.setWordWrap(True)
        self.ligand_compatibility.setAccessibleName(
            "Ligand force-field compatibility"
        )
        layout.addWidget(self.ligand_compatibility)

        form = QFormLayout()
        self.fields["charge_method"] = QComboBox()
        self.fields["charge_method"].addItems(["AM1-BCC", "Gasteiger"])
        self.charge_method_label = QLabel("Charge method")
        form.addRow(self.charge_method_label, self.fields["charge_method"])
        self.fields["charge"] = QDoubleSpinBox()
        self.fields["charge"].setRange(-20, 20)
        self.fields["charge"].setDecimals(0)
        form.addRow("Expected net charge", self.fields["charge"])
        layout.addLayout(form)
        self.charmm_ligand_controls = build_charmm_controls(self.fields)
        layout.addWidget(self.charmm_ligand_controls)
        for source in (self.fields["protein"].line_edit.textChanged,
                       self.fields["ligand"].line_edit.textChanged,
                       self.fields["force_field"].currentTextChanged,
                       self.fields["charge"].valueChanged,
                       self.fields["source_residue_name"].textChanged,
                       self.fields["excluded_residue_names"].textChanged,
                       self.fields["ligand_from_complex"].toggled):
            source.connect(lambda *_: reset_ligand_consent(self.fields))
        self.fields["ligand_from_complex"].toggled.connect(self._refresh_ligand_compatibility)
        self.fields["ligand_protonation_confirmed"].toggled.connect(self._refresh_ligand_compatibility)

        self.generate_topology_button = QPushButton("Prepare system")
        self.generate_topology_button.clicked.connect(
            self.generate_topology_preview
        )
        layout.addWidget(self.generate_topology_button)
        parent.addWidget(group)

        self.fields["force_field"].currentTextChanged.connect(
            self._refresh_ligand_compatibility
        )
        self.fields["ligand"].line_edit.textChanged.connect(
            self._refresh_ligand_compatibility
        )
        self._refresh_ligand_compatibility()

    def _build_steps(self, parent: QVBoxLayout) -> None:
        group = QGroupBox("Required MD stages")
        layout = QHBoxLayout(group)
        for key, label in [
            ("run_minimization", "Minimization"),
            ("run_nvt", "NVT Equilibration"),
            ("run_npt", "NPT Equilibration"),
            ("run_production", "Production MD"),
        ]:
            cb = QCheckBox(label)
            cb.setChecked(True)
            cb.setEnabled(False)
            cb.setToolTip(
                "Run All follows minimization, NVT, NPT, then production. "
                "Use validated restart points in MD Run to resume."
            )
            self.fields[key] = cb
            layout.addWidget(cb)
        layout.addStretch(1)
        parent.addWidget(group)

    def set_state(self, state: dict) -> None:
        if self.is_busy():
            self.request_log.emit("MD Setup inputs cannot change while a project task is active.")
            return
        if self._structure_repair_dialog is not None:
            self._structure_repair_dialog.reset_session()
            self._structure_repair_dialog.deleteLater()
            self._structure_repair_dialog = None
        super().set_state(state)
        reset_ligand_consent(self.fields)
        # Restore provenance after path signals have invalidated prior approval;
        # dictionary order must not decide whether an approved digest survives.
        report_path = str(state.get("structure_repair_report", ""))
        self.fields["structure_repair_report"].setText(report_path)
        self.fields["structure_repair_report"].setToolTip(report_path)
        self.fields["structure_repair_report_sha256"].setText(
            str(state.get("structure_repair_report_sha256", "")) if report_path else ""
        )
        charge = state.get("charge_method")
        if charge is not None and self.fields["charge_method"].findText(str(charge)) < 0:
            self.fields["charge_method"].setCurrentIndex(-1)
            self.request_log.emit(
                f"Saved charge method {charge!r} is unsupported. Select AM1-BCC or Gasteiger before preparation."
            )
        for key in ("run_minimization", "run_nvt", "run_npt", "run_production"):
            self.fields[key].setChecked(True)
            self.fields[key].setEnabled(False)
        self._refresh_ligand_compatibility()

    def is_busy(self) -> bool:
        dialog_busy = self._structure_repair_dialog is not None and self._structure_repair_dialog.is_busy()
        return dialog_busy or self._preparation_active or any(
            worker is not None and worker.isRunning()
            for worker in (
                getattr(self, "_prep_worker", None),
                getattr(self, "_force_field_import_worker", None),
            )
        )

    def open_structure_repair(self) -> None:
        if self.is_busy() or self.operation_blocked():
            self.request_log.emit("Wait for the current operation before inspecting a structure.")
            return
        if self._structure_repair_dialog is not None:
            self._structure_repair_dialog.close()
            self._structure_repair_dialog.deleteLater()
        source = self.fields["protein"].text()
        project = self.fields["project_dir"].text()
        output = str(Path(project) / "structure_repairs") if project else ""
        dialog = StructureRepairDialog(source, output, self)
        dialog.operation_blocked = self._repair_operation_blocked
        dialog.repair_adopted.connect(self._adopt_structure_repair)
        self._structure_repair_dialog = dialog
        dialog.show()

    def _repair_operation_blocked(self) -> bool:
        return self.operation_blocked() or self._preparation_active or any(
            worker is not None and worker.isRunning()
            for worker in (getattr(self, "_prep_worker", None), getattr(self, "_force_field_import_worker", None))
        )

    def _clear_structure_repair_provenance(self) -> None:
        self.fields["structure_repair_report"].clear()
        self.fields["structure_repair_report"].setToolTip("")
        self.fields["structure_repair_report_sha256"].clear()

    def _adopt_structure_repair(
        self, candidate_path: str, report_path: str, approved_report_sha256: str,
    ) -> None:
        self.fields["protein"].setText(candidate_path)
        self.fields["structure_repair_report"].setText(report_path)
        self.fields["structure_repair_report"].setToolTip(report_path)
        self.fields["structure_repair_report_sha256"].setText(approved_report_sha256)
        self.refresh_preview()
        self.request_log.emit(
            f"Verified structural repair adopted: {candidate_path}. "
            "Structural readiness does not establish force-field or covalent topology readiness."
        )

    def _build_production(self, parent: QVBoxLayout) -> None:
        group = QGroupBox("Production MD Parameters")
        form = QFormLayout(group)
        self.fields["duration"] = QDoubleSpinBox()
        self.fields["duration"].setDecimals(3)
        self.fields["duration"].setRange(0.001, 1_000_000)
        self.fields["duration"].setValue(100.0)
        self.fields["duration"].setToolTip(tooltip("duration"))
        form.addRow("Duration (ns)", self.fields["duration"])

        self.fields["timestep"] = QDoubleSpinBox()
        self.fields["timestep"].setDecimals(3)
        self.fields["timestep"].setRange(0.001, 100.0)
        self.fields["timestep"].setValue(2.0)
        self.fields["timestep"].setToolTip(tooltip("timestep"))
        form.addRow("Timestep (fs)", self.fields["timestep"])

        self.fields["temperature"] = QDoubleSpinBox()
        self.fields["temperature"].setRange(1.0, 1000.0)
        self.fields["temperature"].setValue(300.0)
        form.addRow("Temperature (K)", self.fields["temperature"])

        self.fields["pressure"] = QDoubleSpinBox()
        self.fields["pressure"].setDecimals(3)
        self.fields["pressure"].setRange(0.001, 1000.0)
        self.fields["pressure"].setValue(1.0)
        form.addRow("Pressure (bar)", self.fields["pressure"])

        self.fields["save_every"] = QDoubleSpinBox()
        self.fields["save_every"].setDecimals(3)
        self.fields["save_every"].setRange(0.001, 1_000_000.0)
        self.fields["save_every"].setValue(10.0)
        self.fields["save_every"].setToolTip(tooltip("save_every"))
        form.addRow("Save every (ps)", self.fields["save_every"])

        for key, label in (("nvt_steps", "NVT steps"), ("npt_steps", "NPT steps")):
            self.fields[key] = QSpinBox()
            self.fields[key].setRange(1, 1_000_000_000)
            self.fields[key].setValue(50000)
            form.addRow(label, self.fields[key])
        self.short_test_button = QPushButton("Short pipeline test: 2 ps per MD stage")
        self.short_test_button.setToolTip(
            "Set 1 fs timestep, 2 ps each for NVT/NPT/production, and 0.2 ps production output. "
            "A smoke test checks execution; it does not establish equilibration."
        )
        self.short_test_button.clicked.connect(self._use_short_pipeline_test)
        form.addRow(self.short_test_button)
        row = QHBoxLayout()
        save = QPushButton("Save .MDP Files")
        save.clicked.connect(self.save_mdp_files)
        row.addWidget(save)
        edit = QPushButton("Preview MDP")
        edit.clicked.connect(self.open_mdp_editor)
        row.addWidget(edit)
        row.addStretch(1)
        form.addRow(row)
        parent.addWidget(group)

    def _use_short_pipeline_test(self) -> None:
        if self.is_busy() or self.operation_blocked():
            self.request_log.emit("Wait for the active project task before changing the protocol.")
            return
        for key, value in (("timestep", 1.0), ("duration", 0.002), ("save_every", 0.2),
                           ("nvt_steps", 2000), ("npt_steps", 2000)):
            self.fields[key].setValue(value)
        self.request_log.emit("Short execution test selected: 2 ps NVT, 2 ps NPT, 2 ps production. Not an equilibration protocol.")

    def md_parameters(self) -> MDParameters:
        return MDParameters(
            duration_ns=self.fields["duration"].value(),
            nvt_steps=self.fields["nvt_steps"].value(),
            npt_steps=self.fields["npt_steps"].value(),
            timestep_fs=self.fields["timestep"].value(),
            temperature_k=self.fields["temperature"].value(),
            pressure_bar=self.fields["pressure"].value(),
            save_every_ps=self.fields["save_every"].value(),
            force_field=self.fields["force_field"].currentText(),
            water_model=self.fields["water_model"].currentText(),
            box_type=self.fields["box_type"].currentText(),
            box_padding_nm=self.fields["box_padding"].value(),
            ion_concentration_m=self.fields["ion_concentration"].value(),
        )

    def refresh_preview(self) -> None:
        try:
            files = generate_all_mdp(self.md_parameters())
        except ValueError as exc:
            self.preview.setPlainText(f"Input error: {exc}")
            self.refresh_tree({})
            return
        self.preview.setPlainText(files["md.mdp"])
        self.refresh_tree(files)

    def refresh_tree(self, files: dict[str, str]) -> None:
        self.tree.clear()
        root_label = Path(self.fields["project_dir"].text()).name or "my_project"
        root = QTreeWidgetItem([root_label + "/"])
        self.tree.addTopLevelItem(root)
        for key in ["protein", "ligand"]:
            value = self.fields[key].text()
            root.addChild(QTreeWidgetItem([Path(value).name if value else f"{key} not selected"]))
        for filename in files:
            root.addChild(QTreeWidgetItem([filename]))
        root.setExpanded(True)

    def generate_topology_preview(self) -> None:
        if self.is_busy() or self.operation_blocked():
            self.request_log.emit(
                "Preparation is unavailable while another project operation is running."
            )
            return
        protein_path = self.fields["protein"].text().strip()
        ligand_path = self.fields["ligand"].text().strip()
        work_dir = self.fields["project_dir"].text().strip()
        force_field = self.fields["force_field"].currentText()

        if not protein_path or not work_dir:
            QMessageBox.warning(
                self,
                "Missing inputs",
                "Select both a project folder and a protein PDB before generating topology.",
            )
            return

        report_path = self.fields["structure_repair_report"].text()
        approved_report_sha256 = self.fields["structure_repair_report_sha256"].text()
        if report_path and not approved_report_sha256:
            QMessageBox.warning(
                self,
                "Repair approval required",
                "This saved repair has no verified approval record. "
                "Use Inspect / repair structure to re-adopt a verified candidate before preparation.",
            )
            return

        ligand_options = {}
        if ligand_path:
            if force_field.upper().startswith("CHARMM36"):
                try:
                    ligand_options = charmm_request_options(self.fields)
                except ValueError as exc:
                    QMessageBox.warning(self, "Review CHARMM ligand inputs", str(exc))
                    return
            elif not force_field.upper().startswith("AMBER"):
                QMessageBox.warning(
                    self, "Incompatible ligand workflow",
                    "Ligand preparation supports AMBER with ACPYPE/GAFF2 or CHARMM36m with SwissParam MATCH.",
                )
                return

        try:
            parameters = self.md_parameters()
            generate_all_mdp(parameters)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid MD parameters", str(exc))
            return

        request = PreparationRequest(
            system=SystemPrepParams(
                pdb_path=protein_path,
                work_dir=work_dir,
                force_field=force_field,
                water_model=self.fields["water_model"].currentText(),
                box_type=self.fields["box_type"].currentText(),
                box_padding_nm=float(self.fields["box_padding"].value()),
                ion_concentration_m=float(self.fields["ion_concentration"].value()),
            ),
            ligand_path=ligand_path or None,
            charge_method=self.fields["charge_method"].currentText(),
            net_charge=int(self.fields["charge"].value()),
            md_parameters=parameters,
            gromacs_binary=str(self.settings.value("gromacs_binary", "auto")),
            conda_environment=str(
                self.settings.value("conda_environment", "moldynstudio")
            ),
            requested_cores=max(1, int(self.settings.value("cores", 4))),
            gpu_mode=str(self.settings.value("gpu_mode", "Auto")),
            structure_repair_report=report_path,
            structure_repair_report_sha256=approved_report_sha256,
            **ligand_options,
        )
        self._prep_worker = PreparationWorker(request, parent=self)
        project_dir = Path(request.system.work_dir).expanduser().resolve()
        self._prep_worker.log.connect(lambda line: self.request_log.emit(line))
        self._prep_worker.done.connect(
            partial(self._finish_preparation, project_dir)
        )
        self._prep_worker.finished.connect(self._release_preparation_controls)
        self._preparation_active = True
        self.generate_topology_button.setEnabled(False)
        self.generate_topology_button.setText("Preparing system…")
        for widget in self.fields.values():
            widget.setEnabled(False)
        self.window().installEventFilter(self)
        self.request_log.emit(
            f"Starting complete system preparation pipeline (force field: "
            f"{self.fields['force_field'].currentText()})."
        )
        try:
            self._prep_worker.start()
        except Exception as exc:
            self._prep_worker = None
            self._release_preparation_controls()
            self.request_log.emit(f"[FAIL] Could not start system preparation: {exc}")

    def _release_preparation_controls(self) -> None:
        self._preparation_active = False
        self.generate_topology_button.setEnabled(True)
        self.generate_topology_button.setText("Prepare system")
        for key, widget in self.fields.items():
            widget.setEnabled(not key.startswith("run_"))
        self._refresh_ligand_compatibility()

    def _finish_preparation(
        self,
        project_dir: Path,
        ok: bool,
        message: str,
    ) -> None:
        self.request_log.emit(("[OK] " if ok else "[FAIL] ") + message)
        if not ok:
            return
        self.project_prepared.emit(
            str(project_dir),
            str(manifest_path(project_dir).resolve()),
        )

    def _select_charmm_archive(self) -> str:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import official CHARMM36m package",
            "",
            (
                f"CHARMM36m package ({CHARMM36_ARCHIVE_NAME});;"
                "Compressed archives (*.tgz *.tar.gz);;All Files (*)"
            ),
        )
        return path

    def _refresh_charmm_status(self) -> None:
        cached = cached_force_field_dir()
        if is_valid_force_field(cached):
            self.charmm_status.setText(
                "Ready offline — CHARMM36m February 2026"
            )
            self.charmm_status.setToolTip(str(cached))
            self.charmm_status.setStyleSheet(
                "color: #176B37; font-weight: 600;"
            )
            self.charmm_import_button.setText("Verify package…")
        else:
            self.charmm_status.setText(
                "Action needed — download or import the official package once"
            )
            self.charmm_status.setToolTip(
                "After import, CHARMM36m simulations run without Internet."
            )
            self.charmm_status.setStyleSheet(
                "color: #805200; font-weight: 600;"
            )
            self.charmm_import_button.setText("Import package…")

    def _refresh_ligand_compatibility(self) -> None:
        ligand_selected = bool(self.fields["ligand"].text().strip())
        force_field = self.fields["force_field"].currentText()
        is_amber = force_field.upper().startswith("AMBER")
        is_charmm = force_field.upper().startswith("CHARMM")
        if is_charmm:
            self.fields["water_model"].setCurrentText("TIP3P")
            self.fields["water_model"].setEnabled(False)
            self.fields["water_model"].setToolTip(
                "CHARMM36m uses the compatible modified TIP3P water model "
                "from the locally installed force-field package."
            )
        else:
            self.fields["water_model"].setEnabled(True)
            self.fields["water_model"].setToolTip(tooltip("water_model"))

        controls_enabled = ligand_selected and is_amber
        self.acpype.setText("SwissParam MATCH (CHARMM-compatible)" if is_charmm else
                            "ACPYPE / GAFF2 (AMBER-family)" if is_amber else
                            "Automatic ligand parameters unavailable")
        self.fields["charge_method"].setVisible(not is_charmm)
        self.charge_method_label.setVisible(not is_charmm)
        self.fields["charge_method"].setEnabled(controls_enabled)
        self.fields["charge"].setEnabled(ligand_selected and (is_amber or is_charmm))
        self.charmm_ligand_controls.setVisible(is_charmm)
        from_complex = self.fields["ligand_from_complex"].isChecked()
        for key in ("source_residue_name", "excluded_residue_names"):
            self.fields[key].setEnabled(is_charmm and from_complex and not self._preparation_active)

        if ligand_selected and is_charmm:
            reviewed = self.fields["ligand_protonation_confirmed"].isChecked()
            text = (
                "Ready for MATCH parameter checks. Verified cached results can be reused offline."
                if reviewed else
                "SwissParam MATCH — review MOL2 hydrogens, protonation and expected charge before preparation."
            )
            color = "#176B37" if reviewed else "#875600"
        elif ligand_selected and not is_amber:
            text = (
                "Blocked — arbitrary ligand parameters cannot be generated "
                f"safely for {force_field}. Choose an AMBER force field for "
                "the built-in non-covalent ACPYPE/GAFF2 workflow."
            )
            color = "#A12622"
        elif ligand_selected:
            text = (
                "Ready — the selected ligand will use ACPYPE/GAFF2 with the "
                "AMBER-family protein force field."
            )
            color = "#176B37"
        else:
            text = (
                "Optional — select a noncovalent ligand. CHARMM36m uses SwissParam MATCH; "
                "AMBER uses ACPYPE/GAFF2."
            )
            color = "#555555"
        self.ligand_compatibility.setText(text)
        self.ligand_compatibility.setStyleSheet(f"color: {color};")

    def _import_charmm_package(self) -> None:
        archive = self._select_charmm_archive()
        if not archive:
            return
        self._start_charmm_install(archive)

    def _start_charmm_install(self, archive: str | None) -> None:
        if not self.charmm_import_button.isEnabled() or self.is_busy() or self.operation_blocked():
            return
        self.charmm_import_button.setEnabled(False)
        self.charmm_download_button.setEnabled(False)
        self.charmm_import_button.setText("Checking package…")
        self.charmm_status.setText(
            "Downloading official package, then checking checksum…"
            if archive is None else "Checking checksum and installing local files…"
        )
        self.charmm_status.setToolTip("")
        self.charmm_status.setStyleSheet(
            "color: #2C5F8A; font-weight: 600;"
        )
        self._force_field_import_worker = ForceFieldImportWorker(
            archive,
            parent=self,
        )
        self._force_field_import_worker.done.connect(
            self._finish_charmm_import
        )
        self.window().installEventFilter(self)
        self._force_field_import_worker.start()

    def eventFilter(self, watched, event):
        if (
            event.type() == QEvent.Close and watched is self.window()
            and self.is_busy()
        ):
            event.ignore()
            self.request_log.emit(
                "System preparation or force-field installation is still running. "
                "Close again after it finishes."
            )
            return True
        return super().eventFilter(watched, event)

    def _finish_charmm_import(self, ok: bool, detail: str) -> None:
        self.charmm_import_button.setEnabled(True)
        self.charmm_download_button.setEnabled(True)
        if not ok:
            self.charmm_status.setText(
                "Install failed — retry download or import the official package"
            )
            self.charmm_status.setToolTip(detail)
            self.charmm_status.setStyleSheet(
                "color: #A12622; font-weight: 600;"
            )
            self.charmm_import_button.setText("Try again…")
            QMessageBox.warning(
                self,
                "CHARMM36m import failed",
                detail,
            )
            return
        installed = Path(detail)
        self.charmm_status.setText(
            "Ready offline — CHARMM36m February 2026"
        )
        self.charmm_status.setToolTip(str(installed))
        self.charmm_status.setStyleSheet(
            "color: #176B37; font-weight: 600;"
        )
        self.charmm_import_button.setText("Verify package…")
        self.request_log.emit(f"[OK] Offline CHARMM36m installed at {installed}.")
        QMessageBox.information(
            self,
            "CHARMM36m ready",
            "CHARMM36m was validated and installed locally. Future "
            "simulations do not need Internet access.",
        )

    def save_mdp_files(self) -> None:
        if self.is_busy() or self.operation_blocked():
            self.request_log.emit("MDP files cannot be overwritten while a project operation is running.")
            return
        default_dir = self.fields["project_dir"].text()
        target = QFileDialog.getExistingDirectory(self, "Select project folder", default_dir or str(Path.home()))
        if not target:
            return
        try:
            files = generate_all_mdp(self.md_parameters())
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid MD parameters", str(exc))
            return
        for filename, content in files.items():
            Path(target, filename).write_text(content, encoding="utf-8")
        self.request_log.emit(f"Saved {len(files)} MDP files to {target}.")

    def open_mdp_editor(self) -> None:
        dialog = MDPEditor("Production MDP Preview", self.preview.toPlainText(), self)
        dialog.exec_()

    def preview_text(self) -> str:
        checks = validate_md_inputs({
            "protein": self.fields["protein"].text(),
            "ligand": self.fields["ligand"].text(),
        })
        validation = "\n".join(f"{name}: {result.message}" for name, result in checks.items() if name in {"protein", "ligand"})
        params = self.md_parameters()
        return (
            "[MD Setup Preview]\n"
            f"Force field: {params.force_field}\n"
            f"Water model: {params.water_model}\n"
            f"Box: {params.box_type}, padding {params.box_padding_nm:.2f} nm\n"
            f"Ions: {params.ion_concentration_m:.3f} M\n"
            f"Production: {params.duration_ns:.3f} ns, dt {params.timestep_fs:.3f} fs\n\n"
            f"{validation}"
        )
