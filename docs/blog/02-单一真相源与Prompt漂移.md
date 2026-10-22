# Agent 项目的单一真相源：当 CLI 和 Web UI 的 Prompt 开始漂移时

> 作者：Agent Dev 实践笔记 | 发布日期：2026-09-21 | 标签：Agent, 架构设计, Prompt Engineering, 软件工程

> 🎓 **相关交互课件**：Web UI 的「🎓 交互课件中心」页签（`http://127.0.0.1:7860/courses/`）——《FC 循环与安全门》可见 system prompt 与工具 schema 如何进入每次请求，《Skill 路由 / ReAct 对比》可见提示词注入对模型行为的直接影响。本文聚焦的"单一真相源"工程约束，对应源码 `ai_agent/app_core.py` 的 `build_system_prompt()` / `TOOL_SCHEMAS`。

---

## 引子

让我问你一个问题：如果你的 Agent 项目需要同时支持 **CLI 命令行** 和 **Gradio Web 界面**，你会怎么组织代码？

大部分人第一反应是：**按 interface 拆分**。`cli/` 放 CLI 相关代码，`web/` 放 Web UI 相关代码，各自维护各自的 System Prompt、工具注册、对话历史管理。

听起来很合理对不对？我当时也是这么想的。直到有一天，我加了一个新工具叫 `plan_task`——一个让 Agent 先做规划再执行的"元工具"。

我在 CLI 的 System Prompt 里加了路由指令："如果用户说**规划**、**计划**、**拆解任务**、**制定方案**，优先调用 `plan_task`"。然后打开 Web UI 测试——Web UI 里 Agent 完全不调 `plan_task`，每次都直接用基础工具瞎跑。

查了半小时，原因很蠢：**Web UI 的 System Prompt 里忘写这条路由指令了**。两个 interface 各维护一份 Prompt，改了一份忘了改另一份。

这就是我想讲的"**Prompt 漂移**"——一个不起眼的架构决定，会在项目演进过程中变成真正的 bug。

---

## 一、症状一览：Prompt 漂移长什么样

Prompt 漂移不是一个"罕见病"。只要你项目有两个以上入口，它就迟早会来。常见的 4 种症状：

### 症状 1：路由指令不同步

```python
# CLI 的 System Prompt（main.py 里手写）
"如果用户说规划、计划、拆解任务 → 用 plan_task；"
"如果用户说从知识库查询 → 用 search_knowledge；"
"如果用户说提取 JSON 结构 → 用 extract_structured；"

# Web UI 的 System Prompt（web_ui.py 里手写）
"如果用户说规划、计划、拆解任务 → 用 plan_task；"
# ↑ search_knowledge 和 extract_structured 的路由指令漏写了！
```

结果：CLI 里 Agent 能正确路由到 21 种工具，Web UI 里 Agent 最多只能正确路由 18 种。你永远在一边能用一边不能用之间调试。

### 症状 2：工具数量文案打架

```python
# README banner:
print(f"本项目共 21 个工具")

# CLI 启动时的 banner:
print(f"✅ 已加载 20 个工具")    # ← 数错了

# Web UI 的页面标题:
gr.Markdown(f"## AI Agent · 支持 18 种工具")  # ← 更离谱
```

工具实际有 21 个，但三处文案三个不同数字。为什么？因为每个地方都是手数的。

### 症状 3：工具列表重复定义

```python
# main.py 里：
CLI_TOOLS = ["calculator", "get_weather", "file_read", ...]  # 手写了一遍

# web_ui.py 里：
WEB_TOOLS = ["calculator", "get_weather", "file_read", ...]  # 又手写了一遍

# 某天加了 web_search：
CLI_TOOLS.append("web_search")   # ← 忘了改 WEB_TOOLS
```

Tool Schema 本身是给 LLM 看的，但 tool_map 是给你的代码看的——两个地方的工具名必须严格一致，少一个或多一个都会出问题。

### 症状 4：System Prompt 里的安全指引不同步

```python
# CLI Prompt 里有：
"执行破坏性操作前必须先调 human_confirm 让用户确认"

# Web UI Prompt 里可能没有：
# （Web UI 版本可能被简化了，因为你觉得"Web UI 默认禁用 run_terminal"）
```

结果：CLI 上 Agent 会在删文件前礼貌地问"确定吗？"，Web UI 上可能直接干了。

---

## 二、根本原因：为什么会漂移

漂移的根源很简单——**两份代码 = 双倍修改点**。你加一个新工具，至少要改 4 个地方：

```
加一个新工具的变更清单：
  ① 写工具实现 → tools/my_new_tool.py
  ② 写 Tool Schema → app_core.py TOOL_SCHEMAS
  ③ CLI Prompt 里加路由指令 → main.py SYSTEM_PROMPT
  ④ Web UI Prompt 里加路由指令 → web_ui.py SYSTEM_PROMPT
  ⑤ CLI 的 tool_map 里注册 → main.py
  ⑥ Web UI 的 tool_map 里注册 → web_ui.py
  ⑦ 两个 interface 的 banner / 标题文案里的工具数
  
一共 7 处。你改了 6 处忘了 1 处 → Bug。
```

