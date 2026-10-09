"""Keep the wheel's sibling OpenMM runtime and dynamically loaded plugins."""
from pathlib import Path
import hashlib
import json
import os
import sys

from PyInstaller.utils.hooks import get_package_paths


datas = []
_MSVC_DLLS = (
    "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
    "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll",
)


def _file_version(path):
    import pefile

    with pefile.PE(str(path), fast_load=True) as binary:
        binary.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]]
        )
        info = binary.VS_FIXEDFILEINFO[0]
        return (info.FileVersionMS >> 16, info.FileVersionMS & 0xffff,
                info.FileVersionLS >> 16, info.FileVersionLS & 0xffff)


def _select_msvc_runtime(directories, read_version=_file_version):
    complete = [
        (tuple(read_version(directory / name) for name in _MSVC_DLLS), directory)
        for directory in directories
        if all((directory / name).is_file() for name in _MSVC_DLLS)
    ]
    # MSVC 14.40 changed std::mutex initialization; older Qt runtime copies crash.
    # https://github.com/microsoft/STL/issues/4730
    if not complete or max(min(versions) for versions, _ in complete) < (14, 40, 0, 0):
        raise RuntimeError(
            "OpenMM requires a complete Microsoft Visual C++ runtime family >=14.40. "
            "Install the current x64 Redistributable on the build host, or set "
            "MOLDYN_MSVC_RUNTIME_DIR to its extracted runtime DLL directory."
        )
    selected_versions, selected = max(complete, key=lambda item: min(item[0]))
    if any(any(selected < other for selected, other in zip(selected_versions, versions))
           for versions, _ in complete):
        raise RuntimeError(
            "No complete Microsoft Visual C++ runtime family avoids downgrading a DLL. "
            "Set MOLDYN_MSVC_RUNTIME_DIR to a current, complete x64 Redistributable family."
        )
    return selected


def _windows_msvc_binaries():
    if sys.platform != "win32":
        return []
    try:
        _, qt_package = get_package_paths("PyQt5")
    except ImportError:
        return []
    qt_runtime = Path(qt_package) / "Qt5" / "bin"
    if not qt_runtime.is_dir():
        return []
    candidates = [qt_runtime]
    if os.environ.get("SystemRoot"):
        candidates.append(Path(os.environ["SystemRoot"]) / "System32")
    if os.environ.get("MOLDYN_MSVC_RUNTIME_DIR"):
        candidates.append(Path(os.environ["MOLDYN_MSVC_RUNTIME_DIR"]))
    selected = _select_msvc_runtime(candidates)
    from PyInstaller.config import CONF
    manifest = Path(CONF["workpath"]) / "openmm-msvc-runtime.json"
    manifest.write_text(json.dumps({
        "component": "Microsoft Visual C++ runtime",
        "source_directory": str(selected),
        "upstream": "https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist",
        "files": [{"name": name, "version": _file_version(selected / name),
                   "sha256": hashlib.sha256((selected / name).read_bytes()).hexdigest()}
                  for name in _MSVC_DLLS],
    }, indent=2) + "\n", encoding="utf-8")
    datas.append((str(manifest), "assets/licenses/runtime"))
    return [(str(selected / name), destination)
            for name in _MSVC_DLLS for destination in (".", "PyQt5/Qt5/bin")]


_base, _package = get_package_paths("openmm")
_runtime = Path(_package).parent / "OpenMM.libs" / "lib"
binaries = [
    (str(source), (Path("OpenMM.libs") / "lib" / source.relative_to(_runtime).parent).as_posix())
    for source in sorted(_runtime.rglob("*"))
    if source.is_file() and (source.suffix.lower() in {".dll", ".dylib"} or ".so" in source.suffixes)
]
binaries += _windows_msvc_binaries()
