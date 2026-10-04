# 多 Agent 协作拆解

> 以本项目（AI Agent 交互式演示，基于 LM Studio 本地大模型）为实例，拆解 `run_multi_agent()` 的 Planner / Executor / Critic 三角色协作机制。
> 协议与模式选择的讨论见《深度理解 Agent 模式选择》§四（那篇讲"多 Agent 与 FC/ReAct 的正交关系"），本篇只讲代码怎么运转。

---

## 一、先搞清楚：多 Agent 到底是什么

在本项目里，**多 Agent 不是框架，而是一个函数**：`run_multi_agent()`（`ai_agent/multi_agent.py:128`）把"规划 → 评审 → 执行 → 再评审 → 汇总"这条流水线用普通 Python 控制流写出来，每个"角色"只是一次带着不同 system prompt 的 `client.chat()` 调用。

| 维度 | 单 Agent FC（AgentRuntime） | 单 Agent ReAct | 多 Agent（本文） |
|------|---------------------------|---------------|-----------------|
| 模型实例 | 1 个 | 1 个 | 逻辑上 3 个角色（物理上同一个模型） |
| 循环结构 | `finish_reason == "tool_calls"` 驱动（runtime.py:90） | 正则解析 Action 驱动 | 两段 `for round_num` 评审循环（multi_agent.py:181, :256） |
| 上下文 | 一条共享 history 不断追加 | 一条共享 history | 每个角色每次调用都是**全新 messages**（:163, :183, :236） |
| 工具调用 | 有（协议化 tool_calls） | 有（文本 Action） | **没有**——只用纯 `client.chat()`（:167, :197, :248, :271, :295, :334） |
| 终止条件 | 模型不再返回 tool_calls | 解析到 Final Answer | Critic 说"✅ 通过"或 `max_rounds` 耗尽 |
| 质量机制 | 无 | 无（靠 Observation 自我修正） | 有（Critic 独立评审 + 带意见重试） |

一句话概括多 Agent 的实现本质：**把单 Agent 的"思考-行动-观察"循环拆给三段独立 messages 扮演，角色间用显式字符串拼接传递上下文，用 emoji 标记做评审结论解析**。

---

## 二、架构全景：多 Agent 在整个系统里的位置

多 Agent 有**两条进入路径**，最终汇聚到同一个函数：

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              用户输入                                     │
│            "用多 Agent 协作写一篇关于 AI Agent 的科普文章"                  │
└───────────────┬──────────────────────────────────┬──────────────────────┘
                │ 路径 A（普通对话模式）              │ 路径 B（Web 模式菜单）
                ▼                                   ▼
┌───────────────────────────────────┐  ┌───────────────────────────────────┐
│ AgentRuntime FC 循环               │  │ web_ui.py:740-741 模式分发          │
│ LLM 按 system prompt 路由指令       │  │ mode == "multi_agent"              │
│ （app_core.py:62）选中工具          │  │   → _run_multi_agent() (:675-689)  │
│ run_multi_agent（schema 在          │  │ 直接调用 run_multi_agent()          │
│ app_core.py:394-405，闭包在         │  │ 不经 FC 循环                        │
│ app_core.py:627-628）              │  │                                    │
└───────────────┬───────────────────┘  └───────────────┬───────────────────┘
                └──────────────────┬───────────────────┘
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  run_multi_agent(client, user_task, max_rounds=2)   multi_agent.py:128   │
│                                                                          │
│  Phase 1  Planner 出计划        :161-178   client.chat(label="Planner 规划")│
│       └→ _parse_subtasks() 解析编号清单    :172-175                        │
│  Phase 2  Critic 审计划循环     :180-226   不通过 → 带意见重规划 → 再审     │
│  Phase 3  逐子任务执行+评审     :228-313                                   │
│       ├→ Executor 执行子任务 i             :248-253                        │
│       ├→ Critic 审子任务 i（最多 max_rounds 轮）:256-303                   │
│       └→ accumulated_context 拼接+截断     :305-313                        │
│  Phase 4  Planner 汇总整合      :315-342   client.chat(label="Planner 汇总")│
│                                                                          │
│  所有角色调用都走 client.chat()（client.py:279-299）—— 纯对话，无 tools     │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
                             ▼
                   LLM（LM Studio 本地模型）
              同一个模型，六~十几次独立调用
