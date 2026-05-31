from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any, Callable

from ..core.config import LANGUAGE_BY_EXTENSION
from .client import LspClient
from .lsp_specs import SERVER_SPECS, diagnostic_line, format_check_result, is_file_supported, language_id


DocumentReader = Callable[[dict[str, Any]], "tuple[str, str]"]


def remaining_seconds(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def run_check(
    manager,
    root: Path,
    files: list[dict[str, Any]],
    read_document: DocumentReader,
    path: str | None = None,
    limit: int = 80,
    timeout_seconds: float = 5.0,
    signature_seed: str = "",
) -> str:
    deadline = time.monotonic() + max(0.1, timeout_seconds)
    if path:
        targets = _path_targets(root, files, path)
        if not targets:
            return f"CHK|not_found|{path}"
    else:
        targets = [item for item in files if _supported(item)]
    running = manager.ensure_running(root, targets, deadline)
    if not running:
        return _unavailable_result(manager)

    signature = _workspace_signature(targets, signature_seed)
    opened: list[tuple[LspClient, str, str]] = []
    unchecked = 0
    for index, item in enumerate(targets):
        if remaining_seconds(deadline) <= 0:
            unchecked += len(targets) - index
            break
        client = _client_for(item, running)
        if client is None:
            unchecked += 1
            continue
        try:
            text, uri = read_document(item)
            client.open_document(uri, language_id(item), text, signature)
            opened.append((client, uri, item["path"]))
        except (OSError, UnicodeDecodeError, ValueError):
            unchecked += 1

    missing = _await_diagnostics(opened, deadline)
    rows = _collect(opened, limit)
    checked = len(opened) - len(missing)
    return format_check_result(rows, checked, unchecked + len(missing), limit)


def _supported(item: dict[str, Any]) -> bool:
    return any(is_file_supported(spec, item) for spec in SERVER_SPECS)


def _client_for(item: dict[str, Any], running: dict[str, LspClient]) -> LspClient | None:
    for spec in SERVER_SPECS:
        if spec.key in running and is_file_supported(spec, item):
            return running[spec.key]
    return None


def _await_diagnostics(opened: list[tuple[LspClient, str, str]], deadline: float) -> set[tuple[int, str]]:
    by_client: dict[LspClient, set[str]] = {}
    for client, uri, _ in opened:
        by_client.setdefault(client, set()).add(uri)
    items = list(by_client.items())
    missing: set[tuple[int, str]] = set()
    for index, (client, uris) in enumerate(items):
        remaining = remaining_seconds(deadline)
        share = remaining / max(1, len(items) - index) if remaining > 0 else 0.0
        not_arrived = client.wait_for_diagnostics(uris, share)
        for uri in list(not_arrived):
            if remaining_seconds(deadline) > 0:
                client.pull_diagnostics(uri, min(remaining_seconds(deadline), 0.5))
            if client.received_diagnostics(uri):
                not_arrived.discard(uri)
        for uri in not_arrived:
            missing.add((id(client), uri))
    return missing


def _collect(opened: list[tuple[LspClient, str, str]], limit: int) -> list:
    rows = []
    for client, uri, path in opened:
        for diagnostic in client.diagnostics_for(uri):
            rows.append(diagnostic_line(path, diagnostic))
            if len(rows) >= max(1, limit):
                return rows
    return rows


def _workspace_signature(files: list[dict[str, Any]], seed: str) -> str:
    digest = hashlib.sha1()
    digest.update(seed.encode("utf-8", errors="surrogateescape"))
    digest.update(b"\0")
    for item in sorted(files, key=lambda value: value["path"]):
        if not _supported(item):
            continue
        digest.update(item["path"].encode("utf-8", errors="surrogateescape"))
        digest.update(str(item.get("sha1", "")).encode("ascii", errors="ignore"))
        digest.update(str(item.get("mtime_ns", 0)).encode("ascii", errors="ignore"))
        digest.update(b"\0")
    return digest.hexdigest()


def _path_targets(root: Path, files: list[dict[str, Any]], path: str) -> list[dict[str, Any]]:
    needle = path.replace("\\", "/").strip("/").lower()
    indexed = [item for item in files if item["path"].lower() == needle]
    if indexed:
        return indexed
    return _filesystem_target(root, path)


def _filesystem_target(root: Path, path: str) -> list[dict[str, Any]]:
    try:
        raw = Path(path)
        target = raw.resolve() if raw.is_absolute() else (root / path.replace("\\", "/").strip("/")).resolve()
        rel = target.relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return []
    if not target.is_file():
        return []
    extension = target.suffix.lower()
    item = {
        "path": rel,
        "name": target.name,
        "extension": extension,
        "language": LANGUAGE_BY_EXTENSION.get(extension, "text"),
        "mtime_ns": target.stat().st_mtime_ns,
        "source_root": str(root),
        "source_path": rel,
    }
    return [item] if _supported(item) else []


def _unavailable_result(manager) -> str:
    if manager.unavailable:
        detail = ",".join(f"{key}:{state}" for key, (state, _) in sorted(manager.unavailable.items()))
        return f"CHK|unavailable|servers={detail}"
    return "CHK|not_started"
