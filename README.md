# 本地 AI Agent 交互式演示（学习项目）

基于 **LM Studio 本地大模型** 的交互式 AI Agent 学习演示平台。一个统一的自然语言入口，驱动 **18 种 LLM/Agent 核心能力** 的自主路由与展示，帮助理解 Agent 的底层工作方式。

> 深度理念文章：[通用 Agent 时代价值迁移](docs/通用Agent时代价值迁移.md)

---

## ✨ 功能特性

| 能力类别 | 能力 | 触发示例 |
|---------|------|---------|
| **LLM 基础** | 普通对话 · 流式输出 · 思维链推理 · 原生推理模式 · 结构化输出 | `用流式方式讲个笑话` / `思维链解答：一个人花8元买鸡...` / `从这段文字提取结构化信息` |
| **Agent 核心** | Function Calling 工具路由 · 反思与自我修正 · Planner-Executor 任务拆解 · Human-in-the-loop | `反思方式解答：三个连续奇数之和为 45` / `规划并写一篇 AI Agent 科普文章` |
| **知识增强** | RAG 本地知识库问答 · Few-shot Prompt Engineering | `查一下资料：什么是 ReAct 模式？` / `用 Few-shot 做情感分类` |
| **多模态/执行** | 图像识别 · 代码执行 · SQL 查询 · 终端命令 · 文件读写 · 天气查询 · 网络搜索 | `写代码计算 1 到 100 的和` / `用 sql_query 查每个城市有多少用户` / `搜索 2026 年最流行的 Agent 框架` |
| **上下文管理** | 长对话自动摘要压缩（超 5000 字符） | `对话太长了，帮我压缩一下历史` |

---

## 📐 架构

```
AI-Agent/
├── main.py                      # AgentDemoApp — 初始化 + 工具注册 + 交互循环（主入口）
├── ai_agent/
│   ├── __init__.py
│   ├── __main__.py              # 支持 python -m ai_agent 启动
│   ├── client.py                # LMStudioClient — OpenAI 兼容 API 封装
│   │                             #   chat / chat_stream / chat_with_tools（Function Calling 循环）
│   │                             #   chat_with_reasoning / chat_with_native_reasoning
│   │                             #   chat_with_reflexion（反思迭代）
│   │                             #   analyze_image（多模态）
│   │                             #   plan_and_execute（Planner-Executor）
│   │                             #   structured_extract（JSON 结构化）
│   │                             #   summarize_messages（长对话压缩）
│   ├── rag.py                   # RAGKnowledgeBase — PDF/MD/TXT → embedding → 余弦检索
│   ├── tools/                   # 10 个模块级工具函数 + TOOL schema
│   │   ├── __init__.py          # 统一 re-export + 9 个 TOOL schema + FEW_SHOT_TEMPLATES
│   │   ├── demo.py              # get_weather, calculator
│   │   ├── system.py            # run_code, run_terminal, human_confirm + 安全拦截
│   │   ├── fs.py                # file_read, file_write
│   │   ├── search.py            # web_search（博查 AI 优先，DuckDuckGo 降级）
│   │   └── sql.py               # sql_query（SQLite demo）
│   └── tests/
│       └── test_tools.py        # 单元测试（calculator AST / 路径安全 / SQL 只读 / 危险命令拦截等）
├── docs/
│   └── 通用Agent时代价值迁移.md  # 深度理念文章
├── requirements.txt
├── .env.example
└── .gitignore
```

**核心机制**：`AgentDemoApp` 将各能力封装为 Function Calling 的 tool schema，启动后进入自然语言交互循环，模型根据用户输入自主选择调用哪个工具，实现"自然语言驱动能力路由"。

```
用户输入自然语言
       │
       ▼
  chat_with_tools 循环
       │
       ├── finish_reason=stop  → 直接返回文本
       ├── finish_reason=tool_calls
       │     ├── 解析 tool_call JSON
       │     ├── tool_map[name](**args) 执行
       │     ├── 结果喂回 history
       │     └── 继续循环（最多 5 轮）
```

