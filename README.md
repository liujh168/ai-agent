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
- `ai_agent/app_core.py`：提示词、工具 schema、工具注册
- `ai_agent/persistence.py`：线程安全的 SQLite 会话存储
- `ai_agent/web_ui.py`：按浏览器隔离状态的 Gradio UI

## 环境要求

- Python 3.10-3.12
- LM Studio 已启动 OpenAI 兼容服务
- 已加载一个可进行聊天/工具调用的非 embedding 模型

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
python -m ai_agent.web_ui --share
```

CLI：

```powershell
python main.py
# 或
python -m ai_agent
```

代码执行和终端执行默认关闭。确需在本地演示时显式启用：

```powershell
python main.py --enable-dangerous-tools
```

每次执行危险工具前仍会要求人工确认。

## 安全默认值

| 能力 | 默认策略 |
| --- | --- |
| `file_read/file_write` | 只能访问 `AGENT_WORKSPACE_ROOT` 内的路径 |
| `sql_query` | 使用 SQLite 只读连接和 `query_only` |
| `run_code/run_terminal` | 默认关闭，CLI 需显式启用并逐次确认 |
| Web UI | 不开放代码和终端执行 |
| `--share` | 关闭文件写入、执行型工具和 MCP |
| MCP | 只有配置显式允许列表时才在 Web 中开放 |
| Calculator | 限制表达式长度、指数和结果规模 |

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
| `AGENT_WORKSPACE_ROOT` | 文件工具允许访问的根目录 | 项目根目录 |
| `ENABLE_DANGEROUS_TOOLS` | 环境变量方式启用危险工具 | `false` |
| `MCP_SERVERS` | MCP URL，使用分号分隔 | 空 |
| `MCP_ALLOWED_TOOLS` | Web 允许的 MCP 工具，使用分号分隔 | 空 |
| `SKILL_DIR` | Skill Markdown 目录 | `skills` |

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
  app_core.py       # 提示词、工具 schema、注册与策略工厂
  client.py         # LM Studio 客户端与高级 Agent 模式
  runtime.py        # 统一事件式工具循环
  events.py         # AgentEvent
  tooling.py        # ToolPolicy / ToolExecutor
  persistence.py    # SQLite 会话持久化
  rag.py            # RAG 检索与安全缓存
  skill.py          # Skill 加载与匹配
  mcp_client.py     # MCP Streamable HTTP 客户端
  multi_agent.py    # Planner / Executor / Critic
  web_ui.py         # Gradio Web UI
  tests/            # 离线单元与契约测试
docs/
  ...               # 教学文档
skills/
  example_plan.md   # Skill 示例
```

## License

学习演示项目，可自由用于教学和本地实验。
