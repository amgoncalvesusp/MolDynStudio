"""Explicit chemistry review and cloud consent for CHARMM ligand setup."""

from pathlib import Path
import re

from PyQt5.QtWidgets import QCheckBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout, QWidget


def build_charmm_controls(fields: dict) -> QWidget:
    panel = QWidget()
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(0, 0, 0, 0)
    explanation = QLabel(
        "CHARMM36m ligands use SwissParam MATCH (CGenFF-derived). "
        "Supply one MOL2 molecule with explicit hydrogens and reviewed protonation. "
        "The expected net charge must match the returned parameters."
    )
    explanation.setWordWrap(True)
    layout.addWidget(explanation)
    fields["ligand_from_complex"] = QCheckBox("Use all ligand poses from the protein/complex PDB")
    fields["ligand_from_complex"].setToolTip(
        "The MOL2 supplies chemistry and atom names; each matching PDB residue supplies "
        "its own heavy-atom coordinates. Originals are preserved."
    )
    layout.addWidget(fields["ligand_from_complex"])
    form = QFormLayout()
    fields["source_residue_name"] = QLineEdit()
    fields["source_residue_name"].setPlaceholderText("e.g. TWB")
    fields["source_residue_name"].setMaxLength(3)
    form.addRow("Ligand residue name", fields["source_residue_name"])
    fields["excluded_residue_names"] = QLineEdit()
    fields["excluded_residue_names"].setPlaceholderText("Explicit exclusions, e.g. GOL, EDO")
    fields["excluded_residue_names"].setToolTip(
        "Only listed extra compounds are removed from the derived protein input. "
        "Unrecognized retained compounds stop preparation; waters are retained."
    )
    form.addRow("Exclude extra compounds", fields["excluded_residue_names"])
    layout.addLayout(form)
    fields["ligand_protonation_confirmed"] = QCheckBox(
        "I verified hydrogens, protonation and expected net charge"
    )
    fields["allow_ligand_upload"] = QCheckBox("Allow uploading this ligand MOL2 to SwissParam")
    fields["allow_ligand_upload"].setToolTip(
        "The public SwissParam service receives the ligand structure when new parameters "
        "are needed. Verified cached parameters work offline. Protein files are not uploaded. "
        "Consent is cleared when inputs change or a project is reopened."
    )
    layout.addWidget(fields["ligand_protonation_confirmed"])
    layout.addWidget(fields["allow_ligand_upload"])
    links = QLabel('<a href="https://www.swissparam.ch/">SwissParam service and terms</a>')
    links.setOpenExternalLinks(True)
    layout.addWidget(links)
    return panel


def reset_ligand_consent(fields: dict) -> None:
    for key in ("ligand_protonation_confirmed", "allow_ligand_upload"):
        fields[key].setChecked(False)


def charmm_request_options(fields: dict) -> dict:
    """Validate the visible choices without reading files or initiating network I/O."""
    if Path(fields["ligand"].text().strip()).suffix.lower() != ".mol2":
        raise ValueError("CHARMM ligands require a MOL2 file with explicit hydrogens and reviewed protonation.")
    if not fields["ligand_protonation_confirmed"].isChecked():
        raise ValueError("Review the ligand hydrogens, protonation and expected net charge, then confirm the chemistry review.")
    from_complex = fields["ligand_from_complex"].isChecked()
    residue = fields["source_residue_name"].text().strip().upper() if from_complex else ""
    excluded = tuple(dict.fromkeys(
        item.strip().upper() for item in fields["excluded_residue_names"].text().split(",")
        if item.strip()
    )) if from_complex else ()
    if from_complex and not re.fullmatch(r"[A-Z0-9]{1,3}", residue):
        raise ValueError("Enter the ligand residue name from the complex PDB (one to three letters or digits).")
    if any(not re.fullmatch(r"[A-Z0-9]{1,3}", item) for item in excluded) or residue in excluded:
        raise ValueError("List valid extra compound residue names, separated by commas; do not exclude the selected ligand.")
    return {
        "ligand_backend": "swissparam_match",
        "ligand_protonation_confirmed": True,
        "allow_ligand_upload": fields["allow_ligand_upload"].isChecked(),
        "source_residue_name": residue,
        "excluded_residue_names": excluded,
    }
