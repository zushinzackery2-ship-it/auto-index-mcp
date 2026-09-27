from __future__ import annotations

import argparse


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="auto-index-mcp list",
        description="List every index recorded in the user-level registry.",
    )
    parser.parse_args(argv)

    from ..runtime.timefmt import iso_time
    from ..registry import IndexRegistry, index_dir_size

    registry = IndexRegistry()
    entries = registry.verify()
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
        size = index_dir_size(entry["index_dir"]) if entry["index_exists"] else 0
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
