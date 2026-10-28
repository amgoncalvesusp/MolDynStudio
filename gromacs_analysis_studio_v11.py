#!/usr/bin/env python3
"""MolDynStudio desktop application entry point.

The MD Setup and MD Run pages prepare projects and launch a real, staged
GROMACS pipeline. Post-MD analysis modules that do not yet launch external
backends remain explicitly presented as previews.
"""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional
from uuid import uuid4

from PyQt5.QtCore import Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QColor, QDesktopServices, QIcon, QKeySequence, QPalette
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from core.project_manager import (
    ProjectFormatError,
    ProjectManager,
    is_windows_absolute_path,
)
from core.settings import SettingsStore
from core.evidence_bundle import export_evidence_bundle
from settings_dialog import SettingsDialog
from tabs.analysis_tab import AnalysisTab
from tabs.md_run_tab import MDRunTab
from tabs.md_setup_tab import MDSetupTab
from tabs.qc_dashboard_tab import QCDashboardWidget
from windows.help_dialog import HelpDialog
from windows.project_viewer import ProjectViewerDock


APP_NAME = "MolDynStudio"
APP_VERSION = "v1.3.0"
APP_INVENTOR = "Adriano Marques Gonçalves (UNIARA)"

NAV_ITEMS = [
    "Project",
    "MD Setup",
    "MD Run",
    "Analysis",
    "Load System",
    "Trajectory Preprocessing",
    "Structural Stability",
    "Interaction Analysis",
    "Solvent and Ions",
    "Energy Analysis",
    "End-Point Free Energy",
    "Batch Comparison",
    "Results Viewer",
    "Reports & Export",
    "Settings",
]


class FileSelector(QWidget):
    def __init__(self, title: str, file_filter: str = "All Files (*)", save: bool = False):
        super().__init__()
        self.file_filter = file_filter
        self.save_mode = save
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.line_edit = QLineEdit()
        self.line_edit.setPlaceholderText(title)
        self.line_edit.setAccessibleName(title)
        self.setFocusProxy(self.line_edit)
        layout.addWidget(self.line_edit)
        self.button = QPushButton("Browse")
        self.button.setAccessibleName(f"Browse for {title}")
        self.button.clicked.connect(self.browse)
        layout.addWidget(self.button)

    def browse(self):
        if self.save_mode:
            path, _ = QFileDialog.getSaveFileName(self, "Select file", "", self.file_filter)
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Select file", "", self.file_filter)
        if path:
            self.line_edit.setText(path)

    def text(self) -> str:
        return self.line_edit.text().strip()

    def setText(self, text: str):
        self.line_edit.setText(text)


class DirSelector(QWidget):
    def __init__(self, title: str):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.line_edit = QLineEdit()
        self.line_edit.setPlaceholderText(title)
        self.line_edit.setAccessibleName(title)
        self.setFocusProxy(self.line_edit)
        layout.addWidget(self.line_edit)
        self.button = QPushButton("Browse")
        self.button.setAccessibleName(f"Browse for {title}")
        self.button.clicked.connect(self.browse)
        layout.addWidget(self.button)

    def browse(self):
        path = QFileDialog.getExistingDirectory(self, "Select directory")
        if path:
            self.line_edit.setText(path)

    def text(self) -> str:
        return self.line_edit.text().strip()

    def setText(self, text: str):
        self.line_edit.setText(text)


class BasePage(QWidget):
    request_log = pyqtSignal(str)
    request_preview = pyqtSignal(str)
    request_qc_files = pyqtSignal(object)

    def __init__(self, title: str, subtitle: str = ""):
        super().__init__()
        self.title = title
        self.subtitle = subtitle
        self.fields: Dict[str, QWidget] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 22px; font-weight: 700; color: #2C2C2C;")
        outer.addWidget(title_label)

        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setWordWrap(True)
            subtitle_label.setStyleSheet("color: #555555; font-size: 12px;")
            outer.addWidget(subtitle_label)

        self.body_layout = QVBoxLayout()
        self.body_layout.setSpacing(12)
        outer.addLayout(self.body_layout, 1)

    def add_group(self, title: str) -> tuple[QGroupBox, QVBoxLayout]:
        group = QGroupBox(title)
        layout = QVBoxLayout(group)
        layout.setSpacing(8)
        self.body_layout.addWidget(group)
        return group, layout

    def get_state(self) -> Dict:
        state: Dict[str, object] = {}
        for key, widget in self.fields.items():
            if isinstance(widget, QLineEdit):
                state[key] = widget.text()
            elif isinstance(widget, QCheckBox):
                state[key] = widget.isChecked()
            elif isinstance(widget, QComboBox):
                state[key] = widget.currentText()
            elif isinstance(widget, QSpinBox):
                state[key] = widget.value()
            elif isinstance(widget, QDoubleSpinBox):
                state[key] = widget.value()
            elif isinstance(widget, FileSelector):
                state[key] = widget.text()
            elif isinstance(widget, DirSelector):
                state[key] = widget.text()
        return state

    def set_state(self, state: Dict):
        for key, value in state.items():
            widget = self.fields.get(key)
            if widget is None:
                continue
            if isinstance(widget, QLineEdit):
                widget.setText(str(value))
            elif isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QComboBox):
                idx = widget.findText(str(value))
                if idx >= 0:
                    widget.setCurrentIndex(idx)
            elif isinstance(widget, QSpinBox):
                widget.setValue(int(value))
            elif isinstance(widget, QDoubleSpinBox):
                widget.setValue(float(value))
            elif isinstance(widget, FileSelector):
                widget.setText(str(value))
            elif isinstance(widget, DirSelector):
                widget.setText(str(value))

    def preview_text(self) -> str:
        return f"[{self.title}] Use this page's controls to load and inspect existing results."

    def action_bar(self, include_preview: bool = True) -> QWidget:
        frame = QFrame()
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch(1)
        if include_preview:
            preview = QPushButton("Preview configuration")
            preview.clicked.connect(lambda: self.request_preview.emit(self.preview_text()))
            layout.addWidget(preview)
        return frame


class ProjectPage(BasePage):
    def __init__(self):
        super().__init__(
            "Project",
            "Save your workspace, then prepare a system in MD Setup or load an existing trajectory in Load System.",
        )
        group, layout = self.add_group("Project Settings")
        form = QFormLayout()
        layout.addLayout(form)

        self.fields["project_name"] = QLineEdit("analysis_project_001")
        form.addRow("Project name", self.fields["project_name"])

        self.fields["working_dir"] = DirSelector("Project working directory")
        self.fields["working_dir"].setText(str(Path.home() / "gromacs_analysis_project"))
        form.addRow("Working directory", self.fields["working_dir"])

        self.fields["project_type"] = QComboBox()
        self.fields["project_type"].addItems(["Protein", "Protein-Ligand"])
        form.addRow("Project type", self.fields["project_type"])

        self.fields["force_field_family"] = QComboBox()
        self.fields["force_field_family"].addItems(["CHARMM", "AMBER", "OPLS", "Other"])
        form.addRow("Force field family", self.fields["force_field_family"])

        self.fields["analysis_mode"] = QComboBox()
        self.fields["analysis_mode"].addItems(["Simple", "Advanced"])
        form.addRow("Analysis mode", self.fields["analysis_mode"])

        bundle_group, bundle_layout = self.add_group("Planning presets")
        self.fields["template"] = QComboBox()
        self.fields["template"].addItems([
            "Protein Quick Stability",
            "Protein Full Stability",
            "Protein-Ligand Binding Check",
            "Protein-Ligand Quick MM/GBSA",
            "Protein-Ligand Refined MM/PBSA",
            "Solvation Around Ligand",
            "Ligand Batch Ranking",
        ])
        bundle_layout.addWidget(self.fields["template"])
        hint = QLabel(
            "These planning preferences are saved with the session. They do not run analyses or change the MD protocol. "
            "Next: open MD Setup for a new simulation, or Load System for existing MD files."
        )
        hint.setWordWrap(True)
        bundle_layout.addWidget(hint)
        self.body_layout.addWidget(self.action_bar())

    def preview_text(self) -> str:
        name = self.fields["project_name"].text()
        ptype = self.fields["project_type"].currentText()
        mode = self.fields["analysis_mode"].currentText()
        template = self.fields["template"].currentText()
        return (
            f"[Project Preview]\n"
            f"Project: {name}\nType: {ptype}\nMode: {mode}\nTemplate: {template}\n"
            f"Recommended output folder: {self.fields['working_dir'].text()}"
        )


