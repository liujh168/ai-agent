# 把 Skill 做成 Markdown 文件：Agent 框架的"低代码"设计决策

> 作者：Agent Dev 实践笔记 | 发布日期：2026-09-21 | 标签：Agent, Skill, Markdown, 低代码, Routing

> 🎓 **配套交互课件**：Web UI 的「🎓 交互课件中心」页签 → 《Skill 路由 / ReAct 对比》（`http://127.0.0.1:7860/courses/#/react`）——同一道题的 FC / ReAct 双栏同步回放，「真实代码」页签可对照 `skill.py` 的 `SkillRegistry.match()` 源码。

---

## 引子

你有没有想过：如果你的 Agent 可以"根据用户输入自动切换工作模式"，它会是什么样？

比如：
- 用户说"帮我规划并写一篇关于 AI Agent 的科普文章" → Agent 自动进入"规划-调研-分步骤写作-交付"模式
- 用户说"计算一下 2^20 是多少" → Agent 只调 calculator 工具，算完给答案
- 用户说"我想了解一下" → Agent 进入"RAG 检索 + 知识回答"模式

上面这三种场景，工具不一样、Prompt 不一样、迭代次数也不一样。怎么实现？

### 三种常见做法

**做法 A：全部靠一个巨大的 System Prompt + LLM 自己判断**

```python
SYSTEM_PROMPT = """你是一个万能助手。
如果用户说规划 → 用 plan_task；
如果用户说计算 → 用 calculator；
如果用户说查知识 → 用 search_knowledge；
如果用户说..."""
```

问题：Prompt 越来越长，路由指令越多 LLM 越容易漏看；所有工具始终一次性暴露给 LLM（21 个工具让它选一个，它会犹豫很久）。

**做法 B：硬编码条件分支**

```python
if "规划" in user_input:
    prompt = PLAN_PROMPT
    tools = PLAN_TOOLS
elif "计算" in user_input:
    prompt = CALC_PROMPT
    tools = ["calculator"]
# ...
```

问题：每加一个"场景"都要改 Python 代码、加分支、重启。非开发者（产品经理、行业专家）根本无法贡献新场景。

**做法 C（我们选的）：Skill = Markdown 文件**

让 Skill 变成一个**声明式、可独立维护、非开发者也能写**的单元。新增一个 Skill = 在 `skills/` 目录下加一个 `.md` 文件，重启 Agent 自动加载。

这篇文章就来讲 Skill 的设计——为什么选 Markdown Frontmatter、关键词路由怎么实现、Skill 激活时到底发生了什么。

---

## 一、先建立认知：Tool vs Skill

| 维度 | Tool（工具） | Skill（技能） |
|------|-------------|--------------|
| 定义方式 | Python 函数 + OpenAI Function Schema | Markdown 文件 + YAML Frontmatter |
| 粒度 | 原子操作（查天气、算数学、读文件） | 复合工作流（规划调研→分步骤执行→交付） |
| 触发方式 | LLM 在工具列表里自由选择 | 关键词匹配 → 自动激活 |
| Prompt | 全部共享同一个 SYSTEM_PROMPT | 激活时**完全替换** system prompt |
| 工具可见性 | 一次性暴露全部 21 个 | 只暴露 Skill 声明的 `required_tools` |
| 迭代上限 | 全局 `max_iterations=5` | Skill 可以自定义 `max_rounds` |

一句话概括：**Tool 是手，Skill 是一套编排好的"手脚配合动作"**。

---

## 二、Skill 文件长什么样

先看一个真实例子——`skills/example_plan.md`：

```markdown
---
name: plan_and_write
description: 把复杂任务拆解为有序子任务并逐个完成，产出结构化成果
trigger_keywords:
  - 规划
  - 规划一下
  - 规划并写
  - 规划任务
  - 规划方案
  - 拆解
  - 拆解一下
  - 拆解复杂
  - 分步骤
  - 写大纲
  - plan
  - outline
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
1. **明确范围**：如果用户没有说清楚具体要求（比如文章主题、字数、读者画像），先用一句话反问确认
2. **多路调研**：
   - 优先查本地知识库（search_knowledge）获取基础概念和已有资料
   - 补充互联网搜索（web_search）获取最新信息和不同观点
   - 交叉对比，标注信息来源
3. **分步骤执行**：
   - 每个子任务独立完成，不要一口气输出全部内容
   - 子任务之间累积上下文，避免重复劳动
   - 用 extract_structured 提取关键数据点，用 file_write 保存阶段性成果
4. **最终交付**：
   - 用 file_write 保存完整成果到本地文件
   - 返回文件路径 + 核心要点摘要

### 注意事项
- 工具调用失败时要灵活降级：search_knowledge 返回"不可用"→ 换 web_search
- 每个子任务的产出要具体可执行，不要写空泛的套话
- 不要让用户等太久，到 max_rounds 时交付阶段性成果也比超时好
```

