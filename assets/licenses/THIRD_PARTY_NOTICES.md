# Third-party components

These notices identify third-party software; they do not grant a license to
MolDynStudio's own code or change any upstream license. Dependency versions may
vary between installations. Consult the license texts shipped with each exact
version, including embedded libraries and fonts.

## Distribution status

The repository currently has no application license declaration. The PyPI builds
of PyQt5 and PyQtWebEngine use GPLv3, not LGPL. Before distributing a combined
application, the copyright holder must select and comply with a GPL-compatible
application license, or obtain the appropriate commercial licenses. Publishing
source on GitHub alone does not resolve this requirement. This document is not a
substitute for corresponding source, installation information, or other required
license materials. Binary release approval remains pending that decision and an
audit of the actual bundled components.

## Windows runtime

The Windows executable bundles Python and selected GUI/scientific dependencies.
`build/create_installer.py` collects installed license files and distribution
metadata into `dist/licenses`, also embedded at `assets/licenses/runtime` in the
executable. Include the external `licenses` directory in the release ZIP so that
recipients can read it without executing the program. `manifest.json` records
the inspected versions and identifies packages with no separate installed
license text; metadata alone is not a substitute for those texts.

| Component | Upstream licensing information |
| --- | --- |
| Python | PSF license and included third-party notices; https://docs.python.org/3/license.html |
| PyQt5, PyQtWebEngine | GPLv3 or separate commercial license; https://www.riverbankcomputing.com/software/pyqt |
| Qt5, Qt WebEngine | LGPLv3/GPL and component-specific licenses; https://doc.qt.io/qt-5/licensing.html |
| Chromium within Qt WebEngine | Multiple licenses and corresponding-source obligations; https://doc.qt.io/qt-5/qtwebengine-licensing.html |
| NumPy, SciPy, pandas, seaborn | BSD-family; exact distributions contain additional bundled-component notices |
| Matplotlib | Matplotlib/PSF-derived license plus bundled fonts and other notices |
| openpyxl | MIT; its dependencies have their own licenses |
| PyInstaller bootloader | GPL with the PyInstaller exception; https://pyinstaller.org/en/stable/license.html |

The installed Qt wheel's top-level license is not a complete inventory of
Chromium's third-party code. A release maintainer must supply the exact bundled
Qt/WebEngine/Chromium notices and corresponding source as required, and provide
a compliant way to modify/rebuild/relink the combined work. Do not interpret the
automatic collection step as a declaration that these obligations are fulfilled.

## Linux and external scientific tools

The Linux installer contains MolDynStudio source and these notices. It installs
Python GUI dependencies from their package indexes into a user-local virtual
environment; it does not contain those dependency binaries. Installed packages
retain their own metadata and licenses. GROMACS, AmberTools, gmx_MMPBSA,
MDAnalysis, MDTraj, ProDy and other tools requested by `environment.yml` are
separately downloaded packages, not re-licensed or owned by MolDynStudio.

## CHARMM36m / CGenFF parameters

No CHARMM force-field archive is redistributed with MolDynStudio. The optional
download action retrieves the pinned GROMACS-format package directly from the
MacKerell laboratory; users can alternatively import their own official archive.
The package's upstream terms apply. Preserve its attribution/citation files.

Official source and download information:

- https://mackerell.umaryland.edu/charmm_ff.shtml
- https://github.com/mackerell-lab/charmm36-force-field

The latter source repository declares MIT, but this does **not** establish that
the separately converted GROMACS archive, including all its components, is
covered by that license. MolDynStudio does not assert redistribution permission
for that archive. CGenFF parameter data are distinct from the CGenFF assignment
program/service; that program is not included or licensed by MolDynStudio.
