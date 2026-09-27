"""Fixed synthetic workloads; all fixtures and registry state are temporary."""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import statistics
import sys
import tempfile
import threading
import time
import tracemalloc
from pathlib import Path

import psutil

from auto_index_mcp.application.watch_session import WatchSession
from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.embedding.backend import BagHashEmbedder
from auto_index_mcp.registry import IndexRegistry
from auto_index_mcp.storage.embeddings import EmbeddingStore
from auto_index_mcp.embedding.indexer import SymbolEmbedder


def measure(fn):
    process = psutil.Process()
    cpu_start = sum(process.cpu_times()[:2])
    rss_start = process.memory_info().rss
    peak_rss = [rss_start]
    stopped = threading.Event()

    def sample():
        while not stopped.wait(0.005):
            peak_rss[0] = max(peak_rss[0], process.memory_info().rss)

    sampler = threading.Thread(target=sample, daemon=True)
    tracemalloc.start()
    sampler.start()
    started = time.perf_counter()
    try:
        result = fn()
        elapsed = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        peak_rss[0] = max(peak_rss[0], process.memory_info().rss)
    finally:
        stopped.set()
        sampler.join()
        tracemalloc.stop()
    return dict(seconds=round(elapsed, 4), python_peak_mib=round(peak / 1048576, 3),
                rss_start_mib=round(rss_start / 1048576, 3), rss_peak_mib=round(peak_rss[0] / 1048576, 3),
                cpu_seconds=round(sum(process.cpu_times()[:2]) - cpu_start, 4), result=result)


def module_path(root, index):
    return root / f"pkg{index % 20}" / f"mod_{index}.py"


def write_module(root, index, revision=0):
    path = module_path(root, index)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"def helper_{index}(value):\n    return value + {index + revision}\n\n"
        f"def run_{index}(value):\n    return helper_{index}(value)\n", encoding="utf-8",
    )
    return path


def query_latencies(service, root, files, count):
    samples = dict(symbol=[], text=[])
    for number in range(count + 1):
        index = (number * 997) % files
        for kind in samples:
            started = time.perf_counter()
            if kind == "symbol":
                result = service.symbol_search(f"helper_{index}")
            else:
                path = module_path(root, index).relative_to(root).as_posix()
                result = service.text_search("return", file_pattern=path)
            assert result["items"] and not result.get("error"), result
            if number:
                samples[kind].append((time.perf_counter() - started) * 1000)
    return dict((kind, dict(count=len(values), p50_ms=round(statistics.median(values), 3),
                            p95_ms=round(sorted(values)[math.ceil(len(values) * 0.95) - 1], 3)))
                for kind, values in samples.items())


def benchmark_index(root, files, queries):
    for index in range(files):
        write_module(root, index)
    service = AutoIndexService(root / ".auto-index-mcp")
    service.registry = IndexRegistry(root.parent / "registry.json")
    service.semantic_enabled = False
    try:
        cold = measure(lambda: service.enable(str(root), refresh_embedder=False))
        warm = measure(lambda: service.enable_reusing_index(str(root)))
        synced = measure(service.sync_index_to_filesystem)
        session = WatchSession(root, service.store, service.rebuild_sync)
        previous = session.baseline()
        changed = {write_module(root, 0, 1)}
        current = session.update_snapshot(previous, changed)
        single = measure(lambda: session.apply(previous, current))
        previous = current
        changed = {write_module(root, index, 2) for index in range(min(files, 100))}
        current = session.update_snapshot(previous, changed)
        burst = measure(lambda: session.apply(previous, current))
        assert single["result"]["rewritten"] == 1, single
        assert burst["result"]["rewritten"] == len(changed), burst
        latencies = query_latencies(service, root, files, queries)
        db_bytes = sum(path.stat().st_size for path in service.index_root.glob("*.db*"))
        for entry in (cold, warm):
            entry["result"] = dict((key, entry["result"].get(key))
                                   for key in ("status", "file_count", "reused_count", "error_count"))
        return dict(cold=cold, warm_reuse=warm, warm_sync=synced, single_update=single,
                    burst_update=burst, query_latency=latencies, database_mib=round(db_bytes / 1048576, 3))
    finally:
        service.disable()


def benchmark_embedding(root, files):
    symbols = []
    for index in range(files):
        path = f"file_{index:05}.py"
        (root / path).write_text("def compute(rows):\n    return sum(rows)\n", encoding="utf-8")
        symbols.append(dict(file_path=path, name="compute", line=1, end_line=2, kind="function"))
    store = EmbeddingStore(root / "embeddings.db")
    store.initialize()
    indexer = SymbolEmbedder(BagHashEmbedder(), store)
    cold = measure(lambda: indexer.embed_project(root, symbols))
    warm = measure(lambda: indexer.embed_project(root, symbols))
    # Warm optional numeric imports before tracing query allocations.
    indexer.search("compute rows", 10)
    search = measure(lambda: len(indexer.search("compute rows", 10)))
    return dict(backend="baghash-test-backend", cold=cold, warm=warm, search=search)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--files", type=int, default=1500)
    parser.add_argument("--queries", type=int, default=40)
    parser.add_argument("--mode", choices=("index", "embedding", "all"), default="index")
    args = parser.parse_args()
    if args.files < 1 or args.queries < 1:
        parser.error("files and queries must be positive")
    with tempfile.TemporaryDirectory(prefix="aidx_resource_") as temporary:
        root = Path(temporary)
        report = dict(files=args.files, python=sys.version.split()[0], platform=platform.platform(),
                      logical_cpus=os.cpu_count(), note="build/update timings include tracemalloc and 5ms RSS sampling")
        if args.mode in ("index", "all"):
            project = root / "project"
            project.mkdir()
            report["index"] = benchmark_index(project, args.files, args.queries)
        if args.mode in ("embedding", "all"):
            project = root / "semantic"
            project.mkdir()
            report["embedding"] = benchmark_embedding(project, args.files)
        print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