就这么简单。Frontmatter 里写元数据（名字、触发词、需要哪些工具、迭代上限），Markdown 正文就是 Skill 激活时**完全替换**掉原来 System Prompt 的内容。

---

## 三、为什么选 Markdown Frontmatter

不是 YAML，不是 JSON Schema，不是 TOML。原因有三个：

### 3.1 零外部依赖

我们用一个正则表达式解析 Frontmatter，**不引入 PyYAML**：

```python
# skill.py — 整个 Skill 定义文件的解析入口
_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)

def _parse_md(file_path: str) -> SkillDef:
    with open(file_path, encoding="utf-8") as f:
        content = f.read()
    fm_match = _FM_RE.match(content)
    if not fm_match:
        raise ValueError(f"缺少 Frontmatter 的 --- 分隔: {file_path}")
    fm_text = fm_match.group(1)
    instructions = fm_match.group(2).strip()  # ← Markdown 正文直接当 system prompt
    meta = _parse_frontmatter(fm_text)
    return SkillDef(
        name=meta.get("name", ""),
        trigger_keywords=tuple(meta.get("trigger_keywords", [])),
        required_tools=tuple(meta.get("required_tools", [])),
        max_rounds=int(meta.get("max_rounds", 5)),
        instructions=instructions,
        source_path=file_path,
    )
```

PyYAML 体积约 2MB，引入它只为了解析几个 Skill 文件，不划算。

### 3.2 非开发者可写

产品经理、行业专家、领域专家可能不会写 Python，但**会写 Markdown**。他们可以直接在 VSCode / Obsidian / Notion 里写 Skill 定义，保存为 `.md` 文件放进 `skills/` 目录，Agent 下次启动自动加载。

### 3.3 Markdown 正文就是 Prompt

Frontmatter 下方的 Markdown 正文，我们直接当 System Prompt 用。你可以用 `##` 分章节、用 `- ` 列清单、用代码块给 LLM 提供 few-shot 示例——这些 Markdown 语法 LLM 天然能理解（它就是用 Markdown 训练的）。

---

## 四、关键词路由怎么实现

Skill 加载后，用户输入来了，我们需要判断"该激活哪个 Skill"。我们没有用 LLM meta-routing（让 LLM 从候选里选）——那样每次多一次 API 调用。我们用的是**纯字符串匹配 + 打分算法**，微秒级完成。

### 4.1 核心算法

```python
# skill.py match() — 简化版
def match(self, user_input: str) -> SkillDef | None:
    best_score = 0.0
    best_skill = None
    user_lower = user_input.lower()
    input_len = max(len(user_input), 1)

    for skill in self.skills.values():
        # 1. 子串匹配（不区分大小写）
        matched_keywords = [kw for kw in skill.trigger_keywords
                            if kw and kw.lower() in user_lower]
        if not matched_keywords:
            continue

        # 2. 计算"关键词覆盖字符数"
        covered_positions = set()
        for keyword in matched_keywords:
            # 找到所有出现位置（去重重叠）
            start = 0
            while True:
                found = user_lower.find(keyword, start)
                if found < 0:
                    break
                covered_positions.update(range(found, found + len(keyword)))
                start = found + 1

        hit_chars = len(covered_positions)
        score = hit_chars / input_len       # 分数 = 命中字符 / 总字符

        # 3. 多关键词奖励（≥2 个同时命中 × 1.3）
        hit_count = len(set(matched_keywords))
        if hit_count >= 2:
            score *= 1.3

        # 4. 阈值：必须 > 0.3 才算有效命中
        if score > best_score and score > 0.3:
            best_score = score
            best_skill = skill

    return best_skill
```

### 4.2 实际打分示例

假设用户输入："帮我规划并写一篇关于 AI Agent 的文章大纲"（共 28 字符）

| Skill | 触发词 | 命中 | 覆盖字符 | 基础分 | 多词奖励 | 最终分 |
|-------|--------|------|---------|--------|---------|--------|
| plan_and_write | 规划、规划并写、写大纲 | ✅ | 13 字符 | 13/28 = 0.464 | × 1.3 → 0.603 | ✅ 激活 |
| search_rag | 查知识库、RAG | ❌ | 0 | - | - | - |

- 单个短关键词误触的典型场景：用户输入 "这个代码规划一下"（6 字符）。命中"规划"（2 字符）。基础分 = 2/6 = 0.333。但只命中 1 个词，没有 ×1.3 奖励。最终分 0.333 > 0.3，刚好过阈值。这是一个**边界 case**，偶尔会被激活，但因为 Skill 本身的 instructions 里有"先确认范围"，即使误激活也不会出大问题。
- 阈值 0.3 + 多关键词奖励的组合，就是为了让"规划"这种单独短关键词**刚好卡在边界**，而"规划并写"+"写大纲"组合**明显超过边界**。

### 4.3 为什么不用 LLM meta-routing

