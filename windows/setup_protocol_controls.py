"""Essential and advanced protocol controls shared by the MD Setup page."""

from PyQt5.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from utils.tooltips import tooltip


def build_protocol_controls(page, parent: QVBoxLayout) -> None:
    group = QGroupBox("Simulation protocol")
    layout = QVBoxLayout(group)
    essentials = QFormLayout()
    _build_essentials(page, essentials)
    layout.addLayout(essentials)

    page.protocol_advanced_button = QPushButton("Advanced protocol parameters")
    page.protocol_advanced_button.setCheckable(True)
    page.protocol_advanced_button.setAccessibleName("Show advanced protocol parameters")
    layout.addWidget(page.protocol_advanced_button)
    page.protocol_advanced = QWidget()
    _build_advanced(page, QFormLayout(page.protocol_advanced))
    page.protocol_advanced.setVisible(False)
    page.protocol_advanced_button.toggled.connect(page.protocol_advanced.setVisible)
    layout.addWidget(page.protocol_advanced)
    _build_actions(page, layout)
    parent.addWidget(group)


def _build_essentials(page, form: QFormLayout) -> None:
    page.fields["duration"] = QDoubleSpinBox()
    page.fields["duration"].setDecimals(3)
    page.fields["duration"].setRange(0.001, 1_000_000)
    page.fields["duration"].setValue(100.0)
    page.fields["duration"].setToolTip(tooltip("duration"))
    form.addRow("Production duration (ns)", page.fields["duration"])

    page.fields["temperature"] = QDoubleSpinBox()
    page.fields["temperature"].setRange(1.0, 1000.0)
    page.fields["temperature"].setValue(300.0)
    form.addRow("Temperature (K)", page.fields["temperature"])

    page.fields["pressure"] = QDoubleSpinBox()
    page.fields["pressure"].setDecimals(3)
    page.fields["pressure"].setRange(0.001, 1000.0)
    page.fields["pressure"].setValue(1.0)
    form.addRow("Pressure (bar)", page.fields["pressure"])


def _build_advanced(page, form: QFormLayout) -> None:
    page.fields["timestep"] = QDoubleSpinBox()
    page.fields["timestep"].setDecimals(3)
    page.fields["timestep"].setRange(0.001, 100.0)
    page.fields["timestep"].setValue(2.0)
    page.fields["timestep"].setToolTip(tooltip("timestep"))
    form.addRow("Timestep (fs)", page.fields["timestep"])

    page.fields["save_every"] = QDoubleSpinBox()
    page.fields["save_every"].setDecimals(3)
    page.fields["save_every"].setRange(0.001, 1_000_000.0)
    page.fields["save_every"].setValue(10.0)
    page.fields["save_every"].setToolTip(tooltip("save_every"))
    form.addRow("Save every (ps)", page.fields["save_every"])

    for key, label in (("nvt_steps", "NVT steps"), ("npt_steps", "NPT steps")):
        page.fields[key] = QSpinBox()
        page.fields[key].setRange(1, 1_000_000_000)
        page.fields[key].setValue(50000)
        form.addRow(label, page.fields[key])


def _build_actions(page, layout: QVBoxLayout) -> None:
    page.short_test_button = QPushButton("Short pipeline test: 2 ps per MD stage")
    page.short_test_button.setToolTip(
        "Set 1 fs timestep, 2 ps each for NVT/NPT/production, and 0.2 ps production output. "
        "A smoke test checks execution; it does not establish equilibration."
    )
    page.short_test_button.clicked.connect(page._use_short_pipeline_test)
    layout.addWidget(page.short_test_button)
    note = QLabel("Short tests check execution only. Review protocol parameters before a research run.")
    note.setWordWrap(True)
    layout.addWidget(note)
    row = QHBoxLayout()
    save = QPushButton("Save .MDP Files")
    save.clicked.connect(page.save_mdp_files)
    row.addWidget(save)
    edit = QPushButton("Preview production MDP")
    edit.clicked.connect(page.open_mdp_editor)
    row.addWidget(edit)
    row.addStretch(1)
    layout.addLayout(row)
