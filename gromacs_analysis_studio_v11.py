#!/usr/bin/env python3
"""
GROMACS Analysis Studio v1.1 Prototype
--------------------------------------
Runnable PyQt5 prototype for testing the interface blueprint.
This version focuses on navigation, session persistence, command preview,
and mock execution flow for a future full analysis app.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QIcon
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
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from core.project_manager import ProjectFormatError, ProjectManager
from core.settings import SettingsStore
from settings_dialog import SettingsDialog
from tabs.analysis_tab import AnalysisTab
from tabs.md_run_tab import MDRunTab
from tabs.md_setup_tab import MDSetupTab
from tabs.qc_dashboard_tab import QCDashboardWidget
from windows.help_dialog import HelpDialog

try:
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure
    MATPLOTLIB_OK = True
except Exception:
    MATPLOTLIB_OK = False
    FigureCanvas = object  # type: ignore[misc,assignment]
    Figure = object  # type: ignore[misc,assignment]


APP_NAME = "MolDynStudio"
APP_VERSION = "v1.0.1"
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
        layout.addWidget(self.line_edit)
        self.button = QPushButton("Browse")
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
        layout.addWidget(self.line_edit)
        self.button = QPushButton("Browse")
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


class PlotPanel(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        if MATPLOTLIB_OK:
            self.figure = Figure(figsize=(6, 4))
            self.canvas = FigureCanvas(self.figure)
            layout.addWidget(self.canvas)
            self.draw_demo()
        else:
            label = QLabel("Matplotlib is not available. Install matplotlib to preview charts here.")
            label.setWordWrap(True)
            layout.addWidget(label)

    def draw_demo(self):
        if not MATPLOTLIB_OK:
            return
        ax = self.figure.add_subplot(111)
        x = list(range(10))
        y = [0.30, 0.36, 0.32, 0.28, 0.27, 0.31, 0.29, 0.26, 0.25, 0.24]
        ax.clear()
        ax.plot(x, y, linewidth=2)
        ax.set_title("Demo RMSD preview")
        ax.set_xlabel("Frame index")
        ax.set_ylabel("RMSD (nm)")
        ax.grid(True, alpha=0.3)
        self.figure.tight_layout()
        self.canvas.draw_idle()


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
        return f"[{self.title}] No preview text implemented yet."

    def action_bar(self, include_preview: bool = True) -> QWidget:
        frame = QFrame()
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch(1)
        if include_preview:
            preview = QPushButton("Preview")
            preview.clicked.connect(lambda: self.request_preview.emit(self.preview_text()))
            layout.addWidget(preview)
        run = QPushButton("Run This Module")
        run.clicked.connect(lambda: self.request_log.emit(f"Queued module: {self.title}"))
        layout.addWidget(run)
        return frame


class ProjectPage(BasePage):
    def __init__(self):
        super().__init__(
            "Project",
            "Create a new analysis session, define the project type, and choose the default operating mode.",
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
        self.fields["project_type"].addItems(["Protein", "Proteinâ€“Ligand"])
        form.addRow("Project type", self.fields["project_type"])

        self.fields["force_field_family"] = QComboBox()
        self.fields["force_field_family"].addItems(["CHARMM", "AMBER", "OPLS", "Other"])
        form.addRow("Force field family", self.fields["force_field_family"])

        self.fields["analysis_mode"] = QComboBox()
        self.fields["analysis_mode"].addItems(["Simple", "Advanced"])
        form.addRow("Analysis mode", self.fields["analysis_mode"])

        bundle_group, bundle_layout = self.add_group("Quick Start Templates")
        self.fields["template"] = QComboBox()
        self.fields["template"].addItems([
            "Protein Quick Stability",
            "Protein Full Stability",
            "Proteinâ€“Ligand Binding Check",
            "Proteinâ€“Ligand Quick MM/GBSA",
            "Proteinâ€“Ligand Refined MM/PBSA",
            "Solvation Around Ligand",
            "Ligand Batch Ranking",
        ])
        bundle_layout.addWidget(self.fields["template"])
        hint = QLabel(
            "Typical default: Protein projects start with preprocessing + structural stability + energy. "
            "Proteinâ€“ligand projects usually add interaction analysis and optional quick MM/GBSA."
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
    def __init__(self):
        super().__init__(
            "Load System",
            "Load the main GROMACS files. TPR is required; trajectory and energy files are strongly recommended.",
        )
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
            "Typical proteinâ€“ligand setup:\n"
            "- TPR\n- Processed XTC\n- EDR\n- Optional NDX\n"
        )
        summary_layout.addWidget(self.summary)

        button_row = QHBoxLayout()
        validate_btn = QPushButton("Validate Inputs")
        validate_btn.clicked.connect(self.validate_inputs)
        button_row.addWidget(validate_btn)
        preview_btn = QPushButton("Preview Groups")
        preview_btn.clicked.connect(lambda: self.request_log.emit("Group preview is a placeholder in the prototype."))
        button_row.addWidget(preview_btn)
        summary_layout.addLayout(button_row)

        self.body_layout.addWidget(self.action_bar())

    def validate_inputs(self):
        messages: List[str] = []
        tpr = self.fields["tpr_file"].text()
        traj = self.fields["trajectory_file"].text()
        edr = self.fields["energy_file"].text()
        ndx = self.fields["index_file"].text()
        ligand = self.fields["ligand_name"].text().strip()

        messages.append(f"TPR: {'OK' if tpr else 'Missing'}")
        messages.append(f"Trajectory: {'OK' if traj else 'Missing'}")
        messages.append(f"Energy: {'OK' if edr else 'Missing'}")
        messages.append(f"Index: {'OK' if ndx else 'Optional / not loaded'}")
        messages.append(f"Ligand hint: {ligand if ligand else 'Not provided'}")
        messages.append("\nRecommendation: load TPR + XTC + EDR before running any analysis bundle.")
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
            "Typical default for proteinâ€“ligand analysis: remove PBC jumps + center molecule + compact unit cell + backbone fit."
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
            "Configure gmx_MMPBSA runs for MM/GBSA and MM/PBSA. Use GB for fast ranking and PB for refinement or cross-checking.",
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
            "Quick = ~100 frames for rapid ranking. Standard = ~200 frames for stronger estimates. "
            "Refined = ~1000 frames for detailed decomposition or final reporting. Keep the same window and interval when comparing ligands."
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
        autodetect_btn = QPushButton("Add demo runs")
        autodetect_btn.clicked.connect(self.add_demo_rows)
        button_row.addWidget(autodetect_btn)
        table_layout.addLayout(button_row)

        metrics_group, metrics_layout = self.add_group("Metrics")
        metrics = [
            "RMSD", "RMSF", "Radius of gyration", "SASA", "H-bond occupancy",
            "Contact frequency", "Distances", "Energy terms", "Total Î”G", "Per-residue Î”G"
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
        values = data or [f"Run_{row+1}", "", "Proteinâ€“Ligand", "LIG", "Ready"]
        for col, value in enumerate(values, start=1):
            self.runs_table.setItem(row, col, QTableWidgetItem(value))

    def add_demo_rows(self):
        demos = [
            ["Ligand_A", "/projects/ligA", "Proteinâ€“Ligand", "LGA", "Ready"],
            ["Ligand_B", "/projects/ligB", "Proteinâ€“Ligand", "LGB", "Ready"],
            ["Ligand_C", "/projects/ligC", "Proteinâ€“Ligand", "LGC", "Missing EDR"],
        ]
        for row in demos:
            self.add_row(row)
        self.request_log.emit("Demo comparison rows added.")

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
            "Inspect plots, tables, residue maps, and a compact dashboard. This is a prototype viewer with demo content.",
        )
        self.tabs = QTabWidget()
        self.body_layout.addWidget(self.tabs, 1)

        plots_tab = QWidget()
        plots_layout = QVBoxLayout(plots_tab)
        plots_layout.addWidget(PlotPanel())
        self.tabs.addTab(plots_tab, "Plots")

        tables_tab = QWidget()
        tables_layout = QVBoxLayout(tables_tab)
        table = QTableWidget(5, 3)
        table.setHorizontalHeaderLabels(["Metric", "Average", "Std. Dev."])
        rows = [
            ("RMSD", "0.28", "0.04"),
            ("Rg", "2.13", "0.03"),
            ("SASA", "145.2", "4.1"),
            ("H-bonds", "3.8", "1.1"),
            ("Î”G", "-32.5", "2.8"),
        ]
        for i, row in enumerate(rows):
            for j, value in enumerate(row):
                table.setItem(i, j, QTableWidgetItem(value))
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        tables_layout.addWidget(table)
        self.tabs.addTab(tables_tab, "Tables")

        residue_tab = QWidget()
        residue_layout = QVBoxLayout(residue_tab)
        residue_layout.addWidget(QLabel("Residue map view placeholder. Future versions can color-code RMSF, contacts, or Î”G decomposition."))
        self.tabs.addTab(residue_tab, "Residue Maps")

        self.dashboard_tab = QWidget()
        dashboard_layout = QVBoxLayout(self.dashboard_tab)
        self.qc_dashboard = QCDashboardWidget()
        dashboard_layout.addWidget(self.qc_dashboard)
        self.tabs.addTab(self.dashboard_tab, "Dashboard")

        self.body_layout.addWidget(self.action_bar())

    def load_qc_files(self, paths: object) -> None:
        file_paths = [str(path) for path in paths]
        if not file_paths:
            return
        self.qc_dashboard.load_files(file_paths)
        dashboard_index = self.tabs.indexOf(self.dashboard_tab)
        if dashboard_index >= 0:
            self.tabs.setCurrentIndex(dashboard_index)


class ReportsPage(BasePage):
    def __init__(self):
        super().__init__(
            "Reports & Export",
            "Create quick reports, full reports, figure packs, or raw data bundles.",
        )
        group, layout = self.add_group("Report options")
        for key, text, checked in [
            ("quick_report", "Quick report", True),
            ("full_report", "Full report", False),
            ("publication_figures", "Publication figure pack", True),
            ("raw_data_bundle", "Raw data bundle", True),
        ]:
            cb = QCheckBox(text)
            cb.setChecked(checked)
            self.fields[key] = cb
            layout.addWidget(cb)

        form = QFormLayout()
        self.fields["export_format"] = QComboBox()
        self.fields["export_format"].addItems(["PDF", "CSV", "XLSX", "PNG", "SVG", "JSON session file"])
        form.addRow("Primary export format", self.fields["export_format"])
        self.fields["export_dir"] = DirSelector("Export directory")
        self.fields["export_dir"].setText(str(Path.home() / "gromacs_analysis_exports"))
        form.addRow("Export directory", self.fields["export_dir"])
        layout.addLayout(form)
        self.body_layout.addWidget(self.action_bar())

    def preview_text(self) -> str:
        selected = [
            widget.text() for widget in self.fields.values()
            if isinstance(widget, QCheckBox) and widget.isChecked()
        ]
        return (
            "[Reports & Export Preview]\n"
            f"Selected outputs: {', '.join(selected) if selected else 'None'}\n"
            f"Primary format: {self.fields['export_format'].currentText()}\n"
            f"Export directory: {self.fields['export_dir'].text()}"
        )


class SettingsPage(BasePage):
    def __init__(self):
        super().__init__(
            "Settings",
            "Configure executable paths and default plotting/export behavior. The interface language is fixed to English in this prototype.",
        )
        group, layout = self.add_group("Executable paths")
        form = QFormLayout()
        layout.addLayout(form)
        self.fields["gmx_path"] = FileSelector("Path to gmx executable", "All Files (*)")
        form.addRow("gmx path", self.fields["gmx_path"])
        self.fields["gmx_mmpbsa_path"] = FileSelector("Path to gmx_MMPBSA executable", "All Files (*)")
        form.addRow("gmx_MMPBSA path", self.fields["gmx_mmpbsa_path"])

        ui_group, ui_layout = self.add_group("Defaults")
        self.fields["theme"] = QComboBox()
        self.fields["theme"].addItems(["Dark", "Light"])
        self.fields["theme"].setCurrentText("Dark")
        ui_layout.addWidget(QLabel("Theme"))
        ui_layout.addWidget(self.fields["theme"])

        self.fields["default_selection"] = QComboBox()
        self.fields["default_selection"].addItems(["Protein", "Backbone", "Protein + ligand"])
        ui_layout.addWidget(QLabel("Default analysis selection"))
        ui_layout.addWidget(self.fields["default_selection"])

        self.body_layout.addWidget(self.action_bar())

    def preview_text(self) -> str:
        return (
            "[Settings Preview]\n"
            f"gmx: {self.fields['gmx_path'].text() or 'Not set'}\n"
            f"gmx_MMPBSA: {self.fields['gmx_mmpbsa_path'].text() or 'Not set'}\n"
            f"Theme: {self.fields['theme'].currentText()}\n"
            f"Default selection: {self.fields['default_selection'].currentText()}"
        )


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} - {APP_VERSION}")
        self.resize(1480, 920)
        self.current_session_path: Optional[Path] = None
        self.project_manager = ProjectManager()
        self.settings = SettingsStore()
        self._progress_value = 0
        self._timer: Optional[QTimer] = None
        self._autosave_timer = QTimer(self)
        self._autosave_timer.timeout.connect(self.autosave_session)

        self._apply_style()
        logo_path = Path(__file__).resolve().parent / "assets" / "logo_256.png"
        if logo_path.exists():
            self.setWindowIcon(QIcon(str(logo_path)))
        self._build_ui()
        self._autosave_timer.start(5 * 60 * 1000)
        self.statusBar().showMessage("MolDynStudio ready. Configure MD setup, run previews, or load an existing project.")

    def _apply_style(self):
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
            }
            QPushButton {
                background: #FFFFFF;
                border: 1px solid #DCDCDC;
                border-radius: 3px;
                padding: 8px 14px;
                font-weight: 700;
                color: #2C2C2C;
            }
            QPushButton:hover { background: #FFF3E0; border-color: #E87722; }
            QPushButton:disabled { color: #BDBDBD; }
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
            QListWidget::item:selected { background: #FFF3E0; border-left: 3px solid #E87722; }
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
        self.nav.setMinimumWidth(230)
        for item in NAV_ITEMS:
            QListWidgetItem(item, self.nav)
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
        self.side_info.setMinimumWidth(320)
        self.side_info.setPlainText(
            "Recommendations\n\n"
            "- Start in MD Setup to generate MDP files and validate inputs\n"
            "- Use MD Run for pipeline monitoring\n"
            "- Use Analysis for RMSD/RMSF/SASA/PCA/MM-PBSA workflows\n"
        )
        content_wrapper.addWidget(self.side_info)
        content_wrapper.setStretchFactor(0, 1)

        log_group = QGroupBox("Run Log")
        log_layout = QVBoxLayout(log_group)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(170)
        log_layout.addWidget(self.log)
        self.progress = QProgressBar()
        self.progress.setValue(0)
        log_layout.addWidget(self.progress)
        main_splitter.addWidget(log_group)
        main_splitter.setStretchFactor(0, 1)

        self.pages: Dict[str, BasePage] = {}
        self._add_page("Project", ProjectPage())
        self._add_page("MD Setup", MDSetupTab())
        self._add_page("MD Run", MDRunTab())
        self._add_page("Analysis", AnalysisTab())
        self._add_page("Load System", LoadSystemPage())
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
                "Suggested for proteinâ€“ligand systems: H-bonds + Contacts + Distances.",
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
        self._add_page("Reports & Export", ReportsPage())
        self._add_page("Settings", SettingsPage())

        self.nav.setCurrentRow(0)

    def _build_menu(self):
        menu = self.menuBar()

        file_menu = menu.addMenu("Arquivo")
        new_action = QAction("Novo Projeto", self)
        new_action.triggered.connect(self.new_session)
        file_menu.addAction(new_action)
        open_action = QAction("Abrir Projeto (.mds)", self)
        open_action.triggered.connect(self.load_session)
        file_menu.addAction(open_action)
        save_action = QAction("Salvar Projeto", self)
        save_action.triggered.connect(self.save_session)
        file_menu.addAction(save_action)
        file_menu.addSeparator()
        preferences_action = QAction("Preferencias", self)
        preferences_action.triggered.connect(self.open_preferences)
        file_menu.addAction(preferences_action)

        analysis_menu = menu.addMenu("Analise")
        for name in ["MD Setup", "MD Run", "Analysis", "End-Point Free Energy", "Results Viewer"]:
            action = QAction(name, self)
            action.triggered.connect(lambda checked=False, page=name: self.switch_to_page(page))
            analysis_menu.addAction(action)

        language_menu = menu.addMenu("Idioma")
        english = QAction("English", self)
        english.setEnabled(False)
        language_menu.addAction(english)
        portuguese = QAction("Portugues", self)
        portuguese.setEnabled(False)
        language_menu.addAction(portuguese)

        help_menu = menu.addMenu("Ajuda")
        docs_action = QAction("Documentacao", self)
        docs_action.triggered.connect(self.open_help)
        help_menu.addAction(docs_action)

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

        preview_btn = QPushButton("Preview current module")
        preview_btn.clicked.connect(self.preview_current_page)
        toolbar.addWidget(preview_btn)

        run_btn = QPushButton("Run selected analyses")
        run_btn.clicked.connect(self.run_mock_bundle)
        toolbar.addWidget(run_btn)

        prefs_btn = QPushButton("Preferences")
        prefs_btn.clicked.connect(self.open_preferences)
        toolbar.addWidget(prefs_btn)

    def _add_page(self, name: str, page: BasePage):
        page.request_log.connect(self.append_log)
        page.request_preview.connect(self.show_preview)
        page.request_qc_files.connect(self.load_qc_dashboard_files)
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
        self.side_info.setPlainText(self.context_text(name))

    def switch_to_page(self, name: str):
        try:
            self.nav.setCurrentRow(NAV_ITEMS.index(name))
        except ValueError:
            self.append_log(f"Page not found: {name}")

    def open_preferences(self):
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec_():
            self.append_log("Preferences updated.")

    def open_help(self):
        HelpDialog(self).exec_()

    def context_text(self, name: str) -> str:
        texts = {
            "MD Setup": "Prepare proteins, ligands, force fields, solvent, ions, and MDP files before launching a run.",
            "MD Run": "Monitor minimization, equilibration, production, logs, and thermodynamic traces.",
            "Analysis": "Run post-MD analyses and open the MM-PBSA/MM-GBSA dialog or CPPTRAJ builder.",
            "Project": "Set the project type first. Proteinâ€“ligand projects unlock the most useful interaction and free-energy workflows.",
            "Load System": "Recommended minimum: TPR + processed trajectory. Add EDR for energy plots and NDX for custom groups.",
            "Trajectory Preprocessing": "Typical standard: center the system, remove PBC jumps, and fit the protein backbone before analysis.",
            "Structural Stability": "Common bundle: RMSD + RMSF + radius of gyration + SASA. This is often the first quality-control step.",
            "Interaction Analysis": "Useful for proteinâ€“ligand systems: hydrogen bonds, contacts, and distances should often be run together.",
            "Solvent and Ions": "Best used when solvent participation matters: hydration shells, binding-site waters, or ion effects near the receptor.",
            "Energy Analysis": "Good default bundle: potential, temperature, pressure, and density from the equilibrated region.",
            "End-Point Free Energy": "Suggested default: MM/GBSA for quick ranking. Use MM/PBSA later for refinement or a cross-check.",
            "Batch Comparison": "Use the same time window and frame interval when comparing ligands or replicas.",
            "Results Viewer": "This prototype viewer contains demo content. Later versions can show real outputs from XVG/CSV/MMPBSA files.",
            "Reports & Export": "Quick report is the safest default. Full report should combine stability, interactions, energy, and free-energy summaries.",
            "Settings": "Keep executable paths here. This prototype does not call external tools yet, but the fields are ready for the next stage.",
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

    def preview_current_page(self):
        self.show_preview(self.current_page().preview_text())

    def session_data(self) -> Dict:
        data = {
            "app": APP_NAME,
            "version": APP_VERSION,
            "inventor": APP_INVENTOR,
            "current_page": self.nav.currentRow(),
            "pages": {name: page.get_state() for name, page in self.pages.items()},
        }
        return data

    def apply_session_data(self, data: Dict):
        pages = data.get("pages", {})
        for name, state in pages.items():
            page = self.pages.get(name)
            if page is not None:
                page.set_state(state)
        current_page = int(data.get("current_page", 0))
        self.nav.setCurrentRow(max(0, min(current_page, len(NAV_ITEMS) - 1)))

    def new_session(self):
        for page in self.pages.values():
            page.set_state({})
        self.log.clear()
        self.progress.setValue(0)
        self.current_session_path = None
        self.nav.setCurrentRow(0)
        self.append_log("Started a new MolDynStudio project.")

    def save_session(self):
        default_path = str(self.current_session_path or (Path.home() / "MolDynStudio" / "moldynstudio_project.mds"))
        path, _ = QFileDialog.getSaveFileName(self, "Save project", default_path, "MolDynStudio Project (*.mds);;JSON (*.json)")
        if not path:
            return
        out_path = self.project_manager.save(path, self.session_data())
        self.current_session_path = out_path
        self.append_log(f"Project saved to: {out_path}")

    def load_session(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open project", str(Path.home()), "MolDynStudio Project (*.mds);;JSON (*.json)")
        if not path:
            return
        in_path = Path(path)
        try:
            if in_path.suffix.lower() == ".mds":
                data = self.project_manager.load(in_path)
            else:
                data = json.loads(in_path.read_text(encoding="utf-8"))
            self.apply_session_data(data)
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

    def run_mock_bundle(self):
        self.append_log("Preparing a mock run for the currently selected analyses...")
        self.progress.setValue(0)
        self._progress_value = 0
        if self._timer is not None:
            self._timer.stop()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance_progress)
        self._timer.start(120)

    def _advance_progress(self):
        self._progress_value += 8
        self.progress.setValue(self._progress_value)
        if self._progress_value in {16, 40, 72}:
            self.append_log(f"Mock execution step reached {self._progress_value}%.")
        if self._progress_value >= 100:
            if self._timer is not None:
                self._timer.stop()
            self.progress.setValue(100)
            self.append_log(
                "Prototype run completed. No external commands were executed; this build is for UI and session testing."
            )


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    window = MainWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
