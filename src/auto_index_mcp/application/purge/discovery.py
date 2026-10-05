"""Combine registered, inline and mounted caches without source ignore rules."""
from pathlib import Path

from ...cache.discovery import cache_directories
from ...cache.identity import directory_identity, identity_key
from ...cache.inventory import file_inventory, is_link
from ...cache.layout import cache_directory
from ...cache.locking import lock_directory
from ...domain.errors import ServiceError
from ...registry.markers import is_safe_to_delete, read_index_marker
from ...registry.projection import projected_entries
from ...registry.paths import registry_key
from ...registry.paths import KNOWN_INDEX_FILES
from ...registry.store import IndexRegistry
from ...runtime.control.contracts import check_deadline, within


def participants(store, root):
    return [member for member in store.members()
            if (member.get("root") and within(member["root"], root))
            or any(row.get("root") and within(row["root"], root) for row in member.get("indexes", []))]


def discover(project, root, deadline, members=()):
    inline = list(cache_directories(root, deadline=deadline))
    actual_roots = [root, *(directory.parent for directory in inline if directory.name == ".auto-index-mcp")]
    registries = dict(((registry_key(project.registry.directory()), project.registry),))
    identities = dict((directory_identity(actual), actual) for actual in actual_roots)
    for member in members:
        if member.get("registry_dir"):
            registries.setdefault(registry_key(member["registry_dir"]), IndexRegistry(Path(member["registry_dir"])))
    rows = []
    for registry in registries.values():
        entries = registry.strict_entries()
        moved = [(entry["root"], identities[entry["root_identity"]]) for entry in entries.values()
                 if entry.get("root_identity") in identities
                 and registry_key(entry["root"]) != registry_key(identities[entry["root_identity"]])]
        projected = projected_entries(entries, actual_roots)
        for entry in projected.values():
            candidate = _project_entry(entry, moved)
            candidate["registry_dir"] = str(registry.directory())
            rows.append(candidate)
    for directory in inline:
        marker = read_index_marker(directory)
        owner = directory.parent if directory.name == ".auto-index-mcp" else Path((marker or {}).get("root", root))
        rows.append(dict(root=str(owner), index_dir=str(directory), registry_dir=str(project.registry.directory())))
    if project.index_root is not None:
        rows.append(dict(root=str(root), index_dir=str(project.index_root), registry_dir=str(project.registry.directory())))
    for member in members:
        rows.extend(dict(row, registry_dir=member["registry_dir"]) for row in member.get("indexes", []))
    result = dict()
    for row in rows:
        check_deadline(deadline)
        if not row.get("root") or not row.get("index_dir") or not within(row["root"], root):
            continue
        candidate = _candidate(row)
        key = registry_key(candidate["index_dir"])
        if key in result:
            result[key]["registries"] = sorted(set(result[key]["registries"] + candidate["registries"]))
        else:
            result[key] = candidate
    return sorted(result.values(), key=lambda row: row["coordination_dir"])


def _project_entry(entry, moved):
    candidate = dict(entry)
    for previous, actual in moved:
        if not within(candidate["root"], previous):
            continue
        rebound = actual / Path(candidate["root"]).resolve().relative_to(Path(previous).resolve())
        if directory_identity(rebound) == candidate.get("root_identity"):
            candidate["root"] = str(rebound)
    return candidate


def _candidate(row):
    directory = Path(row["index_dir"])
    root = Path(row["root"]).resolve()
    registries = [str(Path(row["registry_dir"]).resolve())]
    if not directory.exists():
        return dict(root=str(root), index_dir=str(directory.resolve()), identity=None, owned_root=str(root),
                    coordination_dir=str(cache_directory() / "locks" / identity_key(directory)),
                    root_identity=directory_identity(root), bytes=0, registries=registries)
    if is_link(directory):
        raise ServiceError("refused", f"linked cache directory: {directory}")
    marker = read_index_marker(directory)
    previous = (marker or {}).get("root", str(root))
    legitimate_root = (registry_key(previous) == registry_key(root)
                       or (marker or {}).get("root_identity") == directory_identity(root)
                       or (directory.name == ".auto-index-mcp" and directory.parent.resolve() == root))
    if not legitimate_root:
        raise ServiceError("refused", f"registered root conflicts with cache ownership: {directory}")
    for name in KNOWN_INDEX_FILES:
        path = directory / name
        try:
            if path.exists() and is_link(path):
                raise ServiceError("refused", f"linked cache artifact: {path}")
        except FileNotFoundError:
            continue
    if not is_safe_to_delete(directory, previous):
        raise ServiceError("refused", f"cache ownership cannot be verified: {directory}")
    copied = bool(marker and marker.get("index_id") and marker["index_id"] != identity_key(directory))
    coordination = cache_directory() / "locks" / identity_key(directory) if copied else lock_directory(directory)
    inventory = file_inventory(directory)
    if inventory["partial"]:
        raise ServiceError("refused", f"cache inventory incomplete: {directory}", errors=inventory["errors"])
    return dict(root=str(root), root_identity=directory_identity(root),
                index_dir=str(directory.resolve()), identity=directory_identity(directory),
                owned_root=previous, coordination_dir=str(coordination), bytes=inventory["bytes"], registries=registries)


def validate(target, root):
    directory = Path(target["index_dir"])
    if not within(target["root"], root):
        raise ServiceError("refused", "cache root left the bound workspace")
    if directory_identity(Path(target["root"])) != target["root_identity"]:
        raise ServiceError("refused", f"cache project identity changed: {directory}")
    if not directory.exists():
        return
    current = _candidate(dict(root=target["root"], index_dir=target["index_dir"],
                              registry_dir=target["registries"][0]))
    if any(current[key] != target[key] for key in ("identity", "coordination_dir", "owned_root")):
        raise ServiceError("refused", f"cache ownership changed: {directory}")