```

**关键洞察：多 Agent 的"角色"不是对象，是 messages 列表。** 代码里不存在 `Planner` 类或 `Executor` 实例——每次"调用某个角色"就是现场拼装一个 `[{"role": "system", ...}, {"role": "user", ...}]` 列表发给同一个 LLM。角色人格完全由 `ROLE_PROMPTS` 里的 system prompt 决定（对应设计决策清单 K4）。

---

## 三、核心模块拆解（上）：角色人格与两个解析器

### 3.1 ROLE_PROMPTS：三份 system prompt 就是全部"角色定义"

**位置**：`ai_agent/multi_agent.py:40-81`

```python
ROLE_PROMPTS = {
    "planner": (
        "你是一个严谨的任务规划者（Planner）。"
        "你的职责是把用户请求拆解为 2-4 个有序、可执行的子任务。\n\n"
        "严格按以下格式输出（只输出子任务清单，不要解释）：\n"
        "  1. 第一个子任务的具体描述\n"
        ...
        "- 总数 2-4 个，太少是没拆开，太多是拆过细"
    ),
    "executor": (
        "你是一个高效的执行者（Executor）。"
        ...
        "4. Critic 指出你的产出问题时，要针对性修正，不要从头重写。\n\n"
        "输出格式：\n"
        "  ## 子任务 N 产出\n"
        "  （具体内容）\n"
    ),
    "critic": (
        "你是一个严格的质量评审者（Critic）。"
        ...
        "4. 如果整体合格，明确说「✅ 通过」并简述理由；\n"
        "5. 如果不通过，明确说「❌ 不通过」并列出问题清单。\n\n"
        "输出格式：\n"
        "  ## 评审结论: ✅通过 或 ❌不通过\n"
        ...
    ),
}
```

三份 prompt 的职责切分（multi_agent.py:41-80）：

- **Planner**（:41-54）：被要求输出"数字+点+空格"开头的编号清单——这个格式约定不是随意写的，是为了让 §3.3 的正则解析器能稳定提取；
- **Executor**（:55-66）：被要求"输出要具体、可直接合并到最终成果"，并且"Critic 指出问题时要针对性修正，不要从头重写"——后半句直接对应 Phase 3 的修正循环（:283-301）；
- **Critic**（:67-80）：被要求用「✅ 通过」/「❌ 不通过」作为结论标记——这个 emoji 约定是给 §3.2 的解析器看的，不是给人看的。

**角色人格 = system prompt，协作协议 = 输出格式约定。** 多 Agent 系统的全部"智能"都在这三段文字里，代码只是传话筒。

顺带一提：multi_agent.py:89-96 定义了一个 `RoleCall` dataclass（`from_role` / `to_role` / `content`），它在当前代码里**没有被任何地方使用**——它是教学性的"概念占位"，用类型声明告诉你"角色间的一次消息传递长什么样"，实际的消息传递靠字符串拼进 messages 完成。

### 3.2 _parse_critic_decision()：用 emoji 当结构化输出

**位置**：`ai_agent/multi_agent.py:99-111`

```python
def _parse_critic_decision(text: str) -> bool:
    """解析 Critic 的评审结论：True=通过，False=不通过"""
    # 先看明确的结论标记
    if "✅" in text or "通过" in text.split("\n")[0]:
        # 但要确认不是「不通过」
        first_line = text.split("\n")[0] if text else ""
        if "不通过" in first_line or "❌" in first_line:
            return False
        return True
    if "❌" in text or "不通过" in text:
        return False
    # 没明显标记 → 默认通过（避免卡死循环）
    return True
