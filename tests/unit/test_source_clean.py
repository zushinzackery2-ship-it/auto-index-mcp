from __future__ import annotations

from auto_index_mcp.languages.source_clean import clean_source_lines
from auto_index_mcp.indexing.analysis import enrich_symbols
from auto_index_mcp.indexing.nesting import annotate_symbol_nesting
from auto_index_mcp.languages.c_family import extract_c_family_symbols


def _clean(source: str, language: str) -> list[str]:
    return clean_source_lines(source.splitlines(), language)


def test_block_comment_spanning_lines_is_blanked():
    source = "\n".join(
        [
            "int alive() {",
            "    /* dead { brace",
            "       still dead } here",
            "    */ return 1;",
            "}",
        ]
    )
    cleaned = _clean(source, "cpp")
    assert "{" not in cleaned[1]
    assert "}" not in cleaned[2]
    assert "return 1;" in cleaned[3]
    # Line count and per-line length are preserved.
    assert len(cleaned) == 5
    assert all(len(a) == len(b) for a, b in zip(cleaned, source.splitlines()))


def test_line_comment_and_string_protection():
    cleaned = _clean('const char* url = "http://x"; // note { brace', "cpp")
    assert "http" not in cleaned[0]
    assert "note" not in cleaned[0]
    assert "{" not in cleaned[0]
    assert cleaned[0].startswith("const char* url =")


def test_escaped_quote_does_not_end_string():
    cleaned = _clean(r'log("a \" b { c"); int x = 1;', "cpp")
    assert "{" not in cleaned[0]
    assert "int x = 1;" in cleaned[0]


def test_python_triple_quote_spans_lines():
    source = "\n".join(
        [
            "def f():",
            '    doc = """',
            "    if fake: {",
            '    """',
            "    return 2",
        ]
    )
    cleaned = _clean(source, "python")
    assert "if fake" not in cleaned[2]
    assert "{" not in cleaned[2]
    assert "return 2" in cleaned[4]


def test_python_hash_comment_blanked_but_c_preprocessor_kept():
    assert "nope" not in _clean("x = 1  # nope", "python")[0]
    assert "#include <a.h>" in _clean("#include <a.h>", "cpp")[0]


def test_js_template_literal_spans_lines():
    source = "\n".join(
        [
            "const s = `hello",
            "function fake() {",
            "`;",
            "function real() {",
            "}",
        ]
    )
    cleaned = _clean(source, "javascript")
    assert "fake" not in cleaned[1]
    assert "function real() {" in cleaned[3]


def test_pascal_brace_comment_and_doubled_quote():
    source = "\n".join(
        [
            "{ procedure NotReal; }",
            "s := 'it''s ok';",
            "(* procedure AlsoDead; *)",
        ]
    )
    cleaned = _clean(source, "pascal")
    assert "NotReal" not in cleaned[0]
    assert "it" not in cleaned[1]
    assert "s :=" in cleaned[1]
    assert "AlsoDead" not in cleaned[2]


def test_unterminated_single_line_string_does_not_leak():
    cleaned = _clean('bad = "unterminated\nnext = 1', "python")
    assert "next = 1" in cleaned[1]


def test_c_family_ignores_commented_out_function():
    source = [
        "/*",
        "void DeadCode(int a)",
        "{",
        "    dead();",
        "}",
        "*/",
        "void RealCode(int a)",
        "{",
        "    real();",
        "}",
    ]
    symbols = extract_c_family_symbols(source)
    names = [symbol.name for symbol in symbols]
    assert names == ["RealCode"]
    real = symbols[0]
    assert real.line == 7
    assert real.end_line == 10


def test_c_family_brace_in_comment_does_not_extend_span():
    source = [
        "void Short()",
        "{ /* unmatched in comment { { */",
        "    work();",
        "}",
        "void Next()",
        "{",
        "}",
    ]
    symbols = extract_c_family_symbols(source)
    by_name = {symbol.name: symbol for symbol in symbols}
    assert by_name["Short"].end_line == 4
    assert "Next" in by_name


def test_nesting_ignores_braces_inside_comments():
    source = [
        "void Outer()",
        "{",
        "    /* { { { { fake depth */",
        "    if (x) {",
        "        y();",
        "    }",
        "}",
    ]
    cleaned = clean_source_lines(source, "cpp")
    symbols = extract_c_family_symbols(source, cleaned)
    annotated = annotate_symbol_nesting(cleaned, symbols, "cpp")
    assert annotated[0].max_block_depth == 1


def test_calls_ignore_strings_and_comments():
    source = [
        "void Caller()",
        "{",
        '    log("fake_call(1)");',
        "    // other_fake(2);",
        "    real_call(3);",
        "}",
    ]
    cleaned = clean_source_lines(source, "cpp")
    symbols = extract_c_family_symbols(source, cleaned)
    enriched = enrich_symbols(source, symbols, "cpp", cleaned)
    calls = enriched[0].calls
    assert "real_call" in calls
    assert "log" in calls
    assert "fake_call" not in calls
    assert "other_fake" not in calls
