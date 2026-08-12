"""Uniform tool-layer error handling.

Service-layer exceptions used to escape as raw MCP protocol errors
("Error executing tool ...") with no recovery guidance. Every tool call is
routed through :func:`run_tool`, so the agent always receives a structured
dictionary with an ``error`` code and an actionable ``hint`` instead.
"""

from __future__ import annotations

from typing import Any, Callable

from ..core.tool_errors import (
    ENABLE_HINT,
    error_response,
    internal_error,
    invalid_argument,
)


def run_tool(fn: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> dict[str, Any]:
    try:
        return fn(*args, **kwargs)
    except ValueError as exc:
        return invalid_argument(str(exc))
    except KeyError as exc:
        message = exc.args[0] if exc.args else str(exc)
        return error_response("not-found", str(message))
    except RuntimeError as exc:
        return error_response("not-enabled", str(exc), hint=ENABLE_HINT)
    except Exception as exc:  # noqa: BLE001 - last-resort structured surface
        return internal_error(exc)
