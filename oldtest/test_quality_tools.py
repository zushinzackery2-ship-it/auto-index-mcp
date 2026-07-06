from pathlib import Path
import json
from typing import Any

from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.mcp_api.quality import register_quality_tools
from auto_index_mcp.workspace.view import WorkspaceView


def test_nesting_check_uses_persisted_symbol_nesting(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text(
        "\n".join(
            [
                "class Runner:",
                "    def run(self):",
                "        def inner():",
                "            if True:",
                "                for item in [1]:",
                "                    while item:",
                "                        return item",
                "        return inner()",
            ]
        ),
        encoding="utf-8",
    )

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)

    summary = service.file_summary("main.py")
    inner = next(symbol for symbol in summary["symbols"] if symbol["name"] == "inner")
    nesting = service.nesting_check(max_depth=1)

    assert inner["parent_name"] == "run"
    assert inner["parent_kind"] == "method"
    assert inner["depth"] == 2
    assert inner["nesting_path"] == "Runner.run.inner"
    assert inner["max_block_depth"] == 3
    assert nesting["summary"]["max_symbol_depth"] == 2
    assert nesting["summary"]["max_block_depth"] == 3
    assert nesting["summary"]["missing_nesting_symbols"] == 0
    assert any(finding["symbol"] == "inner" for finding in nesting["findings"])

    assert service.store is not None
    with service.store.read_connect() as conn:
        row = conn.execute(
            """
            SELECT depth, nesting_path, max_block_depth
            FROM symbol_nesting
            WHERE file_path=? AND symbol_name=?
            """,
            ("main.py", "inner"),
        ).fetchone()
        quality_row = conn.execute(
            "SELECT quality_findings FROM files WHERE path=?",
            ("main.py",),
        ).fetchone()

    assert dict(row) == {"depth": 2, "nesting_path": "Runner.run.inner", "max_block_depth": 3}
    assert isinstance(json.loads(quality_row["quality_findings"]), list)


def test_dangling_check_reports_unused_symbol_and_unreachable_code(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text(
        "\n".join(
            [
                "def used():",
                "    return 1",
                "",
                "def caller():",
                "    return used()",
                "",
                "def unused():",
                "    value = 1",
                "    return value",
                "    value += 1",
                "",
                "def main():",
                "    return caller()",
            ]
        ),
        encoding="utf-8",
    )

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)

    def fail_source_read(self: WorkspaceView, root: Path, item: dict[str, Any]) -> str:
        raise AssertionError("quality tools must use cached index findings")

    monkeypatch.setattr(WorkspaceView, "read_indexed_text", fail_source_read)
    result = service.dangling_check()
    findings = result["findings"]

    assert any(finding["kind"] == "unused_symbol" and finding["symbol"] == "unused" for finding in findings)
    assert not any(finding["kind"] == "unused_symbol" and finding["symbol"] == "used" for finding in findings)
    assert any(
        finding["kind"] == "unreachable_statement"
        and finding["confidence"] == "high"
        and finding["line"] == 10
        for finding in findings
    )


def test_dangling_check_ignores_protocol_type_helpers(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text(
        "\n".join(
            [
                "from typing import Protocol, cast",
                "",
                "class _Service(Protocol):",
                "    def ready(self) -> bool:",
                "        ...",
                "",
                "class Runner:",
                "    def run(self) -> bool:",
                "        service = cast(_Service, self)",
                "        return service.ready()",
            ]
        ),
        encoding="utf-8",
    )

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)

    findings = service.dangling_check()["findings"]

    assert not any(finding.get("symbol") == "_Service" for finding in findings)


def test_dangling_check_ignores_dynamic_wiring_helpers(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text(
        "\n".join(
            [
                "class ServiceMixin:",
                "    def ready(self):",
                "        return True",
                "",
                "class Factory:",
                "    @classmethod",
                "    def create(cls):",
                "        return cls()",
                "",
                "def register_search_tools(mcp, service):",
                "    return service",
                "",
                "def boot():",
                "    return Factory.create()",
                "",
                "class UnusedBox:",
                "    def method(self):",
                "        return None",
                "",
                "def unused():",
                "    return None",
            ]
        ),
        encoding="utf-8",
    )

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)

    dangling_symbols = {finding.get("symbol") for finding in service.dangling_check()["findings"]}

    assert "ServiceMixin" not in dangling_symbols
    assert "Factory" not in dangling_symbols
    assert "register_search_tools" not in dangling_symbols
    assert "UnusedBox" in dangling_symbols
    assert "unused" in dangling_symbols


def test_dangling_check_counts_calls_after_hash_inside_string(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text(
        "\n".join(
            [
                "def caller(value):",
                "    if value.startswith('#') or _helper(value):",
                "        return True",
                "    return False",
                "",
                "def _helper(value):",
                "    return bool(value)",
            ]
        ),
        encoding="utf-8",
    )

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)

    summary = service.file_summary("main.py")
    helper = next(symbol for symbol in summary["symbols"] if symbol["name"] == "_helper")
    findings = service.dangling_check()["findings"]

    assert "caller" in helper["called_by"]
    assert not any(finding.get("symbol") == "_helper" for finding in findings)


def test_quality_tools_are_registered() -> None:
    class FakeMcp:
        def __init__(self) -> None:
            self.names: list[str] = []

        def tool(self) -> Any:
            def decorate(func: Any) -> Any:
                self.names.append(func.__name__)
                return func

            return decorate

    fake = FakeMcp()
    register_quality_tools(fake, object())  # type: ignore[arg-type]

    assert fake.names == ["auto_index_quality_check"]
