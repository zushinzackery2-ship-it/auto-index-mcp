from auto_index_mcp.core.quality_nesting import nesting_report
from auto_index_mcp.core.service import AutoIndexService


def test_resolve_path_supports_glob_patterns(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "active.cpp").write_text("void Active() {}\n", encoding="utf-8")
    (project / "notes.txt").write_text("Active\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)

    paths = [item["path"] for item in service.resolve_path("*.cpp")["items"]]

    assert paths == ["active.cpp"]


def test_search_supports_exclude_paths(tmp_path) -> None:
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "reference_origin").mkdir()
    (project / "src" / "active.cpp").write_text("void ActiveNeedle() {}\n", encoding="utf-8")
    (project / "reference_origin" / "legacy.cpp").write_text("void LegacyNeedle() {}\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)

    result = service.text_search("Needle", exclude_paths=["reference_origin/**"])
    paths = [item["path"] for item in result["items"]]

    assert paths == ["src/active.cpp"]


def test_quality_checks_support_exclude_paths_and_active_sources(tmp_path) -> None:
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "legacy").mkdir()
    (project / "reference_origin").mkdir()
    (project / "app.vcxproj").write_text(
        '<Project><ItemGroup><ClCompile Include="src\\active.cpp" /></ItemGroup></Project>',
        encoding="utf-8",
    )
    (project / "src" / "active.cpp").write_text(
        "void ActiveUnused() { if (true) { if (true) { if (true) { return; } } } }\n",
        encoding="utf-8",
    )
    (project / "legacy" / "old.cpp").write_text("void LegacyUnused() {}\n", encoding="utf-8")
    (project / "reference_origin" / "copy.cpp").write_text("void ReferenceUnused() {}\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)

    dangling = service.dangling_check(
        include_low_confidence=False,
        exclude_paths=["reference_origin/**"],
        active_only=True,
    )
    nesting = service.nesting_check(
        max_depth=2,
        languages=["cpp"],
        exclude_paths=["reference_origin/**"],
        active_only=True,
    )

    dangling_symbols = {finding.get("symbol") for finding in dangling["findings"]}
    nesting_paths = {finding["path"] for finding in nesting["findings"]}

    assert "ActiveUnused" in dangling_symbols
    assert "LegacyUnused" not in dangling_symbols
    assert "ReferenceUnused" not in dangling_symbols
    assert nesting_paths == {"src/active.cpp"}


def test_nesting_report_marks_low_coverage_unreliable() -> None:
    files = [
        {
            "path": "a.cpp",
            "language": "cpp",
            "symbols": [
                {"name": "A", "kind": "function", "line": 1, "end_line": 3},
                {"name": "B", "kind": "function", "line": 5, "end_line": 7},
            ],
        }
    ]

    report = nesting_report(files, max_depth=4)

    assert report["summary"]["reliable"] is False
    assert report["summary"]["nesting_coverage"] == 0.0
    assert report["warnings"]
