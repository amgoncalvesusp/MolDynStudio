"""MD setup tab for system preparation and MDP generation."""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSplitter,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtCore import QThread, Qt, pyqtSignal

from core.forcefield_manager import (
    CHARMM36_ARCHIVE_NAME,
    cached_force_field_dir,
    install_charmm36_archive,
    is_valid_force_field,
)
from core.system_prep import SystemPrepParams, SystemPrepWorker
from tabs.base import MolDynBasePage, PathSelector
from utils.file_validators import validate_md_inputs
from utils.mdp_generator import MDParameters, generate_all_mdp
from utils.tooltips import tooltip
from utils.topology_builder import LigandParams, LigandParamWorker
from windows.mdp_editor import MDPEditor


class ForceFieldImportWorker(QThread):
    """Validate and install a force-field archive without blocking the UI."""

    done = pyqtSignal(bool, str)

    def __init__(self, archive: str, parent=None):
        super().__init__(parent)
        self.archive = archive

    def run(self) -> None:
        try:
            installed = install_charmm36_archive(self.archive)
        except (OSError, ValueError) as exc:
            self.done.emit(False, str(exc))
            return
        self.done.emit(True, str(installed))


class MDSetupTab(MolDynBasePage):
    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        title = QLabel("MD Setup")
        title.setObjectName("PageTitle")
        outer.addWidget(title)
        splitter = QSplitter(Qt.Horizontal)
        outer.addWidget(splitter, 1)

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_scroll.setWidget(left)
        splitter.addWidget(left_scroll)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)

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

        self.fields["ligand"] = PathSelector("Ligand MOL2/SDF", "Ligands (*.mol2 *.sdf *.pdb);;All Files (*)")
        form.addRow("Ligand (MOL2/SDF)", self.fields["ligand"])

        self.fields["force_field"] = QComboBox()
        self.fields["force_field"].addItems(["AMBER99SB-ILDN", "CHARMM36m", "OPLS-AA", "AMBER14SB"])
        self.fields["force_field"].setToolTip(tooltip("force_field"))
        form.addRow("Force Field", self.fields["force_field"])

        charmm_package = QWidget()
        charmm_layout = QHBoxLayout(charmm_package)
        charmm_layout.setContentsMargins(0, 0, 0, 0)
        self.charmm_status = QLabel()
        self.charmm_status.setWordWrap(True)
        charmm_layout.addWidget(self.charmm_status, 1)
        self.charmm_import_button = QPushButton("Import package…")
        self.charmm_import_button.clicked.connect(self._import_charmm_package)
        charmm_layout.addWidget(self.charmm_import_button)
        form.addRow("Offline CHARMM36m", charmm_package)

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
        self.acpype = QRadioButton("ACPYPE / GAFF2 (AMBER-family)")
        self.acpype.setChecked(True)
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
        self.fields["charge_method"].addItems(["AM1-BCC", "RESP", "Gasteiger", "Formal charge only"])
        form.addRow("Charge method", self.fields["charge_method"])
        self.fields["charge"] = QDoubleSpinBox()
        self.fields["charge"].setRange(-20, 20)
        self.fields["charge"].setDecimals(0)
        form.addRow("Charge", self.fields["charge"])
        layout.addLayout(form)

        self.generate_topology_button = QPushButton("Generate Topology")
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
        group = QGroupBox("Steps to Run")
        layout = QHBoxLayout(group)
        for key, label in [
            ("run_minimization", "Minimization"),
            ("run_nvt", "NVT Equilibration"),
            ("run_npt", "NPT Equilibration"),
            ("run_production", "Production MD"),
        ]:
            cb = QCheckBox(label)
            cb.setChecked(True)
            self.fields[key] = cb
            layout.addWidget(cb)
        layout.addStretch(1)
        parent.addWidget(group)

    def _build_production(self, parent: QVBoxLayout) -> None:
        group = QGroupBox("Production MD Parameters")
        form = QFormLayout(group)
        self.fields["duration"] = QDoubleSpinBox()
        self.fields["duration"].setRange(0.001, 1_000_000)
        self.fields["duration"].setValue(100.0)
        self.fields["duration"].setToolTip(tooltip("duration"))
        form.addRow("Duration (ns)", self.fields["duration"])

        self.fields["timestep"] = QDoubleSpinBox()
        self.fields["timestep"].setRange(0.001, 100.0)
        self.fields["timestep"].setValue(2.0)
        self.fields["timestep"].setToolTip(tooltip("timestep"))
        form.addRow("Timestep (fs)", self.fields["timestep"])

        self.fields["temperature"] = QDoubleSpinBox()
        self.fields["temperature"].setRange(1.0, 1000.0)
        self.fields["temperature"].setValue(300.0)
        form.addRow("Temperature (K)", self.fields["temperature"])

        self.fields["pressure"] = QDoubleSpinBox()
        self.fields["pressure"].setRange(0.001, 1000.0)
        self.fields["pressure"].setValue(1.0)
        form.addRow("Pressure (bar)", self.fields["pressure"])

        self.fields["save_every"] = QDoubleSpinBox()
        self.fields["save_every"].setRange(0.001, 1_000_000.0)
        self.fields["save_every"].setValue(10.0)
        self.fields["save_every"].setToolTip(tooltip("save_every"))
        form.addRow("Save every (ps)", self.fields["save_every"])

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

    def md_parameters(self) -> MDParameters:
        return MDParameters(
            duration_ns=self.fields["duration"].value(),
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
        files = generate_all_mdp(self.md_parameters())
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

        if ligand_path and not force_field.upper().startswith("AMBER"):
            QMessageBox.warning(
                self,
                "Incompatible ligand workflow",
                "Automatic CGenFF ligand generation is not bundled. "
                "For a non-covalent ligand, choose an AMBER-family protein "
                "force field and use the built-in ACPYPE/GAFF2 workflow. "
                "CHARMM36m remains available offline for systems whose "
                "residues already exist in its locally installed topology "
                "library.",
            )
            return

        self._prep_worker = SystemPrepWorker(
            SystemPrepParams(
                pdb_path=protein_path,
                work_dir=work_dir,
                force_field=force_field,
                water_model=self.fields["water_model"].currentText(),
                box_type=self.fields["box_type"].currentText(),
                box_padding_nm=float(self.fields["box_padding"].value()),
                ion_concentration_m=float(self.fields["ion_concentration"].value()),
            ),
            parent=self,
        )
        self._prep_worker.log.connect(lambda line: self.request_log.emit(line))
        self._prep_worker.done.connect(
            lambda ok, msg: self.request_log.emit(
                ("[OK] " if ok else "[FAIL] ") + msg
            )
        )
        self.request_log.emit(
            f"Starting system preparation pipeline (force field: "
            f"{self.fields['force_field'].currentText()})."
        )
        self._prep_worker.start()

        if ligand_path:
            self._lig_worker = LigandParamWorker(
                LigandParams(
                    ligand_path=ligand_path,
                    work_dir=work_dir,
                    charge_method=self.fields["charge_method"].currentText(),
                    net_charge=int(self.fields["charge"].value()),
                ),
                parent=self,
            )
            self._lig_worker.log.connect(lambda line: self.request_log.emit(line))
            self._lig_worker.done.connect(
                lambda ok, msg: self.request_log.emit(
                    ("[OK] " if ok else "[FAIL] ") + msg
                )
            )
            self.request_log.emit("Starting ACPYPE ligand parameterization.")
            self._lig_worker.start()

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
                "Action needed — import the official package once"
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
        self.acpype.setEnabled(controls_enabled)
        self.fields["charge_method"].setEnabled(controls_enabled)
        self.fields["charge"].setEnabled(controls_enabled)

        if ligand_selected and not is_amber:
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
                "Optional — select a separate non-covalent ligand to enable "
                "ACPYPE/GAFF2 parameters."
            )
            color = "#555555"
        self.ligand_compatibility.setText(text)
        self.ligand_compatibility.setStyleSheet(f"color: {color};")

    def _import_charmm_package(self) -> None:
        archive = self._select_charmm_archive()
        if not archive:
            return
        self.charmm_import_button.setEnabled(False)
        self.charmm_import_button.setText("Checking package…")
        self.charmm_status.setText(
            "Checking checksum and installing local files…"
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
        self._force_field_import_worker.start()

    def _finish_charmm_import(self, ok: bool, detail: str) -> None:
        self.charmm_import_button.setEnabled(True)
        if not ok:
            self.charmm_status.setText(
                "Import failed — select the official February 2026 package"
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
        default_dir = self.fields["project_dir"].text()
        target = QFileDialog.getExistingDirectory(self, "Select project folder", default_dir or str(Path.home()))
        if not target:
            return
        files = generate_all_mdp(self.md_parameters())
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
