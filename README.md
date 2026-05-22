<div align="center">

# auto-index-mcp

**面向编码 Agent 的持久化 MCP 代码索引器**

*SQLite 持久索引、低上下文代码导航、符号级搜索、事件驱动自动更新*

![Python](https://img.shields.io/badge/Python-3.11%2B-blue?style=flat-square)
![MCP](https://img.shields.io/badge/MCP-Compatible-green?style=flat-square)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey?style=flat-square)
![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)

</div>

---

> [!NOTE]
> **仓库边界**
> 本仓库只包含独立的 `auto-index-mcp` 服务端，不依赖同级工作区或本机私有项目文件。

## 功能概览

| 功能 | 说明 |
|:-----|:-----|
| **持久索引** | 将文件、符号、import、元数据写入 SQLite，MCP 进程重启后仍可复用。 |
| **精确增量更新** | 普通文件新增、修改、删除只更新受影响记录，不做整库重建。 |
| **嵌套工作区** | 父目录发现子目录已有索引库时只挂链接，不重复维护子目录数据。 |
| **低上下文导航** | 提供 overview、tree、query、get、resolve、diff 等轻量工具。 |
| **符号索引** | 支持 Python AST 符号，JavaScript/TypeScript/通用文本轻量符号提取。 |
| **代码搜索** | 优先使用 ripgrep；遇到嵌套数据库时使用索引文件集合回退搜索。 |
| **自动刷新** | 使用系统文件变更事件触发，短 debounce 合并连续变更，再做轻量快照比对。 |
| **兼容工具名** | 保留常用文件查找、摘要、符号体、代码搜索、watcher/settings 等兼容入口。 |
| **MCP Resource** | 通过 `files://{file_path}` 暴露当前索引项目内的文件内容。 |

---

## 核心 API

| 分类 | API | 说明 |
|:-----|:----|:-----|
| **生命周期** | `auto_index_enable()` | 设置项目根目录，可选择立即重建索引。 |
| **生命周期** | `auto_index_status()` | 返回根目录、索引库路径、文件数量、更新时间、最近错误。 |
| **生命周期** | `auto_index_rebuild()` | 强制全量扫描并重写持久索引。 |
| **生命周期** | `auto_index_clear()` | 清空索引数据，可选择删除 SQLite 文件。 |
| **导航** | `auto_index_overview()` | 返回语言分布、目录分布、样例文件等紧凑概览。 |
| **导航** | `auto_index_tree_get()` | 返回目录级摘要、语言构成和样例文件。 |
| **导航** | `auto_index_query()` | 按文本、语言、父目录和游标查询索引文件。 |
| **导航** | `auto_index_get()` | 返回单个索引文件记录。 |
| **导航** | `auto_index_resolve_path()` | 按文件名或路径片段解析候选文件。 |
| **搜索** | `auto_index_text_search()` | 对源码进行 literal 或 regex 搜索。 |
| **搜索** | `auto_index_symbol_search()` | 按名称、签名、类型搜索符号。 |
| **搜索** | `auto_index_symbol_body()` | 返回指定符号的源码片段。 |
| **漂移检查** | `auto_index_diff_filesystem()` | 对比索引与当前文件系统的新增、删除、变化。 |
| **自动刷新** | `auto_index_watcher_start()` | 启动文件系统事件驱动的自动刷新。 |
| **自动刷新** | `auto_index_watcher_status()` | 查看 watcher 运行状态、触发次数、最近结果。 |
| **兼容入口** | `set_project_path()` | 用常见项目设置工具名初始化索引。 |
| **兼容入口** | `find_files()` | 按 glob 或文件名查找索引文件。 |
| **兼容入口** | `get_file_summary()` | 返回单文件 import、符号和复杂度摘要。 |
| **兼容入口** | `get_symbol_body()` | 按兼容格式返回符号源码体。 |
| **兼容入口** | `search_code_advanced()` | 支持文件过滤、regex、上下文、分页和 fuzzy 搜索。 |

---

## 设计边界

| 模块 | 职责 |
|:-----|:-----|
| `core/` | 对外服务编排、状态管理、生命周期入口。 |
| `indexing/` | 扫描、SQLite 存储、增量更新、watcher、轻量快照。 |
| `workspace/` | 嵌套工作区发现、父子索引聚合、路径安全检查。 |
| `languages/` | Python、JavaScript/TypeScript 和通用文本符号提取。 |
| `search/` | ripgrep/Python fallback 搜索后端。 |
| `mcp_api/` | MCP 工具注册，按生命周期、导航、搜索、兼容入口拆分。 |
| `compatibility/` | 常见兼容工具名和返回格式适配。 |

---

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

当前 watcher 不是固定每隔几秒扫一次目录，而是由系统文件变更事件触发。默认 debounce 为 0.25 秒，只用于合并连续保存、批量生成、SQLite WAL 写入等事件风暴。更新工作串行执行，一次快照/更新未结束时不会并发启动下一次。

---

## 目录结构

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
|       |-- workspace/
|       |-- __main__.py
|       `-- server.py
|-- tests/
|   |-- test_auto_index_service.py
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

当前发布包按 Windows 环境验证；其他平台未作为正式发布目标验证。

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

**Runtime:** Python 3.11+ | **Platform:** Windows | **Protocol:** MCP | **License:** MIT

</div>
