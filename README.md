# 本地 AI Agent 教学演示

基于 LM Studio 的本地 Agent 学习项目。项目保留显式的 Agent 实现，不依赖 LangChain 等重型框架，重点展示：

- Function Calling：模型自主选择工具并读取工具结果
- ReAct：`Thought -> Action -> Observation -> Final Answer`
- RAG：PDF/Markdown/TXT 提取、embedding、检索与来源引用
- Skill：声明式 Markdown 工作流与受限工具集
- MCP：Streamable HTTP 工具发现与调用
- 多 Agent：Planner、Executor、Critic 协作
- 会话持久化、评测报告、安全边界与事件轨迹

## 架构

```text
用户输入
   |
   v
AgentRuntime.run()
   |
   +-- AgentEvent(run_started / route_selected)
   +-- 模型 Function Calling 循环
   +-- ToolExecutor 权限与确认检查
   +-- AgentEvent(tool_started / tool_finished / observation)
   +-- AgentEvent(final / error)
   |
   +--> CLI 文本渲染
   +--> Gradio 分色轨迹
```

核心模块：

- `ai_agent/runtime.py`：统一工具调用循环与事件流
- `ai_agent/events.py`：`AgentEvent` 事件类型
- `ai_agent/tooling.py`：`ToolPolicy`、`ToolExecutor`
- `ai_agent/app_core.py`：提示词、工具 schema、工具注册（单一真相源）
- `ai_agent/bootstrap.py`：CLI / Web 共享的服务装配（client / RAG / MCP / Skill / tool_map）
- `ai_agent/session_service.py`：CLI / Web 共享的会话业务规则（自动命名、恢复、压缩阈值）
- `ai_agent/persistence.py`：线程安全的 SQLite 会话存储
- `ai_agent/web_ui.py`：按浏览器隔离状态的 Gradio UI（状态装配与事件流）
- `ai_agent/web_render.py`：Web 呈现层纯函数（LLM 调用检查器 / 执行轨迹 HTML、`SessionState` TypedDict）
- `ai_agent/client.py`：LM Studio 客户端 + 高级 Agent 模式（所有 API 调用经 `_create_completion` 收口）
- `ai_agent/llm_trace.py`：LLM 调用快照（输入消息/工具/参数、输出、用量、耗时，供双界面展示）
- `ai_agent/cli_trace.py`：CLI 侧的调用摘要与 `/calls` 详情渲染（rich）
- `ai_agent/rag.py`：RAG 检索与安全缓存
- `ai_agent/skill.py`：Skill 加载与关键词路由匹配
- `ai_agent/mcp_client.py`：MCP Streamable HTTP 客户端
- `ai_agent/config.py`：集中配置 + 环境变量覆盖
- `ai_agent/multi_agent.py`：Planner / Executor / Critic 三角色协作
- `ai_agent/eval.py`：20+ Gold Case 评测框架 + HTML 报告

详细 API 见 [docs/project/API-接口文档.md](docs/project/API-接口文档.md)。

## 环境要求

- Python 3.10-3.12
- LM Studio 已启动 OpenAI 兼容服务
- 已加载一个可进行聊天/工具调用的非 embedding 模型（推荐 qwen3.5-9b / qwen3.6-35b）

当前锁定的主要版本包括 Gradio 5.50.0、FastAPI 0.141.1、Starlette 0.52.1 和 OpenAI Python SDK 1.109.1。完整版本见 `requirements.lock` 与 `requirements-dev.lock`。

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

Linux/macOS：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

仅安装运行依赖时使用 `requirements.txt`。

## 启动

Web UI：

```powershell
python -m ai_agent.web_ui
```

可选参数：

```powershell
python -m ai_agent.web_ui --host 127.0.0.1 --port 7860
python -m ai_agent.web_ui --share          # 生成公网分享链接
```

CLI：

```powershell
python main.py
# 或
python -m ai_agent
```

### CLI 参数

