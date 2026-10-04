# 深度理解 Agent 模式选择：一个循环，两种协议

> 本文以本项目真实代码为证据，系统拆解 Function Calling（FC）与 ReAct 两种工具调用协议、
> reasoning_content 折中方案、多 Agent 架构的正交关系，以及三者的模型能力门槛与选择策略。
> 所有代码引用均可按 `文件:行号` 定位核实。

## 一、引子：循环是本体，协议是变量

讨论 FC 与 ReAct 之前，先确立一个贯穿全文的事实：**两者必须有同一个 Agent 循环，循环不是附属品——循环是本体，FC 和 ReAct 只是循环内部"行动"环节的两种编码方式。**

把 `AgentRuntime.run`（FC 循环，`ai_agent/runtime.py:90-183`）和 `run_react`（ReAct 循环，`ai_agent/demos/react.py:90-158`）的骨架抽出来对照：

| 循环部件 | FC（runtime.py） | ReAct（react.py） |
|---|---|---|
| 迭代计数 + 上限 | `for iteration in range(1, max_iterations+1)`，默认 5（:90, :27） | 同一行代码，默认 6（:90, :42） |
| 请求模型 | `create_chat_completion(messages=history, tools=...)`（:104） | `create_chat_completion(messages=history)`（:92） |
| 解析输出，分流 | `finish_reason == "tool_calls"` → 执行工具；否则 → final 退出（:121, :169-178） | 正则解析：有 `Final Answer` → 退出；有 `Action` → 执行工具（:105-109, :115） |
| 执行工具 | `executor.execute(name, arguments)`（:143） | `tool_map[action_name](**args)`（:130） |
| 结果回灌上下文 | `history.append({"role": "tool", ...})`（:145-151） | `history.append({"role": "user", "content": f"Observation: ..."})`（:139） |
| 参数解析失败兜底 | 返回错误让模型"按 schema 重新调用"（:135-141） | "Action Input 不是合法 JSON"（:124-125） |
| 跑飞保险丝 | 耗尽迭代 → "已达到最大工具调用迭代次数"（:181） | 耗尽迭代 → 返回最后一轮内容（:156-158） |

逐行同构。实质差异只有两处**编码层**分歧：

1. **意图信号的来源**：FC 靠 API 结构化字段（`finish_reason == "tool_calls"`）判断"模型想调工具"；ReAct 靠正则从正文里抠 `Action:`；
2. **Observation 的回灌角色**：FC 用专门的 `role="tool"` 消息（带 `tool_call_id` 对账）；ReAct 把观察结果伪装成一条 user 消息。

### 为什么循环在原理上不可省略

LLM 是无状态的纯函数：每次请求都是"给完整上下文 → 返回一段输出"，模型自己记不住任何事、也执行不了任何动作。只要任务需要"调用工具并依据结果继续"，信息就必须走出模型再回来：

```
模型说"我要查北京天气" → （模型到此为止，它无法自己执行）
宿主程序执行 get_weather → 得到结果
结果必须作为新上下文再次请求模型 → 模型才能基于真实结果作答
```

这个"模型 → 宿主 → 模型"的往返，最小形态就是循环的一次迭代。没有循环，工具调用的结果永远回不到模型手里。两个看似的例外都不是例外：

- **模型一轮答完不调工具**：循环体只跑一圈就退出（runtime.py:169-178），但循环结构必须在场，因为要不要循环是模型运行时决定的，宿主事先不知道；
- **只调一次工具**：也至少需要两次请求（提出调用 → 回灌结果后作答），即循环迭代两次。真正无循环的只有零工具的纯对话——那已经不是 Agent。

所以：**Agent = 循环 + 模型 + 工具。FC 与 ReAct 竞争的不是"要不要循环"，而是"循环里模型表达意图、宿主解析意图用什么协议"。** 这也是理解一切 Agent 框架的万能钥匙：LangChain 的 `AgentExecutor`、AutoGen、Assistants API，剥到底都是同一个 `for` 循环；框架差异全部落在两个编码层问题——意图怎么表达、结果怎么回灌。

## 二、工具接入成本：docstring vs JSON Schema

### ReAct：一段 docstring 就是全部"协议"

`ai_agent/demos/react.py:65-68` 展示了 ReAct 注册工具的全部工作：

```python
for name, func in tool_map.items():
    doc = (func.__doc__ or "无描述").strip().split("\n")[0]
    tool_descriptions += f"- {name}: {doc}\n"
```

