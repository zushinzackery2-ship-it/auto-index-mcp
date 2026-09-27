"""Uniform tool-layer error handling.

Service-layer exceptions used to escape as raw MCP protocol errors
("Error executing tool ...") with no recovery guidance. Every tool call is
routed through :func:`run_tool`, so the agent always receives a structured
dictionary with an ``error`` code and an actionable ``hint`` instead.
"""

from __future__ import annotations

from typing import Any, Callable
import logging
import time

import anyio

from ..domain.errors import ServiceError
from ..runtime.diagnostics import diagnostic_scope
from ..domain.responses import (
    error_response,
    internal_error,
    invalid_argument,
)


def run_tool(fn: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> dict[str, Any]:
    try:
        return fn(*args, **kwargs)
    except ValueError as exc:
        return invalid_argument(str(exc))
    except ServiceError as exc:
        return error_response(exc.code, str(exc), hint=exc.hint, **exc.details)
    except KeyError as exc:
        message = exc.args[0] if exc.args else str(exc)
        return error_response("not-found", str(message))
    except TimeoutError as exc:
        return error_response("busy", str(exc), hint="check auto_index_status() for active operations")
    except Exception as exc:  # noqa: BLE001 - last-resort structured surface
        logging.getLogger(__name__).exception("tool failed function=%s", getattr(fn, "__name__", repr(fn)))
        return internal_error(exc)


async def run_service(service: Any, fn: Callable, *args: Any, control_plane: bool = False, **kwargs: Any) -> Any:
    """Serialize service requests off the MCP loop; protocol traffic stays live."""
    def invoke():
        with diagnostic_scope(getattr(service, "index_root", None)):
            started = time.monotonic()
            result = execute()
            code = result.get("error") if isinstance(result, dict) else None
            logger = logging.getLogger(__name__)
            logger.log(logging.WARNING if code else logging.INFO,
                       "request=%s error=%s elapsed=%.3fs", getattr(fn, "__name__", "call"),
                       code, time.monotonic() - started)
            return result

    def execute():
        if control_plane:
            return run_tool(fn, *args, **kwargs)
        with service._request_lock:
            return run_tool(fn, *args, **kwargs)

    return await anyio.to_thread.run_sync(invoke)
