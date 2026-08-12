from __future__ import annotations

from pathlib import Path

from auto_index_mcp.core.service import AutoIndexService


def main() -> None:
    project = Path(__file__).resolve().parents[1]
    service = AutoIndexService(index_root=project / ".smoke-index")
    print(service.enable(str(project), rebuild=True))
    print(service.overview(limit=5))
    print(service.find_files(query="AutoIndexService", limit=5))
    print(service.file_summary("src/auto_index_mcp/core/service.py"))
    print(service.symbol_search(text="AutoIndexService", limit=5))
    print(service.symbol_body("AutoIndexService", "src/auto_index_mcp/core/service.py"))
    print(service.symbol_refs("AutoIndexService", direction="callers"))
    print(service.text_search("AutoIndexService", limit=5))
    print(service.find_files(query="service.py"))
    print(service.diff_filesystem())


if __name__ == "__main__":
    main()
