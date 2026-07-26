from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest

from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.embedding.backend import BagHashEmbedder

# Shared helpers are exposed as fixtures only: tests/ and oldtest/ each carry
# a conftest.py, so explicit ``import conftest`` would be ambiguous.


@pytest.fixture(autouse=True)
def _disable_default_embedding_backend(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """Tests never pick up the host's real embedding model by accident."""
    if request.node.get_closest_marker("allow_default_embedder"):
        return
    monkeypatch.setattr(
        "auto_index_mcp.core.service_embedding.create_embedder",
        lambda env=None: None,
    )


class WindowedBagHashEmbedder(BagHashEmbedder):
    """BagHash stand-in with a deterministic windowing capability.

    Splits the text into fixed-size line windows so multi-chunk indexer and
    store behavior is testable without the real ONNX tokenizer.
    """

    def __init__(self, dim: int = 128, lines_per_window: int = 4) -> None:
        super().__init__(dim=dim)
        self.lines_per_window = lines_per_window

    @property
    def name(self) -> str:
        return "windowed-baghash"

    @property
    def text_fingerprint(self) -> str:
        return f"lines={self.lines_per_window}"

    def window_texts(self, text: str) -> list[str]:
        lines = text.splitlines()
        if len(lines) <= self.lines_per_window:
            return [text]
        windows = []
        for start in range(0, len(lines), self.lines_per_window):
            piece = "\n".join(lines[start:start + self.lines_per_window]).strip()
            if piece:
                windows.append(piece)
        return windows or [text]


@pytest.fixture
def windowed_embedder_cls() -> type[WindowedBagHashEmbedder]:
    return WindowedBagHashEmbedder


@pytest.fixture
def write_file() -> Callable[[Path, str], None]:
    def _write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    return _write


@pytest.fixture
def install_embedder(monkeypatch: pytest.MonkeyPatch) -> Callable[[Any], None]:
    def _install(embedder: Any) -> None:
        monkeypatch.setattr(
            "auto_index_mcp.core.service_embedding.create_embedder",
            lambda env=None: embedder,
        )

    return _install


@pytest.fixture
def make_service(tmp_path: Path) -> Callable[..., AutoIndexService]:
    services: list[AutoIndexService] = []

    def _make(project: Path, rebuild: bool = True) -> AutoIndexService:
        service = AutoIndexService(index_root=tmp_path / ".idx")
        service.enable(str(project), rebuild=rebuild)
        services.append(service)
        return service

    yield _make
    for service in services:
        try:
            service.disable()
        except Exception:
            pass


@pytest.fixture
def wait_embedding() -> Callable[..., dict]:
    def _wait(service: AutoIndexService, timeout: float = 10.0) -> dict:
        assert service.embedding_background is not None
        assert service.embedding_background.wait(timeout) is True
        status = service.embedding_background.status()
        assert status["state"] == "done"
        assert status["last_result"] is not None
        return status["last_result"]

    return _wait