```

这是把 Critic 的自由文本评审变成布尔值的唯一通道。注意三层防御：

1. 先看 ✅ / 首行"通过"——但要排除首行同时出现"不通过"的情况（Critic 可能写"❌ 不通过"也匹配到"通过"子串）；
2. 再看 ❌ / "不通过"；
3. **什么标记都没有 → 默认通过**（:110-111 的注释说得很直白：避免卡死循环）。这是脆弱但必要的兜底——解析失败时宁可放行也不死循环，对应设计决策清单 K5 的权衡。

它和 ReAct 用正则抠 `Action:` 是同一种脆弱性（见《深度理解 Agent 模式选择》§四）：协议靠 prompt 约定 + 文本解析维持。这层解析有专门的单元测试覆盖（`ai_agent/tests/test_core.py:195-213`，含"首行同时有'不通过'和'通过'"的对抗用例）。

### 3.3 _parse_subtasks()：用正则提取编号清单

**位置**：`ai_agent/multi_agent.py:114-120`

```python
def _parse_subtasks(plan_text: str) -> list[str]:
    """从 Planner 输出中提取子任务清单"""
    import re

    # 匹配「数字. xxx」或「数字、xxx」
    items = re.findall(r"^\s*\d+[.、)]\s*(.+)$", plan_text, re.MULTILINE)
    return [i.strip() for i in items if i.strip()]
```

一行正则 `^\s*\d+[.、)]\s*(.+)$` 兼容三种编号风格（`1.` / `1、` / `1)`），多行模式逐行提取。这就是为什么 Planner 的 system prompt 里要反复强调"每行以数字+点+空格开头"（:49）——**prompt 是给解析器写的**。解析不到编号时的兜底在调用方（:173-175）：整个计划文本当成一个子任务，退化但不崩溃。解析器同样被测试覆盖（test_core.py:215-221）。

---

## 四、核心模块拆解（下）：run_multi_agent() 四阶段流水线

**位置**：`ai_agent/multi_agent.py:128-342`

### 4.1 入口与 trace_callback

```python
def run_multi_agent(
    client,  # LMStudioClient 实例
    user_task: str,
    max_rounds: int = 2,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    trace_callback=None,  # 可选：每行 print 文本会传给这个 callback
) -> str:
    ...
    def _emit(line: str) -> None:
        if trace_callback is not None:
            trace_callback(line)
        else:
            print(line, flush=True)
```

（:128-152）`max_rounds` 默认 2——两个评审循环各自最多重试 2 轮。`_emit` 是统一出口：CLI 场景没有 callback 就直接 print；Web UI 传入 callback 把每行文本推进 Gradio 事件队列（web_ui.py:678-679）。注意这是**单向**信息流（Agent → 外部），不构成反向依赖。

### 4.2 Phase 1：Planner 出计划（:161-178）

```python
planner_msgs = [
    {"role": "system", "content": ROLE_PROMPTS["planner"]},
    {"role": "user", "content": f"请为下面的任务制定执行计划：\n\n{user_task}"},
]
plan_text = client.chat(
    planner_msgs, temperature=temperature, max_tokens=512, label="Planner 规划"
)
...
subtasks = _parse_subtasks(plan_text)
if not subtasks:
    subtasks = [plan_text.strip()]
    _emit("\n[WARN] 未能解析出子任务清单，将整个计划作为一个子任务执行")
