"""Explicit space reclamation and model retention for registered caches."""
import argparse
import json
from pathlib import Path

from ..cache.inventory import cache_inventory
from ..registry import IndexRegistry, registry_key
from ..registry.compaction import compact_entry


def positive_integer(text):
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("value must be positive")
    return value


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="auto-index-mcp compact",
                                     description="Reclaim SQLite free pages and optionally prune model namespaces.")
    parser.add_argument("targets", nargs="+", help="registered project roots or index directories")
    parser.add_argument("--max-models", type=positive_integer, default=None)
    parser.add_argument("--max-vector-bytes", type=positive_integer, default=None)
    parser.add_argument("--keep-model", default=None, help="protect this exact model namespace from pruning")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    registry = IndexRegistry()
    if not args.dry_run:
        for target in args.targets:
            registry.reconcile_root(target)
    keys = set(registry_key(target) for target in args.targets)
    entries = [entry for entry in registry.verify(roots=args.targets if args.dry_run else None)
               if entry["key"] in keys or registry_key(entry["root"]) in keys]
    if not entries:
        print("no registered caches selected")
        return 1
    for entry in entries:
        if args.dry_run:
            print(json.dumps(dict(index=entry["index_dir"], space=cache_inventory(Path(entry["index_dir"])),
                                  max_models=args.max_models, max_vector_bytes=args.max_vector_bytes), indent=2))
        else:
            result = compact_entry(entry, args.keep_model, args.max_models, args.max_vector_bytes)
            print(f"compacted: {entry['index_dir']}; reclaimed {result['reclaimed']} bytes")
            print(json.dumps(result["retention"]))
    return 0
