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
| **符号索引** | 支持 Python AST 符号，JavaScript/TypeScript、C/C++、Pascal 和通用文本轻量符号提取。 |
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
| **生命周期** | `auto_index_status()` | 返回根目录、索引库路径、文件数量、更新时间、最近错误，以及 watcher/embedding 运行状态。 |
| **生命周期** | `auto_index_ignore()` | 查看 `.gitignore`/默认排除/运行期 ignore 规则，或追加、替换、清空运行期 ignore 模式。 |
| **生命周期** | `auto_index_rebuild()` | 派发后台全量扫描并重写持久索引，请通过 `auto_index_status()` 观察进度。 |
| **生命周期** | `auto_index_clear()` | 清空索引数据，可选择删除 SQLite 文件。 |
| **导航** | `auto_index_overview()` | 返回语言分布、目录分布、样例文件等紧凑概览。 |
| **导航** | `auto_index_tree_get()` | 返回目录级摘要、语言构成和样例文件。 |
| **导航** | `auto_index_query()` | 按文本、语言、父目录和游标查询索引文件。 |
| **导航** | `auto_index_file()` | 返回单个索引文件记录，`detail="summary"` 给出 import、符号和复杂度摘要，`detail="full"` 给出完整记录。 |
| **导航** | `auto_index_resolve_path()` | 按文件名或路径片段解析候选文件。 |
| **搜索** | `auto_index_text_search()` | 对源码进行 literal 或 regex 搜索。 |
| **搜索** | `auto_index_symbol_search()` | 按名称、签名、类型搜索符号。 |
| **搜索** | `auto_index_symbol_body()` | 返回指定符号的源码片段。 |
| **语义搜索** | `auto_index_semantic_search()` | 自然语言语义搜索，默认使用仓库随附 ONNX 模型，返回最相似的符号及行范围。 |
| **语义搜索** | `auto_index_embedding_status()` | 报告语义 embedding 后端是否启用及向量数量。 |
| **质量检查** | `auto_index_quality_check()` | 从持久化缓存报告代码质量问题；`kind="nesting"` 读 `symbol_nesting` 嵌套深度，`kind="dangling"` 读 `quality_findings` 疑似悬空/不可达代码，`kind="all"` 同时返回两者。 |
| **漂移检查** | `auto_index_diff_filesystem()` | 对比索引与当前文件系统的新增、删除、变化。 |
| **自动刷新** | `auto_index_watcher_start()` | 非阻塞启动文件系统事件驱动的自动刷新。 |
| **自动刷新** | `auto_index_watcher_stop()` | 停止文件系统事件驱动的自动刷新。 |

MCP 工具面只注册 `auto_index_*` 主线入口，不再暴露旧命名兼容工具。旧的 `set_project_path()`、`find_files()`、`get_file_summary()`、`get_symbol_body()`、`search_code_advanced()` 已移除，请使用上表中的 native API。

`auto_index_enable()` 会返回 whole-workspace total files 和 local files。父工作区复用子索引时，local 只代表父库自身保存的文件数量，total 才代表包含子索引后的可导航文件数量。首次设置或切换到一个已有索引根目录时会复用 `.auto-index-mcp/index.db`；需要强制全量刷新时使用 `auto_index_rebuild()`、`auto_index_enable(rebuild=True)` 或 CLI `--rebuild`，这些入口会派发后台重建并立即返回。若另一个 MCP 进程正在持有构建锁，本进程返回 `indexing-in-other-process`，不会在请求线程等待锁超时。

索引边界默认读取项目根目录 `.gitignore`，并叠加内置排除目录（如 `.venv/`、`third-party/`、`node_modules/`、`.auto-index-mcp/`）和 `auto_index_ignore()` 配置的运行期模式。ignore 规则会同时约束源码扫描、child-index discovery 和 watcher snapshot；`.gitignore` 或运行期 ignore 变化会让旧索引失效，下一次启用会后台重建。

`auto_index_text_search()` 与 `auto_index_quality_check()` 支持 `exclude_paths`，用于排除 `reference_origin/**`、`dist/**`、`_deps/**` 等目录；质量检查还支持 `active_only`，会基于索引阶段缓存的 Visual Studio `.vcxproj` `ClCompile` 列表过滤 C/C++ 编译源。`auto_index_quality_check(kind="nesting")` 会输出 `nesting_coverage`、`reliable` 和 `warnings`，覆盖率过低时不要把结果当作结构质量结论。

---

## 设计边界

| 模块 | 职责 |
|:-----|:-----|
| `core/` | 对外服务编排、状态管理、生命周期入口。 |
| `indexing/` | 扫描、SQLite 存储、child-index 定位、增量更新、watcher、轻量快照。 |
| `workspace/` | 嵌套工作区发现、父子索引聚合、路径安全检查、搜索上下文读取。 |
| `languages/` | Python、JavaScript/TypeScript 和通用文本符号提取。 |
| `search/` | ripgrep/Python fallback 搜索后端。 |
| `embedding/` | 可选语义 embedding 后端（ONNX）、向量存储、符号级增量索引器。 |
| `mcp_api/` | MCP 工具注册，按生命周期、导航、搜索、语义、质量检查拆分。 |

