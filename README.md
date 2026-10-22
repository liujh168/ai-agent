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

> **CLI 与 Web UI 的分工**：CLI（`python main.py`）是最薄的验证面——改完代码想确认 Agent 还能跑，不用开浏览器；也适合脚本化、管道输入等自动化场景。Web UI（`python -m ai_agent.web_ui`）适合可视化学习：API 调用检查器、执行轨迹、🎓 交互课件中心都只在 Web 侧提供。两者共享 `app_core` / `bootstrap` 同一套装配（见 docs/blog/02），行为一致，按场景任选。

## 可选能力

- `python -m pip install -e .[faiss]`：RAG 片段超过阈值时启用 FAISS 向量索引加速（未装则自动回退纯 Python 余弦检索）。
- `MCP_SERVERS`：在 `.env` 中配置外部 MCP 服务器，支持 Streamable HTTP（`https://...`）与 stdio（`stdio:python -m my_server`）两种接入方式，多个用分号分隔。

## 文档导航

- [docs/README.md](docs/README.md)：文档总索引，说明三层目录的组织方式。
- [docs/blog/](docs/blog/)：6 篇教学博文系列，从 Function Calling 极简实现一路讲到安全防御、评测框架与对照实验，适合按顺序阅读。
- [docs/project/](docs/project/)：原理拆解与技术文档——架构总览、Quickstart 十分钟跑通、34 条设计决策清单、RAG/Skill/MCP/多 Agent 专题、API 与配置参考、FAQ。

## 学习路径指引

推荐按「**文档建概念 → 课件动手回放 → 源码逐行对照 → demo 本地验证**」的闭环学习：

1. **读文档**：`docs/blog/` 建立直觉，`docs/project/` 深入机制（每篇相关文档顶部都有 🎓 课件指引）。
2. **动手回放**：启动 Web UI（`python -m ai_agent.web_ui`），打开「🎓 交互课件中心」页签——四门交互课程可逐步播放真实报文、调参模拟器，FC 课程的「安全门」还支持确认/拒绝分支决策。

| 课程 | 讲什么 | 对照文档 | 对照源码 |
|---|---|---|---|
| MCP 交互演示 | initialize 握手 → tools/list → tools/call 全会话 | `docs/project/MCP客户端协议拆解.md` | `ai_agent/mcp_client.py` |
| FC 循环与安全门 | FC 循环 + 风险分类人工确认（含分支） | `docs/blog/01`、`docs/blog/04` | `runtime.py` / `tooling.py` |
| 压缩 × RAG 实验 | 可拖动的压缩触发 + 向量检索模拟器 | `docs/blog/06` | `rag.py` / `app_core.py` |
| Skill 路由 / ReAct 对比 | 同一道题 FC vs ReAct 双栏同步回放 | `docs/blog/03` | `skill.py` / `demos/react.py` |

3. **看源码**：课件「真实代码」页签的折叠区有「课件步骤 → 真实函数」对照表 + 项目源码原文。
4. **跑 demo**：`ai_agent/demos/` 下每个机制都有零依赖可运行脚本（`python fc_safety_demo.py` 等，不依赖 LM Studio），跑一遍加深印象。

## 运行测试

```powershell
.venv/Scripts/python -m pytest ai_agent/tests/
```

全部为离线测试（mock LLM），无需启动 LM Studio。

## License

学习演示项目，可自由用于教学和本地实验。
