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
`dist/MolDynStudio.exe` in the ZIP. Build the Linux source installer on Linux:

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
