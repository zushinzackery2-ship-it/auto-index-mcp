from __future__ import annotations

import hashlib
import queue
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .config import LANGUAGE_BY_EXTENSION
from .lsp_session import DiagnosticLine, LspSession
from .lsp_specs import SERVER_SPECS, diagnostic_line, format_check_result, is_file_supported, language_id


DocumentReader = Callable[[dict[str, Any]], tuple[str, str]]
IO_SLICE_SECONDS = 0.25


def remaining_seconds(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


class LspCheckMixin:
    def check(
        self,
        root: Path | None,
        files: list[dict[str, Any]],
        read_document: DocumentReader,
        path: str | None = None,
        limit: int = 80,
        timeout_seconds: float = 5.0,
        workspace_signature_seed: str = "",
    ) -> str:
        if root is None:
            return "CHK|not_configured"
        timeout_seconds = max(0.1, timeout_seconds)
        deadline = time.monotonic() + timeout_seconds
        targets = self._check_targets(root, files, path)
        if path and not targets:
            return f"CHK|not_found|{path}"
        target_context = targets if path else files
        running = {key: session for key, session in self.sessions.items() if session.is_running()}
        self._mark_stopped_sessions_unavailable(target_context, running)
        if not running:
            if self.unavailable_servers:
                return self._unavailable_check_result()
            return "CHK|not_started"

        workspace_signature = self._workspace_signature(target_context, workspace_signature_seed)
        missing_server_keys = self._missing_target_server_keys(targets, running)
        opened, unchecked = self._open_targets(targets, running, read_document, workspace_signature, deadline)
        missing_diagnostics = self._wait_for_opened_diagnostics(opened, remaining_seconds(deadline))
        diagnostics = self._collect_diagnostics(opened, limit)
        checked = len(opened) - len(missing_diagnostics)
        result = format_check_result(diagnostics, checked, unchecked + len(missing_diagnostics), limit)
        return self._with_unavailable_servers(result, missing_server_keys)

    def _check_targets(self, root: Path, files: list[dict[str, Any]], path: str | None) -> list[dict[str, Any]]:
        if not path:
            return self._default_check_targets(files)
        if files:
            needle = path.replace("\\", "/").strip("/").lower()
            indexed = [item for item in files if item["path"].lower() == needle]
            if indexed:
                return indexed
        return self._filesystem_target(root, path)

    def _default_check_targets(self, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
        checked_paths = self.bootstrap.checked_paths
        if not checked_paths:
            return files
        normalized = {path.lower() for path in checked_paths}
        return [
            item
            for item in files
            if not is_file_supported(SERVER_SPECS[0], item) or item["path"].lower() in normalized
        ]

    def _filesystem_target(self, root: Path, path: str) -> list[dict[str, Any]]:
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
            "parent": str(Path(rel).parent).replace("\\", "/"),
            "extension": extension,
            "language": LANGUAGE_BY_EXTENSION.get(extension, "text"),
            "mtime_ns": target.stat().st_mtime_ns,
            "source_root": str(root),
            "source_path": rel,
        }
        return [item] if any(is_file_supported(spec, item) for spec in SERVER_SPECS) else []

    def _open_targets(
        self,
        files: list[dict[str, Any]],
        running: dict[str, LspSession],
        read_document: DocumentReader,
        workspace_signature: str,
        deadline: float,
    ) -> tuple[list[tuple[LspSession, str, str]], int]:
        opened = []
        unchecked = 0
        for index, item in enumerate(files):
            if remaining_seconds(deadline) <= 0:
                unchecked += len(files) - index
                break
            session = self._session_for_item(item, running)
            if session is None:
                unchecked += 1
                continue
            try:
                text, uri = _run_with_budget(lambda: read_document(item), deadline)
                _run_with_budget(
                    lambda: session.open_document(uri, language_id(item), int(item.get("mtime_ns", 1)), text, workspace_signature),
                    deadline,
                )
                opened.append((session, uri, item["path"]))
            except (OSError, UnicodeDecodeError, ValueError, TimeoutError):
                unchecked += 1
        return opened, unchecked

    def _workspace_signature(self, files: list[dict[str, Any]], seed: str = "") -> str:
        digest = hashlib.sha1()
        digest.update(seed.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        for item in sorted(files, key=lambda value: value["path"]):
            if not any(is_file_supported(spec, item) for spec in SERVER_SPECS):
                continue
            _add_item_signature(digest, item)
        return digest.hexdigest()

    def _session_for_item(self, item: dict[str, Any], running: dict[str, LspSession]) -> LspSession | None:
        for spec in SERVER_SPECS:
            if spec.key in running and is_file_supported(spec, item):
                return running[spec.key]
        return None

    def _mark_stopped_sessions_unavailable(self, files: list[dict[str, Any]], running: dict[str, LspSession]) -> None:
        for key in list(self.sessions):
            if key not in running and key not in self.unavailable_servers:
                self.unavailable_servers[key] = ("error", self._target_count(key, files))

    def _target_count(self, key: str, files: list[dict[str, Any]]) -> int:
        spec = next((candidate for candidate in SERVER_SPECS if candidate.key == key), None)
        return sum(1 for item in files if spec and is_file_supported(spec, item))

    def _missing_target_server_keys(self, files: list[dict[str, Any]], running: dict[str, LspSession]) -> set[str]:
        keys = set()
        for item in files:
            for spec in SERVER_SPECS:
                if spec.key not in running and spec.key in self.unavailable_servers and is_file_supported(spec, item):
                    keys.add(spec.key)
                    break
        return keys

    def _wait_for_opened_diagnostics(self, opened: list[tuple[LspSession, str, str]], timeout_seconds: float) -> set[tuple[LspSession, str]]:
        by_session: dict[LspSession, set[str]] = {}
        for session, uri, _ in opened:
            by_session.setdefault(session, set()).add(uri)
        sessions = list(by_session.items())
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        for index, (session, uris) in enumerate(sessions):
            remaining = remaining_seconds(deadline)
            if remaining <= 0:
                break
            session.wait_for_diagnostics(uris, remaining / max(1, len(sessions) - index))
        return {
            (session, uri)
            for session, uris in sessions
            for uri in uris
            if uri not in session.diagnostics
        }

    def _collect_diagnostics(self, opened: list[tuple[LspSession, str, str]], limit: int) -> list[DiagnosticLine]:
        rows = []
        for session, uri, path in opened:
            for diagnostic in session.diagnostics.get(uri, []):
                rows.append(diagnostic_line(path, diagnostic))
                if len(rows) >= max(1, limit):
                    return rows
        return rows

    def _unavailable_check_result(self) -> str:
        return f"CHK|unavailable|servers={self._unavailable_server_details(set(self.unavailable_servers))}"

    def _with_unavailable_servers(self, result: str, keys: set[str]) -> str:
        if not keys:
            return result
        lines = result.splitlines()
        lines[0] = f"{lines[0]}|servers={self._unavailable_server_details(keys)}"
        return "\n".join(lines)

    def _unavailable_server_details(self, keys: set[str]) -> str:
        return ",".join(f"{key}:{state}:{count}" for key, (state, count) in sorted(self.unavailable_servers.items()) if key in keys)

def _run_with_budget(operation: Callable[[], Any], deadline: float) -> Any:
    remaining = remaining_seconds(deadline)
    if remaining <= 0:
        raise TimeoutError("LSP check budget exhausted")
    result_queue: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def worker() -> None:
        try:
            result_queue.put_nowait((True, operation()))
        except BaseException as exc:
            try:
                result_queue.put_nowait((False, exc))
            except queue.Full:
                pass

    thread = threading.Thread(target=worker, name="auto-index-lsp-check-io", daemon=True)
    thread.start()
    try:
        ok, value = result_queue.get(timeout=min(remaining, IO_SLICE_SECONDS))
    except queue.Empty as exc:
        raise TimeoutError("LSP check IO operation timed out") from exc
    if ok:
        return value
    raise value


def _add_item_signature(digest: Any, item: dict[str, Any]) -> None:
    digest.update(item["path"].encode("utf-8", errors="surrogateescape"))
    digest.update(b"\0")
    digest.update(str(item.get("sha1", "")).encode("ascii", errors="ignore"))
    digest.update(b"\0")
    digest.update(str(item.get("size", 0)).encode("ascii", errors="ignore"))
    digest.update(b"\0")
    digest.update(str(item.get("mtime_ns", 0)).encode("ascii", errors="ignore"))
    digest.update(b"\0")
