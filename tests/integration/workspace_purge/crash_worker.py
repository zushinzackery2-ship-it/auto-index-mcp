"""Abrupt exit at durable purge boundaries, run only by the crash regression."""
import os
from pathlib import Path
import sys

from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.runtime.control.journal import OperationJournal


def main():
    root, stage = Path(sys.argv[1]), sys.argv[2]
    service = AutoIndexService()
    service.semantic_enabled = False
    service.enable(str(root), rebuild=False, refresh_embedder=False)
    save, clean = OperationJournal.save, service.purging._clean

    def save_then_exit(journal, phase, **details):
        save(journal, phase, **details)
        if (stage == "notify" and phase == "freeze") or (stage == "final" and phase == "final"):
            os._exit(73)

    def clean_then_exit(target):
        if stage == "lease":
            os._exit(73)
        result = clean(target)
        if stage == "delete":
            os._exit(73)
        return result

    OperationJournal.save = save_then_exit
    service.purging._clean = clean_then_exit
    service.purge(timeout_seconds=15)
    raise AssertionError("crash boundary was not reached")


if __name__ == "__main__":
    main()
