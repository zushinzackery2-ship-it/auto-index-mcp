from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="auto-index-mcp clean",
        description=(
            "Delete index directories recorded in the registry. With no "
            "selector this cleans orphaned and ephemeral entries only."
        ),
    )
    parser.add_argument("targets", nargs="*", help="project roots whose indexes should be removed")
    parser.add_argument("--all", action="store_true", help="select every registered index")
    parser.add_argument("--orphans", action="store_true", help="select entries whose project or index is gone")
    parser.add_argument("--ephemeral", action="store_true", help="select entries built at an overridden index location")
    parser.add_argument("--older-than", default=None, metavar="AGE",
                        help="only entries not attached for AGE (e.g. 30d, 12h; bare number = days)")
    parser.add_argument("--scan", default=None, metavar="DIR",
                        help="scan DIR for existing indexes and adopt them into the registry (no deletion)")
    parser.add_argument("--dry-run", action="store_true", help="print what would happen without deleting")
    parser.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args(argv)

    from ..registry import IndexRegistry

    registry = IndexRegistry()
    if args.scan is not None:
        return _clean_scan(registry, args.scan)

    try:
        max_age = _parse_age(args.older_than) if args.older_than else None
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    candidates = _clean_candidates(registry, args, max_age)
    if not candidates:
        print("nothing to clean")
        return 0
    for candidate in candidates:
        marker = " (index already gone; registry entry only)" if not candidate["index_exists"] else ""
        print(f"clean: {candidate['root']} -> {candidate['index_dir']}{marker}")
    if args.dry_run:
        print(f"dry run: {len(candidates)} entries selected, nothing deleted")
        return 0
    if not args.yes and not _confirm(f"delete {len(candidates)} index directories? [y/N] "):
        print("aborted")
        return 1
    return _clean_apply(registry, candidates)


def _clean_scan(registry: Any, base: str) -> int:
    base_path = Path(base).resolve()
    if not base_path.is_dir():
        print(f"error: not a directory: {base_path}", file=sys.stderr)
        return 1
    adopted = registry.scan_and_adopt(base_path)
    for entry in adopted:
        print(f"adopted: {entry['root']} -> {entry['index_dir']}")
    print(f"scan: {len(adopted)} new indexes registered under {base_path}")
    return 0


def _clean_candidates(registry: Any, args: Any, max_age: float | None) -> list[dict[str, Any]]:
    from ..registry import registry_key

    entries = registry.verify()
    target_keys = {registry_key(Path(target)) for target in args.targets}
    use_default = not (args.targets or args.all or args.orphans or args.ephemeral)
    # An explicit --older-than alone means "everything old enough", not the
    # orphans+ephemeral safety default.
    if use_default and max_age is not None:
        selected = entries
    elif use_default:
        selected = [entry for entry in entries if entry["orphan"] or entry.get("ephemeral")]
    else:
        selected = [
            entry for entry in entries
            if args.all
            or registry_key(entry["root"]) in target_keys
            or entry["key"] in target_keys
            or (args.orphans and entry["orphan"])
            or (args.ephemeral and entry.get("ephemeral"))
        ]
    if max_age is not None:
        cutoff = time.time() - max_age
        selected = [
            entry for entry in selected
            if float(entry.get("last_attached_at") or 0.0) < cutoff
        ]
    return selected


def _clean_apply(registry: Any, candidates: list[dict[str, Any]]) -> int:
    from ..registry.cleanup import clean_entry

    failures = 0
    for entry in candidates:
        result = clean_entry(registry, entry)
        if result["status"] not in ("removed", "unregistered"):
            failures += 1
        print(f"{result['status']}: {entry['index_dir']}")
        for note in result["notes"]:
            print(f"    {note}")
    return 1 if failures else 0


def _parse_age(text: str) -> float:
    """Duration in seconds from '30d' / '12h' / '45m' / '90s' (bare = days)."""
    import re

    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([smhdw]?)", text.strip().lower())
    if match is None:
        raise ValueError(f"invalid --older-than value: {text!r} (expected e.g. 30d, 12h)")
    value = float(match.group(1))
    unit = match.group(2) or "d"
    seconds = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0, "w": 604800.0}[unit]
    return value * seconds


def _confirm(prompt: str) -> bool:
    if not sys.stdin.isatty():
        print("refusing to delete without -y on a non-interactive terminal", file=sys.stderr)
        return False
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False
