"""Cold-start bootstrap: resolve the project root without user friction.

A fresh MCP session used to require the agent to know it must call
``auto_index_enable`` with the right absolute path before anything worked.
Now every tool funnels through :func:`ensure_enabled`, which resolves the
root through progressively broader fallbacks:

1. explicit ``--project-path`` / prior enable (service already configured);
2. ``AUTO_INDEX_PROJECT_PATH`` environment variable;
3. the MCP client's ``roots`` capability (workspace folders);
4. a structured error telling the agent exactly what to call.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Optional

import anyio

from mcp.server.fastmcp import Context

from ..core.service import AutoIndexService
from ..domain.responses import not_enabled

PROJECT_PATH_ENV = "AUTO_INDEX_PROJECT_PATH"


def resolve_env_root() -> Path | None:
    raw = os.environ.get(PROJECT_PATH_ENV, "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_dir() else None


def uri_to_path(uri: str) -> Path | None:
    """Filesystem path for a ``file://`` URI, or None for other schemes."""
    from urllib.parse import unquote, urlsplit

    parsed = urlsplit(str(uri))
    if parsed.scheme != "file":
        return None
    raw = unquote(parsed.path or "")
    if parsed.netloc and parsed.netloc.lower() != "localhost":
        raw = f"//{parsed.netloc}{raw}"  # UNC share
    elif os.name == "nt" and re.match(r"^/[A-Za-z]:", raw):
        raw = raw[1:]
    if not raw:
        return None
    return Path(raw)


async def roots_from_client(ctx: Optional[Context]) -> list[Path]:
    """Workspace roots advertised by the MCP client, best-effort."""
    if ctx is None:
        return []
    try:
        with anyio.fail_after(3.0):
            result = await ctx.session.list_roots()
    except Exception:  # noqa: BLE001 - clients without roots support raise here
        return []
    found: list[Path] = []
    for root in result.roots:
        path = uri_to_path(str(root.uri))
        if path is not None and path.is_dir():
            found.append(path)
    return found


async def ensure_enabled(
    service: AutoIndexService,
    ctx: Optional[Context],
) -> dict[str, Any] | None:
    """Lazily enable the index when a tool is called on a fresh session.

    Returns None when the service is usable, otherwise a structured error
    the tool should return as-is.
    """
    if service.enabled and service.store is not None and service.root_path is not None:
        return None
    root = await resolve_project_root(service, ctx)
    if root is None:
        return not_enabled(
            "no project root is configured and none could be discovered "
            "from the environment or the MCP client"
        )
    from .lifecycle import start_or_defer_auto_watch
    from .guard import run_service

    def attach():
        if service.enabled and service.store is not None:
            return dict(enabled=True)
        result = service.enable_reusing_index(str(root), source="cold-start")
        start_or_defer_auto_watch(service, result)
        return result

    result = await run_service(service, attach)
    return result if "error" in result else None


async def resolve_project_root(
    service: AutoIndexService,
    ctx: Optional[Context],
) -> Path | None:
    if service.root_path is not None:
        return service.root_path
    env_root = resolve_env_root()
    if env_root is not None:
        return env_root
    client_roots = await roots_from_client(ctx)
    if client_roots:
        return client_roots[0]
    return None
