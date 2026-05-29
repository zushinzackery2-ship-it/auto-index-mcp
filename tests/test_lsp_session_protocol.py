from __future__ import annotations

from typing import cast

from auto_index_mcp.core.lsp_session import LspSession, ProcessFactory
from auto_index_mcp.core.lsp_specs import SERVER_SPECS

from tests.lsp_fixtures import FakeProcessFactory, messages_from_stream


def _process_factory(factory: FakeProcessFactory) -> ProcessFactory:
    return cast(ProcessFactory, factory)


def test_lsp_session_responds_to_workspace_configuration_request(tmp_path) -> None:
    factory = FakeProcessFactory()
    session = LspSession(SERVER_SPECS[1], "/bin/pyright-langserver", _process_factory(factory))

    assert session.start(tmp_path, timeout_seconds=0.2) == "ready"

    session._handle_message(
        {
            "jsonrpc": "2.0",
            "id": 99,
            "method": "workspace/configuration",
            "params": {"items": [{"section": "python.analysis"}, {"section": "python"}]},
        }
    )

    messages = messages_from_stream(factory.processes[0].stdin.getvalue())

    assert any(message.get("id") == 99 and message.get("result") == [None, None] for message in messages)


def test_lsp_session_discards_diagnostics_older_than_client_document_version(tmp_path) -> None:
    factory = FakeProcessFactory()
    session = LspSession(SERVER_SPECS[1], "/bin/pyright-langserver", _process_factory(factory))
    source = tmp_path / "tool.py"
    uri = source.as_uri()

    assert session.start(tmp_path, timeout_seconds=0.2) == "ready"

    session.open_document(uri, "python", 10_000_000, "value = 1\n")
    session.open_document(uri, "python", 10_000_001, "value = 2\n")
    session._handle_message(
        {
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {"uri": uri, "version": 1, "diagnostics": [{"message": "old"}]},
        }
    )
    session._handle_message(
        {
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {"uri": uri, "version": 2, "diagnostics": []},
        }
    )

    assert session.open_versions[uri] == 2
    assert session.diagnostics[uri] == []


def test_lsp_session_normalizes_windows_file_uri_variants(tmp_path) -> None:
    factory = FakeProcessFactory()
    session = LspSession(SERVER_SPECS[1], "/bin/pyright-langserver", _process_factory(factory))
    uri = "file:///D:/Project/src/tool.py"

    assert session.start(tmp_path, timeout_seconds=0.2) == "ready"

    session.open_document(uri, "python", 1, "value = 1\n")
    session._handle_message(
        {
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {"uri": "file:///d%3A/Project/src/tool.py", "version": 1, "diagnostics": []},
        }
    )

    assert uri in session.diagnostics


def test_lsp_session_reports_unknown_server_request(tmp_path) -> None:
    factory = FakeProcessFactory()
    session = LspSession(SERVER_SPECS[1], "/bin/pyright-langserver", _process_factory(factory))

    assert session.start(tmp_path, timeout_seconds=0.2) == "ready"

    session._handle_message({"jsonrpc": "2.0", "id": 100, "method": "workspace/unknown", "params": {}})

    messages = messages_from_stream(factory.processes[0].stdin.getvalue())
    response = next(message for message in messages if message.get("id") == 100)

    assert response["error"]["code"] == -32601