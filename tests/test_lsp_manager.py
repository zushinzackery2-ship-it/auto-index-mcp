from pathlib import Path

from auto_index_mcp.lsp.checks import run_check
from auto_index_mcp.lsp.manager import LspManager


class FakeClient:
    def __init__(self, spec) -> None:
        self.spec = spec
        self._running = True
        self._diags: dict[str, list] = {}
        self.opened: list[str] = []

    def start(self, timeout_seconds: float) -> str:
        return "ready"

    def is_running(self) -> bool:
        return self._running

    def open_document(self, uri: str, language_id: str, text: str, signature: str = "") -> None:
        self.opened.append(uri)
        self._diags.setdefault(uri, [
            {"severity": 1, "range": {"start": {"line": 0, "character": 0}}, "message": "boom"}
        ])

    def wait_for_diagnostics(self, uris, timeout_seconds):
        return {uri for uri in uris if uri not in self._diags}

    def received_diagnostics(self, uri: str) -> bool:
        return uri in self._diags

    def diagnostics_for(self, uri: str):
        return self._diags.get(uri, [])

    def pull_diagnostics(self, uri: str, timeout_seconds: float) -> None:
        pass

    def shutdown(self, timeout_seconds: float) -> str:
        self._running = False
        return "stopped"


def _manager(tmp_path: Path) -> tuple[LspManager, list]:
    created: list = []

    def factory(spec, executable, root):
        created.append(spec.key)
        return FakeClient(spec)

    manager = LspManager(
        tmp_path / "lsp",
        executable_resolver=lambda name, root=None: "/bin/" + name,
        client_factory=factory,
    )
    return manager, created


PY_FILES = [{"path": "a.py", "language": "python", "extension": ".py", "sha1": "x", "mtime_ns": 1}]


def test_start_lazily_spawns_only_present_families(tmp_path: Path) -> None:
    manager, created = _manager(tmp_path)

    result = manager.start(tmp_path, PY_FILES, 2.0)

    assert "LSP|ready" in result
    assert created == ["pyright"]  # no clangd/tsserver/etc for a python-only project
    assert set(manager.running_clients()) == {"pyright"}
    assert manager.shutdown(1.0).startswith("LSP|stopped")
    assert manager.running_clients() == {}


def test_run_check_reports_pushed_diagnostics(tmp_path: Path) -> None:
    manager, _ = _manager(tmp_path)
    manager.start(tmp_path, PY_FILES, 2.0)

    def read_document(item):
        return "x = 1\n", (tmp_path / item["path"]).resolve().as_uri()

    result = run_check(manager, tmp_path, PY_FILES, read_document, path=None, limit=80, timeout_seconds=2.0)

    assert result.startswith("CHK|issues")
    assert "boom" in result


def test_start_reports_missing_when_executable_absent(tmp_path: Path) -> None:
    manager = LspManager(
        tmp_path / "lsp",
        executable_resolver=lambda name, root=None: None,
        client_factory=lambda spec, executable, root: FakeClient(spec),
    )

    result = manager.start(tmp_path, PY_FILES, 2.0)

    assert "unavailable" in result and "pyright" in result
    assert manager.running_clients() == {}
