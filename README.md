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

## 功能概览

| 功能 | 说明 |
|:-----|:-----|
| **持久索引** | 将文件、符号、import、元数据写入 SQLite，MCP 进程重启后仍可复用。 |
| **精确增量更新** | 普通文件新增、修改、删除只更新受影响记录，不做整库重建。 |
| **嵌套工作区** | 父目录发现子目录已有索引库时只挂链接，不重复维护子目录数据。 |
| **低上下文导航** | 提供 overview、tree、query、get、resolve、diff 等轻量工具。 |
| **符号索引** | 支持 Python AST 符号，JavaScript/TypeScript、C/C++、Pascal 和通用文本轻量符号提取。 |
| **代码搜索** | 支持源码内容和符号名称搜索，同时支持正则匹配。 |
| **语义搜索** | 通过自然语言找到最相关的符号，基于本地 ONNX 模型，无网络依赖。 |
| **自动刷新** | 文件变更时自动增量更新索引，无需手动重建。 |
| **质量检查** | 基于持久索引缓存报告嵌套过深、疑似悬空代码和不可达代码。 |
| **MCP Resource** | 通过 `files://{file_path}` 暴露当前索引项目内的文件内容。 |

---

## 核心 API

| 分类 | API | 说明 |
|:-----|:----|:-----|
| **生命周期** | `auto_index_enable()` | 设置项目根目录，默认复用已有索引，可显式重建。 |
| **生命周期** | `auto_index_disable()` | 停用当前索引状态并停止自动刷新。 |
| **生命周期** | `auto_index_status()` | 返回索引状态，包括文件数量、更新时间、错误信息及后台任务进度。 |
| **生命周期** | `auto_index_ignore()` | 配置索引排除规则，支持忽略大文件或特定目录。 |
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
| **语义搜索** | `auto_index_embedding_status()` | 报告语义 embedding 后端是否启用及向量数量；`build_timer` 给出语义向量构建的实时计时。 |
| **质量检查** | `auto_index_quality_check()` | 检查代码质量，报告嵌套深度过深、悬空代码或不可达代码等问题。 |
| **漂移检查** | `auto_index_diff_filesystem()` | 对比索引与当前文件系统的新增、删除、变化。 |
| **自动刷新** | `auto_index_watcher_start()` | 非阻塞启动文件系统事件驱动的自动刷新。 |
| **自动刷新** | `auto_index_watcher_stop()` | 停止文件系统事件驱动的自动刷新。 |

可通过 `auto_index_enable(rebuild=True)` 强制全量重建索引，或使用 `auto_index_rebuild()` 后台重建。所有 API 详细参数见各工具的在线帮助。

> [!NOTE]
> **已知局限**
> `auto_index_symbol_search()` 按名称/签名模糊匹配，子类 signature 含基类名时可能排在基类定义之前；精确读符号体请用 `auto_index_symbol_body()`。`kind="dangling"` 默认 `include_low_confidence=false`，配置/文档类 orphan 低置信项不展示。不可达检测：Python AST 路径为 high 置信；C/JS 等为大括号启发式 medium 置信，嵌套块内可能误报或漏报。

---


## 语义搜索

`auto_index_semantic_search()` 通过自然语言搜索找到最相关的代码符号。需要额外安装依赖：

```bash
pip install -e ".[semantic]"
```

默认使用内置 MiniLM ONNX 模型（约 90MB），纯本地推理，无网络依赖。可通过 `AUTO_INDEX_EMBEDDING_MODEL` 环境变量指定自定义模型目录（须包含 `model.onnx` 和 `tokenizer.json`）。


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
auto-index-mcp --project-path /path/to/project
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

本地测试以 pytest 为准（无 GitHub Actions CI）：

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
