"""Preserve installed runtime notices; this is not a license-compliance audit."""

from __future__ import annotations

import importlib.metadata
import json
import shutil
import sys
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def collect_runtime_licenses(repo_root: Path, destination: Path) -> None:
    """Copy original texts and metadata for the resolved runtime dependency tree."""
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(repo_root / "LICENSE", destination / "MOLDYNSTUDIO-LICENSE.txt")
    shutil.copy2(
        repo_root / "assets/licenses/THIRD_PARTY_NOTICES.md",
        destination / "THIRD_PARTY_NOTICES.md",
    )
    requirements = (repo_root / "requirements.txt").read_text(encoding="utf-8")
    pending = [
        Requirement(line).name
        for line in requirements.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    manifest = []
    seen = set()
    # Include the bootloader's exception, not PyInstaller's build-only dependency tree.
    pending.append("pyinstaller")
    while pending:
        name = canonicalize_name(pending.pop())
        if name in seen:
            continue
        seen.add(name)
        distribution = importlib.metadata.distribution(name)
        package_dir = destination / f"{name}-{distribution.version}"
        package_dir.mkdir(exist_ok=True)
        copied = []
        for entry in distribution.files or ():
            if not any(
                word in entry.name.lower()
                for word in ("license", "licence", "copying", "notice", "copyright")
            ) and entry.name != "METADATA":
                continue
            # Distribution RECORD entries can point outside site-packages.
            if entry.is_absolute() or ".." in entry.parts:
                continue
            source = Path(distribution.locate_file(entry))
            if not source.is_file():
                continue
            target = package_dir / entry
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            if entry.name != "METADATA":
                copied.append(entry.as_posix())
        manifest.append({"name": name, "version": distribution.version, "license_files": copied})
        if name != "pyinstaller":
            for raw_requirement in distribution.requires or ():
                requirement = Requirement(raw_requirement)
                if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
                    pending.append(requirement.name)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise FileNotFoundError(f"Python runtime license missing: {python_license}")
    shutil.copy2(python_license, destination / "PYTHON-LICENSE.txt")
    (destination / "manifest.json").write_text(
        json.dumps(sorted(manifest, key=lambda item: item["name"]), indent=2) + "\n",
        encoding="utf-8",
    )