把函数 docstring 第一行拼进自然语言 system prompt。接入新工具的成本 = 写一个 Python 函数 + 一句 docstring，**没有 schema 要维护**：

- 工具描述是自然语言文本，模型靠"读说明书"理解工具；
- 参数约定写在 prompt 里（`Action Input: {"key": "value"}`），宿主只做 `json.loads(args_str)` 然后 `tool_map[action_name](**args)`（react.py:123-130），把 JSON 直接展开成 kwargs 调用。

代价是**脆弱性全在解析层**：react.py:9-17 维护一组正则从自由文本里抠 `Thought:` / `Action:` / `Action Input:` / `Final Answer:`；模型跑偏只能靠 react.py:144-153 的"你没按格式输出，请重试"兜底。参数也没有类型校验——模型写错照样传进去，错了在 Observation 里报错让模型自己修正。

### FC：工业界的协议化做法

对照 `ai_agent/app_core.py:103` 的 `TOOL_SCHEMAS`，同一个 `calculator` 工具要这样声明：

```python
{
    "type": "function",
    "function": {
        "name": "calculator",
        "description": "执行数学表达式计算，如 3+5*2",
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "数学表达式"},
            },
            "required": ["expression"],
        },
    },
}
```

这是结构化协议：**JSON Schema 是契约**（参数名、类型、必填、枚举都显式声明）；工具调用是 API 一等公民（`tools=tools` 作为请求参数传入，`ai_agent/client.py:170`；结果以 `role="tool"` 消息回传）；流式响应中 tool_calls 分片到达，需要按 index 聚合（client.py:523-552）；运行时由 `AgentRuntime` 状态机兜底（client.py:328-364）。

代价正是"无需维护 JSON schema"的反面：**schema 和函数实现是两份要手工同步的真相**。`ai_agent/tools/__init__.py:12` 特意注明"工具 schema 的唯一真相源是 app_core.TOOL_SCHEMAS"——加参数要改两处，改漏了就出现协议与实现的漂移。

### 一句话

ReAct 用 docstring 换透明度，FC 用 schema 换可靠性；两者是同一个"思考-行动-观察"循环的两种封装层级。对应关系：

| ReAct（文本式，透明） | FC（协议化，黑盒） |
|---|---|
| `Thought: 我需要先查天气` | 模型内部的隐式推理，不可见 |
| `Action: get_weather` | `tool_calls[0].function.name` |
| `Action Input: {"city": "北京"}` | `tool_calls[0].function.arguments` |
| `Observation: 北京晴 25°C` | `role="tool"` 消息回传 |
| `Final Answer: ...` | 无 tool_calls 的普通 content |

react.py:51-54 的注释说得很直白：chat_with_tools 的思考是黑盒，ReAct 把每一步 Thought 打印出来。ReAct 的教学价值在于用纯 prompt 技巧证明：**协议化工具调用不是魔法，其本质就是"思考→行动→观察"的文本循环**；FC 只是把它从"正则解析自由文本"升级为"训练进模型的结构化输出"。

## 三、折中方案：reasoning_content

FC 可靠但思考黑盒，ReAct 透明但协议脆弱。`ai_agent/demos/reasoning.py:25` 的 `run_native_reasoning` 提供了协议化的折中，关键是两个动作：

```python
# reasoning.py:41 —— 请求时多传一个参数
reasoning_effort=reasoning_effort,   # "high" / "medium" / "low"

# reasoning.py:49 —— 响应时多收一个字段
reasoning = getattr(message, "reasoning_content", None)
```

`reasoning_effort` 是推理模型（qwen3、DeepSeek-R1、o1 类）的 API 级开关：模型在给出 `content` 之前先生成内部推理，服务端把推理放进**独立的结构化字段** `reasoning_content` 返回。

与光谱上另两点的对比：

- **提示词 CoT**（reasoning.py:4-22 的 `run_reasoning`）：推理用正文写出，与答案混在同一字段，靠约定标记切分——任何模型可用，但推理与答案无结构性分离；
- **ReAct 的 Thought**：模型在正文里写给宿主看，用正则从自由文本里抠——可见性是用可靠性换来的；
- **reasoning_content**：模型思考完后由服务端装进独立字段——可见性由训练与 API 保证，工具调用的 schema 协议原封不动。

