from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _disable_default_embedding_backend(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    if request.node.get_closest_marker("allow_default_embedder"):
        return
    monkeypatch.setattr(
        "auto_index_mcp.core.service_watcher.create_embedder",
        lambda env=None: None,
    )