| 参数 | 说明 | 默认 |
|------|------|------|
| `--db DB_PATH` | SQLite 数据库路径 | `agent_sessions.db` |
| `--enable-dangerous-tools` | 显式启用代码/终端执行 | `false` |

### Web UI 参数

| 参数 | 说明 | 默认 |
|------|------|------|
| `--host` | 绑定地址 | `127.0.0.1` |
| `--port` | 端口 | `7860` |
| `--share` | 生成公网分享链接 | `false` |

> 完整配置参考见 [docs/project/Config-配置参考.md](docs/project/Config-配置参考.md)。

## 观察 LLM API 的输入输出

所有模式（Function Calling / ReAct / 多 Agent 等）的请求都经过 `LMStudioClient._create_completion`
这一个收口点，每次调用的输入与输出会被快照记录下来，两个界面都能查看：

- **Web UI**：右栏「API 调用检查器」按「第几轮提问」分组列出本会话的所有调用——历史轮折叠、
  最新一轮展开；chat 模式流式生成时输出实时增长；卡片内输入（消息 + 工具 schema + 采样参数）
  与输出（content / tool_calls / finish_reason / token 用量）左右对照，附请求 JSON、响应 JSON
  与可复制运行的等价 Python 代码，鼠标悬停代码块可一键复制。「🗑 清空调用记录」可随时重置。
- **CLI**：每轮回复后自动打印一行调用摘要（次数 / token / 耗时），输入 `/calls` 查看上一轮
  每次调用的完整输入输出面板，`/calls json` 额外打印请求/响应 JSON 与等价代码，
  `/calls off` 可关闭摘要行。

> 流式响应服务端不返回 usage，此时 token 数为字符估算值，界面以「≈」标注。

## 安全默认值

| 能力 | 默认策略 |
| --- | --- |
| `file_read/file_write` | 只能访问 `AGENT_WORKSPACE_ROOT` 内的路径 |
| `sql_query` | 使用 SQLite 只读连接（URI `mode=ro` + `PRAGMA query_only=ON`） |
| `run_code/run_terminal` | 默认关闭，CLI 需显式启用并逐次确认 |
| Web UI | 不开放代码和终端执行 |
| `--share` | 关闭文件写入、执行型工具和 MCP |
| MCP | 只有配置显式允许列表时才在 Web 中开放 |
| Calculator | AST 白名单校验 + 指数/结果规模限制 |

Web 和 CLI 中的 `human_confirm` 只是教学示例，真正的权限控制由 `ToolExecutor` 在工具执行前完成。

## 配置

复制 `.env.example` 为 `.env` 后可修改：

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `LM_STUDIO_BASE_URL` | LM Studio OpenAI 地址 | `http://localhost:1234/v1` |
| `LM_STUDIO_API_KEY` | 本地服务 API key | `lm-studio` |
| `LM_STUDIO_MODEL` | 模型 ID；`auto` 自动选择聊天模型 | `auto` |
| `LM_STUDIO_TIMEOUT` | 模型请求超时秒数 | `120` |
| `EMBED_MODEL` | RAG embedding 模型 | `text-embedding-nomic-embed-text-v1.5` |
| `RAG_CHUNK_SIZE` | RAG 分块字符数 | `500` |
| `RAG_CHUNK_OVERLAP` | RAG 分块重叠字符数 | `50` |
| `RAG_DIRECTORIES` | RAG 知识扫描目录，分号分隔 | `docs/knowledge` |
| `AGENT_WORKSPACE_ROOT` | 文件工具允许访问的根目录 | 项目根目录 |
| `ENABLE_DANGEROUS_TOOLS` | 环境变量方式启用危险工具 | `false` |
| `MCP_SERVERS` | MCP URL，分号分隔 | 空 |
| `MCP_ALLOWED_TOOLS` | Web 允许的 MCP 工具，分号分隔 | 空 |
| `SKILL_DIR` | Skill Markdown 目录 | `skills` |
| `HISTORY_MAX_CHARS` | 对话历史自动压缩阈值（字符数） | `5000` |

