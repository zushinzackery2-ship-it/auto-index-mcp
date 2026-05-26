from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .clangd_bootstrap import ClangdBootstrap, prepare_clangd
from .lsp_resolver import resolve_lsp_executable
from .lsp_session import DiagnosticLine, LspSession, ProcessFactory


ExecutableResolver = Callable[[str], str | None]
DocumentReader = Callable[[dict[str, Any]], tuple[str, str]]


@dataclass(frozen=True)
class LspServerSpec:
    key: str
    family: str
    executable: str
    args: tuple[str, ...]
    languages: frozenset[str]
    extensions: frozenset[str]


SERVER_SPECS = (
    LspServerSpec("clangd", "c-family", "clangd", (), frozenset({"c", "cpp"}), frozenset({".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".m", ".mm", ".cu"})),
    LspServerSpec("pyright", "python", "pyright-langserver", ("--stdio",), frozenset({"python"}), frozenset({".py"})),
    LspServerSpec("tsserver", "js-ts", "typescript-language-server", ("--stdio",), frozenset({"javascript", "typescript"}), frozenset({".js", ".jsx", ".ts", ".tsx"})),
    LspServerSpec("rust-analyzer", "rust", "rust-analyzer", (), frozenset({"rust"}), frozenset({".rs"})),
    LspServerSpec("gopls", "go", "gopls", (), frozenset({"go"}), frozenset({".go"})),
)


class LspManager:
    def __init__(
        self,
        executable_resolver: ExecutableResolver | None = None,
        process_factory: ProcessFactory | None = None,
    ) -> None:
        self.executable_resolver = executable_resolver or resolve_lsp_executable
        self.process_factory = process_factory or subprocess.Popen
        self.sessions: dict[str, LspSession] = {}

    def start(self, root: Path | None, files: list[dict[str, Any]], timeout_seconds: float = 10.0) -> str:
        if root is None:
            return "LSP|not_configured"
        timeout_seconds = max(0.1, timeout_seconds)
        targets = self._target_specs(files)
        if not targets:
            return f"LSP|no_targets|{root.as_posix()}"

        lines = []
        ready_count = 0
        missing_count = 0
        error_count = 0
        bootstrap = prepare_clangd(root, files)
        for spec, count in targets:
            session = self.sessions.get(spec.key)
            if session and session.is_running():
                state = "ready"
                ready_count += 1
            else:
                executable = self.executable_resolver(spec.executable)
                if not executable:
                    state = "missing"
                    missing_count += 1
                else:
                    session = LspSession(_effective_spec(spec, bootstrap), executable, self.process_factory)
                    self.sessions[spec.key] = session
                    state = session.start(root, timeout_seconds)
                    if state == "ready":
                        ready_count += 1
                    else:
                        error_count += 1
            lines.append(self._server_line(spec, state, count, root, bootstrap))

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
        if not running:
            return "CHK|not_started"

        targets = self._check_targets(files, path)
        if path and not targets:
            return f"CHK|not_found|{path}"
        opened, unchecked = self._open_targets(targets, running, read_document)
        self._wait_for_opened_diagnostics(opened, timeout_seconds)
        diagnostics = self._collect_diagnostics(opened, limit)
        return _format_check_result(diagnostics, len(opened), unchecked, limit)

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
        return "\n".join([f"LSP|stopped|{root.as_posix()}"] + lines)

    def stop_all(self, timeout_seconds: float = 2.0) -> None:
        for session in list(self.sessions.values()):
            session.shutdown(timeout_seconds)
        self.sessions.clear()

    def _target_specs(self, files: list[dict[str, Any]]) -> list[tuple[LspServerSpec, int]]:
        targets = []
        for spec in SERVER_SPECS:
            count = sum(1 for item in files if _is_file_supported(spec, item))
            if count > 0:
                targets.append((spec, count))
        return targets

    def _server_line(self, spec: LspServerSpec, state: str, count: int, root: Path, bootstrap: ClangdBootstrap) -> str:
        flags = []
        if spec.key == "clangd":
            flags.extend(bootstrap.flags or (f"ccdb{_presence(root / 'compile_commands.json')}", f".clangd{_presence(root / '.clangd')}", "cfg=none"))
        suffix = "/" + "/".join(flags) if flags else ""
        return f"S:{spec.key}/{spec.family}/{state}/files={count}{suffix}"

    def _check_targets(self, files: list[dict[str, Any]], path: str | None) -> list[dict[str, Any]]:
        if not path:
            return files
        needle = path.replace("\\", "/").strip("/").lower()
        return [item for item in files if item["path"].lower() == needle]

    def _open_targets(
        self,
        files: list[dict[str, Any]],
        running: dict[str, LspSession],
        read_document: DocumentReader,
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
                session.open_document(uri, _language_id(item), int(item.get("mtime_ns", 1)), text)
                opened.append((session, uri, item["path"]))
            except (OSError, UnicodeDecodeError, ValueError):
                unchecked += 1
        return opened, unchecked

    def _session_for_item(self, item: dict[str, Any], running: dict[str, LspSession]) -> LspSession | None:
        for spec in SERVER_SPECS:
            if spec.key in running and _is_file_supported(spec, item):
                return running[spec.key]
        return None

    def _wait_for_opened_diagnostics(self, opened: list[tuple[LspSession, str, str]], timeout_seconds: float) -> None:
        by_session: dict[LspSession, set[str]] = {}
        for session, uri, _ in opened:
            by_session.setdefault(session, set()).add(uri)
        per_session_timeout = max(0.1, timeout_seconds) / max(1, len(by_session))
        for session, uris in by_session.items():
            session.wait_for_diagnostics(uris, per_session_timeout)

    def _collect_diagnostics(self, opened: list[tuple[LspSession, str, str]], limit: int) -> list[DiagnosticLine]:
        rows = []
        for session, uri, path in opened:
            for diagnostic in session.diagnostics.get(uri, []):
                rows.append(_diagnostic_line(path, diagnostic))
                if len(rows) >= max(1, limit):
                    return rows
        return rows


def _is_file_supported(spec: LspServerSpec, item: dict[str, Any]) -> bool:
    return item.get("language", "") in spec.languages or item.get("extension", "") in spec.extensions


def _effective_spec(spec: LspServerSpec, bootstrap: ClangdBootstrap) -> LspServerSpec:
    if spec.key != "clangd" or not bootstrap.args:
        return spec
    return LspServerSpec(spec.key, spec.family, spec.executable, tuple(dict.fromkeys((*spec.args, *bootstrap.args))), spec.languages, spec.extensions)


def _language_id(item: dict[str, Any]) -> str:
    extension = item.get("extension", "")
    language = item.get("language", "text")
    if extension == ".c":
        return "c"
    if language == "cpp":
        return "cpp"
    if language == "javascript":
        return "javascript"
    if language == "typescript":
        return "typescript"
    return language


def _diagnostic_line(path: str, diagnostic: dict[str, Any]) -> DiagnosticLine:
    start = diagnostic.get("range", {}).get("start", {})
    return DiagnosticLine(
        severity=_severity(diagnostic.get("severity")),
        path=path,
        line=int(start.get("line", 0)) + 1,
        character=int(start.get("character", 0)) + 1,
        message=" ".join(str(diagnostic.get("message", "")).split()),
    )


def _format_check_result(diagnostics: list[DiagnosticLine], checked: int, unchecked: int, limit: int) -> str:
    if not diagnostics:
        status = "clean" if unchecked == 0 else "partial"
        suffix = f"|unchecked={unchecked}" if unchecked else ""
        return f"CHK|{status}|files={checked}{suffix}"
    status = "issues" if unchecked == 0 else "partial"
    lines = [f"CHK|{status}|count={len(diagnostics)}|files={checked}|limit={limit}"]
    if unchecked:
        lines[0] += f"|unchecked={unchecked}"
    lines.extend(f"{row.severity}|{row.path}|{row.line}:{row.character}|{row.message}" for row in diagnostics)
    return "\n".join(lines)


def _severity(value: Any) -> str:
    return {1: "E", 2: "W", 3: "I", 4: "H"}.get(value, "D")


def _presence(path: Path) -> str:
    return "+" if path.exists() else "-"