```

（:163-175）Planner 的 messages 只有两条——**它是无状态的**，不携带任何对话历史，每次调用都是"系统人格 + 一条任务指令"。`max_tokens=512` 比默认值小，因为计划只需要清单不需要长文。`label="Planner 规划"` 只用于 Web UI 的 API 调用检查器展示（client.py:285），不会发给模型。

### 4.3 Phase 2：Critic 审计划循环（:180-226）

这是第一个评审循环，对应设计决策清单 K5：

```python
for round_num in range(1, max_rounds + 1):
    _emit(f"\n🔄 [第 {round_num} 轮] Critic 评审计划...")
    critic_msgs = [
        {"role": "system", "content": ROLE_PROMPTS["critic"]},
        {
            "role": "user",
            "content": (
                f"请评审 Planner 的执行计划。\n\n"
                f"【原始任务】\n{user_task}\n\n"
                f"【Planner 的计划】\n{plan_text}\n\n"
                f"【提取的子任务清单】\n"
                + "\n".join(f"  {i + 1}. {s}" for i, s in enumerate(subtasks))
                + "\n\n请给出评审结论。"
            ),
        },
    ]
    critic_reply = client.chat(
        critic_msgs, temperature=temperature, max_tokens=512, label="Critic 审计划"
    )

    if _parse_critic_decision(critic_reply):
        _emit("\n✅ Critic 通过了 Planner 的计划！")
        break

    # ❌ 不通过：把批评意见追加进 Planner 的新 messages，带着意见重规划
    planner_msgs = [
        {"role": "system", "content": ROLE_PROMPTS["planner"]},
        {"role": "user", "content": f"请为下面的任务制定执行计划：\n\n{user_task}"},
        {"role": "assistant", "content": plan_text},        # 之前的计划
        {
            "role": "user",
            "content": (
                f"Critic 的评审意见（不通过）：\n\n{critic_reply}\n\n"
                "请根据以上批评意见重新规划，输出完整的新计划。"
            ),
        },
    ]
    plan_text = client.chat(
        planner_msgs, temperature=temperature, max_tokens=512, label="Planner 重规划"
    )
    subtasks = _parse_subtasks(plan_text) or [plan_text.strip()]
else:
    _emit(f"\n[WARN] Critic 连续 {max_rounds} 轮不通过，将按当前计划继续执行")
```

（:181-226）三个要点：

1. **Critic 看到的上下文是显式拼接的**：原始任务 + 计划原文 + 提取后的子任务清单，全部塞进一条 user 消息（:187-194）。Critic 没有参与产出过程，批评起来没有自我辩护的包袱——这正是独立 messages 的价值；
2. **重规划不是简单重试**：新的 planner_msgs 里追加了 `{"role": "assistant", "content": plan_text}`（旧计划）和一条带批评意见的 user 消息（:207-218），Planner 能看到"我之前写了什么、被批了什么"，针对性修正而不是瞎重试；
3. **`for-else` 兜底**（:225-226）：`max_rounds` 轮都不通过时，打印警告但**按当前计划继续执行**——评审机制是质量增强，不是准入门槛，绝不能把流程卡死。

### 4.4 Phase 3：Executor 逐个子任务执行 + 子任务级评审（:228-313）

```python
all_outputs = []
accumulated_context = f"原始任务：{user_task}\n\n执行计划：\n{plan_text}"

for i, subtask in enumerate(subtasks, 1):
    executor_msgs = [
        {"role": "system", "content": ROLE_PROMPTS["executor"]},
        {
            "role": "user",
            "content": (
                f"整体任务回顾：{user_task}\n"
                f"已完成的前序子任务产出：\n{accumulated_context}\n"
                f"当前子任务：{i}. {subtask}\n\n"
                "请完成这个子任务，输出具体成果。"
            ),
        },
    ]
    exec_output = client.chat(
        executor_msgs, ..., label=f"Executor 子任务 {i}/{len(subtasks)}",
    )
