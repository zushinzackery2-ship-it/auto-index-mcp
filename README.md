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
| **嵌套工作区** | 挂载子目录已有索引，统一聚合文件、符号和语义结果。 |
| **符号级导航** | 符号搜索按匹配质量排序，`symbol_body` 按名取源码，`symbol_refs` 给出调用图（查找引用）。 |
| **路径容错** | 输入路径自动归一化：反斜杠、项目内绝对路径、大小写、唯一文件名均可解析。 |
| **结构化错误** | 工具区分无命中、参数错误、依赖缺失和查询超时，并提供诊断与完整性状态。 |
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
| **导航** | `auto_index_tree_get()` | 目录级摘要；`depth` 相对于请求的 `dir` 计算。 |
| **导航** | `auto_index_files()` | 模糊文件查找：路径子串、裸文件名、glob、符号名回退，可按语言/目录过滤。 |
| **导航** | `auto_index_file()` | 单文件索引记录：import、符号行区间、复杂度；`detail="full"` 附调用图与嵌套数据。 |
| **搜索** | `auto_index_text_search()` | 源码 literal/regex 搜索，返回紧凑 path/line 命中。 |
| **搜索** | `auto_index_symbol_search()` | 符号定义搜索：精确名 > 名前缀 > 名子串 > 签名，无命中自动子词放宽。 |
| **搜索** | `auto_index_symbol_body()` | 按名取符号源码；`path` 可省略（全库解析，歧义时返回候选）。 |
| **搜索** | `auto_index_symbol_refs()` | 调用图（查找引用）：callers / callees，改函数前看影响面。 |
| **语义** | `auto_index_semantic_search()` | 自然语言语义搜索，词法与向量混合排序。 |
| **质量** | `auto_index_quality_check()` | 嵌套过深、悬空代码、不可达代码检查。 |

所有路径参数为项目相对正斜杠格式；反斜杠、项目内绝对路径会自动归一化，未命中时可返回近似候选。工具错误包含 `error`、`message`，并按情况附带 `hint` 或 `candidates`；Resource 失败使用 MCP 资源错误，其消息包含同样的结构化诊断。

> [!NOTE]
> **已知局限**
> Python 引用分析覆盖格式化字符串、别名、装饰器和回调传参；动态分发、运行时注入及跨语言绑定仍可能漏报或误报。同名方法存在无法确定接收者的引用时标记为低置信；`kind="dangling"` 默认不显示低置信项。Python 不可达检测使用 AST，C/JS 等使用启发式分析；质量结果应结合调用方式判断。

---

## CLI 预建索引（推荐）

提前构建索引可以减少 MCP 首次查询的等待。

```bat
cd D:\your\project
auto-index-mcp build
```

```bat
auto-index-mcp build D:\your\project --rebuild
auto-index-mcp build D:\your\project --no-semantic
auto-index-mcp status D:\your\project
```

`build` 为一次性命令：已有兼容索引也会先与文件系统对账，再按需构建语义向量，完成后退出（exit code 0/1）。写入与运行中的 MCP 进程使用相同的跨进程租约；`--rebuild` 强制全量重建，`--no-semantic` 只更新导航索引。`status` 只读打印已有索引摘要。

不带子命令时 `auto-index-mcp` 即为 MCP server（stdio），现有客户端配置无需变更。CLI 子命令共五个：`build` / `status` / `list` / `clean` / `serve`。

---

## 索引注册与清理

索引默认存放在 `<root>/.auto-index-mcp/`。每次 `enable` / `build` 自动登记到用户级注册表，以索引目录为身份；同一项目的多个索引分别保留。旧版注册表会自动迁移。

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

`clean` 校验目录归属，并在持有维护租约期间删除已知数据库、伴生文件、日志和标记。活跃 watcher、构建或 schema 写入会阻止清理；删除失败保留注册记录并报告原因。未知文件和用于跨进程协调的稳定锁文件保留，因此空索引目录也可能继续存在。非交互终端必须带 `-y` 才会删除。

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

排序为混合评分：向量余弦相似度为主，符号名/签名的词法匹配校正排名。正文最多采样 256 行、8,000 字符，每个符号最多 8 个重叠窗口；超出这些范围的内容不参与嵌入。主索引和子索引统一检索，模型配置使用独立向量空间；结果区分 `empty`、`ready`、`building`、`waiting`、`partial`、`failed`，并提供 `complete` 与来源状态。

