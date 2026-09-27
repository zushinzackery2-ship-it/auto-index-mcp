"""Command dispatch; existing MCP launch configurations remain valid."""
from __future__ import annotations

import sys
import sqlite3


def main(argv: list[str] | None = None) -> int:
    from .commands import build, clean, listing, status

    args = list(sys.argv[1:]) if argv is None else list(argv)
    commands = dict(build=build.run, clean=clean.run, list=listing.run, status=status.run)
    if args and args[0] in commands:
        try:
            return commands[args[0]](args[1:])
        except (OSError, sqlite3.Error, RuntimeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    if args and args[0] == "serve":
        args = args[1:]
    from .mcp_api.server import main as serve_main

    serve_main(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
