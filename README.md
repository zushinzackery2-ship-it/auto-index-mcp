<div align="center">

# auto-index-mcp

**面向编码 Agent 的持久化 MCP 代码索引器**

*SQLite 持久索引、低上下文代码导航、符号级搜索、事件驱动自动更新*

![Python](https://img.shields.io/badge/Python-3.11%2B-blue?style=flat-square)
![MCP](https://img.shields.io/badge/MCP-Compatible-green?style=flat-square)
![Platform](https://img.shields.io/badge/Platform-Windows%20x64-lightgrey?style=flat-square)
![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)

</div>

---

> [!NOTE]
> **仓库边界**
> 本仓库只包含独立的 `auto-index-mcp` 服务端，不依赖同级工作区或本机私有项目文件；当前 `main` 只维护无 LSP 版本，不注册 LSP/clangd 工具面。

## 功能概览

| 功能 | 说明 |
|:-----|:-----|
| **持久索引** | 将文件、符号、import、元数据写入 SQLite，MCP 进程重启后仍可复用。 |
| **精确增量更新** | 普通文件新增、修改、删除只更新受影响记录，不做整库重建。 |
| **嵌套工作区** | 父目录发现子目录已有索引库时只挂链接，不重复维护子目录数据。 |
| **低上下文导航** | 提供 overview、tree、query、get、resolve、diff 等轻量工具。 |
| **符号索引** | 支持 Python AST 符号，JavaScript/TypeScript/通用文本轻量符号提取。 |
| **代码搜索** | 优先使用 ripgrep 按轻量索引目标清单搜索；无 ripgrep 时才回退 Python 索引范围搜索。 |
| **自动刷新** | 使用系统文件变更事件触发，短 debounce 合并连续变更，再做轻量快照比对。 |
| **质量检查** | 基于持久索引缓存报告嵌套过深、疑似悬空代码和不可达代码。 |
| **MCP Resource** | 通过 `files://{file_path}` 暴露当前索引项目内的文件内容。 |

---

## 核心 API

| 分类 | API | 说明 |
|:-----|:----|:-----|
| **生命周期** | `auto_index_enable()` | 设置项目根目录，默认复用已有索引，可显式重建。 |
| **生命周期** | `auto_index_disable()` | 停用当前索引状态并停止自动刷新。 |
| **生命周期** | `auto_index_status()` | 返回根目录、索引库路径、文件数量、更新时间、最近错误。 |
| **生命周期** | `auto_index_rebuild()` | 强制全量扫描并重写持久索引。 |
| **生命周期** | `auto_index_clear()` | 清空索引数据，可选择删除 SQLite 文件。 |
| **导航** | `auto_index_overview()` | 返回语言分布、目录分布、样例文件等紧凑概览。 |
| **导航** | `auto_index_tree_get()` | 返回目录级摘要、语言构成和样例文件。 |
| **导航** | `auto_index_query()` | 按文本、语言、父目录和游标查询索引文件。 |
| **导航** | `auto_index_file_summary()` | 返回单文件 import、符号和复杂度摘要。 |
| **导航** | `auto_index_get()` | 返回单个索引文件记录。 |
| **导航** | `auto_index_resolve_path()` | 按文件名或路径片段解析候选文件。 |
| **搜索** | `auto_index_text_search()` | 对源码进行 literal 或 regex 搜索。 |
| **搜索** | `auto_index_symbol_search()` | 按名称、签名、类型搜索符号。 |
| **搜索** | `auto_index_symbol_body()` | 返回指定符号的源码片段。 |
| **质量检查** | `auto_index_nesting_check()` | 从 `symbol_nesting` 缓存读取嵌套复杂度问题。 |
| **质量检查** | `auto_index_dangling_check()` | 从 `quality_findings` 缓存读取疑似悬空代码问题。 |
| **漂移检查** | `auto_index_diff_filesystem()` | 对比索引与当前文件系统的新增、删除、变化。 |
| **自动刷新** | `auto_index_watcher_start()` | 启动文件系统事件驱动的自动刷新。 |
| **自动刷新** | `auto_index_watcher_stop()` | 停止文件系统事件驱动的自动刷新。 |
| **自动刷新** | `auto_index_watcher_status()` | 查看 watcher 运行状态、触发次数、最近结果。 |

MCP 工具面只注册 `auto_index_*` 主线入口，不再暴露旧命名兼容工具。旧的 `set_project_path()`、`find_files()`、`get_file_summary()`、`get_symbol_body()`、`search_code_advanced()` 已移除，请使用上表中的 native API。

`auto_index_enable()` 会返回 whole-workspace total files 和 local files。父工作区复用子索引时，local 只代表父库自身保存的文件数量，total 才代表包含子索引后的可导航文件数量。首次设置或切换到一个已有索引根目录时会复用 `.auto-index-mcp/index.db`；需要强制全量刷新时使用 `auto_index_rebuild()`、`auto_index_enable(rebuild=True)` 或 CLI `--rebuild`。

---

## 设计边界

| 模块 | 职责 |
|:-----|:-----|
| `core/` | 对外服务编排、状态管理、生命周期入口。 |
| `indexing/` | 扫描、SQLite 存储、child-index 定位、增量更新、watcher、轻量快照。 |
| `workspace/` | 嵌套工作区发现、父子索引聚合、路径安全检查、搜索上下文读取。 |
| `languages/` | Python、JavaScript/TypeScript 和通用文本符号提取。 |
| `search/` | ripgrep/Python fallback 搜索后端。 |
| `mcp_api/` | MCP 工具注册，按生命周期、导航、搜索、质量检查拆分。 |

---

## 搜索一致性

`auto_index_query()`、`auto_index_symbol_search()`、`auto_index_file_summary()` 等结构化导航工具读取 SQLite 中的持久索引数据。

`auto_index_text_search()` 的正文匹配遵循“索引范围 + 实时文件内容”模型：

- 文件集合来自当前索引，新增、删除、重命名文件需要 watcher 或重建索引后才进入搜索范围。
- 正文内容优先通过 ripgrep 读取轻量 SQL search-target 清单对应的实时文件；不会把项目根目录交给 ripgrep 做递归全树搜索，也不会为了正文搜索拉取符号/import 等完整文件详情。
- 使用 ripgrep 时按 `limit` 流式读取匹配结果，达到限制后终止子进程，避免大仓高频命中把 stdout 全量收进内存。
- 没有 ripgrep 时回退为 Python 读取索引文件集合；ripgrep timeout/error 会直接返回对应 backend 状态和已收集结果，不再退回 Python 重扫同一批文件。
- 因此，已索引文件的内容刚被修改后，正文搜索通常能立即命中新内容；结构摘要和符号关系仍以索引刷新后的数据为准。

这个分工让低上下文导航保持稳定范围，同时让代码正文搜索尽量贴近磁盘上的最新内容。

## 索引存储

每个项目的 SQLite 索引默认放在项目根目录内：

```text
<project>/.auto-index-mcp/index.db
```

`.auto-index-mcp` 会被扫描器排除，也已经写入 `.gitignore`。

如果父项目包含一个已经有 `.auto-index-mcp/index.db` 的子目录，父索引只保存子库链接，并跳过对子目录源码的重复索引。导航、搜索、摘要、符号体读取会聚合父库和子库。多层嵌套按每层数据库递归展开，并通过 visited db path 避免循环引用。

---

## 自动刷新设计

| 变更类型 | 行为 |
|:-----|:-----|
| **普通文件新增/修改/删除** | 文件系统事件唤醒 watcher，debounce 合并连续变更，再用轻量快照定位变化路径，只重写受影响文件和相关 `called_by` 元数据。 |
| **子索引新增/删除** | 父库执行一次结构重建，挂接或移除子库边界，并自动瘦身重复的子目录记录。 |
| **子索引内容变化** | 父库只刷新 child link metadata，不重写父库源码记录。 |
| **SQLite WAL 更新** | 子库指纹同时覆盖 `index.db`、`index.db-wal`、`index.db-shm`，避免漏掉 WAL 模式下的子库提交。 |
| **自身索引 DB 更新** | 当前项目自己的 `.auto-index-mcp/index.db/-wal/-shm` 事件会被忽略，避免 watcher 自触发 child discovery 或重复扫描。 |

当前 watcher 不是固定每隔几秒扫一次目录，而是由系统文件变更事件触发。默认 debounce 为 0.25 秒，只用于合并连续保存、批量生成、SQLite WAL 写入等事件风暴。更新工作串行执行，一次快照/更新未结束时不会并发启动下一次。

服务进程退出时会执行优雅熄火：`mcp.run()` 返回、异常退出、Python 正常退出、SIGINT、SIGTERM 都会调用 watcher 停止逻辑。默认 stdio 模式下 MCP 进程随客户端生命周期结束；HTTP/SSE 长驻模式下也可以通过 `auto_index_watcher_stop()` 或 `auto_index_disable()` 主动停止监听。

---

## 目录结构

```
auto-index-mcp/
|-- .well-known/
|   `-- mcp.json
|-- scripts/
|   |-- smoke_auto_index.py
|   `-- verify_mcp_stdio.py
|-- src/
|   `-- auto_index_mcp/
|       |-- core/
|       |   |-- index_policy.py
|       |   |-- pagination.py
|       |   |-- quality_dangling.py
|       |   |-- quality_nesting.py
|       |   |-- service.py
|       |   |-- service_quality.py
|       |   `-- service_search.py
|       |-- indexing/
|       |   |-- build_lock.py
|       |   |-- locator.py
|       |   |-- nesting.py
|       |   |-- snapshot.py
|       |   |-- store.py
|       |   `-- watcher.py
|       |-- languages/
|       |-- mcp_api/
|       |-- search/
|       |-- workspace/
|       |   |-- context.py
|       |   |-- discovery.py
|       |   |-- safety.py
|       |   `-- view.py
|       |-- __main__.py
|       `-- server.py
|-- tests/
|   |-- test_auto_index_service.py
|   |-- test_build_lock.py
|   |-- test_child_index_discovery.py
|   |-- test_index_store.py
|   |-- test_language_coverage.py
|   |-- test_search_backend.py
|   |-- test_server_shutdown.py
|   |-- test_service_rebuild_reuse.py
|   |-- test_service_workspace_integration.py
|   |-- test_watcher_core.py
|   `-- test_watcher_updates.py
|-- fastmcp.json
|-- install_windows.bat
|-- pyproject.toml
`-- README.md
```

---

## 安装

### Windows 一键安装

```bat
install_windows.bat
```

脚本会创建 `.venv`，把当前包安装到虚拟环境，验证 MCP 入口，并生成 `mcp-client-config.windows.json` 配置示例。脚本不会自动修改 MCP 客户端配置，也不需要手动启动后端服务。

### 手动安装

```bash
python -m pip install -e .
```

---

## 运行

```bash
python -m auto_index_mcp.server --project-path /path/to/project
```

```bash
auto-index-mcp --project-path /path/to/project
```

传入 `--project-path` 时默认启动自动刷新。脚本或一次性校验场景可以加 `--no-watch` 禁用 watcher。

---

## MCP 配置

MCP 客户端会按配置通过 stdio 拉起本服务，不需要单独手动启动后端。

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

Windows 一键安装后，可以参考安装脚本生成的 `mcp-client-config.windows.json`，其中会使用本项目 `.venv` 里的 Python 绝对路径。

当前发布包按 Windows x64 环境验证；其他平台未作为正式发布目标验证。

---

## 测试

```bash
python -m pytest -q
```

```bash
python scripts/smoke_auto_index.py
```

```bash
python scripts/verify_mcp_stdio.py
```

---

<div align="center">

**Runtime:** Python 3.11+ | **Platform:** Windows x64 | **Protocol:** MCP | **License:** MIT

</div>