| 方案 | 优点 | 缺点 |
|------|------|------|
| 关键词匹配（我们选的） | 零 API 调用、微秒级、可解释 | 语义差（"用 planner 帮我写"vs"用规划帮我写"覆盖不同） |
| LLM meta-routing | 语义精准 | 每次多一次 API 调用（~500ms + 额外 token） |
| 混合方案（预筛 Top-3 → LLM 选） | 平衡 | 稍复杂 |

注释里已标注"后面可以升级为混合方案"——先关键词预筛拿 Top-3 候选，再让 LLM 从 Top-3 里选。但在当前规模（1 个 Skill，默认场景）下，关键词匹配已经完全够用。

---

## 五、Skill 激活时到底发生了什么

`AgentRuntime.run()` 里 Skill 激活是**破坏性替换**——完全隔离：

```python
# runtime.py L56-L67 — 激活的完整效果链
if selected_skill is not None:
    # 1. 替换 system prompt（原来的路由指令全部消失）
    history = self._apply_skill_prompt(history, selected_skill.instructions)
    # 2. 收窄工具集（只保留 Skill 声明的）
    required = set(selected_skill.required_tools)
    active_map = {name: fn for name, fn in tool_map.items()
                  if not required or name in required}
    # 3. 覆盖迭代上限
    max_iterations = selected_skill.max_rounds
```

### 对比：激活 vs 不激活

| 维度 | 默认状态（不激活 Skill） | plan_and_write Skill 激活后 |
|------|------------------------|--------------------------|
| System Prompt | 通用路由 Prompt（~1500 字） | "你是一个专业任务规划与执行者"（Skill 正文） |
| 可见工具数 | 21 | 5（search_knowledge, web_search, file_read, file_write, extract_structured） |
| 迭代上限 | 5 | 5（相同，但 Skill 里可以改成 10） |
| 历史对话保留 | ✅ | ✅（只替换第一个 system message） |

### 为什么是"替换"不是"追加"

追加 Prompt 会让 LLM 收到两份指令，容易冲突。比如原来的通用 Prompt 说"执行破坏性操作前调 human_confirm"，Skill 追加的 Prompt 没提安全。LLM 面对两份指令时行为不可预测。

替换的好处是 Skill 有**完全的身份控制权**——它决定自己是谁、该怎么做、能用什么工具。代价是通用安全指引（human_confirm 等）不会自动继承到 Skill 里，必须在 Skill instructions 里自己写一遍。

---

## 六、写一个 Skill 需要多久

**答：10 分钟。** 流程是：

1. **想清楚场景**：这个 Skill 解决什么问题？（"帮我查资料→提炼关键数据点→写入文件"）
2. **写 Frontmatter**：5-10 个触发词、3-5 个必需工具、迭代上限（默认 5）
3. **写正文**：Markdown 格式的工作流描述 + 注意事项（给 LLM 当 system prompt）
4. **保存为 `.md` 文件**：放 `skills/` 目录下，重启 Agent 自动加载

就这么简单。不需要改 Python、不需要改 Prompt 字符串、不需要重启服务进程（CLI 重启一次、Web UI 自动重新扫描 skills 目录）。

---

## 七、演进方向

当前 Skill 机制还有几个已知局限，可以逐步增强：

| 局限 | 现状 | 演进方向 |
|------|------|---------|
| YAML 子集有限 | 不支持嵌套字典、多行字符串 | 如果未来需要，升级为 PyYAML（但目前 Skill 元数据用不到） |
| 路由是纯关键词 | 语义差 | 关键词预筛 Top-3 → LLM meta-routing |
| 替换丢失安全指引 | Skill Prompt 里没写 human_confirm 就没了 | Skill instructions 支持 `{{base_safety_rules}}` 占位符自动注入 |
| Skill 之间无法串联 | 激活了 Skill A 就不能再激活 Skill B | 支持 Skill 链式触发（Skill A 执行完后让 LLM 决定要不要激活 Skill B） |

---

## 延伸阅读

- [本项目 Skill 实现本质原理](../project/Skill实现本质原理.md)
- [本项目设计决策清单 - K1~K3](../project/设计决策清单.md)
- [LangGraph 官方文档 — Skill 概念对比](https://langchain-ai.github.io/langgraph/concepts/)

---

## 本系列其他文章

- ✅ 第 1 篇：[LLM Agent 极简实现：200 行代码跑通 Function Calling 全流程](./01-Function-Calling-极简实现.md)
- ✅ 第 2 篇：[Agent 项目的单一真相源 — 当 CLI 和 Web UI 的 Prompt 开始漂移时](./02-单一真相源与Prompt漂移.md)
- ✅ 第 3 篇：你正在读的这篇
- 📝 [第 4 篇：让 Agent 安全执行代码 — 从 AST 白名单到 Human-in-the-loop 的三层防御](./04-Agent安全三层防御.md)
- 📝 [第 5 篇：Agent 不是 Demo — 如何用 20 条 Gold Case 搭建可自动运行的评测框架](./05-Gold-Case评测框架.md)
