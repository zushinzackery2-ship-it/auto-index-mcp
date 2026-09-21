"""Repeatable embedding memory / lookup benchmark; writes only temp fixtures."""
from __future__ import annotations

import argparse
import json
import tempfile
import time
import tracemalloc
from pathlib import Path

from auto_index_mcp.embedding.backend import BagHashEmbedder
from auto_index_mcp.embedding.embedding_store import EmbeddingStore
from auto_index_mcp.embedding.indexer import SymbolEmbedder


def measure(fn):
    tracemalloc.start()
    started = time.perf_counter()
    result = fn()
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return dict(seconds=round(elapsed, 3), peak_mib=round(peak / 1048576, 3), result=result)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--files", type=int, default=1500)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="aidx_resource_") as temporary:
        root = Path(temporary)
        symbols = []
        for index in range(args.files):
            path = f"file_{index:05}.py"
            (root / path).write_text("def compute(rows):\n    return sum(rows)\n", encoding="utf-8")
            symbols.append(dict(file_path=path, name="compute", line=1, end_line=2, kind="function"))
        store = EmbeddingStore(root / "embeddings.db")
        store.initialize()
        indexer = SymbolEmbedder(BagHashEmbedder(), store)
        print(json.dumps(dict(files=args.files, cold=measure(lambda: indexer.embed_project(root, symbols)))), flush=True)
        print(json.dumps(dict(warm=measure(lambda: indexer.embed_project(root, symbols)))), flush=True)
        # Import numpy before tracing so library-import allocations do not mask lookup costs.
        try:
            __import__("numpy")
        except ImportError:
            pass
        print(json.dumps(dict(search=measure(lambda: len(indexer.search("compute rows", 10))))), flush=True)


if __name__ == "__main__":
    main()