---

## 🚀 快速开始

### 环境要求

- Python 3.12+
- [LM Studio](https://lmstudio.ai/) 已启动本地服务（默认 `http://localhost:1234/v1`）
- 推荐模型：qwen/qwen3-vl-8b（支持工具调用、图像理解、reasoning_effort）

### 安装运行

```powershell
# 1. 克隆后进入项目目录
cd AI-Agent

# 2. 创建并激活虚拟环境
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. 安装依赖
pip install -r requirements.txt

# 4. （可选）配置 API Key —— 两种方式任选
#    方式 A：创建 .env 文件（自动加载）
copy .env.example .env
#    然后编辑 .env，填入 BOCHA_API_KEY=你的Key
#    方式 B：系统环境变量
set BOCHA_API_KEY=你的Key

# 5. 启动 LM Studio 并加载模型（Server 端口 1234）

# 6. 运行（两种启动方式任选）
python main.py
# 或
python -m ai_agent
```

进入交互模式后直接输入自然语言即可，`exit` / `quit` / `退出` 结束程序。

### 运行单元测试

```powershell
pip install pytest
python -m pytest ai_agent/tests/ -v
```

### 运行评测集（需要 LM Studio 运行中）

```powershell
# 列出所有评测用例
python -m ai_agent.eval --list

# 运行全部评测
python -m ai_agent.eval

# 只跑某个能力维度
python -m ai_agent.eval --tag react
```

评测集输出每条 case 的 pass/fail 原因，最后汇总能力维度通过率 + 可视化条形图。

---

## 📝 注意事项

| 项 | 说明 |
|----|------|
| RAG 索引 | 首次运行需向量化全部 PDF 片段（耗时较长），之后自动走 `_rag_cache.pkl` 缓存 |
| 上下文窗口 | 本地模型默认 4096 token，任务规划等长输出场景已做截断与摘要 |
| 代码执行 | `run_code` 在子进程执行并带 15 秒超时，**仅本地演示**；生产应用使用严格沙箱 |
| 原生推理 | `reasoning_effort` 仅 qwen3 等推理系列模型支持 |
| 网络搜索 | 优先博查 AI（国内稳定），未配 Key 降级 DuckDuckGo（国内可能超时） |
| 安全边界 | 每个涉及文件/终端/SQL 的工具都做了最小权限限制——详见各工具源码注释 |

---

## 🔒 安全设计速览

| 工具 | 安全措施 |
|------|---------|
| `calculator` | AST 白名单，只允许 `+ - * / ** %` 和括号，彻底阻断 eval/exec/__import__ |
| `file_read` | `..` 路径穿越拦截 + 10MB 大小限制 + 自动多编码尝试 |
| `file_write` | `..` 路径穿越拦截 |
| `sql_query` | 只允许 `SELECT / PRAGMA / EXPLAIN / WITH`，DDL/DML 阻断 |
| `run_terminal` | 危险命令子串黑名单（rm -rf / Remove-Item / Set-Content / Stop-Computer 等） |
| `run_code` | 子进程隔离 + 15 秒超时；**⚠️ 非安全沙箱，仅限本地演示** |
| `human_confirm` | 敏感操作强制暂停等用户输入确认 |

---

## 🛤️ 项目路线图

| 阶段 | 内容 |
|------|------|
| ✅ 已完成 | 18 能力交互演示 · RAG 知识库 · 反思/规划/工具路由等 Agent 核心模式 · 安全边界 · 单元测试（安全边界） |
| 📌 下一步 | ReAct 模式（thought→action→observation 可见轨迹）· 评测集框架（gold cases + 自测模式）· README/文档对齐 |
| 🚀 后续 | Web UI（Gradio/Streamlit 可视化）· 对话持久化 · 多 Agent 协作演示 · 生产级沙箱评估 |

---

## 📄 许可

学习演示项目，自由使用。
