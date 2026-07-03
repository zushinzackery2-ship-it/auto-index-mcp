"""One-off benchmark: embedding cost/coverage vs max_length and padding mode.

Reads real symbol rows from this project's .auto-index-mcp/index.db, rebuilds
the exact embedding texts the indexer would produce, then times the ONNX
backend at several truncation lengths with fixed vs dynamic padding. Prints a
table plus token-coverage stats so the default max_length is picked from data,
not guesswork. Not wired into CI; run manually:

    python scripts/bench_embedding_length.py
"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from auto_index_mcp.embedding.backend import resolve_embedding_model_path  # noqa: E402
from auto_index_mcp.embedding.indexer import _symbol_text  # noqa: E402
from auto_index_mcp.core.text_decode import read_text_file  # noqa: E402

LENGTHS = (64, 128, 192, 256)


def load_symbol_texts() -> list[str]:
    db_path = ROOT / ".auto-index-mcp" / "index.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT file_path, name, kind, signature, line, end_line, complexity FROM symbols"
    ).fetchall()
    conn.close()
    lines_cache: dict[str, list[str]] = {}
    texts: list[str] = []
    for row in rows:
        path = row["file_path"]
        if path not in lines_cache:
            try:
                lines_cache[path] = read_text_file(ROOT / path).splitlines()
            except (OSError, UnicodeDecodeError):
                lines_cache[path] = []
        texts.append(_symbol_text(dict(row), lines_cache[path]))
    return texts


def token_coverage(texts: list[str], tokenizer_dir: Path) -> None:
    from tokenizers import Tokenizer

    tokenizer = Tokenizer.from_file(str(tokenizer_dir / "tokenizer.json"))
    # tokenizer.json may ship with truncation/padding persisted; disable both so
    # the distribution reflects true text lengths.
    tokenizer.no_truncation()
    tokenizer.no_padding()
    counts = sorted(len(enc.ids) for enc in tokenizer.encode_batch(texts))
    total = len(counts)

    def pct_within(limit: int) -> float:
        import bisect

        return 100.0 * bisect.bisect_right(counts, limit) / total

    print(f"symbols: {total}")
    print(f"token count p50={counts[total // 2]} p90={counts[int(total * 0.9)]} max={counts[-1]}")
    for limit in LENGTHS:
        print(f"  fully covered at max_length={limit}: {pct_within(limit):.1f}%")


def bench(texts: list[str], model_dir: Path) -> None:
    from auto_index_mcp.embedding.onnx_backend import OnnxEmbedder

    batch = 8
    for max_length in LENGTHS:
        for dynamic in (False, True):
            embedder = OnnxEmbedder(model_dir, max_length=max_length)
            if dynamic:
                embedder._tokenizer.enable_padding()
            start = time.perf_counter()
            for index in range(0, len(texts), batch):
                embedder.embed(texts[index : index + batch])
            elapsed = time.perf_counter() - start
            mode = "dynamic" if dynamic else "fixed"
            print(f"max_length={max_length:3d} padding={mode:7s} total={elapsed:7.2f}s")


def main() -> None:
    model_dir = resolve_embedding_model_path()
    if model_dir is None:
        raise SystemExit("no embedding model available")
    texts = load_symbol_texts()
    token_coverage(texts, model_dir)
    bench(texts, model_dir)


if __name__ == "__main__":
    main()
