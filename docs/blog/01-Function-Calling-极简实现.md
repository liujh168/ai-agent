# LLM Agent 极简实现：200 行代码跑通 Function Calling 全流程

> 作者：Agent Dev 实践笔记 | 发布日期：2026-09-21 | 标签：Agent, Function Calling, Python, LM Studio

---

## 引子

你有没有过这样的经历：看完 OpenAI 的 Function Calling 文档，觉得"哦不就是让模型调个函数嘛"，然后打开编辑器开始写，写了半小时突然发现——

- 模型到底什么时候算调完了？`finish_reason` 有哪几种？
- 模型调了多个函数怎么办？顺序执行还是并行？
- 函数返回值怎么喂回给模型？`role` 是 `"tool"` 还是 `"assistant"`？
- 如果函数抛异常了，该把错误信息包装成字符串喂回去，还是直接崩溃？

网上的教程要么是"Hello World 级别的单轮调用"，要么是"看 LangChain / LlamaIndex 的源码"——中间那层"自己手撸一个能跑的 Agent"，反而没人讲。

这篇文章就来填这个坑。我们用 **200 行左右** 的 Python，基于 LM Studio（本地跑模型，完全免费），手撸一个能跑通 Function Calling 完整循环的 Agent。代码全部来自一个真实的教学项目，不是"演示用伪代码"。

读完这篇，你能独立：
1. 看懂 Function Calling 到底是什么（以及它**不是**什么）
2. 写出一个最小可用的 Agent 主循环
3. 把它接到任何 OpenAI 兼容的模型服务上

---

## 一、先搞清楚：Function Calling 是什么（以及不是什么）

### ❌ 它不是"让模型执行代码"

Function Calling 这个名字很容易误导人。模型**不会**执行你的 Python 函数。它做的事情是：

> 根据对话上下文，**生成一段符合特定 JSON Schema 的 JSON 文本**。

这段 JSON 文本长得像这样：

```json
{
  "name": "calculator",
  "arguments": {
    "expression": "2 ** 10"
  }
}
```

然后由**你的代码**（不是模型）去解析这段 JSON、找到对应的 Python 函数、执行它、拿到返回值、再喂回给模型。

### ✅ 它是"让模型做路由决策"

Function Calling 本质上是**模型输出的一种结构化约束**——你提前告诉模型"我有哪些工具、每个工具的参数长什么样"，模型根据对话判断"现在该不该调工具、调哪个、传什么参数"。

所以整个 Agent 的核心循环其实就是：

```
while True:
    1. 调模型（把对话历史 + 可用工具列表发过去）
    2. 看模型返回：
       a. 如果模型想调工具 → 解析 tool_calls → 执行工具 → 把结果追加到历史
       b. 如果模型想直接回复 → 输出最终答案 → 结束循环
```

就这三步。没有什么神秘的"Agent 框架"。

---

## 二、前置：装 LM Studio + 加载一个模型

为了不依赖 OpenAI API Key（也为了隐私），我们用 **LM Studio**——一个可以在本地跑 Llama / Qwen / Mistral 等开源模型的桌面应用。

1. 下载 LM Studio：<https://lmstudio.ai/>
2. 搜索并下载一个支持 Function Calling 的模型（推荐 **qwen3.5-9b**，中文好、支持 Function Calling、对显存要求友好）
3. 在 LM Studio 里点 **Local Server → Start Server**，默认在 `http://localhost:1234`

跑通后我们可以用 Python `openai` 库直连（LM Studio 完全兼容 OpenAI 的 API 协议）。

---

## 三、完整代码：200 行跑通 Agent

下面是本项目 `ai_agent/` 里的核心代码骨架，去掉了安全策略、RAG、Skill 等增强功能，保留最纯粹的 Function Calling 循环。

### 3.1 依赖

```bash
pip install openai requests
```

### 3.2 完整代码

