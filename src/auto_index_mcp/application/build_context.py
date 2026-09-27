from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..domain.ignore_config import IgnoreConfig
from ..embedding.indexer import SymbolEmbedder
from ..storage.index import IndexStore


@dataclass(frozen=True)
class RebuildContext:
    """Immutable context for one full-tree rebuild."""

    root: Path
    index_root: Path
    store: IndexStore
    embedding_indexer: SymbolEmbedder | None
    ignore_config: IgnoreConfig
    reuse_if_fresh: bool = False
    policy_generation: int = 0
    source_generation: int = 0

    @property
    def key(self) -> tuple[Path, Path]:
        return (self.root.resolve(), self.index_root.resolve())
