from __future__ import annotations

import argparse
import json
from pathlib import Path


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="auto-index-mcp list",
        description="List every index recorded in the user-level registry.",
    )
    parser.add_argument("--json", action="store_true", help="include cache and model space accounting as JSON")
    args = parser.parse_args(argv)

    from ..runtime.timefmt import iso_time
    from ..registry import IndexRegistry, index_dir_size

    registry = IndexRegistry()
    entries = registry.verify()
    from ..cache.inventory import cache_inventory
    from ..registry.cleanup import build_lock_active
    if args.json:
        print(json.dumps(dict(registry=str(registry.path()), entries=[
            dict(entry, space=cache_inventory(Path(entry["index_dir"])), active=build_lock_active(entry["index_dir"]))
            for entry in entries
        ]), indent=2))
        return 0
    print(f"registry: {registry.path()}")
    if not entries:
        print("no registered indexes")
        return 0
    for entry in entries:
        flags = []
        if entry["orphan"]:
            flags.append("orphan")
        if entry.get("ephemeral"):
            flags.append("ephemeral")
        if entry.get("state") in ("residual", "coordination", "failed"):
            flags.append(entry["state"])
        if build_lock_active(entry["index_dir"]):
            flags.append("active")
        size = index_dir_size(entry["index_dir"])
        note = f"  [{', '.join(flags)}]" if flags else ""
        print(f"{entry['root']}{note}")
        print(f"    index:         {entry['index_dir']}  ({_human_size(size)})")
        print(f"    last attached: {iso_time(entry.get('last_attached_at'))}"
              f"  source: {entry.get('source', '?')}")
    return 0


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024.0 or unit == "GiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024.0
    return f"{int(size)} B"
