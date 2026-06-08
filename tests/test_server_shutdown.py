from types import SimpleNamespace

import pytest

from auto_index_mcp.mcp_api import server


class DummyService:
    def __init__(self) -> None:
        self.enabled = []
        self.started = 0
        self.stopped = 0

    def enable_reusing_index(self, root_path: str, rebuild: bool = False) -> dict:
        self.enabled.append((root_path, rebuild))
        return {"root": root_path, "updated_at": 1.0}

    def start_watcher(self, wait_ready: bool = True) -> dict:
        _ = wait_ready
        self.started += 1
        return {"running": True}

    def stop_watcher(self) -> dict:
        self.stopped += 1
        return {"running": False}


class DummyMcp:
    def __init__(self, exc: Exception | None = None) -> None:
        self.exc = exc
        self.settings = SimpleNamespace(port=None)
        self.runs = []

    def run(self, transport: str) -> None:
        self.runs.append(transport)
        if self.exc:
            raise self.exc


def test_main_stops_watcher_when_run_returns(monkeypatch: pytest.MonkeyPatch) -> None:
    service = DummyService()
    mcp = DummyMcp()
    monkeypatch.setattr(server, "_service", service)
    monkeypatch.setattr(server, "mcp", mcp)
    monkeypatch.setattr(server, "_register_shutdown_hooks", lambda: None)
    monkeypatch.setattr(
        server,
        "_parse_args",
        lambda: SimpleNamespace(
            project_path="project",
            rebuild=False,
            no_rebuild=False,
            no_watch=False,
            transport="stdio",
            port=8000,
        ),
    )

    server.main()

    assert service.enabled == [("project", False)]
    assert service.started == 1
    assert service.stopped == 1
    assert mcp.runs == ["stdio"]


def test_main_stops_watcher_when_run_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    service = DummyService()
    mcp = DummyMcp(RuntimeError("server stopped"))
    monkeypatch.setattr(server, "_service", service)
    monkeypatch.setattr(server, "mcp", mcp)
    monkeypatch.setattr(server, "_register_shutdown_hooks", lambda: None)
    monkeypatch.setattr(
        server,
        "_parse_args",
        lambda: SimpleNamespace(
            project_path="project",
            rebuild=False,
            no_rebuild=True,
            no_watch=False,
            transport="streamable-http",
            port=9000,
        ),
    )

    with pytest.raises(RuntimeError, match="server stopped"):
        server.main()

    assert service.enabled == [("project", False)]
    assert service.started == 1
    assert service.stopped == 1
    assert mcp.settings.port == 9000
    assert mcp.runs == ["streamable-http"]


def test_shutdown_signal_stops_watcher_before_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    service = DummyService()
    monkeypatch.setattr(server, "_service", service)

    with pytest.raises(SystemExit) as exc_info:
        server._handle_shutdown_signal(15, None)

    assert service.stopped == 1
    assert exc_info.value.code == 143