| 维度 | 纯 FC | ReAct | FC + reasoning_content |
|---|---|---|---|
| 工具调用可靠性 | 高 | 低 | 高（schema 不动） |
| 推理可见性 | 无 | 有（文本，需解析） | 有（独立字段，无需解析） |
| 模型要求 | tool-use 微调 | 会模仿文本格式即可 | 必须是推理模型 |

局限：非推理模型没有该字段（reasoning.py:43-47 的 try/except 即能力探测 + 降级）；reasoning_content 展示整体思考流，不保证每步对应一次工具调用的动机；`reasoning_effort="high"` 意味着大量计费思考 token，成本与延迟上升——这个旋钮让可解释性与成本之间可以连续权衡，而非 ReAct/FC 式的二选一。

## 四、架构维度：多 Agent 与两者的正交关系

FC 和 ReAct 位于**协议层**（一个模型怎么和工具交换信息），多 Agent 位于**架构层**（一个模型不够时怎么组织多个模型实例）。两者正交：

```
架构层：单 Agent ──────────────── 多 Agent（Planner/Executor/Critic）
协议层：  ├── ReAct（文本循环）      └── 每个角色内部仍可选 ReAct 或 FC
          └── Function Calling（协议循环）
```

本项目 `run_multi_agent`（`ai_agent/multi_agent.py:128`）内部每次角色调用都只用最朴素的 `client.chat()`（:167, :197, :248, :271），但工业系统（CrewAI、AutoGen）可以让 Executor 内部用 FC 调工具。

### 相同点：同一个循环骨架的第三种封装

| 要素 | ReAct | FC | Multi-Agent |
|---|---|---|---|
| 循环体 | Thought → Action → Observation | tool_calls → role="tool" 消息 | 角色间显式消息传递 |
| 上下文传递 | Observation 以 user 消息追加 | 工具结果以 tool 消息追加 | `accumulated_context` 拼接（:306） |
| 终止条件 | 解析到 `Final Answer` | 模型不再返回 tool_calls | Critic 说"✅ 通过" |
| 防死循环 | `max_iterations=6` | `max_iterations=5` | `max_rounds=2`（:131） |

Critic 的评审循环（multi_agent.py:256-303）与 ReAct 的 Action-Observation 循环同构：Executor 产出 = Action，Critic 评审 = Observation，不通过重做 = 下一轮迭代。**多 Agent 本质上是把单 Agent 的"思考-行动-观察"循环拆给不同模型实例扮演，用显式消息传递代替单上下文里的自我对话。** 连解析的脆弱性都一样：ReAct 用正则抠 `Action:`，multi-agent 用正则抠子任务清单（`_parse_subtasks`，:114-120）和 Critic 结论（`_parse_critic_decision`，:99-111，含"默认通过避免卡死"的同款防御逻辑）。

### 不同点：三个轴

1. **分解对象不同**：ReAct/FC 分解**步骤**（同一上下文、同一人格）；多 Agent 分解**角色**（独立 System Prompt 人格，:40-81，独立消息历史）。关键差异是隔离性——Executor 只看到分配的子任务（:236-246），Critic 没有参与产出，批评起来没有自我辩护的包袱；
2. **错误发现机制不同**：ReAct 靠环境反馈（工具报错），FC 靠协议拒绝（参数不合 schema），多 Agent 靠同行评审——产出"能跑但不好"时环境不会报错，只有另一个视角的模型能发现；
3. **代价结构不同**：一次 `run_multi_agent` 轻松 10+ 次 LLM 调用，是单 Agent 的 3 倍以上，用成倍 token 和延迟换质量。multi_agent.py:307-313 的 `accumulated_context` 截断逻辑暴露了多 Agent 特有的成本：角色间传递全文拷贝，上下文管理成了显式工程问题。

**选择顺序永远是先单 Agent 后多 Agent**：瓶颈是"需要外部数据"→ 单 Agent + 工具；瓶颈是"上下文装不下"→ 拆子任务；瓶颈是"质量不稳定"→ 先试单 Agent 的 Reflexion（client.py:410 的 `chat_with_reflexion`），自我批评够不着时才上独立 Critic；瓶颈是"需要对抗性视角"→ 多 Agent 不可替代。

## 五、Web UI 模式菜单：管线实例，不是分类学

既然多 Agent 与 FC/ReAct 正交，如何理解 web-ui 里三者并列的模式选择？看 `ai_agent/web_ui.py:738-741` 的分发逻辑：

