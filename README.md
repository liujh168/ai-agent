# 本地 AI Agent 教学演示

一个**从零手写** AI Agent 的中文教学项目：不依赖 LangChain 等重型框架，用直白可读的 Python 把 Agent 的核心机制逐个实现出来。基于 LM Studio 本地模型服务（OpenAI 兼容接口），覆盖：

- **Function Calling**：模型自主选择工具并读取工具结果
- **ReAct / 思维链 / 反思 / 规划** 等经典 Agent 模式（`ai_agent/demos/`）
- **RAG**：Markdown / PDF / TXT 分块、embedding 检索、来源引用
- **Skill**：把领域工作流写成 Markdown，自动路由与收窄工具集
- **MCP**：接入外部工具服务器（Streamable HTTP 与 stdio 两种 Transport）
- **多 Agent**：Planner / Executor / Critic 三角色协作
- **安全层**：五级风险分类 + 人工确认 + 工作区路径边界
- **上下文压缩**：长对话自动摘要，历史持久化到 SQLite
- **评测**：20+ Gold Case 离线评测框架 + HTML 报告

## 架构全景图

```
用户输入
   │
   ├──────────────► CLI：python main.py（main.py → AgentDemoApp）
   └──────────────► Web UI：python -m ai_agent.web_ui（Gradio）
                          │
                          ▼
              ┌───────────────────────┐
              │ bootstrap.py          │  CLI / Web 共享装配
              │ AgentServices         │  client / RAG / MCP / Skill
              └───────────┬───────────┘
                          ▼
              ┌───────────────────────┐
              │ runtime.py            │  Function Calling 循环
              │ AgentRuntime          │  （Skill 路由 → LLM → 工具 → 回写）
              └───────────┬───────────┘
                          ▼
              ┌───────────────────────┐
              │ tooling.py            │  五级风险策略 + 执行前确认
              │ ToolExecutor          │
              └───────────┬───────────┘
        ┌─────────┬───────┼───────┬──────────┐
        ▼         ▼       ▼       ▼          ▼
   tools/     rag.py  mcp_client  skill.py  multi_agent.py
   内置工具   知识库   MCP 服务器  Skill    多 Agent 协作
```

更详细的时序图与模块依赖关系见 [docs/project/架构总览.md](docs/project/架构总览.md)。

## 三分钟 Quickstart

前置条件：Python 3.10–3.13；已安装 [LM Studio](https://lmstudio.ai/)，加载一个支持 Function Calling 的模型（推荐 qwen3.5-9b），并启动本地服务（默认 `http://localhost:1234`）。

```powershell
# 1. 克隆并进入项目
git clone <repo-url> && cd ai-agent

# 2. 创建虚拟环境（Windows）
python -m venv .venv
.\.venv\Scripts\Activate.ps1
# macOS/Linux: python3 -m venv .venv && source .venv/bin/activate

# 3. 安装项目（editable 模式）
python -m pip install -e .

# 4. 配置环境变量
copy .env.example .env    # macOS/Linux 用 cp
# 默认值已适配本地 LM Studio，一般无需改动

# 5. 启动 CLI 对话
python main.py            # 或 python -m ai_agent / ai-agent
```

可选：启动 Web UI（浏览器访问 http://127.0.0.1:7860）：

```powershell
python -m ai_agent.web_ui    # 或 ai-agent-web
```

进入对话后试试：`北京天气怎么样？123*456 等于多少？` 观察模型如何调用工具。

## 可选能力

- `python -m pip install -e .[faiss]`：RAG 片段超过阈值时启用 FAISS 向量索引加速（未装则自动回退纯 Python 余弦检索）。
- `MCP_SERVERS`：在 `.env` 中配置外部 MCP 服务器，支持 Streamable HTTP（`https://...`）与 stdio（`stdio:python -m my_server`）两种接入方式，多个用分号分隔。

## 文档导航

- [docs/README.md](docs/README.md)：文档总索引，说明三层目录的组织方式。
- [docs/blog/](docs/blog/)：5 篇教学博文系列，从 Function Calling 极简实现一路讲到安全防御与评测框架，适合按顺序阅读。
- [docs/project/](docs/project/)：原理拆解与技术文档——架构总览、Quickstart 十分钟跑通、34 条设计决策清单、RAG/Skill/MCP/多 Agent 专题、API 与配置参考、FAQ。

## 运行测试

```powershell
.venv/Scripts/python -m pytest ai_agent/tests/
```

全部为离线测试（mock LLM），无需启动 LM Studio。

## License

学习演示项目，可自由用于教学和本地实验。