```

（:229-253）**Executor 的上下文传递全靠 `accumulated_context` 这个字符串变量**：每个子任务执行前，它包含"原始任务 + 计划 + 前序子任务产出"；执行完后把新产出追加进去（:305-306）。Executor 看不到 Planner 的规划过程、看不到 Critic 的评审记录，只看到拼好的字符串——角色隔离是物理级的（设计决策清单 K4）。

子任务产出后进入**第二个评审循环**（:256-303），结构与 Phase 2 同构：Critic 评审 → `_parse_critic_decision` 判定 → 不通过则把批评意见 + 旧产出塞进新 executor_msgs 让 Executor 修正（:283-300）→ 最多 `max_rounds` 轮 → `for-else` 兜底保留当前产出继续推进（:302-303）。

每个子任务结束后，上下文截断（:305-313）：

```python
all_outputs.append((i, subtask, exec_output))
accumulated_context += f"\n\n【子任务 {i} 完成】{subtask}\n{exec_output}"
# 截断：后序子任务只需看最近 800 字 + 开头 400 字的原始任务/计划摘要
if len(accumulated_context) > 1200:
    accumulated_context = (
        accumulated_context[:400]
        + "\n…（前序产出已截断，保留最近内容）…\n"
        + accumulated_context[-800:]
    )
```

这是设计决策清单 R5（无索引截断回退）在多 Agent 场景的应用：保留开头 400 字（原始任务和计划是身份锚点）+ 最近 800 字（刚完成的产出最可能被后续子任务引用），丢弃中间。注意**截断只影响后续 Executor 看到的 `accumulated_context`**，`all_outputs` 里保存的是完整产出——Phase 4 汇总用的是后者，截断不会丢最终交付物。

### 4.5 Phase 4：Planner 汇总整合（:315-342）

```python
summary_parts = []
for i, st, out in all_outputs:
    summary_parts.append(f"【子任务 {i}：{st}】\n{out}")
final_summary = "\n\n".join(summary_parts)

final_integrate_msgs = [
    {"role": "system", "content": ROLE_PROMPTS["planner"]},
    {
        "role": "user",
        "content": (
            f"以下是针对任务「{user_task}」的 {len(subtasks)} 个子任务执行产出，"
            "请整合为一份连贯的最终交付物给用户。可以调整结构、合并相关内容，"
            "但不要丢弃任何关键成果。\n\n" + final_summary
        ),
    },
]
integrated = client.chat(
    final_integrate_msgs, ..., label="Planner 汇总整合"
)
return integrated
```

（:318-342）汇总也用 Planner 人格——规划者负责首尾（拆任务和收成果），这是对称设计。这次调用拿到的 `final_summary` 是 `all_outputs` 里的**完整产出**，不受 Phase 3 截断影响。整个函数的返回值就是这一次 LLM 调用的输出。

---

## 五、两个调用入口：FC 工具路由 vs Web 模式菜单

### 入口 A：作为普通 FC 工具被 Agent 循环调用

`run_multi_agent` 在工具体系里的注册链路：

1. **路由指令**：`BASE_SYSTEM_PROMPT` 里写明"用户要求【多 Agent/多智能体/Planner/Executor/Critic/角色协作/分工协作】时，调用 run_multi_agent"（app_core.py:62）；
2. **Schema**：`TOOL_SCHEMAS` 里的声明（app_core.py:394-405），参数只有一个 `task`；
3. **闭包**：`build_tool_map()` 里绑定 client（app_core.py:627-628）：

```python
def run_multi_agent_tool(task: str) -> str:
    return run_multi_agent(client, task)
```

于是整条链路是：用户输入 → `AgentRuntime` 的 FC 循环（runtime.py:90）→ 外层 LLM 返回 `tool_calls=[run_multi_agent(...)]` → `ToolExecutor` 执行闭包 → 进入四阶段流水线 → 返回整合文本作为 tool 结果 → 外层 LLM 基于它生成最终回复。**多 Agent 对外层 Agent 来说只是一个"特别贵的工具"**——一次调用内部可能产生 8-10 次 LLM 请求。

### 入口 B：Web UI 模式菜单直连

Web UI 把多 Agent 作为独立模式，**不经 FC 循环**直接调用（web_ui.py:675-689）：

```python
def _run_multi_agent(self, state: SessionState, user_text: str, event_queue: queue.Queue) -> None:
    params = self._run_params(state)

    def on_trace(line: str) -> None:
        event_queue.put(("trace", (line, "agent")))

    reply = run_multi_agent(
        self.client,
        user_text,
        max_rounds=2,
        temperature=params["temperature"],
        max_tokens=params["max_tokens"],
        trace_callback=on_trace,
    )
    event_queue.put(("done", reply))