而且"改了忘了"不是你的错——人的注意力有限，改到第 5 处时你已经脑疲劳了。

---

## 三、解法：让 `app_core.py` 成为唯一真相源

我们的解法是：**Prompt、Schema、Tool Map 三者统一收敛到一个模块**。CLI 和 Web UI 不再各自维护，而是统一 import。

### 3.1 项目架构对比

```
❌ 之前（interface 驱动拆分）：
┌────────────────────────────┐  ┌────────────────────────────┐
│  main.py (CLI)             │  │  web_ui.py (Web)           │
│  ├── SYSTEM_PROMPT         │  │  ├── SYSTEM_PROMPT         │  ← 两份
│  ├── TOOL_SCHEMAS          │  │  ├── TOOL_SCHEMAS          │  ← 两份
│  ├── build_tool_map()      │  │  ├── build_tool_map()      │  ← 两份
│  └── run_loop()            │  │  └── gradio_interface()    │
└────────────────────────────┘  └────────────────────────────┘

✅ 之后（domain 驱动集中）：
┌─────────────────────────────────────────────────────────┐
│  app_core.py（唯一真相源）                                │
│  ├── SYSTEM_PROMPT    ← 一份，所有人用                    │
│  ├── TOOL_SCHEMAS     ← 一份，所有人用                    │
│  ├── TOOL_COUNT = len(TOOL_SCHEMAS)  ← 自动计数           │
│  ├── build_tool_map()  ← 一个工厂函数，注入不同依赖        │
│  └── build_tool_executor()                                │
└─────────────┬───────────────────────────┬───────────────┘
              │ import                     │ import
              ▼                           ▼
┌──────────────────────┐       ┌──────────────────────┐
│  main.py (CLI)       │       │  web_ui.py (Web)     │
│  ├── from app_core   │       │  ├── from app_core   │
│  └── run_loop()      │       │  └── gradio_interface│
└──────────────────────┘       └──────────────────────┘
```

### 3.2 代码长什么样

```python
# ============================================================
# app_core.py — 唯一真相源
# ============================================================

# 一份 System Prompt，带动态 Skill 简述
BASE_SYSTEM_PROMPT = """你是一个有工具使用能力的 AI Agent。

## 工具路由规则
- 如果用户说"计算"、"算一下" → 用 calculator
- 如果用户说"天气" → 用 get_weather
- 如果用户说"搜索" → 用 web_search
- 如果用户说"读文件" / "打开" → 用 file_read
- 如果用户说"规划"、"计划"、"拆解" → 用 plan_task
- 如果用户说"查知识库"、"RAG" → 用 search_knowledge
- 如果用户要结构化提取 → 用 extract_structured
... （所有 21 个工具的路由指令都在这里）

## 安全规则
执行破坏性操作前必须先调 human_confirm...
"""

# 一份 Tool Schema
TOOL_SCHEMAS = [
    {"type": "function", "function": {"name": "calculator", ...}},
    {"type": "function", "function": {"name": "get_weather", ...}},
    # ... 共 21 个
]

# 自动计数，banner / 标题都引用它
TOOL_COUNT = len(TOOL_SCHEMAS)   # 21，自动同步


def build_tool_map(client, get_rag, workspace_root,
                   get_history=None, set_history=None, mcp_manager=None):
    """一个工厂函数，CLI / Web UI / Eval 都调它，各自注入不同依赖"""
    tool_map = {}

    # 纯函数工具（无依赖）：直接引用
    tool_map["calculator"] = calculator
    tool_map["get_weather"] = get_weather

    # 闭包工具（需要 workspace_root / client）：工厂内绑定
    def workspace_file_read(path, max_chars=3000):
        return file_read(path, max_chars=max_chars, workspace_root=workspace_root)
    workspace_file_read.__name__ = "file_read"
    tool_map["file_read"] = workspace_file_read

    def demo_stream_chat(topic):
        for token in client.chat_stream([...]):
            ...
    tool_map["demo_stream_chat"] = demo_stream_chat

    # summarize_history 条件注册：只有传入了 history 访问器才有
    if get_history is not None:
        tool_map["summarize_history"] = make_summarize_history(client, get_history, set_history)

    # MCP 工具可选追加
    if mcp_manager is not None:
        for name, (_, t) in mcp_manager._tool_names.items():
            tool_map[name] = make_mcp_callable(mcp_manager, name)

    return tool_map
```

### 3.3 CLI 和 Web UI 变成了"薄壳"

