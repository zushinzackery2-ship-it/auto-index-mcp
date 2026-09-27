from __future__ import annotations

from pathlib import Path

from ..runtime.leases import BuildLock
from ..runtime.maintenance import LEASE_NAMES, MaintenanceLease
from .markers import is_safe_to_delete
from .paths import CACHEDIR_TAG_NAME, KNOWN_INDEX_FILES, MARKER_FILE_NAME


def build_lock_active(index_dir) -> bool:
    return any(BuildLock(Path(index_dir) / name).state_info() is not None for name in LEASE_NAMES)


def clean_entry(registry, entry: dict) -> dict:
    directory = Path(entry["index_dir"])
    if not directory.exists():
        removed = registry.unregister(directory, expected_attached_at=entry.get("last_attached_at"))
        return dict(status="unregistered" if removed else "changed", notes=[])
    try:
        with MaintenanceLease(directory, "clean"):
            if not is_safe_to_delete(directory, entry["root"]):
                return dict(status="refused", notes=["directory ownership could not be verified"])
            errors, notes = _remove_files(directory)
            if errors:
                return dict(status="failed", notes=errors + notes)
            if not registry.unregister(directory, expected_attached_at=entry.get("last_attached_at")):
                return dict(status="changed", notes=notes + ["registration changed during cleanup"])
            return dict(status="removed", notes=notes)
    except TimeoutError as exc:
        return dict(status="busy", notes=[str(exc)])
    except OSError as exc:
        return dict(status="failed", notes=[str(exc)])


def remove_index_dir(index_dir) -> tuple[bool, list[str]]:
    directory = Path(index_dir)
    with MaintenanceLease(directory, "clean"):
        errors, notes = _remove_files(directory)
    return False, errors + notes


def _remove_files(directory: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    markers = (MARKER_FILE_NAME, CACHEDIR_TAG_NAME)
    for name in KNOWN_INDEX_FILES:
        if name in LEASE_NAMES or name in markers:
            continue
        try:
            (directory / name).unlink(missing_ok=True)
        except OSError as exc:
            errors.append(f"could not delete {name}: {exc}")
    _remove_known_logs(directory, errors)
    if not errors:
        for name in markers:
            try:
                (directory / name).unlink(missing_ok=True)
            except OSError as exc:
                errors.append(f"could not delete {name}: {exc}")
    leftovers = sorted(entry.name for entry in directory.iterdir() if entry.name not in LEASE_NAMES)
    notes = ["kept stable lease files to preserve cross-process lock identity"]
    if leftovers:
        notes.append(f"kept other files: {', '.join(leftovers)}")
    return errors, notes


def _remove_known_logs(directory: Path, errors: list[str]) -> None:
    logs = directory / "logs"
    if not logs.is_dir() or logs.is_symlink():
        return
    for entry in logs.iterdir():
        if entry.is_file() and entry.name.startswith("server-") and ".log" in entry.name:
            try:
                entry.unlink()
            except OSError as exc:
                errors.append(f"could not delete logs/{entry.name}: {exc}")
    if not any(logs.iterdir()):
        logs.rmdir()


def index_dir_size(index_dir) -> int:
    total = 0
    try:
        for entry in Path(index_dir).iterdir():
            if entry.is_file():
                total += entry.stat().st_size
    except OSError:
        return total
    return total
