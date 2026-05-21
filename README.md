<div align="center">

# auto-index-mcp

**Persistent MCP codebase indexer for low-context agent navigation**

*SQLite-backed auto-indexing, low-context navigation tools, and symbol-aware search for coding agents*

![Python](https://img.shields.io/badge/Python-3.11%2B-blue?style=flat-square)
![MCP](https://img.shields.io/badge/MCP-Compatible-green?style=flat-square)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey?style=flat-square)
![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)

</div>

---

> [!NOTE]
> **Repository Boundary**  
> This repository contains the standalone `auto-index-mcp` server only. It does not depend on sibling workspaces or local-only project files.

## Feature Overview

| Feature | Description |
|:-----|:-----|
| **Persistent Index** | Stores file, symbol, import, and metadata records in SQLite. |
| **Incremental Rebuild** | Reuses unchanged file records during rebuilds to reduce indexing cost. |
| **Low-Context Navigation** | Exposes overview, tree, query, get, resolve, and filesystem diff tools. |
| **Symbol Indexing** | Extracts Python AST symbols and lightweight JavaScript/TypeScript/generic symbols. |
| **Code Search** | Uses ripgrep when available, with a Python fallback search backend. |
| **Watcher** | Provides standard-library polling refresh for active projects. |
| **Compatibility Layer** | Provides familiar file, symbol, search, watcher, and settings tool aliases. |
| **MCP Resource** | Exposes `files://{file_path}` content access for indexed projects. |

---

## Core API

| Category | API | Description |
|:-----|:----|:-----|
| **Lifecycle** | `auto_index_enable()` | Configure a project root and optionally rebuild the index. |
| **Lifecycle** | `auto_index_status()` | Return root, index path, counts, update time, and recent errors. |
| **Lifecycle** | `auto_index_rebuild()` | Force a full scan and persistent index rewrite. |
| **Lifecycle** | `auto_index_clear()` | Clear index data and optionally remove the SQLite file. |
| **Navigation** | `auto_index_overview()` | Return compact language, directory, and sample-file overview. |
| **Navigation** | `auto_index_tree_get()` | Return compact folder summaries with language mix and samples. |
| **Navigation** | `auto_index_query()` | Query indexed files by text, language, parent path, and cursor. |
| **Navigation** | `auto_index_get()` | Return one indexed file record. |
| **Navigation** | `auto_index_resolve_path()` | Resolve fuzzy filenames or paths into indexed candidates. |
| **Search** | `auto_index_text_search()` | Search source text with literal or regex matching. |
| **Search** | `auto_index_symbol_search()` | Search indexed symbols by name, signature, or kind. |
| **Search** | `auto_index_symbol_body()` | Return the source body of one indexed symbol. |
| **Drift Check** | `auto_index_diff_filesystem()` | Compare the persisted index with current filesystem state. |
| **Watcher** | `auto_index_watcher_start()` | Start polling auto-refresh for the active project. |
| **Watcher** | `auto_index_watcher_status()` | Report watcher runtime state. |
| **Compatibility** | `set_project_path()` | Initialize indexing using a familiar project setup tool name. |
| **Compatibility** | `find_files()` | Find indexed files by glob or filename. |
| **Compatibility** | `get_file_summary()` | Return imports, symbols, and complexity metrics for one file. |
| **Compatibility** | `get_symbol_body()` | Return one symbol body using the compatible tool contract. |
| **Compatibility** | `search_code_advanced()` | Search code with file patterns, regex, context, pagination, and fuzzy mode. |

---

## Index Storage

Each project stores its SQLite index inside the configured project root:

```text
<project>/.auto-index-mcp/index.db
```

The `.auto-index-mcp` directory is excluded from scanning and ignored by git.

---

## Directory Structure

```
auto-index-mcp/
|-- .well-known/
|   `-- mcp.json
|-- scripts/
|   `-- smoke_auto_index.py
|-- src/
|   `-- auto_index_mcp/
|       |-- compatibility/
|       |-- core/
|       |-- indexing/
|       |-- languages/
|       |-- mcp_api/
|       |-- search/
|       |-- __main__.py
|       `-- server.py
|-- tests/
|   `-- test_auto_index_service.py
|-- fastmcp.json
|-- install_windows.bat
|-- pyproject.toml
`-- README.md
```

---

## Install

### Windows One-Click

```bat
install_windows.bat
```

The Windows installer creates `.venv`, installs the package into that virtual environment, verifies the MCP entrypoint, and writes `mcp-client-config.windows.json` with the local Python path. It does not modify MCP client settings and does not start a backend service.

### Manual

```bash
python -m pip install -e .
```

---

## Run

```bash
python -m auto_index_mcp.server --project-path /path/to/project
```

```bash
auto-index-mcp --project-path /path/to/project
```

---

## MCP Configuration

MCP clients start this server as a stdio process from the configured command. No separate backend service needs to be started manually.

```json
{
  "mcpServers": {
    "auto-index": {
      "command": "python",
      "args": [
        "-m",
        "auto_index_mcp.server"
      ]
    }
  }
}
```

---

## Test

```bash
python -m pytest -q
```

```bash
python scripts/smoke_auto_index.py
```

---

<div align="center">

**Runtime:** Python 3.11+ | **Protocol:** MCP | **License:** MIT

</div>
