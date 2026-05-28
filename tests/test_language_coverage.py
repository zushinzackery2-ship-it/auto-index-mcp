from pathlib import Path

from auto_index_mcp.core.service import AutoIndexService


def test_pascal_files_are_indexed_and_searchable(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "LuaHandler.pas").write_text(
        "unit LuaHandler;\n\n"
        "function executeMethod(L: PLua_state): integer; cdecl;\n"
        "begin\n"
        "  Result := 0;\n"
        "end;\n",
        encoding="utf-8",
    )

    service = AutoIndexService(index_root=tmp_path / "index")
    result = service.enable(str(project), rebuild=True)
    search = service.text_search("function executeMethod", file_pattern="*LuaHandler.pas")
    summary = service.file_summary("LuaHandler.pas")

    assert result["file_count"] == 1
    assert search["items"][0]["path"] == "LuaHandler.pas"
    assert any(symbol["name"] == "executeMethod" for symbol in summary["symbols"])
