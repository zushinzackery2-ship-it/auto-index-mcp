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
| **LSP 语义检查** | 基于当前索引项目自动探测语言族，Windows 发布包内置 `clangd` 并主动拉取 diagnostics。 |
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
| **LSP** | `auto_index_lsp_start()` | 为当前索引项目自动启动可用 LSP server，返回压缩状态文本。 |
| **LSP** | `auto_index_lsp_check()` | 主动拉取当前项目或指定文件的 LSP diagnostics，返回高密度文本摘要。 |
| **LSP** | `auto_index_lsp_shutdown()` | 关闭当前项目下所有 LSP server。 |
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
| `core/lsp.py` | LSP server 自动探测、进程生命周期、JSON-RPC initialize/shutdown、压缩状态输出。 |
| `core/clangd_bootstrap.py` | `clangd` 编译数据库自动发现、`.vcxproj` 解析和托管配置生成。 |
| `compatibility/` | 常见兼容工具名和返回格式适配。 |

---

## 搜索一致性

`auto_index_query()`、`auto_index_symbol_search()`、`auto_index_file_summary()` 等结构化导航工具读取 SQLite 中的持久索引数据。

`auto_index_text_search()` 和兼容入口 `search_code_advanced()` 的正文匹配遵循“索引范围 + 实时文件内容”模型：

- 文件集合来自当前索引，新增、删除、重命名文件需要 watcher 或重建索引后才进入搜索范围。
- 正文内容优先通过 ripgrep 读取工作区实时文件；没有 ripgrep 或涉及嵌套子索引时回退为 Python 读取索引文件集合。
- 因此，已索引文件的内容刚被修改后，正文搜索通常能立即命中新内容；结构摘要和符号关系仍以索引刷新后的数据为准。

这个分工让低上下文导航保持稳定范围，同时让代码正文搜索尽量贴近磁盘上的最新内容。

## LSP 生命周期

LSP 是索引层之上的按需语义增强。Agent 不需要传项目根目录或语言；`auto_index_lsp_start()` 直接复用 `auto_index_enable()` / `set_project_path()` 已经设置的当前项目，并从索引文件集合里自动统计语言族。

LSP 工具只暴露面向 Agent 的高层入口，不暴露原始 `textDocument/*` 协议：

```text
auto_index_lsp_start(timeout_seconds=10.0)
auto_index_lsp_check(path?, limit=80, timeout_seconds=5.0)
auto_index_lsp_shutdown(timeout_seconds=5.0)
```

`clangd` 按 C family 建模，覆盖 C/C++/Objective-C/CUDA 相关扩展名。Windows 发布包自带 standalone `clangd 22.1.0`，会优先使用 `third-party/clangd_22.1.0/bin/clangd.exe`。Windows 安装器还会把 `pyright-langserver` 安装到项目 `.venv`，把 `typescript-language-server` 安装到 `.auto-index-mcp/lsp/npm` 托管 npm 工作区。找不到本地托管工具时才回退到 PATH。多语言项目会按索引结果尝试启动多个 server，例如 `clangd`、`pyright-langserver`、`typescript-language-server`、`rust-analyzer`、`gopls`。找不到可执行文件不会让整个启动失败，而是在压缩状态里标记 `missing`。

`clangd` 启动前会自动准备编译配置：

- 优先复用项目已有 `compile_commands.json`，包括根目录、`build/**`、`out/**` 下的常见位置。
- 项目没有编译数据库时，自动生成托管数据库到 `.auto-index-mcp/lsp/clangd/compile_commands.json`。
- Windows C++ 项目会优先读取 `.vcxproj` 的 `Release|x64` 配置，提取宏、include 目录和 C++ 标准。
- 项目已有 `.clangd` 时只检测并标记，不覆盖用户文件。
- 生成的 `.auto-index-mcp` 属于本地状态，已被扫描器和 `.gitignore` 排除。

返回值使用面向 Agent 的高密度文本，减少重复 JSON 字段名：

```text
LSP|partial|D:/Project
S:clangd/c-family/ready/files=342/ccdb=project:build/.clangd+/cfg=project
S:pyright/python/missing/files=28
```

没有项目编译数据库时，`clangd` 行会标记托管配置来源：

```text
LSP|unavailable|D:/Project
S:clangd/c-family/missing/files=4/ccdb=managed/.clangd-/cfg=vcxproj/std=c++20
```

状态头含义：

| 状态 | 含义 |
|:-----|:-----|
| `ready` | 目标语言族的 server 都已可用。 |
| `partial` | 部分 server 可用，部分缺失或启动失败。 |
| `unavailable` | 项目有可识别语言族，但没有任何 server 可用。 |
| `no_targets` | 当前索引里没有需要 LSP 的语言族。 |
| `not_configured` | 尚未设置 auto-index 项目根目录。 |

Windows C/C++ 项目只要发布包里保留 `third-party/clangd_22.1.0`，不需要额外安装 LLVM 或把 `clangd.exe` 加进 PATH。Python LSP 由安装器写入 `.venv`；JavaScript/TypeScript LSP 由安装器写入本地托管 npm 工作区，因此需要机器上已有 Node.js/npm。Rust 和 Go 的 server 仍按本机 PATH 查找。

`shutdown` 会关闭当前项目下所有 LSP server：

```text
LSP|stopped|D:/Project
S:clangd/stopped
S:pyright/stopped
```

`check` 是主动拉取 diagnostics 的入口。MCP 不会把后台 LSP 结果主动注入 Agent 上下文；Agent 需要调用 `auto_index_lsp_check()` 才会得到语义检查结果。

```text
CHK|issues|count=2|files=1|limit=80
E|src/main.cpp|12:5|unknown type name 'Foo'
W|src/app.py|8:1|unused import os
```

无诊断时返回：

```text
CHK|clean|files=42
```

LSP 尚未启动时返回：

```text
CHK|not_started
```

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

服务进程退出时会执行优雅熄火：`mcp.run()` 返回、异常退出、Python 正常退出、SIGINT、SIGTERM 都会调用 watcher 停止逻辑。默认 stdio 模式下 MCP 进程随客户端生命周期结束；HTTP/SSE 长驻模式下也可以通过 `auto_index_watcher_stop()` 或 `auto_index_disable()` 主动停止监听。

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
|-- third-party/
|   `-- clangd_22.1.0/
|       `-- bin/clangd.exe
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
