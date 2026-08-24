<div align="center">

# auto-index-mcp

**面向编码 Agent 的持久化 MCP 代码索引器**

*SQLite 持久索引、低上下文代码导航、符号级搜索与调用图、事件驱动自动更新*

![Python](https://img.shields.io/badge/Python-3.11%2B-blue?style=flat-square)
![MCP](https://img.shields.io/badge/MCP-Compatible-green?style=flat-square)
![Platform](https://img.shields.io/badge/Platform-Windows%20x64-lightgrey?style=flat-square)
![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)

</div>

---

## 功能概览

| 功能 | 说明 |
|:-----|:-----|
| **持久索引** | 将文件、符号、import、元数据写入 SQLite，MCP 进程重启后仍可复用。 |
| **冷启动自动化** | 工具首次调用时自动从 MCP 客户端 `roots` 或 `AUTO_INDEX_PROJECT_PATH` 环境变量识别项目根，无需手动 enable。 |
| **CLI 预建索引** | `auto-index-mcp build` 一次性同步建库（含语义向量、带进度条），AI 首次会话即刻可用。 |
| **索引注册与清理** | 每次建库登记到用户级 `registry.json`，`auto-index-mcp list` / `clean` 可集中查看和安全删除散落各项目的索引。 |
| **精确增量更新** | 普通文件新增、修改、删除只更新受影响记录，不做整库重建。 |
| **嵌套工作区** | 父目录发现子目录已有索引库时只挂链接，不重复维护子目录数据。 |
| **符号级导航** | 符号搜索按匹配质量排序，`symbol_body` 按名取源码，`symbol_refs` 给出调用图（查找引用）。 |
| **路径容错** | 输入路径自动归一化：反斜杠、项目内绝对路径、大小写、唯一文件名均可解析。 |
| **结构化错误** | 错误以 `{error, hint, candidates}` 返回，附近似候选，Agent 可自愈重试。 |
| **语义搜索** | 自然语言找符号，本地 ONNX Embedding，词法与向量混合排序，长符号分块嵌入。 |
| **自动刷新** | 文件变更时自动增量更新索引，无需手动重建。 |
| **质量检查** | 基于持久索引缓存报告嵌套过深、疑似悬空代码和不可达代码。 |
| **MCP Resource** | 通过 `files://{file_path}` 暴露当前索引项目内的文件内容。 |

---

## 核心 API（13 个工具）

| 分类 | API | 说明 |
|:-----|:----|:-----|
| **生命周期** | `auto_index_enable()` | 绑定项目根目录；`root_path` 可省略（自动探测），默认复用已有索引。 |
| **生命周期** | `auto_index_status()` | 紧凑索引健康态：文件/向量数、watcher、构建进度，ISO 时间戳。 |
| **生命周期** | `auto_index_manage(action=...)` | 运维统一入口：`rebuild` / `clear` / `watch_start` / `watch_stop` / `disable` / `diff` / `registry`（只读列出注册表） / `ignore_*`。 |
| **导航** | `auto_index_overview()` | 首过概览：语言分布、目录分布、按目录轮转的代表性样例（入口文件优先，测试/归档目录降权）。 |
| **导航** | `auto_index_tree_get()` | 目录级摘要：文件数、语言构成、样例文件名。 |
| **导航** | `auto_index_files()` | 模糊文件查找：路径子串、裸文件名、glob、符号名回退，可按语言/目录过滤。 |
| **导航** | `auto_index_file()` | 单文件索引记录：import、符号行区间、复杂度；`detail="full"` 附调用图与嵌套数据。 |
| **搜索** | `auto_index_text_search()` | 源码 literal/regex 搜索，返回紧凑 path/line 命中。 |
| **搜索** | `auto_index_symbol_search()` | 符号定义搜索：精确名 > 名前缀 > 名子串 > 签名，无命中自动子词放宽。 |
| **搜索** | `auto_index_symbol_body()` | 按名取符号源码；`path` 可省略（全库解析，歧义时返回候选）。 |
| **搜索** | `auto_index_symbol_refs()` | 调用图（查找引用）：callers / callees，改函数前看影响面。 |
| **语义** | `auto_index_semantic_search()` | 自然语言语义搜索，词法与向量混合排序。 |
| **质量** | `auto_index_quality_check()` | 嵌套过深、悬空代码、不可达代码检查。 |

所有路径参数为项目相对正斜杠格式；反斜杠、项目内绝对路径会自动归一化，未命中时错误里附近似候选。错误统一为 `{error, hint, candidates?}` 结构，不抛裸协议异常。

> [!NOTE]
> **已知局限**
> `kind="dangling"` 默认 `include_low_confidence=false`，配置/文档类 orphan 低置信项不展示。不可达检测：Python AST 路径为 high 置信；C/JS 等为大括号启发式 medium 置信，嵌套块内可能误报或漏报。僵尸代码检测按调用关系分析，函数仅被作为参数传递（如 `sort(key=fn)`）时可能误报未使用。

---

## CLI 预建索引（推荐）

MCP 会话首次建库的等待可以完全消除：提前手动建好，AI 连上即复用。

```bat
cd D:\your\project
auto-index-mcp build
```

```bat
auto-index-mcp build D:\your\project --rebuild
auto-index-mcp build D:\your\project --no-semantic
auto-index-mcp status D:\your\project
```

`build` 为一次性命令：同步扫描建库（实时计数），随后同步构建语义向量（精确百分比进度条），完成即退出（exit code 0/1）。与运行中的 MCP 进程通过跨进程构建锁互斥，不会重复扫描。`status` 只读打印已有索引摘要。

不带子命令时 `auto-index-mcp` 即为 MCP server（stdio），现有客户端配置无需变更。CLI 子命令共五个：`build` / `status` / `list` / `clean` / `serve`。

---

## 索引注册与清理

索引存放在各项目内部（`<root>/.auto-index-mcp/`），项目删除后容易遗留。每次 `enable` / `build` 建库时会自动登记到用户级注册表，随时可以集中查看和清理。

### 落盘位置

| 文件 | 位置 | 作用 |
|:-----|:-----|:-----|
| `registry.json` | `AUTO_INDEX_REGISTRY_DIR`（如设置）> Windows `%LOCALAPPDATA%\auto-index-mcp` > POSIX `$XDG_STATE_HOME`（默认 `~/.local/state`）`/auto-index-mcp` | 每用户一份，记录每个索引的 root、index_dir、创建/最后挂载时间、来源、是否 ephemeral。 |
| `marker.json` | 每个索引目录内 | 归属证明：标记该目录属于本工具及其对应项目根，`clean` 删除前校验。 |
| `CACHEDIR.TAG` | 每个索引目录内 | 标准 [cache-directory 标记](https://bford.info/cachedir/)，遵循该约定的备份工具会自动跳过索引。 |

### CLI 用法

```bat
auto-index-mcp list
```

列出注册表中全部索引：root、索引路径与体积、最后挂载时间，并标注 `orphan`（项目或索引已消失）/ `ephemeral`（建在非默认位置）。

```bat
auto-index-mcp clean                       :: 默认只清 orphan + ephemeral
auto-index-mcp clean D:\old\project        :: 清指定项目的索引
auto-index-mcp clean --all -y              :: 清全部注册索引，跳过确认
auto-index-mcp clean --older-than 30d      :: 只清 30 天未挂载的（也支持 12h / 45m）
auto-index-mcp clean --scan D:\Work        :: 扫描目录收编未注册的既有索引（不删除）
auto-index-mcp clean --dry-run             :: 只打印将删除什么
```

### 清理安全策略

`clean` 只删自己的东西：删除前校验目录内 `marker.json`（或 `index.db` 元数据）确实指向该项目根，否则拒绝；只删除已知索引文件（`index.db`、`embeddings.db` 及伴生文件、锁、标记），目录里有陌生文件时保留目录并提示；检测到其他进程正在该目录构建时跳过。非交互终端必须带 `-y` 才会删除。

MCP 侧提供只读视图：`auto_index_manage(action="registry")` 返回注册表全量条目及存活标记；`auto_index_status()` 的 `registered` 字段表示当前项目是否已登记。删除操作刻意只留在 CLI。

---

## 冷启动解析顺序

任何工具在索引未绑定时被调用，按以下顺序自动解析项目根：

1. 启动参数 `--project-path`；
2. 环境变量 `AUTO_INDEX_PROJECT_PATH`；
3. MCP 客户端 `roots` 能力（Cursor / Claude Code 等自动上报工作区）；
4. 全部失败时返回结构化错误，提示调用 `auto_index_enable(root_path=...)`。

---

## 语义搜索

`auto_index_semantic_search()` 通过自然语言搜索找到最相关的代码符号，基于向量 Embedding。需要额外安装依赖：

```bash
pip install -e ".[semantic]"
```

默认使用内置 MiniLM ONNX 模型（约 90MB）进行 Embedding 推理，纯本地计算，无网络依赖。可通过 `AUTO_INDEX_EMBEDDING_MODEL` 环境变量指定自定义模型目录（须包含 `model.onnx` 和 `tokenizer.json`）。

排序为混合评分：向量余弦相似度为主，查询与符号名/签名的词法重合度校正排名。超出模型截断窗口的长符号自动按重叠窗口分块，每块独立向量，尾部内容不丢失。

> [!IMPORTANT]
> **中文查询局限**
> 内置 MiniLM 为英文模型，中文查询主要依赖词法兜底，向量召回有限。中文场景建议用英文描述查询，或通过 `AUTO_INDEX_EMBEDDING_MODEL` 指向多语言 ONNX 模型（如 bge-small-zh、multilingual-MiniLM）。

---

## 安装

### Windows 一键安装

```bat
install_windows.bat
```

脚本会创建 `.venv`、安装依赖、配置环境变量并验证 MCP 入口。若 MCP 客户端已运行，安装后重启以继承新环境变量。

### 手动安装

```bash
python -m pip install -e .
```

语义搜索（可选）：

```bash
python -m pip install -e ".[semantic]"
```

---

## 运行

```bash
python -m auto_index_mcp.server --project-path /path/to/project
```

```bash
auto-index-mcp serve --project-path /path/to/project
```

传入 `--project-path` 时默认启动文件监听。一次性校验场景可加 `--no-watch` 禁用监听。

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

Windows 一键安装后，可以参考安装脚本生成的 `mcp-client-config.windows.json` 示例配置。当前主要在 Windows x64 上验证。

---

## 测试

本地测试以 pytest 为准（无 GitHub Actions CI）。现行用例在 `tests/`（`oldtest/` 为历史归档，不参与默认收集）：

```bash
python -m pytest -q
```

全量重建索引并进行质量检查：

```bash
python scripts/self_quality_check.py
```

烟测：

```bash
python scripts/smoke_auto_index.py
```

---

<div align="center">

**Runtime:** Python 3.11+ | **Platform:** Windows x64 | **Protocol:** MCP | **License:** MIT

</div>
