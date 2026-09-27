from auto_index_mcp.indexing.analysis import enrich_symbols
from auto_index_mcp.languages.python import extract_python_symbols


def symbols(text):
    lines = text.splitlines()
    return {s.name: s for s in enrich_symbols(lines, extract_python_symbols(text, lines), "python")}


def test_formatted_strings_keep_executable_expressions():
    found = symbols("def render():\n    return f'{_human_size(4):{width()}}'\n")
    assert {"_human_size", "width"} <= set(found["render"].calls)


def test_nested_function_calls_belong_to_own_scope():
    found = symbols("def outer():\n    def inner():\n        work()\n    return inner\n")
    assert found["inner"].calls == ["work"]
    assert "work" not in found["outer"].calls
    assert "inner" in found["outer"].refs


def test_import_aliases_and_multiline_callbacks():
    found = symbols(
        "from somewhere import target as alias\n"
        "def run():\n    alias()\n    register(\n        callback,\n    )\n"
    )
    assert "target" in found["run"].calls
    assert "callback" in found["run"].refs