class LoadSystemPage(BasePage):
    inputs_ready = pyqtSignal(object)

    def __init__(self):
        super().__init__(
            "Load System",
            "Load the main GROMACS files. TPR is required; trajectory and energy files are strongly recommended.",
        )
        self.operation_blocked = lambda: False
        group, layout = self.add_group("Input Files")
        form = QFormLayout()
        layout.addLayout(form)

        self.fields["tpr_file"] = FileSelector("Select .tpr file", "GROMACS TPR (*.tpr);;All Files (*)")
        form.addRow("TPR file", self.fields["tpr_file"])

        self.fields["trajectory_file"] = FileSelector("Select .xtc or .trr", "Trajectories (*.xtc *.trr);;All Files (*)")
        form.addRow("Trajectory", self.fields["trajectory_file"])

        self.fields["energy_file"] = FileSelector("Select .edr", "Energy files (*.edr);;All Files (*)")
        form.addRow("Energy file", self.fields["energy_file"])

        self.fields["index_file"] = FileSelector("Select .ndx", "Index files (*.ndx);;All Files (*)")
        form.addRow("Index file", self.fields["index_file"])

        self.fields["reference_structure"] = FileSelector("Select reference .gro or .pdb", "Structures (*.gro *.pdb);;All Files (*)")
        form.addRow("Reference structure", self.fields["reference_structure"])

        self.fields["ligand_name"] = QLineEdit()
        self.fields["ligand_name"].setPlaceholderText("Optional: ligand residue name, e.g. LIG")
        form.addRow("Ligand residue name", self.fields["ligand_name"])

        summary_group, summary_layout = self.add_group("Automatic Summary")
        self.summary = QTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setMinimumHeight(120)
        self.summary.setPlainText(
            "Validation summary will appear here.\n\n"
            "Typical protein-ligand setup:\n"
            "- TPR\n- Processed XTC\n- EDR\n- Optional NDX\n"
        )
        summary_layout.addWidget(self.summary)

        button_row = QHBoxLayout()
        validate_btn = QPushButton("Validate and use inputs")
        validate_btn.clicked.connect(self.validate_inputs)
        button_row.addWidget(validate_btn)
        summary_layout.addLayout(button_row)

        self.body_layout.addWidget(self.action_bar())

    def validate_inputs(self):
        if self.operation_blocked():
            self.summary.setPlainText("Wait for the current operation to finish before changing analysis inputs.")
            return
        messages: List[str] = []
        tpr = self.fields["tpr_file"].text()
        traj = self.fields["trajectory_file"].text()
        edr = self.fields["energy_file"].text()
        ndx = self.fields["index_file"].text()
        ligand = self.fields["ligand_name"].text().strip()

        valid = True
        for label, value, required in (("TPR", tpr, True), ("Trajectory", traj, True), ("Energy", edr, False), ("Index", ndx, False)):
            if not value:
                messages.append(f"{label}: {'Required; select a file' if required else 'Optional / not loaded'}")
                valid = valid and not required
            elif not Path(value).expanduser().is_file():
                messages.append(f"{label}: file not found - {value}")
                valid = False
            else:
                messages.append(f"{label}: file found")
        messages.append(f"Ligand hint: {ligand if ligand else 'Not provided'}")
        messages.append("\nFile existence checked. Scientific compatibility is checked by each analysis backend.")
        if valid:
            self.inputs_ready.emit({"topology": tpr, "trajectory": traj, "energy": edr})
            messages.append("Inputs sent to Analysis. Next: choose an analysis or open the trajectory viewer.")
        self.summary.setPlainText("\n".join(messages))
        self.request_log.emit("Load System validation finished.")

    def preview_text(self) -> str:
        return (
            "[Load System Preview]\n"
            f"TPR: {self.fields['tpr_file'].text() or 'Not set'}\n"
            f"Trajectory: {self.fields['trajectory_file'].text() or 'Not set'}\n"
            f"Energy: {self.fields['energy_file'].text() or 'Not set'}\n"
            f"Index: {self.fields['index_file'].text() or 'Not set'}\n"
            f"Reference: {self.fields['reference_structure'].text() or 'Not set'}\n"
            f"Ligand: {self.fields['ligand_name'].text() or 'Not set'}"
        )


