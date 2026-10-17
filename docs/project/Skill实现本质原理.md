# Skill 实现本质原理

> 以本项目（AI Agent 交互式演示，基于 LM Studio 本地大模型）为实例，拆解 Skill 支持的核心设计与实现机制。

---

## 一、先搞清楚：Skill 到底是什么

在 Agent 系统里，**Tool 是原子操作，Skill 是复合工作流**。

| 维度 | Tool（本项目已有） | Skill（本项目新增） |
|------|-------------------|-------------------|
| 定义方式 | Python 函数 + OpenAI function schema | 声明式 Markdown 文件 |
| 触发方式 | LLM Function Calling 自行选择 | 关键词预筛 → 直接激活 |
| 执行模式 | 单次调用 → 返回 | 多轮 Function Calling 循环（max_rounds 可控） |
| Prompt | 所有工具共享同一个 SYSTEM_PROMPT | Skill 激活时用自己的 instructions 替换 system prompt |
| 工具集 | 全部 21 个工具一次性发给 LLM | 只让 LLM 看到 Skill 声明的 required_tools |
| 扩展成本 | 改 Python、改 `build_tool_map()`、改 `TOOL_SCHEMAS` | 加一个 `.md` 文件 = 加一个能力 |

一句话概括 Skill 的实现本质：**把"场景化 System Prompt + 工具子集过滤 + 多轮执行循环"三件事打包成一个声明式单元**。

---

## 二、架构全景：Skill 在整个系统里的位置

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              用户输入                                    │
│              "帮我规划并写一篇AI科普文章"                                  │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  AgentRuntime.run() 的 Skill 路由层（runtime.py:48-68）                  │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ 取最后一条用户消息 → skill_registry.match(user_input)            │   │
│  │                                                                  │   │
│  │ 命中？ ──Yes──→ 注入 instructions + 过滤工具，走统一工具循环       │   │
│  │  No            → 继续原有 Function Calling 流程（零 Skill 时完全不变） │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└────────────────────────────┬────────────────────────────────────────────┘
                             │ 命中
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  AgentRuntime.run()（runtime.py:59-68）— Skill 在统一工具循环内执行       │
│                                                                         │
│  ① 替换 system prompt                                                   │
│     messages[0].content = skill.instructions （Skill.md 的 Markdown 正文）│
│                                                                         │
│  ② 过滤工具                                                              │
│     filtered_map = {name: fn for name, fn in tool_map.items()            │
│                     if name in skill.required_tools}                     │
│                                                                         │
│  ③ 复用 Function Calling 循环（最多跑 skill.max_rounds 轮）               │
│     for _ in range(skill.max_rounds):                                    │
│         response = llm.chat_with_tools(filtered_schemas)                 │
│         → 执行工具 → 喂回结果 → 下一轮                                    │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
                             ▼
                     LLM 多轮调用原子工具
              （search_knowledge → web_search → file_write → ...）
                             │
                             ▼
                     Skill 返回最终结果
```

**关键洞察：Skill 没有发明新机制，它只是重新组合了现有三件事**
1. System Prompt 可以被替换（你可以在任何 LLM 调用里换一个场景化 prompt）
2. Tool Calling 可以只发一部分工具 schema（你可以控制 LLM 看到哪些工具）
3. Function Calling 本身就是多轮循环（finish_reason == "tool_calls" 就继续循环）

Skill 做的只是：**把"换什么 Prompt、过滤哪些工具、循环多少轮"这三件事从硬编码变量变成了声明式文件**。

---

## 三、Skill 定义格式：为什么用 Markdown + YAML Frontmatter

### 格式示例（`skills/example_plan.md`）

```markdown
---
name: plan_and_write
description: 把复杂任务拆解为有序子任务并逐个完成，产出结构化成果
trigger_keywords:
  - 规划
  - 拆解
  - 分步骤
  - 写大纲
  - plan
required_tools:
  - search_knowledge
  - web_search
  - file_write
  - file_read
  - extract_structured
max_rounds: 5
entry_mode: auto
---

## 你是一个专业任务规划与执行者

