from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .clangd_bootstrap import ClangdBootstrap
from .lsp_session import DiagnosticLine


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


def is_file_supported(spec: LspServerSpec, item: dict[str, Any]) -> bool:
    return item.get("language", "") in spec.languages or item.get("extension", "") in spec.extensions


def effective_spec(spec: LspServerSpec, bootstrap: ClangdBootstrap) -> LspServerSpec:
    if spec.key != "clangd" or not bootstrap.args:
        return spec
    return LspServerSpec(spec.key, spec.family, spec.executable, tuple(dict.fromkeys((*spec.args, *bootstrap.args))), spec.languages, spec.extensions)


def language_id(item: dict[str, Any]) -> str:
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


def diagnostic_line(path: str, diagnostic: dict[str, Any]) -> DiagnosticLine:
    start = diagnostic.get("range", {}).get("start", {})
    return DiagnosticLine(
        severity=severity(diagnostic.get("severity")),
        path=path,
        line=int(start.get("line", 0)) + 1,
        character=int(start.get("character", 0)) + 1,
        message=" ".join(str(diagnostic.get("message", "")).split()),
    )


def format_check_result(diagnostics: list[DiagnosticLine], checked: int, unchecked: int, limit: int) -> str:
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


def severity(value: Any) -> str:
    return {1: "E", 2: "W", 3: "I", 4: "H"}.get(value, "D")


def presence(path: Path) -> str:
    return "+" if path.exists() else "-"
