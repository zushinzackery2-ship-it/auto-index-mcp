from __future__ import annotations

from pathlib import Path

from ..runtime.leases import BuildLock
from ..runtime.maintenance import LEASE_NAMES, MaintenanceLease
from .markers import is_safe_to_delete
from .removal import remove_files as _remove_files, residue
from ..cache.inventory import file_inventory
from ..cache.locking import COORDINATION_FILES, lock_directory


def build_lock_active(index_dir) -> bool:
    directory = Path(index_dir)
    locks = [BuildLock(directory / name) for name in LEASE_NAMES]
    if lock_directory(directory) != directory.resolve():
        locks.extend(BuildLock(directory / name, resolve_cache=False) for name in LEASE_NAMES
                     if (directory / name).exists())
    return any(lock.state_info() is not None for lock in locks)


def clean_entry(registry, entry: dict) -> dict:
    directory = Path(entry["index_dir"])
    if not directory.exists():
        removed = registry.unregister(directory, expected_attached_at=entry.get("last_attached_at"))
        return dict(status="unregistered" if removed else "changed", notes=[])
    try:
        with MaintenanceLease(directory, "clean"):
            return clean_entry_locked(registry, entry)
    except TimeoutError as exc:
        return dict(status="busy", notes=[str(exc)])
    except OSError as exc:
        return dict(status="failed", notes=[str(exc)])


def clean_entry_locked(registry, entry: dict) -> dict:
    directory = Path(entry["index_dir"])
    if not is_safe_to_delete(directory, entry["root"]):
        return dict(status="refused", notes=["directory ownership could not be verified"])
    errors, notes = _remove_files(directory)
    if errors:
        registry.set_state(directory, "failed")
        return dict(status="failed", notes=errors + notes)
    remaining = residue(directory)
    if remaining:
        coordination = all(name in COORDINATION_FILES for name in remaining)
        if not registry.set_state(directory, "coordination" if coordination else "residual"):
            return dict(status="failed", notes=notes + ["registry state update failed"])
        return dict(status="removed" if coordination else "residual", notes=notes)
    if not registry.unregister(directory, expected_attached_at=entry.get("last_attached_at")):
        return dict(status="changed", notes=notes + ["registration changed during cleanup"])
    return dict(status="removed", notes=notes)


def remove_index_dir(index_dir) -> tuple[bool, list[str]]:
    directory = Path(index_dir)
    with MaintenanceLease(directory, "clean"):
        errors, notes = _remove_files(directory)
    return not directory.exists(), errors + notes


def index_dir_size(index_dir) -> int:
    return file_inventory(Path(index_dir))["bytes"]
