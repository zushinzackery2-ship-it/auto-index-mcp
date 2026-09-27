from __future__ import annotations

import shutil
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path


def ripgrep_executable() -> str | None:
    """Use the interpreter's declared dependency even without an activated PATH."""
    try:
        package = distribution("ripgrep")
    except PackageNotFoundError:
        return shutil.which("rg")
    for entry in package.files or []:
        if entry.name not in ("rg", "rg.exe"):
            continue
        executable = Path(package.locate_file(entry))
        if executable.is_file():
            return str(executable.resolve())
    return shutil.which("rg")