```python
if mode == "react":
    self._run_react(state, user_text, event_queue)
elif mode == "multi_agent":
    self._run_multi_agent(state, user_text, event_queue)
else:
    # chat 默认分支 → chat_with_tools
```

UI 的"模式"是 **dispatch 开关**，回答用户视角的一维问题："我这条消息将被哪种执行策略处理？"从控制流看三者确实并列——同一 `if/elif/else` 的三个互斥分支，共享会话状态与事件队列。**实现上的并列性与概念上的正交性不矛盾**：正交维度被"拍平"成菜单，是因为每个 demo 恰好只占组合空间的一个格子（单Agent+FC / 单Agent+ReAct / 多Agent+纯chat），菜单列的是被实例化了的组合。

这是教学项目的设计：multi_agent.py:24-25 的注释自述"与 chat_with_tools 形成对比、与 chat_with_react 形成对比"——三个菜单项就是三节课，排序即认知顺序（先见成品 → 拆成品 → 超越成品）。若严格按正交性设计 UI 应给两个独立开关（协议 × 架构）共 4 种组合，但会端出没实现的组合。**菜单拍平是用产品简约性换概念严格性，教学场景下这是正确取舍。** 工业产品同样如此：ChatGPT 的模型菜单混着通用模型、推理模型、Deep Research（多 Agent 编排），把协议、推理深度、架构差异打包成"一个模式选项"；若本项目演进出生产形态，自然方向是把协议层收成内部实现细节，菜单只留任务复杂度维度（快速回答 / 深度思考 / 团队协作）。

## 六、实验：同一问题跑三种模式

拿一个具体问题当实验样本，把三种模式的运行过程逐一推演，再给出学习路径建议。

### 6.1 设定实验样本

选一个需要工具、又值得拆解的问题：

> **"北京和上海现在的天气分别是多少度？哪个更热，温差几度？"**

这个问题是设计过的：需要两次工具调用（查两个城市）+ 一次计算（温差）+ 一步判断（哪个更热），足以让三种模式的差异全部暴露。

### 6.2 三种模式的运行轨迹

**模式 1：普通对话（Function Calling）**

你看到的：输入问题 → 短暂等待 → **直接出最终答案**："北京 25°C，上海 29°C，上海更热，温差 4°C。"

实际发生的（黑盒内）：

```
请求1: messages + TOOL_SCHEMAS → 模型返回 tool_calls=[get_weather("北京"), get_weather("上海")]
       （可能并行，一轮两个调用）
执行工具 → role="tool" 消息回传
请求2: 模型返回 tool_calls=[calculator("29-25")]
执行 → 回传
请求3: 模型返回纯 content（最终答案）→ AgentRuntime 判定结束
```

- **LLM 调用约 3 次**，但界面上只有一次问答；
- 你能看到工具被调用的痕迹（web_ui 会渲染 tool_calls），但**看不到模型"为什么先查北京再算温差"的推理**；
- 如果答案错了，你只有一个最终答案，**无法定位错在哪一环**——是查错了城市？算错了减法？还是理解错了问题？

**模式 2：ReAct 边想边做**

你看到的（`run_react` 的轨迹输出，react.py:89-138 逐行打印）：

```
│  🔄 第 1 轮
│  💭 Thought: 我需要分别查询北京和上海的天气
│  🔧 Action: get_weather
│  📥 Input:  {"city": "北京"}
│  👁️ Observation: 北京晴 25°C
│  🔄 第 2 轮
│  💭 Thought: 已拿到北京天气，接下来查上海
│  🔧 Action: get_weather
│  📥 Input:  {"city": "上海"}
│  👁️ Observation: 上海多云 29°C
│  🔄 第 3 轮
│  💭 Thought: 上海 29 度比北京 25 度热，温差 29-25=4 度，可以回答了
│  ✅ Final Answer: 北京 25°C，上海 29°C，上海更热，温差 4°C
```

- **LLM 调用 3 次**（每轮一次），每次的意图全部显式可见；
- 注意第 1、2 轮是**串行**的——ReAct 的 prompt 要求"每次只输出一个 Action"（react.py:77），所以查两个城市要两轮，而 FC 可能一轮并行搞定。这是效率差异的直观教学素材；
- 如果出错，你能精确看到是**哪个 Thought 想歪了**——比如模型第 1 轮 Thought 写"我先算温差再说"，你立刻知道它规划错了，而不是对着错误答案猜。

