from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..embedding.indexer import SymbolEmbedder
from ..indexing.store import IndexStore


@dataclass(frozen=True)
class RebuildContext:
    """Immutable context for one full-tree rebuild."""

    root: Path
    index_root: Path
    store: IndexStore
    embedding_indexer: SymbolEmbedder | None

    @property
    def key(self) -> tuple[Path, Path]:
        return (self.root.resolve(), self.index_root.resolve())
