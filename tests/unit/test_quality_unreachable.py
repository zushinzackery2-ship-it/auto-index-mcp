from __future__ import annotations

from auto_index_mcp.quality.unreachable import file_quality_findings
from auto_index_mcp.languages.source_clean import clean_source_lines
from auto_index_mcp.languages.c_family import extract_c_family_symbols
from auto_index_mcp.languages.javascript import extract_javascript_like_symbols


def _item(path: str, language: str, symbols: list[dict]) -> dict:
    return {"path": path, "language": language, "symbols": symbols}


def _python_findings(source: str) -> list[dict]:
    return file_quality_findings(_item("main.py", "python", []), source)


def _cpp_findings(source: str) -> list[dict]:
    lines = source.splitlines()
    cleaned = clean_source_lines(lines, "cpp")
    symbols = [symbol.__dict__ for symbol in extract_c_family_symbols(lines, cleaned)]
    return file_quality_findings(_item("main.cpp", "cpp", symbols), source, cleaned)


def _javascript_findings(source: str) -> list[dict]:
    lines = source.splitlines()
    cleaned = clean_source_lines(lines, "javascript")
    symbols = [symbol.__dict__ for symbol in extract_javascript_like_symbols(lines, cleaned)]
    return file_quality_findings(_item("main.js", "javascript", symbols), source, cleaned)


def test_python_detects_unreachable_after_return():
    source = "\n".join(
        [
            "def unused():",
            "    value = 1",
            "    return value",
            "    value += 1",
        ]
    )
    findings = _python_findings(source)

    assert len(findings) == 1
    assert findings[0]["kind"] == "unreachable_statement"
    assert findings[0]["confidence"] == "high"
    assert findings[0]["line"] == 4
    assert findings[0]["after_line"] == 3


def test_python_detects_unreachable_after_raise_break_continue():
    source = "\n".join(
        [
            "def demo():",
            "    for item in [1]:",
            "        break",
            "        item += 1",
            "    raise ValueError('stop')",
            "    return None",
        ]
    )
    findings = _python_findings(source)
    lines = {finding["line"] for finding in findings}

    assert lines == {4, 6}
    assert all(finding["confidence"] == "high" for finding in findings)


def test_python_no_false_positive_across_branches():
    source = "\n".join(
        [
            "def demo(flag):",
            "    if flag:",
            "        return 1",
            "    else:",
            "        return 2",
        ]
    )

    assert _python_findings(source) == []


def test_python_syntax_error_returns_empty():
    assert _python_findings("def broken(:\n    pass\n") == []


def test_cpp_detects_unreachable_after_return():
    source = "\n".join(
        [
            "int used(int value)",
            "{",
            "    return value;",
            "    value += 1;",
            "}",
        ]
    )
    findings = _cpp_findings(source)

    assert len(findings) == 1
    assert findings[0]["confidence"] == "medium"
    assert findings[0]["line"] == 4
    assert findings[0]["symbol"] == "used"


def test_javascript_detects_unreachable_after_throw():
    source = "\n".join(
        [
            "function fail() {",
            "    throw new Error('stop');",
            "    console.log('never');",
            "}",
        ]
    )
    findings = _javascript_findings(source)

    assert len(findings) == 1
    assert findings[0]["confidence"] == "medium"
    assert findings[0]["line"] == 3
    assert findings[0]["symbol"] == "fail"


def test_javascript_detects_unreachable_after_return():
    source = "\n".join(
        [
            "function done() {",
            "    return 1;",
            "    return 2;",
            "}",
        ]
    )
    findings = _javascript_findings(source)

    assert len(findings) == 1
    assert findings[0]["confidence"] == "medium"
    assert findings[0]["line"] == 3
    assert findings[0]["symbol"] == "done"


def test_brace_does_not_flag_else_line_after_return():
    source = "\n".join(
        [
            "function pick(value) {",
            "    if (value) {",
            "        return 1;",
            "    } else {",
            "        return 2;",
            "    }",
            "}",
        ]
    )
    findings = _javascript_findings(source)

    assert not any(finding["line"] == 4 for finding in findings)


def test_brace_heuristic_may_flag_inner_block_after_terminal():
    """Brace-language scan is per-symbol and medium-confidence; nested blocks can duplicate findings."""
    source = "\n".join(
        [
            "function pick(value) {",
            "    if (value) {",
            "        return 1;",
            "    } else {",
            "        return 2;",
            "    }",
            "    return 3;",
            "}",
        ]
    )
    findings = _javascript_findings(source)

    assert any(finding["line"] == 5 for finding in findings)
    assert not any(finding["line"] == 4 for finding in findings)


def test_brace_ignores_terminal_in_comment():
    source = "\n".join(
        [
            "void real()",
            "{",
            "    /* return dead; */",
            "    work();",
            "}",
        ]
    )

    assert _cpp_findings(source) == []


def test_unsupported_language_returns_empty():
    source = "echo done\n"
    item = _item("run.sh", "shell", [])

    assert file_quality_findings(item, source) == []
