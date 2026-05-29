from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any, Callable

from .clangd_bootstrap import ClangdBootstrap, prepare_clangd
from .config import LANGUAGE_BY_EXTENSION
from .lsp_resolver import resolve_lsp_executable
from .lsp_session import DiagnosticLine, LspSession, ProcessFactory
from .lsp_specs import SERVER_SPECS, LspServerSpec, diagnostic_line, effective_spec, format_check_result, is_file_supported, language_id, presence


ExecutableResolver = Callable[..., str | None]
DocumentReader = Callable[[dict[str, Any]], tuple[str, str]]


class LspManager:
    def __init__(
        self,
        executable_resolver: ExecutableResolver | None = None,
        process_factory: ProcessFactory | None = None,
    ) -> None:
        self.executable_resolver = executable_resolver or resolve_lsp_executable
        self.process_factory = process_factory or subprocess.Popen
        self.sessions: dict[str, LspSession] = {}
        self.session_signatures: dict[str, tuple[str, str, tuple[str, ...], str]] = {}
        self.unavailable_servers: dict[str, tuple[str, int]] = {}
        self.bootstrap = ClangdBootstrap((), ())

    def start(self, root: Path | None, files: list[dict[str, Any]], timeout_seconds: float = 10.0) -> str:
        if root is None:
            return "LSP|not_configured"
        timeout_seconds = max(0.1, timeout_seconds)
        targets = self._target_specs(files)
        if not targets:
            self.stop_all(0.2)
            return f"LSP|no_targets|{root.as_posix()}"
        target_keys = {spec.key for spec, _ in targets}
        for key in [key for key in self.sessions if key not in target_keys]:
            self.sessions[key].shutdown(0.2)
            self.sessions.pop(key, None)
            self.session_signatures.pop(key, None)

        lines = []
        ready_count = 0
        missing_count = 0
        error_count = 0
        unavailable_servers: dict[str, tuple[str, int]] = {}
        bootstrap = prepare_clangd(root, files)
        self.bootstrap = bootstrap
        for spec, count in targets:
            effective = effective_spec(spec, bootstrap)
            executable = self._resolve_executable(spec.executable, root)
            signature = (str(root.resolve()), executable or "", effective.args, bootstrap.signature if spec.key == "clangd" else "")
            session = self.sessions.get(spec.key)
            if session and session.is_running() and self.session_signatures.get(spec.key) == signature:
                state = "ready"
                ready_count += 1
            else:
                if session and session.is_running():
                    session.shutdown(0.2)
                self.sessions.pop(spec.key, None)
                self.session_signatures.pop(spec.key, None)
                if not executable:
                    state = "missing"
                    missing_count += 1
                    unavailable_servers[spec.key] = (state, count)
                else:
                    session = LspSession(effective, executable, self.process_factory)
                    self.sessions[spec.key] = session
                    state = session.start(root, timeout_seconds)
                    if state == "ready":
                        self.session_signatures[spec.key] = signature
                        ready_count += 1
                    else:
                        self.sessions.pop(spec.key, None)
                        error_count += 1
                        unavailable_servers[spec.key] = (state, count)
            lines.append(self._server_line(spec, state, count, root, bootstrap))

        self.unavailable_servers = unavailable_servers
        status = "ready"
        if ready_count == 0 and (missing_count or error_count):
            status = "unavailable"
        elif missing_count or error_count:
            status = "partial"
        return "\n".join([f"LSP|{status}|{root.as_posix()}"] + lines)

    def check(
        self,
        root: Path | None,
        files: list[dict[str, Any]],
        read_document: DocumentReader,
        path: str | None = None,
        limit: int = 80,
        timeout_seconds: float = 5.0,
    ) -> str:
        if root is None:
            return "CHK|not_configured"
        running = {key: session for key, session in self.sessions.items() if session.is_running()}
        self._mark_stopped_sessions_unavailable(files, running)
        if not running:
            if self.unavailable_servers:
                return self._unavailable_check_result()
            return "CHK|not_started"

        targets = self._check_targets(root, files, path)
        if path and not targets:
            return f"CHK|not_found|{path}"
        workspace_signature = self._workspace_signature(files)
        missing_server_keys = self._missing_target_server_keys(targets, running)
        opened, unchecked = self._open_targets(targets, running, read_document, workspace_signature)
        missing_diagnostics = self._wait_for_opened_diagnostics(opened, timeout_seconds)
        diagnostics = self._collect_diagnostics(opened, limit)
        result = format_check_result(diagnostics, len(opened) - len(missing_diagnostics), unchecked + len(missing_diagnostics), limit)
        return self._with_unavailable_servers(result, missing_server_keys)

    def shutdown(self, root: Path | None, timeout_seconds: float = 5.0) -> str:
        if root is None:
            return "LSP|not_configured"
        timeout_seconds = max(0.1, timeout_seconds)
        if not self.sessions:
            return f"LSP|stopped|{root.as_posix()}"

        lines = []
        for key in sorted(self.sessions):
            session = self.sessions[key]
            state = session.shutdown(timeout_seconds)
            lines.append(f"S:{key}/{state}")
        self.sessions.clear()
        self.session_signatures.clear()
        self.unavailable_servers.clear()
        return "\n".join([f"LSP|stopped|{root.as_posix()}"] + lines)

    def stop_all(self, timeout_seconds: float = 2.0) -> None:
        for session in list(self.sessions.values()):
            session.shutdown(timeout_seconds)
        self.sessions.clear()
        self.session_signatures.clear()
        self.unavailable_servers.clear()

    def _target_specs(self, files: list[dict[str, Any]]) -> list[tuple[LspServerSpec, int]]:
        targets = []
        for spec in SERVER_SPECS:
            count = sum(1 for item in files if is_file_supported(spec, item))
            if count > 0:
                targets.append((spec, count))
        return targets

    def _resolve_executable(self, name: str, root: Path) -> str | None:
        try:
            return self.executable_resolver(name, root)
        except TypeError:
            return self.executable_resolver(name)

    def _server_line(self, spec: LspServerSpec, state: str, count: int, root: Path, bootstrap: ClangdBootstrap) -> str:
        flags = []
        if spec.key == "clangd":
            flags.extend(bootstrap.flags or (f"ccdb{presence(root / 'compile_commands.json')}", f".clangd{presence(root / '.clangd')}", "cfg=none"))
        suffix = "/" + "/".join(flags) if flags else ""
        return f"S:{spec.key}/{spec.family}/{state}/files={count}{suffix}"

    def _check_targets(self, root: Path, files: list[dict[str, Any]], path: str | None) -> list[dict[str, Any]]:
        if not path:
            return self._default_check_targets(files)
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
    ) -> tuple[list[tuple[LspSession, str, str]], int]:
        opened = []
        unchecked = 0
        for item in files:
            session = self._session_for_item(item, running)
            if session is None:
                unchecked += 1
                continue
            try:
                text, uri = read_document(item)
                session.open_document(uri, language_id(item), int(item.get("mtime_ns", 1)), text, workspace_signature)
                opened.append((session, uri, item["path"]))
            except (OSError, UnicodeDecodeError, ValueError):
                unchecked += 1
        return opened, unchecked

    def _workspace_signature(self, files: list[dict[str, Any]]) -> str:
        digest = hashlib.sha1()
        for item in sorted(files, key=lambda value: value["path"]):
            if not any(is_file_supported(spec, item) for spec in SERVER_SPECS):
                continue
            digest.update(item["path"].encode("utf-8", errors="surrogateescape"))
            digest.update(b"\0")
            digest.update(str(item.get("sha1", "")).encode("ascii", errors="ignore"))
            digest.update(b"\0")
            digest.update(str(item.get("size", 0)).encode("ascii", errors="ignore"))
            digest.update(b"\0")
            digest.update(str(item.get("mtime_ns", 0)).encode("ascii", errors="ignore"))
            digest.update(b"\0")
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
        per_session_timeout = max(0.1, timeout_seconds) / max(1, len(by_session))
        for session, uris in by_session.items():
            session.wait_for_diagnostics(uris, per_session_timeout)
        return {
            (session, uri)
            for session, uris in by_session.items()
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