**模式 3：多 Agent 协作**

你看到的（`run_multi_agent` 的完整轨迹）：

```
👤 [Planner] 规划: 1. 查询北京天气 2. 查询上海天气 3. 比较并计算温差，整合答案
🔄 [第 1 轮] Critic 评审计划...  ✅ 通过
⚙️  [Executor] 子任务 1/3: 查询北京天气 → 产出...
   🔍 Critic 评审子任务 1 → ✅ 通过
⚙️  [Executor] 子任务 2/3: 查询上海天气 → 产出...
   🔍 Critic 评审子任务 2 → ❌ 不通过：「产出未包含温度数值，需补充」
   ⚙️  Executor 修正后产出: 上海多云 29°C...
   🔍 Critic 复审 → ✅ 通过
⚙️  [Executor] 子任务 3/3: 比较与整合 → 产出...
   🔍 Critic 评审子任务 3 → ✅ 通过
📦 Planner 汇总整合 → 最终交付
```

- **LLM 调用约 8-10 次**（规划 1 + 审计划 1 + 每个子任务执行+评审 2×3 + 汇总 1），成本是前两种的 3 倍；
- 但注意一个关键事实：**这个项目的 Executor 不调工具**（multi_agent.py:248 用的是纯 `client.chat`）——"查北京天气"这件事 Executor 是靠模型自身知识"回答"的，不是真的调用 `get_weather`！如果模型不知道实时天气，它会编一个，然后 Critic 审的也只是"格式和完整性"，审不出"温度是编造的"。这本身就是极好的一课：**多 Agent 解决的是分工和质检，不解决工具接入；没有工具的 Executor，评审再多轮也拿不到真实数据。**

### 6.3 异同总结

| 维度 | FC 普通对话 | ReAct | 多 Agent |
|---|---|---|---|
| 最终答案 | 三者理想情况下**相同**（这是"同"的核心：同一模型、同一能力上限） | 同 | 同（或更 polished，多一步整合） |
| LLM 调用次数 | ~3 | ~3 | ~8-10 |
| 工具调用方式 | 协议化 tool_calls，可并行 | 文本 Action，串行 | 本项目中不调工具 |
| 过程可见性 | 只见工具调用记录 | 每步 Thought 全可见 | 角色间消息全可见 |
| 错误可定位性 | 差（黑盒） | 好（哪步想错了一目了然） | 好（哪个角色掉链子清晰可见） |
| 质量控制 | 无（模型自说自话） | 无（自我修正靠 Observation） | 有（Critic 独立评审） |
| 延迟/成本 | 低 | 低 | 高 3 倍+ |

**"同"的本质**：三种模式不改变模型的能力上限——都是同一个 LLM 在回答问题，最终答案的质量天花板相同。**"异"的本质**：它们在**过程透明度、错误检测机制、成本**三个维度上做了不同的权衡分配。

### 6.4 学习路径建议：怎么用这三个模式做实验

**第一步：同一问题，三模式连跑，对比轨迹而非答案。** 答案大概率相同，学习价值全在过程里。重点观察：FC 的 tool_calls 记录和 ReAct 的 Thought 链是不是**同构的**（都经历了查北京→查上海→算温差）？看出同构，你就理解了"FC 是 ReAct 的协议化封装"这个核心论断。

**第二步：制造故障，观察三种模式的失败方式。** 把问题改成"查询一个叫'亚特兰蒂斯'的城市的天气"——工具会返回错误或编造：
- FC：可能静默给一个编造的答案，你只能从结果怀疑；
- ReAct：你能看到 Observation 报错后模型在 Thought 里如何挣扎、自我修正；
- 多 Agent：Executor 编造后，看 Critic 能不能审出来——大概率审不出，因为它也没有工具核实。这教你**每种模式的错误检测边界在哪**。

**第三步：拆解成本账单。** 开一个需要 3-4 个工具步骤的复杂任务，数三种模式各自的 LLM 调用次数和总耗时（web_ui 有 trace 面板，web_ui.py:886 切换时也有日志）。亲身体会"多 Agent 用 3 倍成本换质量"这句话的分量，以后做工程决策时才有手感。

**第四步（进阶）：思考正交组合。** 做完前三步，问自己：如果让多 Agent 的 Executor 接入 `chat_with_tools`，让 Critic 用 reasoning_content 审查，会怎样？能问出这个问题，说明你已跳出"三选一"的菜单思维，真正掌握了维度分解——这就是这个教学项目想送达的终点。