### 工作流
1. **明确范围**：如果用户没说清楚具体要求，先用一句话反问确认
2. **多路调研**：search_knowledge + web_search 交叉验证
3. **分步骤执行**：每个子任务独立完成，累积上下文
4. **最终交付**：用 file_write 保存完整成果

### 注意事项
工具失败时灵活降级；不要让用户等太久；产出要具体。
```

### 为什么这样设计

**Frontmatter（YAML 部分）** 是机器读的，定义 Skill 的"元数据"。核心字段：
| 字段 | 含义 | 作用 |
|------|------|------|
| `name` | 唯一标识 | 日志打印、调试时定位 Skill |
| `trigger_keywords` | 触发关键词列表 | `SkillRegistry.match()` 用来预筛候选 Skill |
| `required_tools` | 工具名列表 | Skill 执行时只让 LLM 看到这些工具 |
| `max_rounds` | 最大执行轮次 | 防止循环无限跑爆上下文窗口 |
| `enabled` | 是否启用 | 可以临时禁用而不删文件 |

**正文（Markdown 部分）** 是 LLM 读的。它就是 Skill 被激活后注入的 system prompt——和你现有项目里 `SYSTEM_PROMPT` 的角色完全相同，只是场景更聚焦。

### 不引入 PyYAML 依赖

项目的 Frontmatter 解析用的是纯正则（见 `ai_agent/skill.py:100-141` 的 `_parse_frontmatter()`），支持 `key: value`、`key: [a, b]` 和：
```yaml
key:
  - item1
  - item2
```
这种嵌套列表语法。解析失败只会打印警告、跳过该 Skill，不会阻塞启动。

---

## 四、核心模块拆解

### 4.1 SkillRegistry：发现、加载、匹配 Skill 的"大脑"

**位置**：`ai_agent/skill.py:149-333`

它做三件事：**扫描目录 → 解析元数据 → 关键词匹配**。

#### 扫描与加载

```python
class SkillRegistry:
    def __init__(self, skill_dir: str, available_tools: list[str] | None = None):
        self.skills = {}          # name → SkillDef
        self._errors = []

        if not os.path.isdir(skill_dir):
            return                 # 目录不存在 → 零个 Skill，完全安全

        self._load_all(skill_dir, set(available_tools))

    def _load_all(self, skill_dir, available_tools):
        for md_path in sorted(glob.glob(os.path.join(skill_dir, "*.md"))):
            try:
                skill = self._parse_md(md_path, available_tools)
                if skill is None:
                    continue
                self.skills[skill.name] = skill
            except Exception as e:
                self._errors.append(...)   # 单个文件加载失败只记错误，不阻塞启动
```

启动时一次性加载，之后只读不写。`available_tools` 用来校验 Skill 声明的 `required_tools` 是否存在（Skill 声明了不存在的工具时跳过，不会崩溃）。

#### 关键词匹配

这是 Skill 路由的核心算法（`ai_agent/skill.py:267-316`）：

```python
def match(self, user_input: str) -> Optional[SkillDef]:
    best_score = 0.0
    best_skill = None
    user_lower = user_input.lower()
    input_len = max(len(user_input), 1)

    for skill in self.skills.values():
        matched_keywords = [kw.lower() for kw in skill.trigger_keywords
                            if kw and kw.lower() in user_lower]
        if not matched_keywords:
            continue
        # 命中字符数按"覆盖位置"计算：重叠的关键词不重复计数
        covered_positions: set[int] = set()
        for keyword in matched_keywords:
            start = 0
            while True:
                found = user_lower.find(keyword, start)
                if found < 0:
                    break
                covered_positions.update(range(found, found + len(keyword)))
                start = found + 1
        hit_chars = len(covered_positions)
        score = hit_chars / input_len
        # 额外奖励：多个关键词同时命中
        hit_count = len(set(matched_keywords))
        if hit_count >= 2:
            score *= 1.3
        if score > best_score and score > 0.3:
            best_score = score
            best_skill = skill
    return best_skill
