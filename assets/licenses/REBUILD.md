# Source, modification and rebuilding

MolDynStudio v1.1.1 is GPL-3.0-only. The release provides the application source
ZIP alongside the installers, at https://github.com/amgoncalvesusp/MolDynStudio/releases/tag/v1.1.1.
The matching Git tag includes the complete application and build/test scripts.
No signing key or activation service is needed to run a modified version.

## Run or modify the application

Extract the source ZIP. Install Python 3.11 and, from that directory, run:

```
python -m venv .venv
```

Activate `.venv/Scripts/activate` on Windows or `source .venv/bin/activate` on
Linux, then:

```
python -m pip install -r requirements.txt
python main.py
```

Dependencies can be replaced in that environment, including modified compatible
versions of Qt and PyQt. Running from source does not require the frozen EXE.
The release's runtime license manifest records the versions used in the Windows
binary; install those versions to match that environment. The manifest is not a
claim of bit-for-bit reproducibility.

## Build the installers

```
python -m pip install -r requirements.txt -r requirements-build.txt
python build/create_installer.py
```

Build the Windows executable on Windows. Include `dist/licenses` alongside
`dist/MolDynStudio.exe` in the ZIP.

Run the complete executable before archiving it:

```
dist/MolDynStudio.exe --self-check self-check-windows.json
```

On Linux, run the installed launcher with the same arguments. The JSON report
and companion log cover native MDAnalysis/MDTraj distance calculations,
PDBFixer parsing, one OpenMM CPU integration step, ParmEd topology writing, and
construction of every main GUI page using Qt's offscreen platform. The checks
run in an isolated process and return a nonzero exit status on a Python error,
native crash, timeout, or incomplete report. This packaging test does not
replace a real GROMACS simulation or validate scientific force-field choices.

For OpenMM, install the current official Microsoft Visual C++ x64 Redistributable
on the build host, or set `MOLDYN_MSVC_RUNTIME_DIR` to its extracted runtime DLL
directory. The hook selects the newest complete runtime family from this path,
System32, and the Qt wheel; it never replaces newer Qt files with older system
files. Versions below 14.40 fail the build because their mutex implementation can
crash current OpenMM binaries. Record and test the exact selected family: the
14.40 check is a compatibility floor, not a guarantee for every future compiler.
The build embeds its version/hash manifest under `assets/licenses/runtime` and
copies it to `dist/licenses`. Official downloads and terms:
https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist.

Build the Linux source installer on Linux:

```
python build/create_linux_installer.py
```

The Linux installer installs dependencies from their upstream indexes into a
local virtual environment. It does not bundle Python/Qt binary runtimes.

## Corresponding third-party source and notices

The release supplies the original Qt 5.15.2 full source archive, including its
Chromium source and third-party notices. A companion third-party source archive
contains the exact PyQt/PyQtWebEngine source distributions used by the Windows
build and a version/hash/source manifest. These are unmodified upstream sources.
The Windows ZIP also contains the preserved Qt source license/notice texts.

Upstream sources and rebuilding documentation:

- Qt 5.15.2: https://download.qt.io/archive/qt/5.15/5.15.2/single/
- Qt building: https://doc.qt.io/archives/qt-5.15/build-sources.html
- PyQt5: https://pypi.org/project/PyQt5/#files
- PyQtWebEngine: https://pypi.org/project/PyQtWebEngine/#files
- SIP: https://pypi.org/project/PyQt5-sip/#files
- PyQt build/bundle instructions: https://pyqt-builder.readthedocs.io/en/latest/pyqtbundle.html
- Python sources: https://www.python.org/downloads/source/
- PyInstaller and its bootloader exception: https://pyinstaller.org/en/stable/license.html

The Qt wheels are upstream Riverbank wheels, not a custom MolDynStudio Qt fork.
To use a modified Qt, build it with the appropriate Windows compiler and use
Riverbank's documented build/bundle workflow, then rebuild MolDynStudio above.
All redistribution must preserve the applicable third-party terms.

## Assemble a new release

Use `build/prepare_release_materials.py` after both platform checks pass and the
application source commit is fixed. Supply the fresh Windows ZIP, Linux `.run`,
and a source ZIP produced from that exact commit. The required arguments are
`--version`, `--revision` (the full commit SHA), `--windows`, `--linux`, `--source`,
`--output`, and `--cache`. Use a new output directory for each assembly; existing
release artifacts are never overwritten.

The assembler reads the Windows runtime manifest, downloads matching source
distributions, and checks published PyPI and Qt SHA-256 digests. OpenMM wheels
record their own source commit; the license collector reads that value without
loading the native engine. When PyPI has no OpenMM source distribution, the
assembler retrieves that exact official GitHub commit and records the archive's
calculated SHA-256. This calculated digest is not an upstream signature.

Outputs include the original application source, both installers, upstream
source archives, original notice texts in the Windows ZIP, artifact provenance,
and a checksum file. Cached sources with mismatched or missing checksums are
rejected. Application source archives containing `.worktrees` are rejected.
Assembly performs no Git push, tag creation, or release publication.
