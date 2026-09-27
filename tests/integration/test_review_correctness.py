from __future__ import annotations

from auto_index_mcp.indexing.scanner import SourceScanner


def test_cpp_single_line_bodies_do_not_swallow_following_functions(tmp_path, write_file):
    write_file(tmp_path / "code.cpp", "int alpha() { return 1; }\nint beta()\n{\n    return 2;\n}\n")
    records = SourceScanner(str(tmp_path)).scan().records
    assert [symbol.name for symbol in records[0].symbols] == ["alpha", "beta"]
    assert records[0].symbols[0].end_line == 1


def test_python_conditional_definitions_are_indexed(tmp_path, write_file):
    write_file(tmp_path / "platform.py", "if True:\n    def native():\n        return 1\nelse:\n    def portable():\n        return 2\n")
    record = SourceScanner(str(tmp_path)).scan().records[0]
    assert [symbol.name for symbol in record.symbols] == ["native", "portable"]
