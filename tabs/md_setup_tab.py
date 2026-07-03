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
from PyQt5.QtCore import Qt

from core.system_prep import SystemPrepParams, SystemPrepWorker
from tabs.base import MolDynBasePage, PathSelector
from utils.file_validators import validate_md_inputs
from utils.mdp_generator import MDParameters, generate_all_mdp
from utils.tooltips import tooltip
from utils.topology_builder import LigandParams, LigandParamWorker
from windows.mdp_editor import MDPEditor


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
        self.acpype = QRadioButton("ACPYPE")
        self.acpype.setChecked(True)
        self.acpype.setToolTip(tooltip("parameterization"))
        self.cgenff = QRadioButton("CGenFF")
        self.gaff2 = QRadioButton("GAFF2")
        row.addWidget(self.acpype)
        row.addWidget(self.cgenff)
        row.addWidget(self.gaff2)
        row.addStretch(1)
        layout.addLayout(row)

        form = QFormLayout()
        self.fields["charge_method"] = QComboBox()
        self.fields["charge_method"].addItems(["AM1-BCC", "RESP", "Gasteiger", "Formal charge only"])
        form.addRow("Charge method", self.fields["charge_method"])
        self.fields["charge"] = QDoubleSpinBox()
        self.fields["charge"].setRange(-20, 20)
        self.fields["charge"].setDecimals(0)
        form.addRow("Charge", self.fields["charge"])
        layout.addLayout(form)

        button = QPushButton("Generate Topology")
        button.clicked.connect(self.generate_topology_preview)
        layout.addWidget(button)
        parent.addWidget(group)

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
        method = "ACPYPE" if self.acpype.isChecked() else "CGenFF" if self.cgenff.isChecked() else "GAFF2"
        protein_path = self.fields["protein"].text().strip()
        ligand_path = self.fields["ligand"].text().strip()
        work_dir = self.fields["project_dir"].text().strip()

        if not protein_path or not work_dir:
            QMessageBox.warning(
                self,
                "Missing inputs",
                "Select both a project folder and a protein PDB before generating topology.",
            )
            return

        self._prep_worker = SystemPrepWorker(
            SystemPrepParams(
                pdb_path=protein_path,
                work_dir=work_dir,
                force_field=self.fields["force_field"].currentText(),
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

        if ligand_path and method == "ACPYPE":
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
        elif ligand_path:
            self.request_log.emit(
                f"Ligand provided but {method} backend is not yet implemented; "
                "skipping ligand step."
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