默认运行模式是按需语义索引：普通导航不会加载 ONNX 模型，首次语义搜索时才启动向量构建。需要启动后立即构建时设置 `AUTO_INDEX_SEMANTIC_MODE=eager`；不需要语义功能时设置 `AUTO_INDEX_SEMANTIC_MODE=off`。模型推理默认限制为单个 CPU 线程，并使用有界批处理，适合与多个 Agent 共用一台机器。

服务日志写入索引目录的 `logs/server-<pid>.log`，包含 PID、操作 ID、阶段与错误原因。单文件 512 KiB，每进程保留一份轮转日志，初始化时清理到最近 32 个日志文件。`auto_index_status()` 提供日志路径、构建状态、源/策略代次及 watcher owner/standby 状态；等待构建租约时附持有者诊断。

> [!IMPORTANT]
> **中文查询局限**
> 内置 MiniLM 主要面向英文，词法子词拆分也以 ASCII 标识符为主。中文需求建议使用英文描述查询，或配置输入输出格式兼容的多语言 ONNX 模型及对应 tokenizer。

---

## 检索边界

- 文本搜索使用安装依赖中的 ripgrep，即使未激活虚拟环境也可定位。正则采用 ripgrep 语法；不支持的表达式返回错误。整次请求共用 30 秒预算，超时或达到结果上限时检查 `complete`。
- 符号正文最多返回 64 KiB，超限标记 `truncated`；搜索上下文合计最多 32 KiB。`files://` 资源超过 64 KiB 时返回 `source-too-large`。正文定位发现文件已改变时提示索引漂移。
- 默认跳过超过 2 MiB 的源码；确需索引时使用 `ignore_*` 的 `target="privileged"` 配置。根目录和子目录 `.gitignore` 遵循 Git 层级匹配与父目录剪枝语义，运行时规则优先于文件规则。
- MCP 挂载兼容索引后由 watcher 收敛文件变化；禁用 watcher 时可通过 `diff` 检查漂移，或运行 CLI `build` 完成同步。

---

## 目录结构

```
auto-index-mcp/
├── src/auto_index_mcp/
│   ├── application/   生命周期、构建、watcher 与语义协调器
│   ├── core/          AutoIndexService 兼容入口
│   ├── domain/        记录、策略、配置和错误契约
│   ├── storage/       SQLite、符号关系、代次和向量持久化
│   ├── indexing/      扫描、快照与增量发布
│   ├── languages/     Python AST 与多语言解析适配
│   ├── workspace/     主/子索引视图与聚合
│   ├── search/        文本、符号与语义查询
│   ├── embedding/     ONNX 后端、正文窗口与有界推理
│   ├── quality/       嵌套、不可达与未使用候选分析
│   ├── registry/      注册事务、目录归属和清理
│   ├── commands/      build、status、list、clean
│   ├── mcp_api/       MCP 工具、资源和异步调度
│   └── runtime/       租约、执行预算、日志和进程生命周期
├── tests/
│   ├── unit/          单元回归
│   ├── integration/   存储、工作区与真实协议回归
│   ├── performance/   病态输入与规模边界
│   └── fixtures/      数据与全量调用图对照算法
├── oldtest/           历史兼容测试归档
└── scripts/stress/    并发与资源压测
```

---

## 安装

### Windows 一键安装

```bat
install_windows.bat
```

脚本复用或创建 `.venv`，安装语义依赖、配置模型环境变量，并验证 MCP 入口及 ripgrep。若 MCP 客户端已运行，安装后重启以加载新代码和环境变量。发布 wheel 也包含内置模型。

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
python -m pip install -e ".[semantic,dev]"
python -m pytest -q
python -m ruff check --select F src tests scripts
```

严格质量门禁在临时目录重建源码索引；深层嵌套、未核准告警、分析覆盖不足和超过 300 行的生产/默认测试文件均使检查失败。框架回调与公共接口的例外逐项记录原因：

```bash
python scripts/self_quality_check.py
```

协议检查覆盖 13 个工具、资源、结构化错误、watcher 更新、ping 和退出回收。资源基准记录冷/热构建、单文件/突发更新、查询 P50/P95、Python 分配峰值、RSS、CPU 与数据库大小：

```bash
python scripts/verify_mcp_stdio.py
python scripts/stress/resource_bench.py --files 10000 --queries 40
python scripts/stress/run_stress.py K2 4 15 1000
```

---

<div align="center">

**Runtime:** Python 3.11+ | **Platform:** Windows x64 | **Protocol:** MCP | **License:** MIT

</div>
