from pathlib import Path

from auto_index_mcp.lsp.process_guard import ProcessGuard


def test_reap_orphans_kills_only_dead_owner_children(tmp_path: Path) -> None:
    lsp_dir = tmp_path / "lsp"
    killed: list[int] = []
    alive_pids = {100, 999, 555, 777, 888}  # 200 (owner) and 556 (child) are dead

    guard = ProcessGuard(lsp_dir, pid=999, is_alive=lambda p: p in alive_pids, terminate=killed.append)
    servers = lsp_dir / "servers"
    servers.mkdir(parents=True)
    (servers / "200.json").write_text("[555, 556]", encoding="utf-8")   # dead owner
    (servers / "100.json").write_text("[777]", encoding="utf-8")        # live peer MCP
    (servers / "999.json").write_text("[888]", encoding="utf-8")        # our own file

    reaped = guard.reap_orphans()

    assert reaped == [555]                       # only the alive child of the dead owner
    assert killed == [555]
    assert not (servers / "200.json").exists()   # dead owner's file removed
    assert (servers / "100.json").exists()        # live peer untouched
    assert (servers / "999.json").exists()        # our own untouched


def test_register_then_release(tmp_path: Path) -> None:
    guard = ProcessGuard(tmp_path / "lsp", pid=4242, is_alive=lambda p: False, terminate=lambda p: None)

    class _Proc:
        pid = 12345

    guard.register(_Proc())
    guard.register(_Proc())  # idempotent

    assert guard._read_pids(guard.own_file) == [12345]

    guard.release()
    assert not guard.own_file.exists()