一句话：**同问题三模式跑，答案趋同、轨迹迥异——FC 给你结果，ReAct 给你推理，多 Agent 给你质检；学习顺序应是先跑通对比轨迹（理解同构），再制造故障对比失败方式（理解边界），最后数成本账单（理解权衡），三步下来三种模式从"菜单选项"变成你手里的"工程杠杆"。**

## 七、模型能力门槛：三种模式是任何模型都能用的吗

三种模式在"能不能跑"上都是任何模型都能跑，但"跑得好不好"有清晰的能力门槛。

### ReAct：门槛最低，但不是零

纯 prompt 技巧，协议上不挑模型，但要求**指令遵循 + 格式纪律**。react.py:143-153 专门处理"模型未按格式输出"的跑偏——这是为弱模型准备的兜底。弱模型常把 Action Input 写成自然语言（`json.loads` 失败，react.py:124-125），迭代轮次可能全耗在格式纠错上。**任何模型都能"运行" ReAct，只有指令遵循过关的模型才能"收敛"。**

### FC：硬性门槛，不是纯软件问题

要求模型**训练时专门学过工具调用协议**——没微调过的模型会把工具调用写成正文里的一段 JSON 文本，`tool_calls` 字段为空，循环静默退化成普通对话。还依赖**服务栈支持**：本地推理引擎必须实现 tools 参数解析与 tool-call 提取。模型支持 + 引擎支持，缺一不可。

### 多 Agent：门槛最隐蔽——放大单模型的一切缺陷

协议上门槛为零（只用 `client.chat`），但有两个放大效应：

- **能力乘法衰减**：规划 80 分 × 执行 80 分 × 评审 80 分 ≈ 系统 51 分。模型越弱、角色越多，衰减越狠；
- **解析连锁脆弱**：`_parse_subtasks`（multi_agent.py:114-120）解析不出编号列表就退化为"整个计划当一个子任务"（:173-175）；`_parse_critic_decision`（:99-111）解析不出结论就"默认通过"——Critic 形同虚设时，多 Agent 退化成"一个模型分三次自言自语，成本翻三倍"。

### 原生 reasoning：门槛最高

`reasoning_effort` 要求推理模型，API 直接拒绝不支持的模型（reasoning.py:43-47）。这是唯一"硬件级"门槛，prompt 技巧无法模拟。

### 能力门槛总表

| 模式 | 协议门槛 | 真正决定效果的能力 | 弱模型的退化形态 |
|---|---|---|---|
| FC | **高**：tool-use 微调 + 引擎支持 | 协议遵循、参数生成准确率 | 静默退化为纯聊天 |
| ReAct | 零：纯文本 | 指令遵循、格式纪律、自我修正 | 迭代耗尽在格式纠错上 |
| 多 Agent | 零：纯文本 | 各角色能力的**乘积** | Critic 失灵，昂贵的自言自语 |
| 原生 reasoning | **最高**：推理模型 | 模型内生的推理深度 | API 直接报错 |

**反直觉的结论**：模型越弱，越应该用结构简单的模式——复杂架构的失败是乘性的；"三个臭皮匠顶个诸葛亮"在 LLM 上不成立，三个 60 分的模型协作出来往往不到 60 分。

## 八、附：如何判断一个模型是否是推理模型

五个层次，可靠性递增：

1. **看命名与官方文档**（最快最不可靠）：o1/o3、DeepSeek-R1、QwQ、Qwen3 thinking 版、Kimi k 系 thinking 版等。局限：Qwen3 这类混合模型"是不是推理模型"取决于调用方式而非名字；
2. **协议探测**（本项目方式，reasoning.py:34-49）：请求传 `reasoning_effort` 看是否报错——异常本身就是判定；响应检查 `reasoning_content` 字段是否存在非空——存在即铁证。不查文档不看名字，用协议特征说话，且天然完成降级；
3. **观察输出形态**（本地模型土办法）：原始输出有无 `<think>...</think>` 思考标签（DeepSeek-R1 系、QwQ）；普通模型被训练成直接给答案，永远不会自发产生这种结构；
4. **行为特征测试**（终极裁判）：出"9.11 和 9.9 哪个大"这类陷阱题，观察三点——首 token 前的思考停顿、completion tokens 远大于可见答案（部分 API 单列 `reasoning_tokens`）、陷阱题正确率；
5. **注意灰色地带**：混合模型的判定依赖调用参数；服务端可能用 prompt 模板包装普通模型冒充推理模型（只有行为测试能分辨）；Agent 框架的 ReAct/CoT 是"表演推理"而非内生推理——**推理模型的推理是训练出来的，不需要 prompt 引导**。