```

模式分发在 web_ui.py:740-741（`elif mode == "multi_agent"`），菜单项在 web_ui.py:1255-1263。`trace_callback` 把 `_emit` 的每行文本转成 Gradio 事件，实时渲染角色间消息轨迹（轨迹里用红色加粗的 "agent" kind 展示）。两个入口共享同一个 `run_multi_agent()` 函数和同一个 `LMStudioClient`，CLI 侧则只走入口 A。

**与单 Agent FC/ReAct 的正交关系**（详见《深度理解 Agent 模式选择》§四）：FC/ReAct 在协议层（一个模型怎么和工具交换信息），多 Agent 在架构层（一个模型不够时怎么组织多个角色）。本项目的 Executor **不调工具**（:248 是纯 `client.chat`）——"查北京天气"这种子任务是靠模型自身知识"回答"的。工业系统可以让 Executor 内部接一个 `AgentRuntime` 调工具，两个维度自由组合，这正是扩展方向。

---

## 六、完整流程走一遍

假设 Web UI 里选"多 Agent 协作"模式，输入：**"写一篇关于 RAG 的科普文章，500 字左右"**

```
Step 1 — 模式分发（无 LLM 调用）
  web_ui.py:740 → mode == "multi_agent" → _run_multi_agent()
  → run_multi_agent(client, "写一篇关于 RAG 的科普文章，500 字左右", max_rounds=2)

Step 2 — Phase 1：Planner 出计划（第 1 次 LLM 调用）
  planner_msgs = [system: ROLE_PROMPTS["planner"], user: "请为下面的任务制定执行计划：..."]
  → client.chat(label="Planner 规划")（:167）
  → 返回 "1. 阐述 RAG 的核心概念与动机\n2. 讲解检索与生成的技术流程\n
        3. 说明 RAG 的优势与局限\n4. 整合润色为 500 字科普文章"
  → _parse_subtasks() 提取出 4 个子任务（:172）

Step 3 — Phase 2：Critic 审计划（第 2 次 LLM 调用）
  critic_msgs = [system: critic 人格, user: 原始任务 + 计划 + 子任务清单]
  → client.chat(label="Critic 审计划")（:197）
  → 假设返回 "## 评审结论: ❌不通过\n缺少面向读者的通俗化要求..."
  → _parse_critic_decision() → False（:202）
  → Planner 带批评意见重规划（第 3 次调用，:219）→ 新计划
  → 第 2 轮 Critic 复审（第 4 次调用）→ "✅通过" → break

Step 4 — Phase 3：逐子任务执行（每个子任务 1~2 次调用）
  子任务 1：Executor 执行（:248，第 5 次）→ Critic 审（:271，第 6 次）→ ✅
  子任务 2：Executor 执行（第 7 次）→ Critic 审（第 8 次）→ ❌
            → Executor 修正（:295，第 9 次）→ Critic 复审（第 10 次）→ ✅
  子任务 3、4 同理各 2 次调用
  每次子任务完成：accumulated_context 追加产出；若超 1200 字符则截断（:307-313）

Step 5 — Phase 4：汇总（第 13 次 LLM 调用）
  final_integrate_msgs = [system: planner 人格, user: 4 份完整子任务产出 + 整合指令]
  → client.chat(label="Planner 汇总整合")（:334）→ 整合后的 500 字文章
  → return integrated（:342）

