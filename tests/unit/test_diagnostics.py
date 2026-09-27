import logging
from pathlib import Path

from auto_index_mcp.runtime.diagnostics import close_logging, configure_logging, diagnostic_scope


def test_logging_is_scoped_and_shared_owners_release_independently(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first_log = configure_logging(first)
    shared_log = configure_logging(first)
    second_log = configure_logging(second)
    logger = logging.getLogger("auto_index_mcp.tests")
    try:
        with diagnostic_scope(first, "first-operation"):
            logger.info("first-only")
        close_logging(first_log)
        with diagnostic_scope(first, "shared-operation"):
            logger.info("shared-owner-still-active")
        with diagnostic_scope(second, "second-operation"):
            logger.info("second-only")
        first_text = Path(first_log).read_text(encoding="utf-8")
        second_text = Path(second_log).read_text(encoding="utf-8")
        assert "first-only" in first_text and "shared-owner-still-active" in first_text
        assert "second-only" not in first_text
        assert "second-only" in second_text and "first-only" not in second_text
        assert "op=first-operation" in first_text and "pid=" in first_text
    finally:
        close_logging(shared_log)
        close_logging(second_log)
    Path(first_log).unlink()
    Path(second_log).unlink()
