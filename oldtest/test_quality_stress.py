from pathlib import Path
import time

from auto_index_mcp.core.service import AutoIndexService


def test_quality_checks_scale_to_many_symbols(tmp_path: Path) -> None:
    project = tmp_path / "quality_stress"
    project.mkdir()
    file_count = 600

    for index in range(file_count):
        (project / f"mod_{index}.py").write_text(
            "\n".join(
                [
                    f"def used_{index}():",
                    f"    return {index}",
                    "",
                    f"def caller_{index}():",
                    f"    return used_{index}()",
                    "",
                    f"def unused_{index}():",
                    f"    return {index}",
                    "",
                    f"class Box_{index}:",
                    "    def method(self):",
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

    main_lines = ["def main():", "    total = 0"]
    main_lines.extend(f"    total += caller_{index}()" for index in range(file_count))
    main_lines.append("    return total")
    (project / "main.py").write_text("\n".join(main_lines), encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)

    started = time.perf_counter()
    nesting = service.nesting_check(max_depth=2, limit=1000)
    dangling = service.dangling_check(limit=1000)
    elapsed = time.perf_counter() - started

    dangling_symbols = {
        finding.get("symbol")
        for finding in dangling["findings"]
        if finding["kind"] == "unused_symbol"
    }

    assert nesting["summary"]["files_checked"] == file_count + 1
    assert nesting["summary"]["symbols_checked"] >= file_count * 6 + 1
    assert nesting["summary"]["max_symbol_depth"] == 2
    assert nesting["summary"]["max_block_depth"] == 3
    assert dangling["summary"]["total_findings"] >= file_count
    assert "unused_0" in dangling_symbols
    assert "used_0" not in dangling_symbols
    assert elapsed < 30.0
