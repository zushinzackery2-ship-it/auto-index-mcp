from __future__ import annotations

from pathlib import Path

from auto_index_mcp.core.lsp import LspManager
from auto_index_mcp.core.service import AutoIndexService

from tests.lsp_fixtures import FakeProcessFactory, messages_from_stream, publish_after_document_message


def test_explicit_lsp_check_reads_existing_file_missing_from_stale_index(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    indexed = project / "main.cpp"
    indexed.write_text("int main()\n{\n    return 0;\n}\n", encoding="utf-8")

    factory = FakeProcessFactory()
    service = AutoIndexService(index_root=tmp_path / "index")
    service.lsp = LspManager(lambda name: f"/bin/{name}", factory)
    service.enable(str(project), rebuild=True)
    service.start_lsp(timeout_seconds=0.2)

    created_after_index = project / "created_after_index.cpp"
    created_after_index.write_text("int created_after_index()\n{\n    return 1;\n}\n", encoding="utf-8")
    publish_after_document_message(
        factory,
        "textDocument/didOpen",
        1,
        lambda message: service.lsp.sessions["clangd"]._handle_message(
            {
                "jsonrpc": "2.0",
                "method": "textDocument/publishDiagnostics",
                "params": {
                    "uri": created_after_index.as_uri(),
                    "version": message["params"]["textDocument"]["version"],
                    "diagnostics": [],
                },
            }
        ),
    )
    result = service.check_lsp("created_after_index.cpp", timeout_seconds=0.2)
    opened_uris = [
        message["params"]["textDocument"]["uri"]
        for message in messages_from_stream(factory.processes[0].stdin.getvalue())
        if message.get("method") == "textDocument/didOpen"
    ]

    assert result == "CHK|clean|files=1"
    assert created_after_index.as_uri() in opened_uris


def test_explicit_lsp_check_accepts_absolute_filesystem_path(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    indexed = project / "main.cpp"
    indexed.write_text("int main()\n{\n    return 0;\n}\n", encoding="utf-8")

    factory = FakeProcessFactory()
    service = AutoIndexService(index_root=tmp_path / "index")
    service.lsp = LspManager(lambda name: f"/bin/{name}", factory)
    service.enable(str(project), rebuild=True)
    service.start_lsp(timeout_seconds=0.2)

    created_after_index = project / "created_after_index.cpp"
    created_after_index.write_text("int created_after_index()\n{\n    return 1;\n}\n", encoding="utf-8")
    publish_after_document_message(
        factory,
        "textDocument/didOpen",
        1,
        lambda message: service.lsp.sessions["clangd"]._handle_message(
            {
                "jsonrpc": "2.0",
                "method": "textDocument/publishDiagnostics",
                "params": {
                    "uri": created_after_index.as_uri(),
                    "version": message["params"]["textDocument"]["version"],
                    "diagnostics": [],
                },
            }
        ),
    )

    assert service.check_lsp(str(created_after_index.resolve()), timeout_seconds=0.2) == "CHK|clean|files=1"


def test_absolute_check_reuses_unchanged_open_document_diagnostics(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source = project / "main.cpp"
    source.write_text("int main()\n{\n    return 0;\n}\n", encoding="utf-8")

    factory = FakeProcessFactory()
    service = AutoIndexService(index_root=tmp_path / "index")
    service.lsp = LspManager(lambda name: f"/bin/{name}", factory)
    service.enable(str(project), rebuild=True)
    service.start_lsp(timeout_seconds=0.2)

    publish_after_document_message(
        factory,
        "textDocument/didOpen",
        1,
        lambda message: service.lsp.sessions["clangd"]._handle_message(
            {
                "jsonrpc": "2.0",
                "method": "textDocument/publishDiagnostics",
                "params": {
                    "uri": source.as_uri(),
                    "version": message["params"]["textDocument"]["version"],
                    "diagnostics": [],
                },
            }
        ),
    )
    first = service.check_lsp("main.cpp", timeout_seconds=0.2)
    second = service.check_lsp(str(source.resolve()), timeout_seconds=0.2)
    methods = [
        message.get("method")
        for message in messages_from_stream(factory.processes[0].stdin.getvalue())
        if str(message.get("method", "")).startswith("textDocument/")
    ]

    assert first == "CHK|clean|files=1"
    assert second == "CHK|clean|files=1"
    assert methods == ["textDocument/didOpen"]


def test_rechecking_same_file_uses_did_change_and_discards_old_diagnostics(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source = project / "main.cpp"
    source.write_text("int main()\n{\n    Missing value;\n}\n", encoding="utf-8")
    factory = FakeProcessFactory()
    service = AutoIndexService(index_root=tmp_path / "index")
    service.lsp = LspManager(lambda name: f"/bin/{name}", factory)
    service.enable(str(project), rebuild=True)
    service.start_lsp(timeout_seconds=0.2)

    publish_after_document_message(
        factory,
        "textDocument/didOpen",
        1,
        lambda message: service.lsp.sessions["clangd"]._handle_message(
            {
                "jsonrpc": "2.0",
                "method": "textDocument/publishDiagnostics",
                "params": {
                    "uri": source.as_uri(),
                    "version": message["params"]["textDocument"]["version"],
                    "diagnostics": [
                        {
                            "severity": 1,
                            "range": {"start": {"line": 2, "character": 4}},
                            "message": "unknown type name 'Missing'",
                        }
                    ],
                },
            }
        ),
    )
    first = service.check_lsp("main.cpp", timeout_seconds=0.2)
    source.write_text("int main()\n{\n    return 0;\n}\n", encoding="utf-8")
    publish_after_document_message(
        factory,
        "textDocument/didChange",
        1,
        lambda message: service.lsp.sessions["clangd"]._handle_message(
            {
                "jsonrpc": "2.0",
                "method": "textDocument/publishDiagnostics",
                "params": {
                    "uri": source.as_uri(),
                    "version": message["params"]["textDocument"]["version"],
                    "diagnostics": [],
                },
            }
        ),
    )
    second = service.check_lsp("main.cpp", timeout_seconds=0.2)
    methods = [
        message.get("method")
        for message in messages_from_stream(factory.processes[0].stdin.getvalue())
        if str(message.get("method", "")).startswith("textDocument/")
    ]

    assert first == "CHK|issues|count=1|files=1|limit=80\nE|main.cpp|3:5|unknown type name 'Missing'"
    assert second == "CHK|clean|files=1"
    assert methods == ["textDocument/didOpen", "textDocument/didChange"]


def test_versionless_diagnostics_after_did_change_are_accepted(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source = project / "main.cpp"
    source.write_text("int main()\n{\n    return 0;\n}\n", encoding="utf-8")
    factory = FakeProcessFactory()
    service = AutoIndexService(index_root=tmp_path / "index")
    service.lsp = LspManager(lambda name: f"/bin/{name}", factory)
    service.enable(str(project), rebuild=True)
    service.start_lsp(timeout_seconds=0.2)

    publish_after_document_message(
        factory,
        "textDocument/didOpen",
        1,
        lambda message: service.lsp.sessions["clangd"]._handle_message(
            {
                "jsonrpc": "2.0",
                "method": "textDocument/publishDiagnostics",
                "params": {
                    "uri": source.as_uri(),
                    "version": message["params"]["textDocument"]["version"],
                    "diagnostics": [],
                },
            }
        ),
    )
    assert service.check_lsp("main.cpp", timeout_seconds=0.2) == "CHK|clean|files=1"

    publish_after_document_message(
        factory,
        "textDocument/didChange",
        1,
        lambda message: service.lsp.sessions["clangd"]._handle_message(
            {
                "jsonrpc": "2.0",
                "method": "textDocument/publishDiagnostics",
                "params": {
                    "uri": source.as_uri(),
                    "diagnostics": [],
                },
            }
        ),
    )

    assert service.check_lsp("main.cpp", timeout_seconds=0.2) == "CHK|clean|files=1"
