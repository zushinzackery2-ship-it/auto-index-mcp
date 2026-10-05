"""Common source layouts and language constructs must be discoverable."""
import time

from auto_index_mcp.indexing.scanner import SourceScanner


def test_business_models_can_override_recommended_exclusions(tmp_path, make_service, write_file):
    write_file(tmp_path / "models" / "user.py", "class User:\n    pass\n")
    write_file(tmp_path / ".gitignore", "!models/\n!models/**\n")
    service = make_service(tmp_path)
    assert service.symbol_search("User")["items"][0]["name"] == "User"


def test_runtime_negation_overrides_defaults_without_exposing_internal_cache(tmp_path, make_service, write_file):
    write_file(tmp_path / "build" / "recipe.py", "def business_recipe():\n    pass\n")
    write_file(tmp_path / ".auto-index-mcp" / "unsafe.py", "def internal():\n    pass\n")
    service = make_service(tmp_path)
    service.configure_ignore(["!build/", "!build/**", "!.auto-index-mcp/", "!.auto-index-mcp/**"], mode="replace")
    service.rebuild_sync()
    assert service.symbol_search("business_recipe")["items"]
    assert service.symbol_search("internal")["items"] == []


def test_vue_is_searchable_and_script_symbols_keep_original_lines(tmp_path, make_service, write_file):
    write_file(tmp_path / "component.vue", '<template>business title</template>\n<script setup lang="ts">\n'
               'const compute = (value: number): number => value * 2;\n</script>\n')
    service = make_service(tmp_path)
    assert service.text_search("business title")["items"][0]["path"] == "component.vue"
    symbol = service.symbol_search("compute")["items"][0]
    assert symbol["kind"] == "function"
    assert symbol["line"] == symbol["end_line"] == 3


def test_go_receiver_and_types_are_symbols(tmp_path, make_service, write_file):
    write_file(tmp_path / "store.go", "package demo\ntype Store struct {}\n"
               "func (s *Store) Load() int { return 1 }\nfunc Save() {}\n")
    service = make_service(tmp_path)
    symbols = {s["name"]: s for s in service.file_summary("store.go")["symbols"]}
    assert symbols["Load"]["kind"] == "method"
    assert symbols["Load"]["qualified_name"] == "Store.Load"
    assert symbols["Store"]["kind"] == "struct"
    assert symbols["Save"]["kind"] == "function"


def test_additional_text_extensions_have_explicit_coverage(tmp_path, make_service, write_file, monkeypatch):
    monkeypatch.setenv("AUTO_INDEX_TEXT_EXTENSIONS", ".sql,.lua")
    write_file(tmp_path / "query.sql", "select business_value from example;\n")
    service = make_service(tmp_path)
    result = service.text_search("business_value")
    assert result["items"][0]["path"] == "query.sql"
    assert result["scope"] == "indexed-files"
    assert ".sql" in result["coverage"]["text_extensions"]
    assert SourceScanner(str(tmp_path)).read_path(tmp_path / "query.sql").analysis_kind == "text-only"


def test_explicit_receiver_does_not_share_callers_with_same_named_method(tmp_path, make_service, write_file):
    write_file(tmp_path / "code.py", "class Left:\n    def load(self):\n        return 1\n"
               "class Right:\n    def load(self):\n        return 2\n"
               "def caller():\n    return Left().load()\n")
    service = make_service(tmp_path)
    rows = {s["qualified_name"]: s for s in service.symbol_refs("load")["items"]}
    assert rows["Left.load"]["callers"] == ["caller"]
    assert rows["Right.load"]["callers"] == []


def test_unresolved_receiver_is_reported_as_ambiguous(tmp_path, make_service, write_file):
    write_file(tmp_path / "code.py", "class Left:\n    def load(self):\n        return 1\n"
               "class Right:\n    def load(self):\n        return 2\n"
               "def caller(receiver):\n    return receiver.load()\n")
    service = make_service(tmp_path)
    result = service.symbol_refs("load")
    assert all(not s["callers"] for s in result["items"])
    assert all(s["ambiguous_callers"] == ["caller"] for s in result["items"])
    assert all(s["caller_evidence"][0]["resolution"] == "ambiguous" for s in result["items"])


def test_unique_method_with_unknown_receiver_is_not_confidently_unused(tmp_path, make_service, write_file):
    write_file(tmp_path / "code.py", "class Worker:\n    def _execute(self):\n        return 1\n"
               "def caller(receiver):\n    return receiver._execute()\n")
    service = make_service(tmp_path)
    report = service.dangling_check()
    assert not any(row.get("symbol") == "_execute" for row in report["findings"])
    detailed = service.dangling_check(include_low_confidence=True)
    finding = next(row for row in detailed["findings"] if row.get("symbol") == "_execute")
    assert finding["confidence"] == "low"


def test_included_models_source_is_updated_by_watcher(tmp_path, make_service, write_file):
    source = tmp_path / "models" / "user.py"
    write_file(source, "def before():\n    pass\n")
    write_file(tmp_path / ".gitignore", "!models/\n!models/**\n")
    service = make_service(tmp_path)
    service.start_watcher(debounce_seconds=0.1, wait_ready=True)
    write_file(source, "def after():\n    pass\n")
    deadline = time.monotonic() + 5
    while not service.symbol_search("after")["items"] and time.monotonic() < deadline:
        time.sleep(0.05)
    assert service.symbol_search("after")["items"]
