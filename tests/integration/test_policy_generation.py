from __future__ import annotations

import time

from auto_index_mcp.core.service import AutoIndexService


def test_owner_converges_after_another_instance_rebuilds_policy(tmp_path):
    source = tmp_path / "blocked.py"
    source.write_text("def before():\n    return 1\n", encoding="utf-8")
    owner, editor = AutoIndexService(), AutoIndexService()
    owner.semantic_enabled = editor.semantic_enabled = False
    try:
        owner.enable(str(tmp_path))
        owner.start_watcher(wait_ready=True)
        editor.enable(str(tmp_path), rebuild=False)
        editor.configure_ignore(["blocked.py"], mode="add")
        editor.rebuild_sync()
        source.write_text("def after():\n    return 2\n", encoding="utf-8")
        deadline = time.monotonic() + 5
        generation = editor.store.get_metadata_map()["policy_generation"]
        while time.monotonic() < deadline:
            snapshot = owner.watcher._snapshot
            if snapshot and snapshot.policy_generation == generation:
                break
            time.sleep(0.05)
        assert owner.watcher._snapshot.policy_generation == generation
        assert owner.store.get_file("blocked.py") is None
        assert not owner.symbol_search("after")["items"]
    finally:
        owner.disable()
        editor.disable()
