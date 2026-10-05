"""Disk maintenance reclaims derived model data without affecting navigation."""
from auto_index_mcp.cli import main
from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.cache.inventory import cache_inventory
from auto_index_mcp.storage.vectors import SymbolEmbeddingStore


def prepare(root, write_file):
    write_file(root / "main.py", "def active():\n    return 1\n")
    service = AutoIndexService()
    service.semantic_enabled = False
    service.enable(str(root), refresh_embedder=False)
    store = service.embedding_store
    with store.connect() as conn:
        vectors = SymbolEmbeddingStore()
        for model in ("old", "recent"):
            for number in range(64):
                vectors.replace_file(conn, f"file{number}.py", model,
                                     [dict(symbol_name="value", symbol_line=1, text_hash="hash",
                                           vector=[0.125] * 1024)])
        conn.execute("INSERT INTO metadata VALUES ('model-used:old','1')")
        conn.execute("INSERT INTO metadata VALUES ('model-used:recent','2')")
    return service


def test_compact_keeps_selected_model_and_shrinks_database(tmp_path, write_file, capsys):
    service = prepare(tmp_path, write_file)
    directory = service.index_root
    service.disable()
    before = cache_inventory(directory)
    assert main(["compact", str(tmp_path), "--max-models", "1", "--keep-model", "recent"]) == 0
    after = cache_inventory(directory)
    assert [row["model"] for row in after["models"]] == ["recent"]
    assert after["bytes"] < before["bytes"]
    assert after["reusable_bytes"] == 0
    assert "reclaimed" in capsys.readouterr().out
    reader = AutoIndexService()
    reader.semantic_enabled = False
    try:
        reader.enable(str(tmp_path), rebuild=False, refresh_embedder=False)
        assert reader.symbol_body("active")["code"].startswith("def active")
    finally:
        reader.disable()


def test_compact_refuses_mounted_cache_before_pruning_any_model(tmp_path, write_file):
    service = prepare(tmp_path, write_file)
    try:
        before = cache_inventory(service.index_root)
        assert main(["compact", str(tmp_path), "--max-models", "1"]) == 1
        after = cache_inventory(service.index_root)
        assert before["models"] == after["models"]
        assert service.symbol_body("active")["code"].startswith("def active")
    finally:
        service.disable()


def test_clear_removes_model_inventory_and_reclaims_pages(tmp_path, write_file):
    service = prepare(tmp_path, write_file)
    try:
        before = cache_inventory(service.index_root)
        service.clear()
        after = cache_inventory(service.index_root)
        assert after["models"] == []
        assert after["reusable_bytes"] == 0
        assert after["bytes"] < before["bytes"]
    finally:
        service.disable()


def test_compact_dry_run_keeps_all_namespaces(tmp_path, write_file):
    service = prepare(tmp_path, write_file)
    directory = service.index_root
    service.disable()
    before = cache_inventory(directory)["models"]
    assert main(["compact", str(tmp_path), "--max-models", "1", "--dry-run"]) == 0
    assert cache_inventory(directory)["models"] == before