```python
"""agent_minimal.py — 最小可用的 Function Calling Agent"""

import json
import time
from openai import OpenAI


# ---------- 1. Tool Schema：告诉模型有哪些工具可以调 ----------

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "安全计算一个数学表达式（只支持 +、-、*、/、**、% 四则运算）",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "数学表达式，如 '2 ** 10' 或 '(3 + 5) * 2'",
                    }
                },
                "required": ["expression"],
            },
        },
    },
]


# ---------- 2. 工具实现：Python 函数，不是模型代码 ----------

def calculator(expression: str) -> str:
    """安全计算数学表达式（简化版，真实项目需要 AST 白名单）"""
    try:
        # 真实项目这里应该用 ast.parse() + 白名单节点校验
        result = eval(expression, {"__builtins__": {}}, {})
        return str(result)
    except Exception as exc:
        return f"计算失败: {exc}"


tool_map = {
    "calculator": calculator,
}


# ---------- 3. Agent 主循环（迭代器模式） ----------

class Agent:
    def __init__(self, base_url: str = "http://localhost:1234/v1",
                 api_key: str = "lm-studio", model: str = "auto"):
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model

    def run(self, user_input: str, max_iterations: int = 5):
        """Agent 主循环：生成 yield 每一轮的事件"""
        messages = [
            {"role": "system", "content": "你是一个有用的 AI 助手。如果用户问数学问题，用 calculator 工具。"},
            {"role": "user", "content": user_input},
        ]

        for i in range(1, max_iterations + 1):
            # 3.1 调模型
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=TOOL_SCHEMAS,
                temperature=0.3,       # Agent 场景温度要低，减少随机性
            )
            choice = response.choices[0]
            message = choice.message

            # 3.2 模型想调工具 → 执行 → 结果回灌
            if choice.finish_reason == "tool_calls" and message.tool_calls:
                messages.append(message)            # 把 assistant 的 tool_call 请求入历史

                for tc in message.tool_calls:       # 可能同时调多个工具
                    fn = tool_map.get(tc.function.name)
                    if fn is None:
                        result = f"错误：工具 {tc.function.name!r} 未注册"
                    else:
                        args = json.loads(tc.function.arguments)
                        try:
                            result = fn(**args)   # 执行 Python 函数
                        except Exception as exc:
                            result = f"执行异常: {exc}"

                    # 把执行结果追加到 messages
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": str(result),
                    })
                    print(f"  [工具调用] {tc.function.name}({args}) → {result}")

                continue    # 继续下一轮循环（让模型根据结果做下一步）

            # 3.3 模型想直接回复 → 结束循环
            if message.content:
                print(f"\n💡 {message.content}")
                return
            print("⚠️ 模型返回空内容，循环结束")
            return


# ---------- 4. 跑起来 ----------

if __name__ == "__main__":
    agent = Agent()
    # 先检查模型是否已加载
    print(agent.client.chat.completions.create(
        model="auto",
        messages=[{"role": "user", "content": "hi"}],
    ).choices[0].message.content[:50])
    print("模型就绪！\n")

    agent.run("帮我算一下 (15 + 27) * 3 等于多少")
```

---

## 四、跑起来看效果

```bash
# 运行
python agent_minimal.py

# 预期输出（大致）：
# 模型就绪！
#
#   [工具调用] calculator({'expression': '(15 + 27) * 3'}) → 126
#
# 💡 答案是 126。
```

整个过程：
1. 模型收到用户问题，**判断**需要调 calculator，生成 tool_call JSON
2. 你的 Python 代码解析 tool_call，执行 `calculator(expression="(15 + 27) * 3")`
3. 把结果 `"126"` 以 `role="tool"` 追加回对话
4. 模型看到结果，这次不再调工具，直接输出最终答案 `"答案是 126"`
5. `finish_reason` 变成 `"stop"`（或 `"end_turn"`，不同模型有差异），循环结束

---

## 五、核心概念速查

读完上面的代码，你应该已经理解了 Function Calling 的几个关键设计：

