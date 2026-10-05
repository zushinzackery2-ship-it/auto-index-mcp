"""Delete recognized cache outputs and keep unknown residue attributable."""
import re
from pathlib import Path

from ..cache.inventory import is_link
from ..cache.locking import COORDINATION_FILES
from .paths import CACHEDIR_TAG_NAME, KNOWN_INDEX_FILES, MARKER_FILE_NAME

RECOVERY_FILE = re.compile(r"(?:index|embeddings)\.db(?:-wal|-shm|-journal)?\.corrupt-[a-f0-9]{32}")
LOG_FILE = re.compile(r"server-\d+\.log(?:\.\d+)?")
MARKERS = (MARKER_FILE_NAME, CACHEDIR_TAG_NAME)


def remove_files(directory: Path) -> tuple[list[str], list[str]]:
    errors = []
    _remove_folder(directory / "logs", LOG_FILE, errors)
    if errors:
        return errors, []
    for name in KNOWN_INDEX_FILES:
        if name not in (*COORDINATION_FILES, *MARKERS):
            _unlink(directory / name, errors)
    for path in directory.iterdir():
        if RECOVERY_FILE.fullmatch(path.name):
            _unlink(path, errors)
    _remove_folder(directory / "recovery", RECOVERY_FILE, errors)
    ignored = directory / ".gitignore"
    if ignored.is_file() and not is_link(ignored) and ignored.read_text(encoding="utf-8") == "*\n":
        _unlink(ignored, errors)
    remaining = residue(directory)
    if not errors and not remaining:
        for name in MARKERS:
            _unlink(directory / name, errors)
        if not errors:
            directory.rmdir()
    notes = [f"retained: {', '.join(remaining)}"] if remaining else []
    return errors, notes


def residue(directory: Path) -> list[str]:
    if not directory.exists():
        return []
    return sorted(path.name for path in directory.iterdir() if path.name not in MARKERS)


def _unlink(path: Path, errors: list[str]) -> None:
    try:
        if path.exists() and is_link(path):
            return
        path.unlink(missing_ok=True)
    except OSError as exc:
        errors.append(f"could not delete {path.name}: {exc}")


def _remove_folder(directory: Path, pattern, errors) -> None:
    if not directory.is_dir() or is_link(directory):
        return
    for path in directory.iterdir():
        if path.is_file() and pattern.fullmatch(path.name):
            _unlink(path, errors)
    if not any(directory.iterdir()):
        directory.rmdir()