```

**算法本质**：命中关键词覆盖的字符数（重叠部分只算一次）占用户输入总长度的比例。长关键词、多关键词命中 → 高分；score > 0.3 才有效（防止单字/短关键词误触）。多个关键词同时命中额外 1.3x 奖励。

**实际效果示例（当前 `plan_and_write` Skill 的 trigger_keywords）**：
```
"帮我规划并写一篇文章" (10字)
  命中: "规划" + "规划并写"（覆盖 4 字，去重后）, hit_count=2 → 1.3x 奖励
  score = (4/10) * 1.3 = 0.52 > 0.3  → ✅ 匹配

"规划一下AI Agent的学习路线" (17字)
  命中: "规划" + "规划一下"（覆盖 4 字，去重后）, hit_count=2 → 1.3x 奖励
  score = (4/17) * 1.3 ≈ 0.31 > 0.3  → ✅ 匹配

"拆解一下这个复杂问题" (10字)
  命中: "拆解" + "拆解一下"（覆盖 4 字，去重后）, hit_count=2 → 1.3x 奖励
  score = (4/10) * 1.3 = 0.52 > 0.3  → ✅ 匹配

"北京天气怎么样" (7字)
  命中: 无 → ❌ 不匹配

防误触验证:
  "这本书规划得很好" — 只有"规划"(2字)命中, score=2/8=0.25 < 0.3 → ✅ 不误触
```

**为什么不用 LLM meta-routing？** 关键词预筛省 token、响应快、确定性强。后续可以升级为"让 LLM 从候选 Skill 里选"，但那是锦上添花。

---

### 4.2 AgentRuntime：Skill 的执行循环

**位置**：`ai_agent/runtime.py:48-68`

Skill 的"引擎"现在收编在统一的工具循环 `AgentRuntime.run()` 里（CLI 和 Web 共用），命中 Skill 时只做三件事：

#### ① 替换 system prompt

```python
# runtime.py: _apply_skill_prompt() 把 Skill 的 instructions 替换进 system prompt
history = self._apply_skill_prompt(history, selected_skill.instructions)
```

这是 Skill 最核心的设计决策：**Skill 的场景化指令就是它的 system prompt**。LLM 的行为直接由 system prompt 控制——换了 prompt 就等于换了"人格"和"工作流程"。

#### ② 过滤工具子集

```python
required = set(selected_skill.required_tools)
active_map = {name: fn for name, fn in tool_map.items() if not required or name in required}
active_tools = [
    schema for schema in get_tool_schemas(mcp_manager) if schema["function"]["name"] in active_map
]
```

只让 LLM 看到 Skill 声明的工具。好处：
- **减少 token 浪费**：原来 21 个工具 schema 一次全发，现在只发 5 个
- **防止乱选**：LLM 不会在 Skill 执行途中去调 `demo_stream_chat` 这种演示工具
- **安全边界**：Skill 声明的工具就是它的权限范围

#### ③ 复用统一 Function Calling 循环

```python
max_iterations = selected_skill.max_rounds  # 命中 Skill 后，循环上限换成它的 max_rounds

for iteration in range(1, max_iterations + 1):
    response = self.client.create_chat_completion(  # 或 stream_chat_completion（流式分支）
        label=f"Agent 循环 · 第 {iteration}/{max_iterations} 轮",
        model=self.client.model, messages=history,
        tools=active_tools,  # 用过滤后的
        temperature=temperature, max_tokens=max_tokens,
    )
    ...
    # finish_reason == "tool_calls" → 经 ToolExecutor 执行工具 → 喂回结果 → 下一轮