| 概念 | 在哪出现 | 含义 |
|------|---------|------|
| **Tool Schema** | `tools=` 参数 | JSON 对象，告诉模型"有哪些工具、参数长什么样"。不是 Python 代码 |
| **finish_reason** | 模型返回 | `"tool_calls"` = 想调工具；`"stop"` / `"end_turn"` = 想结束 |
| **tool_calls** | message 对象 | 一个数组，模型可以**同时调多个工具**（并行执行） |
| **role = "tool"** | messages 列表 | 工具返回值的角色，**必须用这个**，不能用 `"user"` 或 `"assistant"` |
| **tool_call_id** | messages 列表 | 关联 tool 结果和对应的 tool_call 请求，模型需要这个 ID |
| **循环退出条件** | `max_iterations` | 防止模型陷入无限工具调用循环的保险丝 |

---

## 六、真实项目 vs 极简版：加了哪些东西

上面的 200 行代码是**骨架**。本项目 `ai_agent/` 在这个骨架上增加了：

| 增强 | 位置 | 做了什么 |
|------|------|---------|
| Typed Event 迭代器 | `ai_agent/events.py` | 每次循环 yield 一个 `AgentEvent`，CLI 和 Web UI 各自格式化输出 |
| 安全策略 | `ai_agent/tooling.py` | ToolPolicy 把工具分 4 级风险，不同 surface 有不同安全规则 |
| Tool Map 工厂 | `ai_agent/app_core.py` | `build_tool_map()` 统一注册工具，CLI / Web UI / Eval 共享 |
| Skill 路由 | `ai_agent/skill.py` | 关键词匹配自动激活场景化 Skill，替换 prompt + 收窄工具集 |
| RAG 知识库 | `ai_agent/rag.py` | 把外部资料向量化，Agent 可以"边查资料边回答" |
| 流式输出 | `ai_agent/client.py` | `client.chat_stream()` 逐 token yield，Web UI 可以实时打字机效果 |
| 模型状态缓存 | `ai_agent/client.py` | 首次 ensure_model_loaded 后缓存结果，跳过后续 HTTP /models 检查 |

但**不管加多少层**，核心循环永远是那个 `for iteration in range(max_iterations)`——模型生成 tool_call → 你的代码执行 → 结果回灌 → 下一轮。

---

## 七、给你的下一步

如果你想继续深入，我推荐按这个顺序实践：

1. **把 calculator 换成 file_read**：让 Agent 能读本地 Markdown 文件。你需要：写一个 `file_read(path, max_chars)` Python 函数；在 TOOL_SCHEMAS 里加对应的 schema；tool_map 里注册。完成这三步就是一个能读文件的 Agent。

2. **加第二个工具 + 多步推理**：让 Agent 先查天气（get_weather），再根据天气推荐穿衣建议（不需要工具，直接回复）。这是经典的多步工具调用场景。

3. **加流式输出**：把 `client.chat.completions.create(..., stream=True)` 打开，逐 token yield 给前端。体验立即上一个台阶。

---

## 延伸阅读

- [OpenAI Function Calling 官方文档](https://platform.openai.com/docs/guides/function-calling)
- [本项目设计决策清单 - 架构模式与代码组织](../project/设计决策清单.md)
- [LM Studio 本地模型部署指南](https://lmstudio.ai/)

---

## 本系列其他文章

- ✅ 第 1 篇：你正在读的这篇
- 📝 [第 2 篇：Agent 项目的单一真相源 — 当 CLI 和 Web UI 的 Prompt 开始漂移时](./02-单一真相源与Prompt漂移.md)
- 📝 [第 3 篇：把 Skill 做成 Markdown 文件 — Agent 框架的"低代码"设计决策](./03-Skill做成Markdown.md)
- 📝 [第 4 篇：让 Agent 安全执行代码 — 从 AST 白名单到 Human-in-the-loop 的三层防御](./04-Agent安全三层防御.md)
- 📝 [第 5 篇：Agent 不是 Demo — 如何用 20 条 Gold Case 搭建可自动运行的评测框架](./05-Gold-Case评测框架.md)