## 测试与检查

```powershell
python -m pytest -q
ruff check main.py ai_agent
```

当前测试覆盖事件运行循环、工具权限、路径边界、只读 SQL、会话级联删除、RAG 缓存、MCP 解析与命名空间等。

## 评测

评测会在临时工作区运行，不会修改仓库中的 `README.md`、`greet.txt` 或数据库文件：

```powershell
python -m ai_agent.eval --list
python -m ai_agent.eval --no-rag
python -m ai_agent.eval --tag react
python -m ai_agent.eval --html-report eval_report.html
```

终端/代码执行用例默认跳过。只有在隔离的本机环境明确允许时才使用：

```powershell
python -m ai_agent.eval --enable-dangerous-tools
```

## 项目结构

```text
ai_agent/
  app_core.py       # 提示词、工具 schema、注册与策略工厂（单一真相源）
  bootstrap.py      # CLI / Web 共享的服务装配（client / RAG / MCP / Skill / tool_map）
  session_service.py # CLI / Web 共享的会话业务规则（自动命名 / 恢复 / 压缩阈值）
  client.py         # LM Studio 客户端（演示算法薄委托，实现在 demos/）
  demos/            # 演示算法包：ReAct / 思维链 / 原生推理 / 反思 / 规划 / 结构化提取
  runtime.py        # 统一事件式工具循环（CLI / Web UI 共用）
  events.py         # AgentEvent 类型（frozen dataclass）
  tooling.py        # ToolPolicy（read/write/execute/mcp/network 五级风险分类）/ ToolExecutor
  config.py         # 集中配置 + 环境变量覆盖 + load_env()
  persistence.py    # 线程安全的 SQLite 会话存储
  rag.py            # RAG 检索（余弦相似度 + JSON 缓存 + 原子写入 + collect_rag_files）
  skill.py          # Skill 加载（Markdown Frontmatter）+ 关键词路由匹配
  mcp_client.py     # MCP Streamable HTTP 客户端（极简实现）
  multi_agent.py    # Planner / Executor / Critic 三角色协作
  web_ui.py         # Gradio Web UI（per-browser State + threading.local；渲染委托 web_render）
  web_render.py     # Web 呈现层纯函数（LLM 调用面板 / 执行轨迹 HTML + SessionState TypedDict）
  static/web_ui.css # Web UI 外置样式表（由 web_ui._load_css 读取）
  eval.py           # Gold Case 离线评测框架
  eval_report_template.html  # 评测 HTML 报告模板（string.Template 占位符）
  console.py        # Windows 控制台 UTF-8 适配
  tools/            # 模块级纯工具（无依赖，可独立测试）
    demo.py         # get_weather, calculator
    fs.py           # file_read, file_write
    search.py       # web_search
    sql.py          # sql_query
    system.py       # run_terminal, run_code, human_confirm
    wordpress.py    # wp_publish（薄包装 skills/wordpress-publish/）
  tests/            # 离线单元与契约测试
docs/
  README.md                        # 文档总索引
  project/                         # 项目自身技术文档
    API-接口文档.md                # 工具详解
    Config-配置参考.md             # 配置项详解
    Quickstart-快速上手.md         # 10 分钟跑通
    架构总览.md                   # ASCII 架构图 + 模块关系
    开发指南-Contributing.md       # 如何扩展工具/Skill/评测
    FAQ-故障排查.md               # 常见问题排查
    设计决策清单.md                # 34 条设计决策
    Skill实现本质原理.md           # Skill 专题
    ai-agent-learning-roadmap.html # 学习路线图
  knowledge/                       # 外部知识源（RAG 扫描源）
  blog/                            # 开发博客（5 篇 WordPress 系列）
skills/
  example_plan.md                  # Skill 示例：规划写作工作流
  wordpress-publish.md             # Skill：WordPress 博客发布
  wordpress-publish/               # 可移植的 WordPress 发布器（wp_auto_publisher.py）
```

## License

学习演示项目，可自由用于教学和本地实验。
