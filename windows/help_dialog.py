"""Built-in MolDynStudio help manual."""

from __future__ import annotations

from PyQt5.QtWidgets import QDialog, QDialogButtonBox, QTextEdit, QVBoxLayout


HELP_HTML = """
<h1>MolDynStudio Manual</h1>
<p><b>Inventor:</b> Adriano Marques Gonçalves (UNIARA)</p>
<h2>Workflow</h2>
<ol>
  <li>Create or open an .mds project.</li>
  <li>Use <b>MD Setup</b> to select protein/ligand inputs and generate .mdp files.</li>
  <li>Use <b>MD Run</b> to launch the selected GROMACS build through minimization, NVT, NPT, and production MD. Live logs and parsed thermodynamic data are shown as the real stages run.</li>
  <li>Use <b>Analysis</b> for RMSD, RMSF, SASA, PCA, MM-PBSA/MM-GBSA, and CPPTRAJ scripts.</li>
</ol>
<h2>Runtime and restart</h2>
<p>Choose the GROMACS executable and Conda environment in <b>Settings</b>. On Windows, GROMACS can run through
WSL2; on Linux, the selected native executable is used. A production run with a valid <code>md.cpt</code> can be
resumed with GROMACS <code>-cpi</code>. Keep existing production output files unchanged so GROMACS can append
continuously.</p>
<p><b>GPU Auto</b> follows the capabilities reported by the selected GROMACS build. Installing a GPU driver alone
does not guarantee that the executable has GPU support.</p>
<h2>Supported MD inputs</h2>
<p>Protein-only systems and separate non-covalent ligands with an AMBER-family force field plus ACPYPE/GAFF2 are
supported. Covalent ligands and arbitrary automatic CGenFF generation are not supported.</p>
<h2>Missing Tools</h2>
<p>If a scientific package is missing, the relevant button remains available for configuration but execution
will report the missing dependency. Install from <code>environment.yml</code>.</p>
"""


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("MolDynStudio Documentation")
        self.resize(760, 620)
        layout = QVBoxLayout(self)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setHtml(HELP_HTML)
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
