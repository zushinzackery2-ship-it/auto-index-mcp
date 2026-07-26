"""Simulates one agent's MCP server process for the enable stress harness.

Loops auto_index_enable(rebuild=False) + auto watch exactly like the MCP tool
does, printing one JSON line per call with wall time and status. faulthandler
dumps every thread's stack to stderr if any single iteration exceeds 25s, so a
hung call leaves the exact blocking stack behind.

Env knobs (set by run_stress.py):
  AIDX_STALE            override BuildLock stale_seconds for compressed runs
  AIDX_KILL_AFTER_<id>  abrupt os._exit after N seconds, like a closed session
"""
from __future__ import annotations

import faulthandler
import json
import os
import sys
import time


def emit(**kw) -> None:
    print(json.dumps(kw, ensure_ascii=False), flush=True)


def main() -> None:
    root = sys.argv[1]
    agent = sys.argv[2]
    duration = float(sys.argv[3])

    from auto_index_mcp.core.service import AutoIndexService
    from auto_index_mcp.mcp_api.lifecycle import start_or_defer_auto_watch

    stale_override = os.environ.get("AIDX_STALE")
    if stale_override:
        import functools

        from auto_index_mcp.core import service_rebuild
        from auto_index_mcp.indexing.build_lock import BuildLock

        service_rebuild.BuildLock = functools.partial(  # type: ignore[assignment]
            BuildLock, stale_seconds=float(stale_override)
        )

    kill_after = float(os.environ.get(f"AIDX_KILL_AFTER_{agent}", "0") or 0)
    start = time.monotonic()

    service = AutoIndexService()
    deadline = time.monotonic() + duration
    call_no = 0
    while time.monotonic() < deadline:
        if kill_after and time.monotonic() - start >= kill_after:
            emit(agent=agent, op="abrupt-exit", at=round(time.monotonic() - start, 2))
            os._exit(1)
        call_no += 1
        t0 = time.monotonic()
        status = "?"
        err = None
        result = {}
        # arm per-call: a dump means ONE enable call exceeded 25s
        faulthandler.dump_traceback_later(25.0, repeat=False, file=sys.stderr)
        try:
            result = service.enable_reusing_index(root, False, wait_seconds=3.0)
            start_or_defer_auto_watch(service, result)
            status = result.get("status") or ("enabled" if result.get("enabled") else "?")
        except Exception as exc:  # noqa: BLE001 - harness must record, not die
            status = "exception"
            err = repr(exc)
        finally:
            faulthandler.cancel_dump_traceback_later()
        dt = time.monotonic() - t0
        bg = result.get("background_index") or {}
        emit(
            agent=agent,
            call=call_no,
            op="enable",
            t=round(time.monotonic() - start, 1),
            seconds=round(dt, 3),
            status=status,
            error=err,
            files=result.get("file_count"),
            bg_state=bg.get("state"),
            bg_phase=bg.get("phase"),
        )
        time.sleep(1.0)

    t0 = time.monotonic()
    st = service.status()
    emb = st.get("embedding_background") or {}
    emit(
        agent=agent,
        op="final_status",
        seconds=round(time.monotonic() - t0, 3),
        files=st.get("file_count"),
        bg_state=(st.get("background_index") or {}).get("state"),
        emb_state=emb.get("state"),
        emb_phase=emb.get("phase"),
        errors=st.get("last_errors"),
    )
    os._exit(0)


if __name__ == "__main__":
    main()
