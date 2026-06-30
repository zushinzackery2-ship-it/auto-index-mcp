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


def test_cpp_unreal_style_functions_are_indexed_with_complexity(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "InitNTDevice.cpp").write_text(
        "\n".join(
            [
                "#include <string>",
                "namespace",
                "{",
                "    std::wstring NormalizeDriverImagePath(std::wstring path)",
                "    {",
                "        if (path.empty())",
                "        {",
                "            return L\"\";",
                "        }",
                "        return path;",
                "    }",
                "",
                "    bool StartKernelService(",
                "        const std::wstring& serviceName)",
                "    {",
                "        for (int i = 0; i < 3; ++i)",
                "        {",
                "            if (!serviceName.empty())",
                "            {",
                "                while (false)",
                "                {",
                "                    return false;",
                "                }",
                "            }",
                "        }",
                "        return true;",
                "    }",
                "}",
            ]
        ),
        encoding="utf-8",
    )

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)

    summary = service.file_summary("InitNTDevice.cpp")
    symbols = {symbol["name"]: symbol for symbol in summary["symbols"]}

    assert {"NormalizeDriverImagePath", "StartKernelService"} <= set(symbols)
    assert summary["total_complexity"] >= 4
    assert symbols["StartKernelService"]["max_block_depth"] >= 3


def test_pascal_class_methods_are_indexed(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "Main.pas").write_text(
        "\n".join(
            [
                "unit Main;",
                "interface",
                "type",
                "  TForm1 = class(TForm)",
                "    procedure FormCreate(Sender: TObject);",
                "    function StartDebugger(szExe: string): Boolean;",
                "  end;",
                "implementation",
                "procedure TForm1.FormCreate(Sender: TObject);",
                "begin",
                "  if True then",
                "  begin",
                "    StartDebugger('app.exe');",
                "  end;",
                "end;",
                "function TForm1.StartDebugger(szExe: string): Boolean;",
                "begin",
                "  Result := szExe <> '';",
                "end;",
            ]
        ),
        encoding="utf-8",
    )

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)

    summary = service.file_summary("Main.pas")
    symbols = {(symbol["kind"], symbol["name"]) for symbol in summary["symbols"]}

    assert ("class", "TForm1") in symbols
    assert ("method", "FormCreate") in symbols
    assert ("method", "StartDebugger") in symbols


def test_pascal_ansi_files_are_decoded(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "AnsiMain.pas").write_bytes(
        "\n".join(
            [
                "unit AnsiMain;",
                "implementation",
                "// 中文注释",
                "procedure RunAnsi;",
                "begin",
                "end;",
            ]
        ).encode("gb18030")
    )

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)

    summary = service.file_summary("AnsiMain.pas")

    assert any(symbol["name"] == "RunAnsi" for symbol in summary["symbols"])


def test_cpp_function_with_default_argument_is_indexed(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "Cfg.cpp").write_text(
        "\n".join(
            [
                "void Configure(int retries = 3)",
                "{",
                "    DoWork(retries);",
                "}",
            ]
        ),
        encoding="utf-8",
    )

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)

    summary = service.file_summary("Cfg.cpp")
    # A default-argument '=' inside the parens must not suppress the function.
    assert any(symbol["name"] == "Configure" for symbol in summary["symbols"])


def test_cpp_long_function_end_line_is_not_capped(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    body = "\n".join(f"    int v{index} = {index};" for index in range(120))
    (project / "Big.cpp").write_text(
        "int Compute()\n{\n" + body + "\n    return 0;\n}\n",
        encoding="utf-8",
    )

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)

    summary = service.file_summary("Big.cpp")
    compute = next(symbol for symbol in summary["symbols"] if symbol["name"] == "Compute")
    # The function spans ~124 lines; end_line must reflect the real closing brace,
    # not the old +80 cap that silently truncated large functions.
    assert compute["end_line"] >= 124