---

## 搜索一致性

`auto_index_query()`、`auto_index_symbol_search()`、`auto_index_file()` 等结构化导航工具读取 SQLite 中的持久索引数据。

`auto_index_text_search()` 的正文匹配遵循“索引范围 + 实时文件内容”模型：

- 文件集合来自当前索引，新增、删除、重命名文件需要 watcher 或重建索引后才进入搜索范围。
- 正文内容优先通过 ripgrep 读取轻量 SQL search-target 清单对应的实时文件；不会把项目根目录交给 ripgrep 做递归全树搜索，也不会为了正文搜索拉取符号/import 等完整文件详情。
- 使用 ripgrep 时按 `limit` 流式读取匹配结果，达到限制后终止子进程，避免大仓高频命中把 stdout 全量收进内存。
- 没有 ripgrep 时回退为 Python 读取索引文件集合；ripgrep timeout/error 会直接返回对应 backend 状态和已收集结果，不再退回 Python 重扫同一批文件。
- 因此，已索引文件的内容刚被修改后，正文搜索通常能立即命中新内容；结构摘要和符号关系仍以索引刷新后的数据为准。

这个分工让低上下文导航保持稳定范围，同时让代码正文搜索尽量贴近磁盘上的最新内容。

## 语义搜索

`auto_index_semantic_search()` 提供自然语言到符号的语义检索：把查询语句 embed 后，按余弦相似度返回最相关的符号、文件路径和行范围。

- **模型优先级**：`AUTO_INDEX_EMBEDDING_MODEL` 指定的目录优先，需包含 `model.onnx` 和 `tokenizer.json`；未设置时使用仓库随附 `models/minilm-onnx/`。
- **后端可插拔**：默认通过 `onnxruntime`（纯 CPU 推理，零 torch 依赖）加载本地 ONNX embedding 模型，当前随附模型为 MiniLM ONNX 版本，约 90MB。
- **可选依赖**：安装 `pip install -e ".[semantic]"` 启用 onnxruntime + tokenizers；依赖缺失或所选模型不可用时 `auto_index_semantic_search()` 明确报告不可用，不做关键词假降级。
- **符号级 chunking**：embedding 文本由 `kind + signature + 符号体源码` 构成，复用已有符号索引作为精确分块，这是 auto-index 相对“全树喂 AI”方案的架构优势。
- **后台向量构建**：rebuild 先完成代码索引写库，embedding 向量随后在独立后台任务生成；若复用旧索引但向量缺失，首次语义搜索会派发后台构建并立即返回 building 状态。向量部分就绪时会先返回已有向量的检索结果，并附带 `embedding.status="partial"`、`vector_count` 和后台状态。
- **增量复用**：每个符号向量带 `text_hash`，rebuild 与 watcher 增量更新时，源码未变的符号直接复用已存向量，只对变更符号重新推理。
- **向量存储**：float32 向量以 BLOB 存入 SQLite `symbol_embeddings` 表，按 `(file_path, symbol_name, symbol_line, model_name)` 自然键定位，不依赖自增 id，跨 rebuild 稳定。

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

当前 watcher 不是固定每隔几秒扫一次目录，而是由系统文件变更事件触发。`auto_index_watcher_start()` 默认只启动后台线程并立即返回，初始快照是否完成通过 `auto_index_status().watcher.ready` 查看。默认 debounce 为 0.25 秒，只用于合并连续保存、批量生成、SQLite WAL 写入等事件风暴。更新工作串行执行，一次快照/更新未结束时不会并发启动下一次。

服务进程退出时会执行优雅熄火：`mcp.run()` 返回、异常退出、Python 正常退出、SIGINT、SIGTERM 都会调用 watcher 停止逻辑。默认 stdio 模式下 MCP 进程随客户端生命周期结束；HTTP/SSE 长驻模式下也可以通过 `auto_index_watcher_stop()` 或 `auto_index_disable()` 主动停止监听。

---

## 目录结构

```
auto-index-mcp/
|-- .well-known/
|   `-- mcp.json
|-- models/
|   `-- minilm-onnx/
|       |-- model.onnx
|       `-- tokenizer.json
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
|       |-- embedding/
|       |   |-- backend.py
|       |   |-- indexer.py
|       |   |-- onnx_backend.py
|       |   `-- vector_store.py
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

脚本会创建 `.venv`，以 `.[semantic]` 安装当前包和 ONNX 语义依赖，验证 MCP 入口，并生成 `mcp-client-config.windows.json` 配置示例。脚本不会自动修改 MCP 客户端配置，也不需要手动启动后端服务。

### 手动安装

```bash
python -m pip install -e .
```

语义搜索需要额外安装 ONNX 运行依赖：

```bash
python -m pip install -e ".[semantic]"
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