```python
# main.py（CLI）
from ai_agent.app_core import (
    SYSTEM_PROMPT, TOOL_SCHEMAS, TOOL_COUNT,
    build_tool_map, build_tool_executor,
)

class AgentDemoApp:
    SYSTEM_PROMPT = SYSTEM_PROMPT       # 直接引用
    TOOLS = TOOL_SCHEMAS                # 直接引用

    def __init__(self, ...):
        self.cfg = Config.from_env()
        self.client = LMStudioClient(self.cfg.base_url, ...)
        # 注入 CLI 的依赖：workspace_root、history 访问器
        self.tool_map = build_tool_map(
            client=self.client,
            get_rag=lambda: self.rag,
            workspace_root=self.workspace_root,
            get_history=lambda: self.history,
            set_history=lambda h: setattr(self, "history", h),
        )
        # CLI 的安全策略：允许 execute 但要确认
        self.tool_executor = build_tool_executor(
            self.tool_map, allow_execute=True, confirm_execute=True, ...
        )

    def run(self):
        print(f"✅ 已加载 {TOOL_COUNT} 个工具")   # 自动同步
        ...


# web_ui.py（Web）
from ai_agent.app_core import (
    SYSTEM_PROMPT, TOOL_SCHEMAS, TOOL_COUNT,
    build_tool_map, build_tool_executor,
)

class AgentApp:
    def __init__(self, ...):
        ...
        # 注入 Web UI 的依赖：同样的接口但不同实现
        self.tool_map = build_tool_map(
            client=self.client,
            get_rag=lambda: self.rag,
            workspace_root=self.project_root,
            # Web UI 不需要 history 访问器（用 Gradio State 组件管理）
        )
        # Web UI 的安全策略：禁止 execute
        self.tool_executor = build_tool_executor(
            self.tool_map, allow_execute=False, ...
        )
```

### 3.4 对比：改一个新工具现在要改几处？

```
加一个新工具的变更清单（重构后）：
  ① 写工具实现 → tools/my_new_tool.py
  ② 写 Tool Schema → app_core.py TOOL_SCHEMAS 列表加一项
  ③ 在 build_tool_map 里注册 → app_core.py build_tool_map() 加一行
  ④ System Prompt 里加路由指令 → app_core.py BASE_SYSTEM_PROMPT 加一行
  
一共 4 处（而且都在同一个文件 app_core.py 里）。
TOOL_COUNT 自动更新，CLI 和 Web UI 的 banner / 标题自动同步。
```

**从 7 处跨文件变成 4 处单文件。** 你不可能"改了一处忘了另一处"——因为另一处就在你眼前。

---

## 四、权衡：单一真相源的代价是什么？

集中是好事，但不是免费的。我们踩了三个坑：

### 坑 1：`app_core.py` 变成"上帝模块"

现在 `app_core.py` 大约 650 行，里面混着 System Prompt（字符串）、Tool Schema（JSON）、工具注册逻辑（Python）、ReAct 工具集清洗、Skill 过滤……看起来有点"杂"。

我们的判断是：**650 行对于一个 Agent 教学项目来说完全在可接受范围内**。如果工具数量超过 50，可以考虑按 domain 拆分 `schemas/` 子目录，但现在 21 个工具没必要。

### 坑 2：闭包工具调试时看不到 `__closure__`

工厂函数里定义的闭包（如 `demo_stream_chat`）调试时要看 `client` 绑定了什么，得去 `fn.__globals__["client"]` 里挖。有点麻烦，但比"漂移"好处理。

### 坑 3：同一个工具在不同入口下行为可能不同

`summarize_history` 工具在 CLI 里注册（CLI 有 history），在 Web UI 里也注册（Web UI 也有 history），但在评测环境里不注册（评测没有 history）。这是**设计好的行为**——评测用的 Agent 是最小化配置，不需要长对话压缩。

---

## 五、如果你的项目也有多入口：快速自检 3 条

看完这篇，我希望你能打开自己的 Agent 项目，按这 3 条检查：

1. **System Prompt 在几个文件里出现？** 如果 > 1，有漂移风险。
2. **Tool Schema 在几个文件里出现？** 如果 > 1，有漂移风险。
3. **工具数量文案在几个地方手数过？** 如果 > 0，应该改成自动计数。

改完这三条，你至少消除了 80% 的"为什么 CLI 能用 Web UI 不行"的调试时间。

---

## 延伸阅读

- [本项目设计决策清单 - A1 单一真相源](../project/设计决策清单.md)
- [本项目设计决策清单 - A2 工厂函数 Tool Map](../project/设计决策清单.md)
- [Clean Architecture 原论（Robert C. Martin）— "核心逻辑收敛"思想的源头](https://blog.cleancoder.com/uncle-bob/2012/08/13/the-clean-architecture.html)

---

## 本系列其他文章

- ✅ 第 1 篇：[LLM Agent 极简实现：200 行代码跑通 Function Calling 全流程](./01-Function-Calling-极简实现.md)
- ✅ 第 2 篇：你正在读的这篇
- 📝 [第 3 篇：把 Skill 做成 Markdown 文件 — Agent 框架的"低代码"设计决策](./03-Skill做成Markdown.md)
- 📝 [第 4 篇：让 Agent 安全执行代码 — 从 AST 白名单到 Human-in-the-loop 的三层防御](./04-Agent安全三层防御.md)
- 📝 [第 5 篇：Agent 不是 Demo — 如何用 20 条 Gold Case 搭建可自动运行的评测框架](./05-Gold-Case评测框架.md)