```

**关键洞察：Function Calling 本身就是多轮循环**——LLM 选工具 → 我们执行 → 喂回结果 → LLM 再选 → ... → LLM 给出最终回答。Skill 做的只是**给这个循环加上限（max_rounds）和过滤后的工具集**。工具执行统一走 `ToolExecutor`（含写/执行/远端调用的风险策略），Skill 内外的工具调用共享同一套安全边界。

---

### 4.3 AgentRuntime.run() 的 Skill 路由层

**位置**：`ai_agent/runtime.py:48-68`

在统一工具循环 `AgentRuntime.run()` 开头有一段前置判断（`chat_with_tools()` 只是它的薄封装）：

```python
selected_skill = None
if skill_registry is not None:
    last_user = next(
        (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
        "",
    )
    if last_user:
        selected_skill = skill_registry.match(last_user)

if selected_skill is not None:
    history = self._apply_skill_prompt(history, selected_skill.instructions)
    required = set(selected_skill.required_tools)
    active_map = {name: fn for name, fn in tool_map.items() if not required or name in required}
    active_tools = [...]               # 只保留 active_map 里的 schema（含 MCP 远端工具）
    max_iterations = selected_skill.max_rounds
```

**为什么不把路由逻辑做成独立函数？** 因为它必须在循环开始前——一旦进入 Function Calling 循环，中间跳出去再回来很麻烦。前置判断最干净：命中就就地换 prompt、换工具集、换轮次上限，然后走同一条循环。

**向后兼容**：`skill_registry=None` 时整个 if 块跳过，一行都不执行。和改动前 100% 一致。

---

### 4.4 build_system_prompt()：动态合成

**位置**：`ai_agent/app_core.py:80-96`

```python
def build_system_prompt(skill_registry=None) -> str:
    prompt = BASE_SYSTEM_PROMPT     # 原始静态版（= 旧的 SYSTEM_PROMPT）
    if skill_registry is not None and skill_registry.count > 0:
        skill_section = "\n\n## 可用的专业能力（Skills）\n\n"
        skill_section += "下面这些是预定义的复合工作流（多步编排 + 专属 Prompt），"
        skill_section += "当用户请求匹配时会自动激活。\n\n"
        for s in skill_registry.list():
            kws = ", ".join(s.trigger_keywords[:3])
            skill_section += f"- 【{s.name}】{s.description}（关键词: {kws}）\n"
        prompt += skill_section
    return prompt
```

**为什么只注入简述，不注入完整 instructions？** Skill 的完整 instructions 可能有几百字。如果一次性注入 5 个 Skill 的完整 prompt，system prompt 就膨胀了。**详细指令在 Skill 激活时才注入**（AgentRuntime 命中 Skill 时替换 system prompt），平时只放简述让 LLM 知道"有什么能力可用"。

---

## 五、完整流程走一遍

假设用户输入：**"帮我规划并写一篇AI科普文章"**

```
Step 1 — 启动时
  main.py: AgentDemoApp.run()
    → _register_tools()    // 注册 21 个原子工具
    → _register_skills()   // SkillRegistry 扫描 skills/*.md
                            // 加载到 plan_and_write Skill
                            // 校验它声明的 required_tools 都存在

Step 2 — 用户输入进 chat_with_tools()
  client.py: chat_with_tools(messages, tools, tool_map, skill_registry=...)
    → 委托给 AgentRuntime.run()，开头做前置路由判断
      → 取最后一条用户消息: "帮我规划并写一篇AI科普文章"
      → skill_registry.match()
          → "规划" + "规划并写" 命中 trigger_keywords（覆盖 4 字，hit_count=2 → 1.3x 奖励）
          → score = (4/14) * 1.3 ≈ 0.37 > 0.3
          → 返回 plan_and_write Skill ✅

Step 3 — AgentRuntime 命中 Skill，就地切换执行上下文
  runtime.py: AgentRuntime.run() 命中分支
    → ① 替换 system prompt
        messages[0].content = plan_and_write 的 Markdown 正文
                                （"## 你是一个专业任务规划与执行者\n\n### 工作流\n..."）
    → ② 过滤工具
        filtered_map = {
          search_knowledge: fn, web_search: fn,
          file_write: fn, file_read: fn, extract_structured: fn
        }  // 其他 16 个工具隐藏
    → ③ 多轮循环（最多 5 轮）

Step 4 — Skill 内部的多轮 Function Calling
  Round 1: LLM.chat → finish_reason="tool_calls"
           → 调 search_knowledge("AI Agent 是什么")
           → 把结果喂回 history
  Round 2: LLM.chat → finish_reason="tool_calls"
           → 调 web_search("2026 AI Agent 最新进展")
           → 把结果喂回 history
  Round 3: LLM.chat → finish_reason="tool_calls"
           → 调 file_write(path="ai_agent_article.md", content="...")
           → 把结果喂回 history
  Round 4: LLM.chat → finish_reason="stop"
           → 返回最终文章摘要 ✅

Step 5 — 返回给用户
  main.py: reply 打印出来
  "已生成文件 ai_agent_article.md，核心要点：..."
```

---

## 六、Skill 与其他能力的关系

```
┌──────────────────────────────────────────────────┐
│              本项目的能力层次模型                    │
├──────────────────────────────────────────────────┤
│  Skill（本项目新增）                                │
│  ├─ 声明式定义（Markdown 文件）                      │
│  ├─ 场景化 system prompt                            │
│  ├─ 工具子集过滤                                     │
│  ├─ 多轮 Function Calling 循环                       │
│  └─ 可以复用 MCP 远端工具                            │
├──────────────────────────────────────────────────┤
│  MCP（本项目已有）                                  │
│  ├─ 远端工具注册（MCP HTTP Client）                  │
│  ├─ 命名冲突自动处理（加 mcp_ 前缀）                  │
│  └─ Skill 的 required_tools 可以声明 MCP 工具名       │
├──────────────────────────────────────────────────┤
│  原子 Tool（本项目已有 21 个）                        │
│  ├─ Python 函数 + OpenAI function schema             │
│  ├─ Function Calling 单次调用                        │
│  └─ Skill 和 MCP 都能复用它们                        │
├──────────────────────────────────────────────────┤
│  LLM（LM Studio 本地模型）                           │
│  └─ 统一提供 chat / function calling / reasoning     │
└──────────────────────────────────────────────────┘
```

**核心事实：Skill 和 MCP 是正交的**。Skill 可以在 `required_tools` 里声明 `mcp_search_api`（MCP 远端工具名），SkillRegistry 会用完整的 tool_map 校验可用性（因为 `build_tool_map` 已经在 MCP 初始化后构建完成）。

---

## 七、扩展性与边界

### Skill 不做什么（保持最小版本）

| 特性 | 状态 | 原因 |
|------|------|------|
| Skill 嵌套（Skill 内调 Skill） | ❌ 暂不 | 避免递归死循环；且 Skill 自己就是多步编排，再嵌套容易混乱 |
| Skill 持久化 | ❌ 暂不 | Skill 实例内存态，对话结束即销毁；后续可存内部状态到数据库续上 |
| LLM meta-routing | ❌ 暂不 | 关键词预筛足够；升级为 LLM 二次确认需多一次 API 调用 |
| Web UI 可视化 | ❌ 暂不 | 当前 trace 能看到工具调用轨迹，但不区分"这是 Skill 内部调用" |
| Skill 热加载 | ❌ 暂不 | 运行时加 md 文件需要重启；后续可加文件监听 |

### 如果想扩展

1. **加 LLM meta-routing**：在 `chat_with_tools` 的路由层，把候选 Skill 的描述发给 LLM，让它从"候选 Skill + 普通工具"里选。准确率更高，但多一次 API 调用
2. **加 Skill 嵌套**：在 `AgentRuntime` 的循环里，检测到 LLM 想调另一个 Skill（比如名字以 `skill_` 开头的工具），递归激活
3. **加 Skill trace**：在 Web UI 的 trace 里加 Skill 专属颜色标记（比如紫色），区分普通工具调用
4. **加单元测试**：为 `SkillRegistry.match()` 写参数化测试，覆盖各种边界输入

### 进阶练习：把"❌ 暂不"变成"动手实现"

上面表格里标 ❌ 的四项，都是刻意留白的作业。每个练习给出了源码锚点和可检查的验收标准，按任意顺序做均可——它们彼此独立。

#### 练习 1：Skill 嵌套——让 Skill 可以调用 Skill

> 原文方向："在 `AgentRuntime` 的循环里，检测到 LLM 想调另一个 Skill（比如名字以 `skill_` 开头的工具），递归激活。"

- **背景**：`SkillDef` 里有一个已解析但从未被消费的字段——`entry_mode: tool_call`（`ai_agent/skill.py:56`，校验在 `:217` 和 `:226-227`）。它是为嵌套预留的占位。而 Skill 路由目前只在循环开始前执行一次（`ai_agent/runtime.py:48-68`），循环内没有任何"再激活一个 Skill"的通道。
- **任务**：把 `entry_mode: tool_call` 的 Skill 注册成名为 `skill_<name>` 的普通工具（schema + 闭包）并入 tool_map；LLM 在 Skill A 的多轮循环中调用 `skill_b` 时，闭包内部递归走一遍 `AgentRuntime.run()` 的 Skill 命中逻辑（换 prompt、过滤工具、限轮次）。必须加递归深度上限（建议 2 层），防止 A 调 B、B 调 A 的死循环。
- **提示**：防递归直接参照 `run_multi_agent_tool` 闭包的做法（`app_core.py:627-635`——把 `run_multi_agent` 自身从 Executor 工具集里 `pop` 掉）：嵌套执行时从子循环的 `active_map` 里剔除所有 `skill_*` 工具，或传递深度计数。子 Skill 的工具过滤逻辑在 `runtime.py:63-67`，原样复用；`required_tools` 的存在性校验可参考 `skill.py:238-242`。
- **验收标准**：仿照 `ai_agent/tests/test_skill.py` 用 `TemporaryDirectory` 造两个 Skill md（A 声明 `entry_mode: tool_call`），用 `test_executor_tools.py:26-40` 的 `_FakeMessage` / `_tool_call_response` 模式 mock client，断言：① 外层循环中 LLM 返回 `skill_b` 的 tool_call 时，内层 Skill 的 instructions 被应用且工具集被二次过滤；② 深度超过上限时闭包返回错误字符串而不是无限递归；③ `pytest ai_agent/tests/` 全部通过。

#### 练习 2：Skill 执行状态持久化到数据库

> 原文方向："Skill 实例内存态，对话结束即销毁；后续可存内部状态到数据库续上。"

- **背景**：会话和消息已经持久化在 SQLite——`ChatStore`（`ai_agent/persistence.py:48`）维护 sessions / messages 两张表（建表在 `:72-97`），写入统一走 `_lock` 保护（`:175-188`）。但 Skill 的每次执行（命中了谁、跑了几轮、成功还是中途耗尽轮次）没有任何记录，重启即失忆。
- **任务**：给 `ChatStore` 加一张 `skill_runs` 表（字段建议：skill_name / session_id / started_at / rounds_used / status / result_excerpt），Skill 命中时插入一条 `running` 记录，循环结束更新为 `done` 或 `exhausted`；提供查询接口，让重启后的 CLI/Web 能回答"上次那个 plan_and_write 跑到第几轮、产出是什么"。
- **提示**：最天然的埋点是 `runtime.py:74-81` 的 `run_started` 事件——它的 payload 已经携带 `skill` 字段；循环结束位置（`runtime.py` 的 for 循环退出点）更新状态。建表 SQL 照抄 `persistence.py:72-97` 的 `CREATE TABLE IF NOT EXISTS` 风格；所有写操作复用 `self._lock` + `commit` 的既有模式，不要新开连接。
- **验收标准**：用 `tempfile` 指向临时 db 文件实例化 `ChatStore`，完整走一遍"插入 running → 更新 done → 关闭 → 重新打开 → 读出记录"的生命周期断言；参照 `test_core.py` 的 `TestConfig` 风格写 3-4 个用例（写入、状态翻转、跨连接读取、未知 session 容错）；`pytest ai_agent/tests/` 全绿。

#### 练习 3：LLM meta-routing——关键词预筛 Top-3 → LLM 选择

> 原文方向："在 `chat_with_tools` 的路由层，把候选 Skill 的描述发给 LLM，让它从"候选 Skill + 普通工具"里选。准确率更高，但多一次 API 调用。"（`ai_agent/skill.py:276` 的注释也写着这个升级方向）

- **背景**：`SkillRegistry.match()`（`skill.py:267-316`）是纯关键词打分，只返回最高分的一个候选，且 `score > 0.3` 的硬阈值会漏掉"语义相关但字面不含关键词"的请求（比如"帮我把这个任务拆开分步骤做"命中，"我不知道从哪开始下手"漏掉）。
- **任务**：把 `match()` 拆成两段：第一阶段保留现有算法但返回得分 > 0 的 Top-3 候选列表；新增 `meta_match(user_input, client)`，把候选的 name + description 发给 LLM，要求只输出 JSON `{"skill": "<name>"}` 或 `{"skill": null}`，选中即激活，选 null 或解析失败回退为第一阶段最高分（保持现有行为兜底）。做成可选开关，默认关闭。
- **提示**：LLM 输出 JSON 的鲁棒解析，直接复用多 Agent 里 `_parse_critic_decision` 的三级兜底思路（`ai_agent/multi_agent.py:104-150`：先 `json.loads` 整段 → 失败则提取 `{` 到 `}` 片段再试 → 仍失败回退默认值）。收窄人格的 system prompt 写法参考 `rag.py:219-244` 的 `answer()`。配置开关照 `config.py:69-81` 加字段、`:129-147` 的 `from_env` 读环境变量的既有模式。
- **验收标准**：用 FakeClient（`test_executor_tools.py` 模式）断言：① 候选为 0 时不发起任何 LLM 调用；② LLM 返回合法 JSON 时激活对应 Skill；③ LLM 返回乱码/空时静默回退到关键词最高分；④ 开关关闭时行为与现状逐字节一致。`pytest ai_agent/tests/ -k skill` 通过。

#### 练习 4：Skill 热加载——改完 md 不用重启

> 原文方向："运行时加 md 文件需要重启；后续可加文件监听。"

- **背景**：`_load_all()` 只在 `SkillRegistry.__init__` 里跑一次（`skill.py:167` 调用，实现在 `:172-184`），之后 `self.skills` 只读。教学演示中"改 Skill 文件 → 重启 → 再试"的循环很打断节奏。
- **任务**：给 `SkillRegistry` 加 `reload()` 方法：重新扫描目录，按文件 mtime 判断哪些 md 新增/被改/被删，增量更新 `self.skills` 和 `self._errors`；再选一个触发点——CLI 加一条 `/reload_skills` 命令，或每轮对话前做一次轻量 mtime 检查（成本只是几次 `os.stat`）。
- **提示**：mtime 对比照搬 RAG 缓存指纹的用法（`rag.py:246-257` 的 `st_mtime_ns`）；**不要引 watchdog 依赖**——教学项目用轮询足够，这与 RAG 篇"依赖少一项就少装一个包"（设计决策 R3）是同一个取舍。注意 `reload()` 要先清空 `_errors` 再重建，否则历史错误会越攒越多；同名 Skill 被修改后应整体替换 `SkillDef`，不要做字段级合并。
- **验收标准**：`test_skill.py` 风格：tmpdir 写入一个 md → 构建 registry → 断言 `count == 1` → 改写 `trigger_keywords` 并 `os.utime` 刷新 mtime → `reload()` → 断言 `match()` 行为随之变化；再删除文件 → `reload()` → 断言 `count == 0` 且无异常。

---

## 八、向后兼容设计要点

所有改动都是**纯 additive** 的。删掉 `skills/` 目录后：

```python
# 1. SkillRegistry 返回 None
skill_registry = SkillRegistry('/nonexistent', [])
registry.count == 0  # True

# 2. build_system_prompt(None) == BASE_SYSTEM_PROMPT
assert build_system_prompt(None) == BASE_SYSTEM_PROMPT  # True

# 3. chat_with_tools(skill_registry=None) 跳过 Skill 路由
if skill_registry is not None:  # None → 跳过
    ...

# 4. 45 个现有单元测试全部通过
# pytest ai_agent/tests/ → 45 passed
```

这保证了 Skill 支持不会破坏任何现有功能——它就是在入口多加了一层"可选过滤器"。

---

## 九、总结

Skill 的实现本质只有三句话：

1. **声明式文件即 Prompt**：Skill 的 `.md` 正文就是场景化 system prompt，Frontmatter 定义"怎么路由、用什么工具、跑几轮"
2. **换 Prompt + 过滤工具 = 换能力**：LLM 的行为由 system prompt 和可见工具集决定，换这两个就换了一个"Agent 人格"
3. **Function Calling 本身就是多轮循环**：Skill 没有发明新执行机制，只是给现有循环加了"过滤后的工具集"和"上限"

理解了这三点，再看 Skill 代码会发现它非常短——`skill.py` 约 350 行（含注释），Skill 命中分支在 `runtime.py` 里只有约 20 行。**Skill 不是一个新框架，而是把三件已知的事情打包成了声明式单元**。
