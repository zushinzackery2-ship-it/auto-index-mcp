"""Structured, self-healing error payloads for MCP tool responses.

Raised exceptions surface to MCP clients as bare protocol errors with no
recovery guidance. Tools return these dictionaries instead: every error
carries a machine-readable ``error`` code, a human message, and a ``hint``
telling the calling agent what to do next (and ``candidates`` when the index
can suggest close matches).
"""

from __future__ import annotations

from typing import Any

ERROR_FORMAT = "auto_index_error"

PATH_FORMAT_HINT = (
    "paths are project-relative with forward slashes, e.g. src/pkg/module.py"
)
ENABLE_HINT = (
    "call auto_index_enable(root_path=<absolute project path>) first; "
    "the index persists across sessions so this is cheap when already built"
)


def error_response(
    error: str,
    message: str,
    hint: str = "",
    **extra: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "format": ERROR_FORMAT,
        "error": error,
        "message": message,
    }
    if hint:
        result["hint"] = hint
    for key, value in extra.items():
        if value is not None:
            result[key] = value
    return result


def file_not_found(path: str, candidates: list[str] | None = None) -> dict[str, Any]:
    hint = PATH_FORMAT_HINT
    if candidates:
        hint = "did you mean one of `candidates`? " + PATH_FORMAT_HINT
    else:
        hint += "; use auto_index_files(query=...) to locate the file"
    return error_response(
        "file-not-found",
        f"indexed file not found: {path}",
        hint=hint,
        given=path,
        candidates=candidates or None,
    )


def symbol_not_found(
    symbol_name: str,
    path: str = "",
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    where = f" in {path}" if path else ""
    hint = "use auto_index_symbol_search(text=...) to find the right symbol name"
    if candidates:
        hint = "did you mean one of `candidates`? " + hint
    return error_response(
        "symbol-not-found",
        f"symbol not found{where}: {symbol_name}",
        hint=hint,
        candidates=candidates or None,
    )


def not_enabled(message: str = "") -> dict[str, Any]:
    return error_response(
        "not-enabled",
        message or "no project root is configured for auto-index",
        hint=ENABLE_HINT,
    )


def invalid_argument(message: str) -> dict[str, Any]:
    return error_response("invalid-argument", message)


def internal_error(exc: BaseException) -> dict[str, Any]:
    return error_response(
        "internal-error",
        f"{type(exc).__name__}: {exc}",
        hint="check auto_index_status() for index health; retry after fixing the input",
    )