## 九、附：ReAct ≠ React.js

两者**没有血缘关系，是同一英文词根的两次独立命名**：

- **React.js**（2013，Facebook）：取自 **reactive programming（响应式编程）**——UI 响应数据变化，循环为 `state 变化 → render → DOM 更新 → 事件 → setState`；
- **ReAct**（2022，Google Research，Yao et al.）：**Reasoning + Acting 的合成词**，循环为 `Thought → Action → Observation`。

构词法都不同，纯属撞名。但抽象到顶层，两者是同一控制论回路（OODA：感知-判断-决策-行动）的两件马甲：

| 回路要素 | React.js | ReAct Agent |
|---|---|---|
| 感知输入 | state / props | Observation（工具返回） |
| 决策计算 | render 函数（state → UI 纯映射） | LLM 前向推理（上下文 → Thought/Action） |
| 作用环境 | DOM 提交 | 工具调用 |
| 循环终止 | 无终止（常驻响应） | Final Answer / max_iterations |

细节上的深层呼应：React 要求 render 是纯函数（副作用放进 effect），ReAct 的 Thought 设计哲学是"推理可追溯"——两边都在对抗不可控的副作用。

实践上两者天天组合：本项目后端跑 ReAct 循环产生事件，经 `on_event` 回调（react.py:58-62）推进 `event_queue`（web_ui.py:659 的 `_run_react`），前端消费事件流重渲染——形成**嵌套双循环**：内层 ReAct 每转一圈，外层 UI 循环被驱动若干圈。界面上"🔄 第 2 轮、💭 Thought: ..."逐行浮现，就是两个循环咬合运转的直接证据。

判别心法：上下文里有没有 LLM？有 → ReAct；循环驱动源是用户事件/state 变化 → React.js，是模型自己产生的 Action 文本 → ReAct。

## 十、总结：FC 与 ReAct 有必要共存吗

**功能覆盖上冗余，职能定位上互补。** FC 是 ReAct 的严格超集——同一循环骨架，可靠性、并行调用、类型校验、流式聚合全是净收益；但"完成任务"不是它们各自的全部职能：ReAct 的 Thought 链是透明推理的教学材料，FC 的 schema 契约是生产系统的可靠性地基，各自最强的价值点都不在共同交集上。

### 统一选择策略

```
模型支持 tool-use 协议吗（探测：发 tools 请求看有无 tool_calls 返回）？
├── 支持 → FC 是默认答案
│     ├── 还需要推理可见？ → 叠加 reasoning_content（推理模型）
│     │                      或 prompt 要求"调用前说明意图"
│     └── 任务需要质量评审？ → 在 FC 之上架多 Agent 架构
└── 不支持 → ReAct 是保底方案
      ├── 教学/调试场景 → 主动选 ReAct（透明性就是目的本身）
      └── 原型验证期 → ReAct 快速试错，定型后迁移到 FC
```

三个决策原则：

1. **能上协议就上协议**：FC 的可靠性是免费获得的——除非协议不可用（弱模型）或透明性本身就是目标（教学）；
2. **降级方向固定**：从协议化退到文本化（FC→ReAct），从复杂架构退到简单循环（多Agent→单Agent），永不反向；
3. **混合是工业常态**：FC 为主干，reasoning_content 补透明性，多 Agent 只在质量瓶颈真的需要独立评审时引入。

### 本项目的共存逻辑

FC（`chat_with_tools` → `AgentRuntime`，client.py:328）与 ReAct（`run_react`，react.py:37）并存不是功能冗余，而是**同一本质的两次曝光**：同一问题跑两种模式，答案趋同、轨迹迥异——这个"同"正是教学核心论据，让学生亲眼看到 FC 的黑盒里装着的就是 ReAct 展示的那个循环。去掉任何一个，教学闭环就断：只有 FC，学生把工具调用当 API 魔法；只有 ReAct，学生不知道工业界如何把它做可靠。**两者互为注解——ReAct 解释 FC 的本质，FC 展示 ReAct 的工程化终点。**