Step 6 — 返回与展示
  Web UI: event_queue.put(("done", reply)) → 聊天气泡显示最终文章
  trace 面板：13 次调用的角色轨迹逐行可见（👤 Planner / ⚙️ Executor / 🔍 Critic）
```

成本账单：4 个子任务 + 一次计划重审 + 一次子任务修正 ≈ **13 次 LLM 调用**，是同任务单 Agent FC（约 3 次）的 4 倍。这就是"用成倍 token 和延迟换质量闭环"的具象化。

---

## 七、边界与扩展

### 当前实现不做什么（保持教学最小版）

| 特性 | 状态 | 原因 |
|------|------|------|
| Executor 调工具 | ❌ | 角色调用全是纯 `client.chat`（:167, :197, :248, :271, :295, :334），没有 tools 参数——多 Agent 解决分工与质检，不解决工具接入 |
| 角色间双向通信 | ❌ | Executor 无法向 Planner 反馈"这个子任务我做不了"；计划是静态的，审通过后就锁死 |
| 不同角色用不同模型 | ❌ | 三个角色共享同一个 `client`，区分只靠 system prompt |
| 结构化评审结论 | ❌ | `_parse_critic_decision` 靠 emoji/关键词解析，模型忘加标记时默认通过（:110-111） |
| 并行执行子任务 | ❌ | 子任务严格串行（有依赖关系靠 accumulated_context 传递），无并发 |
| 结果持久化 | ❌ | 最终文本随函数返回，不写数据库（Web 侧由 send() 统一 append 到会话历史，web_ui.py:798-799） |

### 如果想扩展

1. **让 Executor 接入工具**：把 :248 的 `client.chat(executor_msgs)` 换成 `AgentRuntime.run()`（runtime.py:20），Executor 就能用 FC 调 `web_search` / `search_knowledge`——这是"多 Agent × FC"正交组合的直接落地，但要处理子任务级 tool 权限与轨迹嵌套展示；
2. **结构化评审协议**：让 Critic 输出 JSON（`{"decision": "pass/reject", "issues": [...]}`），复用 P4（结构化输出失败重试）的围栏清洗思路替换 `_parse_critic_decision` 的 emoji 解析；
3. **动态重规划**：Phase 3 中 Executor 连续修正失败时，跳出子任务循环回到 Phase 1 让 Planner 带着"哪个子任务做不动"的信息重规划，把线性流水线改成图；
4. **角色级模型配置**：给 `ROLE_PROMPTS` 的每个角色配独立 client（比如 Critic 用更大的模型），`run_multi_agent` 签名从单 client 改成 `dict[str, client]`；
5. **真正的消息传递**：把闲置的 `RoleCall` dataclass（:89-96）用起来——角色间消息先入队再消费，为并行化和可视化打基础。

---

## 八、总结

多 Agent 协作的实现本质只有三句话：

1. **角色 = system prompt + 独立 messages**：不存在角色对象，每次"角色调用"就是现场拼一个 `[system, user]` 列表发给同一个 LLM（K4），隔离性来自"每次重新拼装"而非任何运行时机制；
2. **协作 = 字符串拼接的上下文传递**：角色之间没有共享状态，`accumulated_context` 一个字符串变量加上 1200 字符的硬截断（R5）就是全部"共享内存"；
3. **质量闭环 = emoji 标记 + for-else 循环**：Critic 的评审结论靠"✅/❌"文本解析成布尔值，不通过就把批评意见塞进下一轮 messages 重试，`max_rounds` 耗尽则兜底放行（K5）——评审是增强器，不是卡点。

整个 `multi_agent.py` 只有 342 行，核心逻辑全是普通控制流（for 循环 + 字符串拼接 + 正则解析）。它证明了一件事：**多 Agent 系统没有魔法——把"谁在什么时候收到什么消息"这条信息流用代码显式写出来，就是多 Agent 编排的全部**。