class PreprocessingPage(BasePage):
    def __init__(self):
        super().__init__(
            "Trajectory Preprocessing",
            "Build an analysis-ready trajectory: center the system, correct PBC, fit to a reference, and save a processed trajectory.",
        )
        group, layout = self.add_group("Preprocessing Operations")
        grid = QGridLayout()
        layout.addLayout(grid)

        options = [
            ("remove_pbc", "Remove PBC jumps"),
            ("center_molecule", "Center molecule"),
            ("compact_cell", "Compact unit cell"),
            ("fit_reference", "Fit to reference"),
            ("strip_solvent", "Strip solvent"),
            ("keep_protein_only", "Keep protein only"),
            ("keep_protein_ligand", "Keep protein + ligand"),
            ("analysis_ready", "Create analysis-ready trajectory"),
        ]
        for idx, (key, label) in enumerate(options):
            cb = QCheckBox(label)
            cb.setChecked(key in {"remove_pbc", "center_molecule", "fit_reference", "analysis_ready"})
            self.fields[key] = cb
            grid.addWidget(cb, idx // 2, idx % 2)

        form_group, form_layout = self.add_group("Parameters")
        form = QFormLayout()
        form_layout.addLayout(form)

        self.fields["fit_mode"] = QComboBox()
        self.fields["fit_mode"].addItems([
            "No fit",
            "Backbone fit",
            "C-alpha fit",
            "Protein heavy atoms",
            "Custom selection",
        ])
        self.fields["fit_mode"].setCurrentText("Backbone fit")
        form.addRow("Fit mode", self.fields["fit_mode"])

        self.fields["output_preset"] = QComboBox()
        self.fields["output_preset"].addItems([
            "Full system",
            "Protein only",
            "Protein + ligand",
            "Binding site only",
        ])
        self.fields["output_preset"].setCurrentText("Protein + ligand")
        form.addRow("Output preset", self.fields["output_preset"])

        self.fields["output_name"] = QLineEdit("processed_analysis.xtc")
        form.addRow("Output trajectory name", self.fields["output_name"])

        help_label = QLabel(
            "Typical default for protein-ligand analysis: remove PBC jumps + center molecule + compact unit cell + backbone fit."
        )
        help_label.setWordWrap(True)
        form_layout.addWidget(help_label)
        self.body_layout.addWidget(self.action_bar())

    def preview_text(self) -> str:
        selected = [
            label.text()
            for label in [self.fields[k] for k in [
                "remove_pbc", "center_molecule", "compact_cell", "fit_reference",
                "strip_solvent", "keep_protein_only", "keep_protein_ligand", "analysis_ready",
            ]]
            if isinstance(label, QCheckBox) and label.isChecked()
        ]
        return (
            "[Preprocessing Preview]\n"
            f"Operations: {', '.join(selected) if selected else 'None selected'}\n"
            f"Fit mode: {self.fields['fit_mode'].currentText()}\n"
            f"Output preset: {self.fields['output_preset'].currentText()}\n"
            f"Output trajectory: {self.fields['output_name'].text()}"
        )


class ChecklistAnalysisPage(BasePage):
    def __init__(
        self,
        title: str,
        subtitle: str,
        analysis_items: List[str],
        recommendations: str,
        selection_defaults: Optional[List[str]] = None,
    ):
        super().__init__(title, subtitle)
        selection_defaults = selection_defaults or ["Protein", "Backbone", "Custom selection"]

        main_group, main_layout = self.add_group("Analysis Selection")
        grid = QGridLayout()
        main_layout.addLayout(grid)
        for idx, item in enumerate(analysis_items):
            key = f"analysis_{idx}"
            cb = QCheckBox(item)
            cb.setChecked(idx < min(3, len(analysis_items)))
            self.fields[key] = cb
            grid.addWidget(cb, idx // 2, idx % 2)

        settings_group, settings_layout = self.add_group("Parameters")
        form = QFormLayout()
        settings_layout.addLayout(form)

        self.fields["target_selection"] = QComboBox()
        self.fields["target_selection"].addItems(selection_defaults)
        form.addRow("Target selection", self.fields["target_selection"])

        self.fields["time_window"] = QComboBox()
        self.fields["time_window"].addItems(["Full trajectory", "Equilibrated region only", "Custom window"])
        self.fields["time_window"].setCurrentText("Equilibrated region only")
        form.addRow("Time window", self.fields["time_window"])

        self.fields["export_csv"] = QCheckBox("Export CSV tables")
        self.fields["export_csv"].setChecked(True)
        settings_layout.addWidget(self.fields["export_csv"])

        self.fields["export_png"] = QCheckBox("Export PNG plots")
        self.fields["export_png"].setChecked(True)
        settings_layout.addWidget(self.fields["export_png"])

        rec_group, rec_layout = self.add_group("Suggested usage")
        label = QLabel(recommendations)
        label.setWordWrap(True)
        rec_layout.addWidget(label)
        self.body_layout.addWidget(self.action_bar())

    def selected_analysis_names(self) -> List[str]:
        names: List[str] = []
        for widget in self.fields.values():
            if isinstance(widget, QCheckBox) and widget.text() not in {"Export CSV tables", "Export PNG plots"} and widget.isChecked():
                names.append(widget.text())
        return names

    def preview_text(self) -> str:
        selected = self.selected_analysis_names()
        return (
            f"[{self.title} Preview]\n"
            f"Selected analyses: {', '.join(selected) if selected else 'None'}\n"
            f"Target selection: {self.fields['target_selection'].currentText()}\n"
            f"Time window: {self.fields['time_window'].currentText()}\n"
            f"Export CSV: {self.fields['export_csv'].isChecked()}\n"
            f"Export PNG: {self.fields['export_png'].isChecked()}"
        )


class FreeEnergyPage(BasePage):
    def __init__(self):
        super().__init__(
            "End-Point Free Energy",
            "Plan MM/GBSA and MM/PBSA sampling. Compare methods and sampling assumptions before interpreting binding-energy estimates.",
        )
        mode_group, mode_layout = self.add_group("Calculation mode")
        form = QFormLayout()
        mode_layout.addLayout(form)

        self.fields["mode"] = QComboBox()
        self.fields["mode"].addItems(["MM/GBSA", "MM/PBSA", "MM/PBSA + MM/GBSA"])
        form.addRow("Mode", self.fields["mode"])

        self.fields["protocol"] = QComboBox()
        self.fields["protocol"].addItems(["Single trajectory", "Multiple trajectory"])
        form.addRow("Protocol", self.fields["protocol"])

        self.fields["snapshot_preset"] = QComboBox()
        self.fields["snapshot_preset"].addItems(["Quick", "Standard", "Refined", "Custom"])
        self.fields["snapshot_preset"].setCurrentText("Quick")
        self.fields["snapshot_preset"].currentTextChanged.connect(self._apply_preset)
        form.addRow("Snapshot preset", self.fields["snapshot_preset"])

        frames_group, frames_layout = self.add_group("Frame sampling")
        form2 = QFormLayout()
        frames_layout.addLayout(form2)

        self.fields["start_frame"] = QSpinBox()
        self.fields["start_frame"].setMaximum(1_000_000)
        self.fields["start_frame"].setValue(1)
        form2.addRow("Start frame", self.fields["start_frame"])

        self.fields["end_frame"] = QSpinBox()
        self.fields["end_frame"].setMaximum(1_000_000)
        self.fields["end_frame"].setValue(1000)
        form2.addRow("End frame", self.fields["end_frame"])

        self.fields["interval"] = QSpinBox()
        self.fields["interval"].setRange(1, 10_000)
        self.fields["interval"].setValue(10)
        form2.addRow("Frame interval", self.fields["interval"])

        self.fields["entropy"] = QComboBox()
        self.fields["entropy"].addItems(["Off", "Quick entropy", "Extended entropy"])
        form2.addRow("Entropy", self.fields["entropy"])

        self.fields["decomposition"] = QComboBox()
        self.fields["decomposition"].addItems(["Off", "Per-residue decomposition", "Pairwise decomposition"])
        form2.addRow("Decomposition", self.fields["decomposition"])

        self.fields["equilibrated_only"] = QCheckBox("Use equilibrated region only")
        self.fields["equilibrated_only"].setChecked(True)
        frames_layout.addWidget(self.fields["equilibrated_only"])

        notes_group, notes_layout = self.add_group("Snapshot guidance")
        notes = QLabel(
            "Quick, Standard, and Refined select approximately 100, 200, and 1000 frames. "
            "Frame count alone does not establish convergence or ranking reliability. Choose an equilibrated window and assess uncertainty across independent runs."
        )
        notes.setWordWrap(True)
        notes_layout.addWidget(notes)
        self.body_layout.addWidget(self.action_bar())
        self._apply_preset("Quick")

    def _apply_preset(self, preset: str):
        mapping = {
            "Quick": (1, 1000, 10),
            "Standard": (1, 2000, 10),
            "Refined": (1, 10000, 10),
            "Custom": None,
        }
        values = mapping.get(preset)
        if values:
            start, end, interval = values
            self.fields["start_frame"].setValue(start)
            self.fields["end_frame"].setValue(end)
            self.fields["interval"].setValue(interval)

    def preview_text(self) -> str:
        start = self.fields["start_frame"].value()
        end = self.fields["end_frame"].value()
        stride = self.fields["interval"].value()
        estimated = max(0, (end - start) // max(stride, 1) + 1)
        return (
            "[End-Point Free Energy Preview]\n"
            f"Mode: {self.fields['mode'].currentText()}\n"
            f"Protocol: {self.fields['protocol'].currentText()}\n"
            f"Snapshot preset: {self.fields['snapshot_preset'].currentText()}\n"
            f"Frames: {start} to {end} every {stride}\n"
            f"Estimated snapshots: {estimated}\n"
            f"Entropy: {self.fields['entropy'].currentText()}\n"
            f"Decomposition: {self.fields['decomposition'].currentText()}"
        )


class BatchComparisonPage(BasePage):
    def __init__(self):
        super().__init__(
            "Batch Comparison",
            "Compare ligands, replicas, or protein variants. Use this module after analyses have already been generated for each run.",
        )
        mode_group, mode_layout = self.add_group("Comparison mode")
        self.fields["comparison_mode"] = QComboBox()
        self.fields["comparison_mode"].addItems([
            "Compare ligands",
            "Compare replicas",
            "Compare protein variants",
            "Compare custom runs",
        ])
        mode_layout.addWidget(self.fields["comparison_mode"])

        table_group, table_layout = self.add_group("Input runs")
        self.runs_table = QTableWidget(0, 6)
        self.runs_table.setHorizontalHeaderLabels([
            "Enabled", "Run name", "Project path", "System type", "Ligand", "Status"
        ])
        self.runs_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.runs_table.verticalHeader().setVisible(False)
        table_layout.addWidget(self.runs_table)

        button_row = QHBoxLayout()
        add_btn = QPushButton("Add run")
        add_btn.clicked.connect(self.add_row)
        button_row.addWidget(add_btn)
        remove_btn = QPushButton("Remove selected")
        remove_btn.clicked.connect(self.remove_row)
        button_row.addWidget(remove_btn)
        table_layout.addLayout(button_row)

        metrics_group, metrics_layout = self.add_group("Metrics")
        metrics = [
            "RMSD", "RMSF", "Radius of gyration", "SASA", "H-bond occupancy",
            "Contact frequency", "Distances", "Energy terms", "Total Delta G", "Per-residue Delta G"
        ]
        grid = QGridLayout()
        metrics_layout.addLayout(grid)
        for idx, metric in enumerate(metrics):
            cb = QCheckBox(metric)
            cb.setChecked(idx < 4)
            self.fields[f"metric_{idx}"] = cb
            grid.addWidget(cb, idx // 2, idx % 2)

        filters_group, filters_layout = self.add_group("Comparison filters")
        for key, text, checked in [
            ("common_window", "Common time window only", True),
            ("normalize_frames", "Normalize frame counts", True),
            ("equilibrated_region", "Use equilibrated region", True),
            ("average_replicates", "Average replicates", False),
            ("show_sd", "Show standard deviation", True),
        ]:
            cb = QCheckBox(text)
            cb.setChecked(checked)
            self.fields[key] = cb
            filters_layout.addWidget(cb)

        self.body_layout.addWidget(self.action_bar())

    def add_row(self, data: Optional[List[str]] = None):
        row = self.runs_table.rowCount()
        self.runs_table.insertRow(row)
        enabled = QTableWidgetItem("Yes")
        enabled.setCheckState(Qt.Checked)
        self.runs_table.setItem(row, 0, enabled)
        values = data or [f"Run_{row+1}", "", "Protein-Ligand", "LIG", "Ready"]
        for col, value in enumerate(values, start=1):
            self.runs_table.setItem(row, col, QTableWidgetItem(value))

    def remove_row(self):
        current = self.runs_table.currentRow()
        if current >= 0:
            self.runs_table.removeRow(current)

    def get_state(self) -> Dict:
        state = super().get_state()
        rows: List[List[str]] = []
        for row in range(self.runs_table.rowCount()):
            enabled_item = self.runs_table.item(row, 0)
            enabled = "Yes" if enabled_item and enabled_item.checkState() == Qt.Checked else "No"
            values = [enabled]
            for col in range(1, self.runs_table.columnCount()):
                item = self.runs_table.item(row, col)
                values.append(item.text() if item else "")
            rows.append(values)
        state["runs_table"] = rows
        return state

    def set_state(self, state: Dict):
        super().set_state(state)
        self.runs_table.setRowCount(0)
        for row_values in state.get("runs_table", []):
            row = self.runs_table.rowCount()
            self.runs_table.insertRow(row)
            enabled_item = QTableWidgetItem("Yes")
            enabled_item.setCheckState(Qt.Checked if row_values[0] == "Yes" else Qt.Unchecked)
            self.runs_table.setItem(row, 0, enabled_item)
            for col, value in enumerate(row_values[1:], start=1):
                self.runs_table.setItem(row, col, QTableWidgetItem(value))

    def preview_text(self) -> str:
        metrics = [
            widget.text()
            for key, widget in self.fields.items()
            if key.startswith("metric_") and isinstance(widget, QCheckBox) and widget.isChecked()
        ]
        return (
            "[Batch Comparison Preview]\n"
            f"Mode: {self.fields['comparison_mode'].currentText()}\n"
            f"Runs loaded: {self.runs_table.rowCount()}\n"
            f"Metrics: {', '.join(metrics) if metrics else 'None'}"
        )


class ResultsViewerPage(BasePage):
    def __init__(self):
        super().__init__(
            "Results Viewer",
            "Inspect measured time series from XVG or CSV files. Load existing results or generate QC outputs from Analysis.",
        )
        self.tabs = QTabWidget()
        self.body_layout.addWidget(self.tabs, 1)

        self.dashboard_tab = QWidget()
        dashboard_layout = QVBoxLayout(self.dashboard_tab)
        self.qc_dashboard = QCDashboardWidget()
        dashboard_layout.addWidget(self.qc_dashboard)
        self.tabs.addTab(self.dashboard_tab, "QC Dashboard")

    def load_qc_files(self, paths: object) -> None:
        file_paths = [str(path) for path in paths]
        if not file_paths:
            return
        self.qc_dashboard.load_files(file_paths)
        dashboard_index = self.tabs.indexOf(self.dashboard_tab)
        if dashboard_index >= 0:
            self.tabs.setCurrentIndex(dashboard_index)


class EvidenceExportWorker(QThread):
    done = pyqtSignal(bool, str)

    def __init__(self, project: Path, destination: Path, parent=None):
        super().__init__(parent)
        self.project = project
        self.destination = destination

    def run(self):
        try:
            archive = export_evidence_bundle(self.project, self.destination)
        except Exception as exc:
            self.done.emit(False, str(exc))
        else:
            self.done.emit(True, str(archive))


class ReportsPage(BasePage):
    def __init__(self, project_provider=None):
        super().__init__(
            "Reports & Export",
            "Export an auditable evidence bundle from a prepared project. Files and checksums record what was actually run.",
        )
        self.project_provider = project_provider or (lambda: None)
        self.operation_blocked = lambda: False
        self._worker = None
        group, layout = self.add_group("Reproducibility export")
        description = QLabel("Bundle existing run metadata, protocol files, logs, and analysis evidence. Large trajectory files remain in the project; the bundle records their identity. Review the generated inventory before sharing.")
        description.setWordWrap(True)
        layout.addWidget(description)
        form = QFormLayout()
        self.fields["export_dir"] = DirSelector("Export directory")
        self.fields["export_dir"].setText(str(Path.home() / "gromacs_analysis_exports"))
        form.addRow("Export directory", self.fields["export_dir"])
        layout.addLayout(form)
        self.export_button = QPushButton("Export evidence bundle")
        self.export_button.setObjectName("PrimaryAction")
        self.export_button.clicked.connect(self.export_bundle)
        layout.addWidget(self.export_button)
        self.status = QLabel("Prepare or reopen an MD project to export its evidence.")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.status)
        self.body_layout.addStretch(1)

    def export_bundle(self) -> None:
        if self.is_busy() or self.operation_blocked():
            self.status.setText("Wait for the current operation to finish before exporting evidence.")
            return
        project = self.project_provider()
        destination = self.fields["export_dir"].text()
        if project is None or not destination:
            self.status.setText("Select a prepared project and an export directory first.")
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = Path(destination) / f"moldynstudio_evidence_{stamp}_{uuid4().hex[:8]}.zip"
        self._worker = EvidenceExportWorker(Path(project), target, self)
        self._worker.done.connect(self._export_finished)
        self._worker.finished.connect(lambda: self.export_button.setEnabled(True))
        self.export_button.setEnabled(False)
        self.status.setText("Exporting evidence and hashing source files. Large trajectories can take time.")
        self._worker.start()

    def is_busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _export_finished(self, ok: bool, message: str) -> None:
        self.status.setText(f"Evidence bundle saved: {message}" if ok else f"Export failed: {message}")
        self.request_log.emit(self.status.text())

    def preview_text(self) -> str:
        return (
            "[Evidence bundle]\n"
            f"Project: {self.project_provider() or 'No prepared project loaded'}\n"
            f"Export directory: {self.fields['export_dir'].text()}"
        )


class SettingsPage(BasePage):
    def __init__(self, settings: SettingsStore, open_preferences):
        super().__init__(
            "Settings",
            "Set shared tool paths and runtime defaults. Changes apply to the next operation.",
        )
        self.settings = settings
        group, layout = self.add_group("Preferences")
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.summary)
        button = QPushButton("Open preferences")
        button.setObjectName("PrimaryAction")
        button.clicked.connect(open_preferences)
        layout.addWidget(button)
        self.body_layout.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        self.summary.setText(self.preview_text())

    def preview_text(self) -> str:
        return (
            f"GROMACS: {self.settings.value('gromacs_binary')}\n"
            f"CPPTRAJ: {self.settings.value('cpptraj_binary')}\n"
            f"DockLens: {self.settings.value('docklens_binary')}\n"
            f"Conda environment: {self.settings.value('conda_environment')}\n"
            f"CPU cores: {self.settings.value('cores')} | GPU: {self.settings.value('gpu_mode')}\n"
            "Interface: English | Theme: Light"
        )


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} - {APP_VERSION}")
        self.resize(1480, 920)
        self.current_session_path: Optional[Path] = None
        self.active_project_dir: Path | None = None
        self.active_manifest_path: Path | None = None
        self._project_context_was_cleared = False
        self.viewer_dock: ProjectViewerDock | None = None
        self.project_manager = ProjectManager()
        self.settings = SettingsStore()
        self._autosave_timer = QTimer(self)
        self._autosave_timer.timeout.connect(self.autosave_session)

        self._apply_style()
        logo_path = Path(__file__).resolve().parent / "assets" / "logo_256.png"
        if logo_path.exists():
            self.setWindowIcon(QIcon(str(logo_path)))
        self._build_ui()
        self._initial_page_states = deepcopy({name: page.get_state() for name, page in self.pages.items()})
        self._autosave_timer.start(5 * 60 * 1000)
        self.statusBar().showMessage(
            "MolDynStudio ready. Configure MD setup, run GROMACS MD, "
            "or load an existing trajectory for analysis."
        )

    def _apply_style(self):
        palette = self.palette()
        palette.setColor(QPalette.PlaceholderText, QColor("#666666"))
        self.setPalette(palette)
        self.setStyleSheet(
            """
            QMainWindow { background: #F5F5F5; }
            QWidget { color: #2C2C2C; font-size: 13px; font-family: "Segoe UI", "SF Pro", Ubuntu, sans-serif; }
            QLabel#PageTitle { font-size: 22px; font-weight: 700; color: #2C2C2C; }
            QGroupBox {
                border: 1px solid #DCDCDC;
                border-radius: 4px;
                margin-top: 10px;
                font-weight: 700;
                background: #FFFFFF;
                padding-top: 12px;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 6px; }
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QTableWidget {
                background: #FFFFFF;
                border: 1px solid #DCDCDC;
                border-radius: 3px;
                padding: 6px;
                selection-background-color: #FFF3E0;
                selection-color: #2C2C2C;
            }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
            QTextEdit:focus, QTableWidget:focus, QPushButton:focus { border: 2px solid #9A470D; }
            QLabel#CapabilityNote { color: #704111; background: #FFF3E0; padding: 10px; }
            QPushButton {
                background: #FFFFFF;
                border: 1px solid #DCDCDC;
                border-radius: 3px;
                padding: 8px 14px;
                font-weight: 700;
                color: #2C2C2C;
            }
            QPushButton:hover { background: #FFF3E0; border-color: #E87722; }
            QPushButton:pressed { background: #F5DFC7; }
            QPushButton:disabled { color: #666666; background: #F1F1F1; }
            QPushButton#PrimaryAction { background: #9A470D; color: #FFFFFF; border-color: #9A470D; }
            QPushButton#PrimaryAction:hover { background: #7C3708; }
            QListWidget {
                background: #FFFFFF;
                border: none;
                padding: 6px;
            }
            QListWidget::item {
                padding: 8px;
                border-radius: 3px;
                margin: 2px 0;
            }
            QListWidget::item:selected { background: #FFF3E0; color: #703509; font-weight: 700; }
            QListWidget::item:focus { border: 1px solid #9A470D; }
            QToolBar { background: #FFFFFF; spacing: 8px; border-bottom: 1px solid #DCDCDC; }
            QMenuBar { background: #FFFFFF; border-bottom: 1px solid #DCDCDC; }
            QMenuBar::item:selected, QMenu::item:selected { background: #FFF3E0; }
            QTabBar::tab { background: #FFFFFF; border: 1px solid #DCDCDC; padding: 8px 12px; }
            QTabBar::tab:selected { border-bottom: 2px solid #E87722; }
            QProgressBar {
                border: 1px solid #DCDCDC;
                border-radius: 3px;
                text-align: center;
                min-height: 20px;
                background: #FFFFFF;
            }
            QProgressBar::chunk { background: #E87722; border-radius: 2px; }
            """
        )

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._build_menu()
        self._build_toolbar()

        splitter = QSplitter(Qt.Horizontal)
        outer.addWidget(splitter, 1)

        self.nav = QListWidget()
        self.nav.setMinimumWidth(190)
        self.nav.setAccessibleName("Workflow pages")
        for item in NAV_ITEMS:
            nav_item = QListWidgetItem(item, self.nav)
            nav_item.setToolTip(self.context_text(item))
        self.nav.currentRowChanged.connect(self._switch_page)
        splitter.addWidget(self.nav)

        main_splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(main_splitter)
        splitter.setStretchFactor(1, 1)

        content_wrapper = QSplitter(Qt.Horizontal)
        main_splitter.addWidget(content_wrapper)

        self.stack = QStackedWidget()
        content_wrapper.addWidget(self.stack)

        self.side_info = QTextEdit()
        self.side_info.setReadOnly(True)
        self.side_info.setMinimumWidth(210)
        self.side_info.setAccessibleName("Workflow guidance")
        self.side_info.setPlainText(
            "Recommendations\n\n"
            "- Start in MD Setup to generate MDP files and validate inputs\n"
            "- Use MD Run for pipeline monitoring\n"
            "- Use Analysis for RMSD/RMSF/SASA/PCA/MM-PBSA workflows\n"
        )
        content_wrapper.addWidget(self.side_info)
        content_wrapper.setStretchFactor(0, 4)
        content_wrapper.setStretchFactor(1, 1)
        content_wrapper.setSizes([850, 250])

        self.log_group = QGroupBox("Run log")
        log_layout = QVBoxLayout(self.log_group)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(90)
        self.log.document().setMaximumBlockCount(5000)
        self.log.setAccessibleName("Application run log")
        log_layout.addWidget(self.log)
        main_splitter.addWidget(self.log_group)
        main_splitter.setStretchFactor(0, 1)
        main_splitter.setSizes([650, 130])

        self.pages: Dict[str, BasePage] = {}
        self._add_page("Project", ProjectPage())
        self._add_page("MD Setup", MDSetupTab(settings=self.settings))
        self._add_page("MD Run", MDRunTab(settings=self.settings))
        setup_page = self.pages["MD Setup"]
        if isinstance(setup_page, MDSetupTab):
            setup_page.project_prepared.connect(self._on_project_prepared)
            setup_page.setup_changed.connect(self._on_setup_changed)
            setup_page.review_requested.connect(self._review_md_run)
            setup_page.operation_blocked = lambda: self._work_in_progress(exclude="MD Setup")
            setup_page.before_preparation = self._release_project_viewer
            setup_page.inspect_3d_button = QPushButton("Inspect structure in 3D")
            setup_page.inspect_3d_button.clicked.connect(
                lambda: self.open_project_viewer(setup_page.fields["protein"].text(), ""))
            setup_page.layout().insertWidget(1, setup_page.inspect_3d_button)
        run_page = self.pages["MD Run"]
        run_page.operation_blocked = lambda: self._work_in_progress(exclude="MD Run")
        run_page.before_run = self._release_project_viewer
        run_page.production_completed.connect(self._on_production_completed)
        run_page.setup_requested.connect(lambda: self.switch_to_page("MD Setup"))
        run_page.execution_settings_requested.connect(self.open_preferences)
        analysis_page = AnalysisTab(settings=self.settings)
        analysis_page.operation_blocked = lambda: self._work_in_progress(exclude="Analysis")
        analysis_page.viewer_opener = self.open_project_viewer
        self._add_page("Analysis", analysis_page)
        load_page = LoadSystemPage()
        load_page.operation_blocked = self._work_in_progress
        load_page.inputs_ready.connect(self._on_analysis_inputs)
        self._add_page("Load System", load_page)
        self._add_page("Trajectory Preprocessing", PreprocessingPage())
        self._add_page(
            "Structural Stability",
            ChecklistAnalysisPage(
                "Structural Stability",
                "Run RMSD, RMSF, radius of gyration, SASA, and related checks.",
                ["RMSD", "RMSF", "Radius of gyration", "SASA", "Secondary structure", "Ramachandran"],
                "Common bundle: RMSD + RMSF + Rg + SASA for protein stability profiling.",
                ["Protein", "Backbone", "C-alpha", "Protein + ligand", "Custom selection"],
            ),
        )
        self._add_page(
            "Interaction Analysis",
            ChecklistAnalysisPage(
                "Interaction Analysis",
                "Track hydrogen bonds, distances, contacts, and salt bridges over time.",
                ["Hydrogen bonds", "Salt bridges", "Distances", "Contacts", "Minimum distance", "Pairwise distance map"],
                "Suggested for protein-ligand systems: H-bonds + Contacts + Distances.",
                ["Protein vs Ligand", "Binding site vs Ligand", "Chain A vs Chain B", "Custom selection"],
            ),
        )
        self._add_page(
            "Solvent and Ions",
            ChecklistAnalysisPage(
                "Solvent and Ions",
                "Inspect hydration shells, ion distributions, density profiles, and water orientation around the system.",
                ["RDF", "Density profile", "Water orientation", "Ion distribution", "Spatial distribution"],
                "Suggested solvent bundle: RDF + Water orientation for ligand hydration, or RDF + Ion distribution for ion effects.",
                ["Protein", "Ligand", "Binding site", "Custom selection"],
            ),
        )
        self._add_page(
            "Energy Analysis",
            ChecklistAnalysisPage(
                "Energy Analysis",
                "Extract and summarize common EDR terms such as potential, temperature, pressure, and density.",
                ["Potential energy", "Kinetic energy", "Total energy", "Temperature", "Pressure", "Density"],
                "Suggested equilibration bundle: Potential + Temperature + Pressure + Density.",
                ["Full system", "Protein", "Protein + ligand"],
            ),
        )
        self._add_page("End-Point Free Energy", FreeEnergyPage())
        self._add_page("Batch Comparison", BatchComparisonPage())
        self._add_page("Results Viewer", ResultsViewerPage())
        self.pages["Results Viewer"].qc_dashboard.analysis_point_requested.connect(self.open_analysis_point)
        reports_page = ReportsPage(lambda: self.active_project_dir)
        reports_page.operation_blocked = lambda: self._work_in_progress(exclude="Reports & Export")
        self._add_page("Reports & Export", reports_page)
        self._add_page("Settings", SettingsPage(self.settings, self.open_preferences))

        self.nav.setCurrentRow(0)

    def _build_menu(self):
        menu = self.menuBar()

        file_menu = menu.addMenu("File")
        new_action = QAction("New project", self)
        new_action.setShortcut(QKeySequence.New)
        new_action.triggered.connect(self.new_session)
        file_menu.addAction(new_action)
        open_action = QAction("Open project (.mds)", self)
        open_action.setShortcut(QKeySequence.Open)
        open_action.triggered.connect(self.load_session)
        file_menu.addAction(open_action)
        save_action = QAction("Save project", self)
        save_action.setShortcut(QKeySequence.Save)
        save_action.triggered.connect(self.save_session)
        file_menu.addAction(save_action)
        file_menu.addSeparator()
        preferences_action = QAction("Preferences", self)
        preferences_action.triggered.connect(self.open_preferences)
        file_menu.addAction(preferences_action)

        analysis_menu = menu.addMenu("Workflow")
        for name in ["MD Setup", "MD Run", "Load System", "Analysis", "Results Viewer", "Reports & Export"]:
            action = QAction(name, self)
            action.triggered.connect(lambda checked=False, page=name: self.switch_to_page(page))
            analysis_menu.addAction(action)

        view_menu = menu.addMenu("View")
        viewer_action = QAction("3D structures and trajectories", self)
        viewer_action.triggered.connect(lambda: self._ensure_project_viewer().show())
        view_menu.addAction(viewer_action)
        for title, attribute in (("Workflow guidance", "side_info"), ("Run log", "log_group")):
            action = QAction(title, self)
            action.setCheckable(True)
            action.setChecked(True)
            action.toggled.connect(lambda visible, name=attribute: getattr(self, name).setVisible(visible))
            view_menu.addAction(action)

        help_menu = menu.addMenu("Help")
        docs_action = QAction("Quick start", self)
        docs_action.triggered.connect(self.open_help)
        help_menu.addAction(docs_action)
        manual_action = QAction("User manual", self)
        manual_action.setShortcut(QKeySequence.HelpContents)
        manual_action.triggered.connect(self.open_user_manual)
        help_menu.addAction(manual_action)

    def _build_toolbar(self):
        toolbar = QToolBar("Main toolbar")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        new_btn = QPushButton("New project")
        new_btn.clicked.connect(self.new_session)
        toolbar.addWidget(new_btn)

        save_btn = QPushButton("Save project")
        save_btn.clicked.connect(self.save_session)
        toolbar.addWidget(save_btn)

        load_btn = QPushButton("Open project")
        load_btn.clicked.connect(self.load_session)
        toolbar.addWidget(load_btn)

        toolbar.addSeparator()

        preview_btn = QPushButton("Review configuration")
        preview_btn.clicked.connect(self.preview_current_page)
        toolbar.addWidget(preview_btn)

        self.run_btn = QPushButton("Run unavailable")
        self.run_btn.setEnabled(False)
        self.run_btn.clicked.connect(self._run_current_page)
        toolbar.addWidget(self.run_btn)

        prefs_btn = QPushButton("Preferences")
        prefs_btn.clicked.connect(self.open_preferences)
        toolbar.addWidget(prefs_btn)

    def _add_page(self, name: str, page: BasePage):
        page.request_log.connect(self.append_log)
        page.request_preview.connect(self.show_preview)
        page.request_qc_files.connect(self.load_qc_dashboard_files)
        if name in {"Trajectory Preprocessing", "Structural Stability", "Interaction Analysis", "Solvent and Ions", "Energy Analysis", "End-Point Free Energy", "Batch Comparison"}:
            note = QLabel("Configuration preview only. These options are saved with the project; this page does not execute calculations. Use Analysis for available backends.")
            note.setObjectName("CapabilityNote")
            note.setWordWrap(True)
            page.body_layout.insertWidget(0, note)
        wrapper = QScrollArea()
        wrapper.setWidgetResizable(True)
        wrapper.setFrameShape(QFrame.NoFrame)
        wrapper.setWidget(page)
        self.stack.addWidget(wrapper)
        self.pages[name] = page

    def _switch_page(self, index: int):
        if index < 0:
            return
        self.stack.setCurrentIndex(index)
        name = NAV_ITEMS[index]
        can_start_md = name == "MD Run"
        self.run_btn.setText("Start MD run" if can_start_md else "Run unavailable")
        self.run_btn.setEnabled(can_start_md)
        self.run_btn.setVisible(can_start_md)
        if name == "MD Run":
            run_page = self.pages.get("MD Run")
            if isinstance(run_page, MDRunTab):
                setup_page = self.pages.get("MD Setup")
                setup_project = (
                    setup_page.fields["project_dir"].text()
                    if isinstance(setup_page, MDSetupTab)
                    else ""
                )
                project_dir = (
                    str(self.active_project_dir)
                    if self.active_project_dir is not None
                    else "" if self._project_context_was_cleared else setup_project
                )
                run_page.load_project(project_dir)
                self._sync_setup_review()
        self.side_info.setPlainText(self.context_text(name))

    def _on_setup_changed(self, snapshot: object) -> None:
        run_page = self.pages.get("MD Run")
        if isinstance(run_page, MDRunTab):
            run_page.set_setup_state(snapshot)
            if self.nav.currentRow() == NAV_ITEMS.index("MD Run"):
                self.run_btn.setEnabled(not run_page.review_panel.pending)

    def _sync_setup_review(self) -> None:
        setup_page = self.pages.get("MD Setup")
        if isinstance(setup_page, MDSetupTab):
            self._on_setup_changed(setup_page.review_snapshot())

    def _review_md_run(self) -> None:
        self.switch_to_page("MD Run")
        run_page = self.pages.get("MD Run")
        if isinstance(run_page, MDRunTab):
            run_page.open_review()

    def switch_to_page(self, name: str):
        try:
            self.nav.setCurrentRow(NAV_ITEMS.index(name))
        except ValueError:
            self.append_log(f"Page not found: {name}")

    def open_preferences(self):
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec_():
            page = self.pages.get("Settings")
            if isinstance(page, SettingsPage):
                page.refresh()
            self.append_log("Preferences updated.")

    def open_help(self):
        HelpDialog(self).exec_()

    def open_user_manual(self):
        path = Path(__file__).resolve().parent / "assets" / "docs" / "USER_MANUAL.md"
        if not path.is_file() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.append_log(f"Could not open the manual. Check this file: {path}")

    def context_text(self, name: str) -> str:
        texts = {
            "MD Setup": "Prepare proteins, ligands, force fields, solvent, ions, and MDP files before launching a run.",
            "MD Run": "Run the selected GROMACS build through minimization, NVT, NPT, and production MD. "
            "Stage outputs, checkpoints, logs, and parsed thermodynamic traces are persisted in the project folder.",
            "Analysis": "Load matching topology and trajectory files, then run supported structural analyses or QC. MM-PBSA/MM-GBSA and CPPTRAJ require their external tools. Next: inspect real outputs in Results Viewer.",
            "Project": "Start here to name and save a workspace. Next: MD Setup for a new simulation, or Load System for existing files. Planning presets do not change protocols automatically.",
            "Load System": "Recommended minimum: TPR + processed trajectory. Add EDR for energy plots and NDX for custom groups.",
            "Trajectory Preprocessing": "Typical standard: center the system, remove PBC jumps, and fit the protein backbone before analysis.",
            "Structural Stability": "Common bundle: RMSD + RMSF + radius of gyration + SASA. This is often the first quality-control step.",
            "Interaction Analysis": "Useful for protein-ligand systems: hydrogen bonds, contacts, and distances should often be run together.",
            "Solvent and Ions": "Best used when solvent participation matters: hydration shells, binding-site waters, or ion effects near the receptor.",
            "Energy Analysis": "Good default bundle: potential, temperature, pressure, and density from the equilibrated region.",
            "End-Point Free Energy": "Configure a sampling plan. Endpoint energies depend on protocol and sampling; frame count alone does not establish convergence or affinity.",
            "Batch Comparison": "Use the same time window and frame interval when comparing ligands or replicas.",
            "Results Viewer": "Load real XVG or CSV time series in the QC dashboard. Numeric stability flags are screening aids; inspect sampling and trajectory quality before scientific interpretation.",
            "Reports & Export": "Export an evidence bundle from the active prepared project. Keep original trajectories and document their provenance; the bundle does not establish convergence or physical validity.",
            "Settings": "Configure the GROMACS executable, Conda environment, CPU cores, and GPU mode used by the MD pipeline.",
        }
        return f"{name}\n\n{texts.get(name, '')}"

    def current_page(self) -> BasePage:
        return self.pages[NAV_ITEMS[self.nav.currentRow()]]

    def append_log(self, text: str):
        self.log.append(text)
        self.statusBar().showMessage(text, 5000)

    def show_preview(self, text: str):
        QMessageBox.information(self, "Preview", text)

    def load_qc_dashboard_files(self, paths: object):
        file_paths = tuple(str(path) for path in paths)
        page = self.pages.get("Results Viewer")
        if isinstance(page, ResultsViewerPage):
            page.load_qc_files(file_paths)
            self.switch_to_page("Results Viewer")
            self.append_log(f"Loaded {len(file_paths)} QC files into the dashboard.")

    def open_analysis_point(self, source: str, row_index: int):
        self._pause_project_viewer()
        if self._work_in_progress():
            message = "Point not opened: wait for the current project or 3D operation, then select it again."
            self.pages["Results Viewer"].qc_dashboard.link_status.setText(message)
            self.append_log(message)
            return
        self._ensure_project_viewer().inspect_analysis_point(source, row_index)

    def preview_current_page(self):
        self.show_preview(self.current_page().preview_text())

    def _run_current_page(self) -> None:
        if NAV_ITEMS[self.nav.currentRow()] != "MD Run":
            return
        run_page = self.pages.get("MD Run")
        if isinstance(run_page, MDRunTab):
            run_page.start_run()

    def session_data(self) -> Dict:
        data = {
            "app": APP_NAME,
            "version": APP_VERSION,
            "inventor": APP_INVENTOR,
            "current_page": self.nav.currentRow(),
            "pages": {name: page.get_state() for name, page in self.pages.items()},
            "project_context": self._serialized_project_context(),
            "viewer_state": self.viewer_dock.get_state() if self.viewer_dock is not None else None,
        }
        return data

    def _serialized_project_context(self) -> dict[str, str] | None:
        if self.active_project_dir is None:
            return None
        manifest_file = self.active_manifest_path or (
            self.active_project_dir / "moldynstudio_run.json"
        )
        try:
            manifest_value = str(manifest_file.relative_to(self.active_project_dir))
        except ValueError:
            manifest_value = str(manifest_file)
        return {
            "project_dir": str(self.active_project_dir),
            "manifest_path": manifest_value.replace("\\", "/"),
        }

    def _on_project_prepared(self, project_dir: str, manifest_path: str) -> None:
        run_page = self.pages.get("MD Run")
        if self._work_in_progress(exclude="MD Setup"):
            self.append_log(
                "Prepared project handoff ignored while the current MD run is active."
            )
            return
        project = Path(project_dir).expanduser().resolve()
        manifest = Path(manifest_path).expanduser()
        if not manifest.is_absolute():
            manifest = project / manifest
        self.active_project_dir = project
        self.active_manifest_path = manifest.resolve()
        self._project_context_was_cleared = False
        if isinstance(run_page, MDRunTab):
            run_page.load_project(str(project))
        self._sync_setup_review()
        self.append_log(f"Prepared MD project loaded: {project}")

    def _work_in_progress(self, exclude: str = "") -> bool:
        return bool(exclude != "3D Viewer" and self.viewer_dock is not None and self.viewer_dock.is_busy()) or any(
            bool(page.is_busy())
            for name, page in self.pages.items()
            if name != exclude and callable(getattr(page, "is_busy", None))
        )

    def _on_analysis_inputs(self, inputs: object, exclude: str = "") -> bool:
        if not isinstance(inputs, Mapping):
            return False
        page = self.pages.get("Analysis")
        if page is None or self._work_in_progress(exclude=exclude):
            self.append_log("Analysis inputs were not changed while another operation is active.")
            return False
        page.set_input_files(*(str(inputs.get(key, "")) for key in ("topology", "trajectory", "energy")))
        if inputs.get("gromacs_binary") and callable(getattr(page, "set_backend", None)):
            page.set_backend(str(inputs.get("gromacs_binary", "")), str(inputs.get("conda_environment", "")))
        page.fields["qc_output"].setText("")
        self.append_log("Analysis inputs loaded. Review selections before starting an analysis.")
        if self.viewer_dock is not None:
            self.viewer_dock.offer_sources(str(inputs.get("topology", "")), str(inputs.get("trajectory", "")))
        return True

    def _on_production_completed(self, inputs: object) -> None:
        if not isinstance(inputs, Mapping):
            return
        if not self._on_analysis_inputs(inputs, exclude="MD Run"):
            return
        load_page = self.pages.get("Load System")
        if isinstance(load_page, LoadSystemPage):
            for source, target in (("topology", "tpr_file"), ("trajectory", "trajectory_file"), ("energy", "energy_file")):
                load_page.fields[target].setText(str(inputs.get(source, "")))
        self.append_log("Production outputs are ready in Analysis. Next: inspect the trajectory and generate QC results.")
    def _ensure_project_viewer(self) -> ProjectViewerDock:
        if self.viewer_dock is None:
            self.viewer_dock = ProjectViewerDock(self, self.settings,
                                               lambda: self._work_in_progress(exclude="3D Viewer"))
            self.addDockWidget(Qt.BottomDockWidgetArea, self.viewer_dock)
        return self.viewer_dock

    def open_project_viewer(self, topology: str, trajectory: str = "") -> None:
        if self._work_in_progress():
            self.append_log("Pause playback or wait for the current project operation before opening the 3D view.")
            return
        self._ensure_project_viewer().show_sources(topology, trajectory)

    def _release_project_viewer(self) -> bool:
        return self.viewer_dock is None or self.viewer_dock.release_sources()

    def _pause_project_viewer(self) -> None:
        if self.viewer_dock is not None:
            self.viewer_dock.viewer.pause()

    def _clear_project_context(self) -> None:
        self.active_project_dir = None
        self.active_manifest_path = None
        self._project_context_was_cleared = True
        run_page = self.pages.get("MD Run")
        if isinstance(run_page, MDRunTab):
            run_page.load_project("")

    def _restore_project_context(
        self,
        context: object,
        session_path: Path | None,
    ) -> None:
        resolved = self._resolve_project_context(context, session_path)
        if resolved is None:
            self._clear_project_context()
            return
        self._on_project_prepared(*(str(path) for path in resolved))

    def _resolve_project_context(self, context: object, session_path: Path | None):
        if not isinstance(context, Mapping):
            return None
        project_text = str(context.get("project_dir", "")).strip()
        if not project_text:
            return None
        if not sys.platform.startswith("win") and is_windows_absolute_path(
            project_text
        ):
            raise ProjectFormatError(
                "This project references a Windows absolute path that cannot be "
                "opened on this platform. Move the .mds file into the prepared "
                "project directory on Windows, save it again, and transfer both "
                "together."
            )
        project = Path(project_text).expanduser()
        if not project.is_absolute():
            base = session_path.parent if session_path is not None else Path.cwd()
            project = base / Path(project_text.replace("\\", "/"))
        project = project.resolve()
        manifest_text = str(context.get("manifest_path", "")).strip()
        manifest_reference = manifest_text or "moldynstudio_run.json"
        if not sys.platform.startswith("win") and is_windows_absolute_path(
            manifest_reference
        ):
            raise ProjectFormatError(
                "This project manifest uses a Windows absolute path that cannot "
                "be opened on this platform. Save the session with portable "
                "relative paths before transferring it."
            )
        manifest = Path(manifest_reference).expanduser()
        if not manifest.is_absolute():
            manifest = project / Path(manifest_reference.replace("\\", "/"))
        return project, manifest

    def _validated_page_states(self, data: object) -> dict:
        if not isinstance(data, Mapping) or not isinstance(data.get("pages", {}), Mapping):
            raise ProjectFormatError("Project pages must be a JSON object.")
        states = deepcopy(self._initial_page_states)
        for name, state in data.get("pages", {}).items():
            if name not in states:
                continue
            if not isinstance(state, Mapping):
                raise ProjectFormatError(f"Invalid page state: {name}.")
            for key, value in state.items():
                if key not in states[name]:
                    continue
                expected = states[name][key]
                valid = isinstance(value, type(expected))
                if type(expected) is float:
                    valid = type(value) in (int, float)
                if key == "runs_table":
                    valid = isinstance(value, list) and all(isinstance(row, list) and len(row) == 6 and all(isinstance(cell, str) for cell in row) for row in value)
                if not valid:
                    raise ProjectFormatError(f"Invalid value for {name}: {key}.")
                states[name][key] = deepcopy(value)
        return states

    def _clear_session_results(self):
        if self.viewer_dock is not None:
            self.viewer_dock.clear_sources()
        self.pages["Results Viewer"].qc_dashboard.clear()
        analysis = self.pages["Analysis"]
        if callable(getattr(analysis, "reset_session", None)):
            analysis.reset_session()
        else:
            analysis.set_input_files("", "", "")
            analysis.results.clear()
        self.pages["Load System"].summary.setPlainText("Select matching topology and trajectory files, then validate the inputs.")
        self.pages["Reports & Export"].status.setText("Prepare or reopen an MD project to export its evidence.")

    def apply_session_data(
        self,
        data: Dict[str, Any],
        *,
        session_path: str | Path | None = None,
    ):
        self._pause_project_viewer()
        if self._work_in_progress():
            self.append_log("Stop or finish the current operation before opening another project.")
            return
        states = self._validated_page_states(data)
        viewer_state = ProjectViewerDock.validate_state(data.get("viewer_state"))
        try:
            current_page = int(data.get("current_page", 0))
        except (TypeError, ValueError) as exc:
            raise ProjectFormatError("Invalid current page in the project file.") from exc
        source = Path(session_path) if session_path is not None else None
        self._resolve_project_context(data.get("project_context"), source)
        self._clear_session_results()
        for name, state in states.items():
            self.pages[name].set_state(state)
        if "project_context" in data:
            self._restore_project_context(data.get("project_context"), source)
        else:
            self.active_project_dir = None
            self.active_manifest_path = None
            self._project_context_was_cleared = False
            run_page = self.pages.get("MD Run")
            if isinstance(run_page, MDRunTab):
                run_page.load_project("")
        self.nav.setCurrentRow(max(0, min(current_page, len(NAV_ITEMS) - 1)))
        if viewer_state:
            self._ensure_project_viewer().set_state(viewer_state)
        self._sync_setup_review()

    def new_session(self):
        self._pause_project_viewer()
        if self._work_in_progress():
            self.append_log("Stop or finish the current operation before starting a new project.")
            return
        self._clear_session_results()
        for name, page in self.pages.items():
            page.set_state(deepcopy(self._initial_page_states[name]))
        self.log.clear()
        self._clear_project_context()
        self._sync_setup_review()
        self.current_session_path = None
        self.nav.setCurrentRow(0)
        self.append_log("Started a new MolDynStudio project.")

    def save_session(self):
        default_path = str(self.current_session_path or (Path.home() / "MolDynStudio" / "moldynstudio_project.mds"))
        path, _ = QFileDialog.getSaveFileName(self, "Save project", default_path, "MolDynStudio Project (*.mds);;JSON (*.json)")
        if not path:
            return
        try:
            out_path = self.project_manager.save(path, self.session_data())
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Save failed", f"Could not save the project. Choose a writable location.\n{exc}")
            return
        self.current_session_path = out_path
        self.append_log(f"Project saved to: {out_path}")

    def load_session(self):
        self._pause_project_viewer()
        if self._work_in_progress():
            self.append_log("Stop or finish the current operation before opening another project.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open project", str(Path.home()), "MolDynStudio Project (*.mds);;JSON (*.json)")
        if not path:
            return
        in_path = Path(path)
        try:
            if in_path.suffix.lower() == ".mds":
                data = self.project_manager.load(in_path)
            else:
                data = json.loads(in_path.read_text(encoding="utf-8"))
            self.apply_session_data(data, session_path=in_path)
            self.current_session_path = in_path
            self.append_log(f"Project loaded from: {in_path}")
        except (ProjectFormatError, json.JSONDecodeError, OSError, ValueError) as exc:
            QMessageBox.critical(self, "Load error", f"Could not load session:\n{exc}")

    def autosave_session(self):
        try:
            target = self.current_session_path or (Path.home() / "MolDynStudio" / "autosave.mds")
            self.project_manager.save(target, self.session_data())
            self.current_session_path = Path(target)
            self.statusBar().showMessage(f"Auto-saved project to {target}", 5000)
        except Exception as exc:
            self.statusBar().showMessage(f"Auto-save failed: {exc}", 5000)

    def closeEvent(self, event):
        self._pause_project_viewer()
        if self._work_in_progress():
            self.append_log("An operation is still active. Stop it or wait for completion before closing MolDynStudio.")
            event.ignore()
            return
        self._autosave_timer.stop()
        if self.viewer_dock is not None:
            self.viewer_dock.clear_sources()
        event.accept()

def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    window = MainWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
