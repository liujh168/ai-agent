"""
ai_agent.app_core — 共享的提示词、工具 schema 与工具注册工厂
============================================================
CLI (main.py) 与 Web UI (web_ui.py) 共用同一份 SYSTEM_PROMPT、TOOL_SCHEMAS、
build_tool_map()，彻底消除两份实现逐渐漂移的问题。

历史上两份实现不一致导致了：
  - SYSTEM_PROMPT 漏路由 plan_task / extract_structured（评测 structured_extract_basic 必失败）；
  - Web 端工具集与 CLI 不一致、且能力数量文案三处打架（18 / 20 / 实际 21）。
把"真相"集中在这里后，banner、Web 标题、README 都引用 TOOL_COUNT，不会再漂移。
"""

from __future__ import annotations

import os

from ai_agent.tools import (
    run_code, get_weather, calculator, web_search,
    file_read, file_write, sql_query, run_terminal, human_confirm,
    FEW_SHOT_TEMPLATES,
)
from ai_agent.multi_agent import run_multi_agent


# ============================================================
# 系统提示词（自然语言 → 工具路由的"真相源"）
# ============================================================

SYSTEM_PROMPT = (
    "你是一个交互式演示助手，负责向用户展示 LLM 的各项能力。"
    "根据用户的自然语言请求，选择合适的工具来完成演示：\n"
    "- 用户要求【流式输出/逐字输出/边打字边回复】时，调用 demo_stream_chat；\n"
    "- 用户要求【思维链/逐步推理/逻辑题/数学推导】时，调用 demo_reasoning；\n"
    "- 用户要求【原生推理/深度思考/reasoning_effort】时，调用 demo_native_reasoning；\n"
    "- 用户要求【ReAct/边想边做/思考与行动交替/推理轨迹/看到我思考】时，调用 demo_react；\n"
    "- 用户要求【写代码并运行/编程计算题/调试代码】时，调用 demo_code_run，"
    "若运行报错则根据错误修正代码后再次调用，直到成功；\n"
    "- 用户要求【查看文件/读取文件/文件内容】时，调用 file_read；\n"
    "- 用户要求【创建文件/写入文件/保存为】时，调用 file_write；\n"
    "- 用户要求【查数据库/SQL查询/统计报表/数据分析】时，调用 sql_query；\n"
    "- 用户询问【AI Agent 概念/资料内容/知识库/查一下资料】时，调用 search_knowledge（本地 PDF 知识库）；\n"
    "- 用户询问【最新信息/当前新闻/外部知识/实时数据/搜索一下】时，调用 web_search（互联网搜索）；\n"
    "- 用户要求【反思/自我修正/需要严谨准确的复杂问题】时，调用 reflexion_answer；\n"
    "- 用户要求【看图/识别图片/OCR/图表理解】时，调用 demo_vision；\n"
    "- 用户要求【终端命令/执行命令/运行命令行/shell】时，调用 run_terminal；\n"
    "- 用户要求【人工确认/确认一下/Human-in-the-loop】时，调用 human_confirm；\n"
    "- 用户要求【Few-shot/示例演示/Prompt Engineering/提示词工程/给我几个示例】时，调用 demo_few_shot；\n"
    "- 用户要求【压缩/摘要/总结对话/缩短历史】时，调用 summarize_history；\n"
    "- 用户要求【多 Agent/多智能体/Planner/Executor/Critic/角色协作/分工协作】时，调用 run_multi_agent（Planner 拆任务 → Executor 执行 → Critic 评审）；\n"
    "- 用户要求【规划任务/拆解任务/分步骤完成复杂任务/写大纲并展开/规划并写一篇文章】时，调用 plan_task；\n"
    "- 用户要求【提取结构化信息/抽取字段/生成 JSON 数据/结构化输出】时，调用 extract_structured；\n"
    "- 执行敏感/破坏性操作前（删除覆盖文件、写入重要数据、SQL DDL/DML、终端写入命令），必须先调用 human_confirm 让用户确认；\n"
    "- run_terminal 内部有关键词安全拦截（Remove-Item/Set-Content/Stop-Computer 等 PowerShell 原生命令会被阻断），若被拦截请改用 human_confirm 或普通文件操作工具；\n"
    "- sql_query demo 模式只允许 SELECT/PRAGMA/EXPLAIN/WITH，DROP/DELETE/UPDATE 会被阻断；\n"
    "- calculator 使用 AST 白名单，不接受 eval/exec/__import__ 等任意代码；\n"
    "- 用户询问【天气】时，调用 get_weather；\n"
    "- 用户要求【计算】时，调用 calculator；\n"
    "- 普通聊天直接回答，不需要调用工具。"
)


# ============================================================
# 工具 schema 列表（Function Calling 的"真相源"，与 tool_map 一一对应）
# ============================================================

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询指定城市的天气",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名称，如北京、上海"},
                },
                "required": ["city"],
            },
        },
    },
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
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "网络搜索工具（优先博查 AI 搜索 API，国内稳定；未配 BOCHA_API_KEY 时降级 DuckDuckGo）。当用户询问最新信息、外部知识、实时数据、当前时间相关问题时调用。注意：这是互联网搜索，不同于 search_knowledge 的本地 PDF 知识库",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "max_results": {"type": "integer", "description": "返回结果数量，默认 3，建议不超过 5"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_read",
            "description": "读取本地文本文件内容。当用户要求查看、读取某个文件时调用。自动处理编码，超长文件截断并保留前后上下文",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径，支持相对路径（不能包含 ..）"},
                    "max_chars": {"type": "integer", "description": "最大读取字符数，默认 3000"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_write",
            "description": "写入本地文本文件。当用户要求生成、创建、保存文件时调用。覆盖或追加模式",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径，支持相对路径（不能包含 ..）"},
                    "content": {"type": "string", "description": "要写入的文本内容"},
                    "mode": {
                        "type": "string",
                        "description": "写入模式：write 覆盖（默认）/ append 追加",
                        "enum": ["write", "append"],
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sql_query",
            "description": "SQLite 数据库查询。首次调用带 demo_seed=True 会自动创建 users/orders 示例表和数据；之后可执行任意 SQL 查询。返回格式化表格结果",
            "parameters": {
                "type": "object",
                "properties": {
                    "db_path": {"type": "string", "description": "SQLite 数据库文件路径，如 demo.db"},
                    "sql": {"type": "string", "description": "SQL 语句，如 SELECT * FROM users WHERE city='北京'"},
                    "demo_seed": {
                        "type": "boolean",
                        "description": "是否首次自动建表+示例数据，默认 True（仅新库生效）",
                    },
                },
                "required": ["db_path", "sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_terminal",
            "description": "执行系统终端命令（Windows 下走 PowerShell）。返回 stdout+stderr+exit_code。注意：执行删除、写入、网络相关等敏感命令前，建议先调用 human_confirm 让用户确认",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的命令，如 Get-Date、dir、python --version"},
                    "timeout": {"type": "integer", "description": "超时秒数，默认 15"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "human_confirm",
            "description": "Human-in-the-loop 人工确认。执行敏感/破坏性操作（删除文件、DDL/DML SQL、终端写入命令、覆盖重要数据）前暂停等待用户输入 y/yes 确认，返回确认或取消结果。这是 Agent 安全流程的关键环节",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "描述即将执行的操作，让用户清楚在确认什么"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "demo_few_shot",
            "description": "Prompt Engineering 演示：用 Few-shot（输入少量示例让模型理解任务模式）来回答问题。支持两种任务类型：sentiment 情感分类 / polish 文本润色。输出会展示 few-shot 提示词结构和最终答案，帮助用户理解 prompt engineering 原理",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "需要用 few-shot 方式回答的问题，如做情感分类、翻译、信息抽取等"},
                    "task_type": {
                        "type": "string",
                        "description": "任务类型：sentiment 情感分类（默认）/ polish 文本润色",
                        "enum": ["sentiment", "polish"],
                    },
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "summarize_history",
            "description": "长对话摘要压缩：把较早的对话历史浓缩成一段摘要，只保留最近几条完整消息。当对话话题切换前、或 history 过长担心超上下文窗口时可主动调用",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "demo_stream_chat",
            "description": "以流式方式演示对话（逐 token 边生成边输出）",
            "parameters": {"type": "object", "properties": {"topic": {"type": "string", "description": "流式演示的话题"}}, "required": ["topic"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "demo_reasoning",
            "description": "演示思维链推理（提示词引导，逐步展示推理过程），适合逻辑题、数学题",
            "parameters": {"type": "object", "properties": {"question": {"type": "string", "description": "需要推理的问题"}}, "required": ["question"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "demo_native_reasoning",
            "description": "演示原生推理模式（通过 reasoning_effort 参数触发模型内置思考，qwen3 等模型支持）",
            "parameters": {"type": "object", "properties": {"question": {"type": "string", "description": "需要推理的问题"}}, "required": ["question"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "demo_react",
            "description": "演示 ReAct（Reasoning + Acting）模式：让模型边想边做，交替输出 Thought→Action→Action Input，执行后把 Observation 喂回，直到 Final Answer。教学用途：展示 Agent 的思考轨迹和工具选择过程，与 Function Calling 黑盒模式形成对比",
            "parameters": {"type": "object", "properties": {"question": {"type": "string", "description": "需要 ReAct 方式回答的问题"}}, "required": ["question"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "demo_code_run",
            "description": "代码执行 Agent：执行 Python 代码并返回输出/错误。当用户要求写代码运行、编程计算题、或想验证代码时调用。返回错误时可修正后再次调用",
            "parameters": {"type": "object", "properties": {"code": {"type": "string", "description": "要执行的 Python 代码"}}, "required": ["code"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": "RAG 知识库问答：基于项目内 PDF/MD/TXT 资料检索并回答。当用户询问 AI Agent 概念、原理、资料内容时调用",
            "parameters": {"type": "object", "properties": {"question": {"type": "string", "description": "需要查询的问题"}}, "required": ["question"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reflexion_answer",
            "description": "反思与自我修正：先作答→自我批评→修正，多轮迭代提升答案质量。适合需要高准确率的复杂问题",
            "parameters": {"type": "object", "properties": {"question": {"type": "string", "description": "需要严谨回答的问题"}}, "required": ["question"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "demo_vision",
            "description": "图像识别：分析本地图片（描述内容/OCR/理解图表）。不填 image_path 时使用自动生成的测试图表",
            "parameters": {"type": "object", "properties": {"image_path": {"type": "string", "description": "图片路径（可选，默认测试图）"}, "question": {"type": "string", "description": "针对图片的问题"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_task",
            "description": "任务规划分解（Planner-Executor）：把复杂任务拆解为有序子任务并逐个执行完成。当用户要求规划任务、拆解任务、分步骤完成复杂任务、写文章大纲并展开时调用",
            "parameters": {"type": "object", "properties": {"task": {"type": "string", "description": "需要规划并执行的任务"}}, "required": ["task"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_structured",
            "description": "结构化输出：从文本中按指定字段提取数据，输出格式化 JSON。当用户要求提取结构化信息、抽取字段、生成 JSON 数据时调用",
            "parameters": {"type": "object", "properties": {"text": {"type": "string", "description": "要提取信息的源文本"}, "fields": {"type": "string", "description": "逗号分隔的字段名，如：姓名,年龄,城市"}}, "required": ["text", "fields"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_multi_agent",
            "description": "多 Agent 协作演示：Planner（规划）→ Executor（执行）→ Critic（评审）三角色分工协作。当用户提到多 Agent、多智能体、Planner-Executor-Critic、角色协作、分工协作时调用。适合复杂任务分解、多步骤产出、需要质量把关的场景",
            "parameters": {"type": "object", "properties": {"task": {"type": "string", "description": "需要多 Agent 协作完成的复杂任务"}}, "required": ["task"]},
        },
    },
]


# 实际注册的工具数量（自动同步，banner / Web 标题 / README 都引用它，避免文案漂移）
TOOL_COUNT = len(TOOL_SCHEMAS)


# ============================================================
# 工具注册工厂（CLI 与 Web UI 共用，消除重复的闭包实现）
# ============================================================

# ReAct 演示时用不到、且会形成递归的内部演示工具
_REACT_EXCLUDE = {
    "demo_stream_chat", "demo_reasoning", "demo_native_reasoning",
    "demo_react", "demo_code_run", "reflexion_answer",
    "demo_vision", "plan_task", "summarize_history",
    "demo_few_shot", "extract_structured",
}


def clean_react_tool_map(tool_map: dict) -> dict:
    """给 ReAct 演示用的"干净"工具集：去掉内部演示工具，但保留 search_knowledge（它调用 RAG，不递归）"""
    cleaned = {k: v for k, v in tool_map.items() if k not in _REACT_EXCLUDE}
    if "search_knowledge" in tool_map:
        cleaned["search_knowledge"] = tool_map["search_knowledge"]
    return cleaned


def build_tool_map(client, get_rag, test_image: str = "_test_chart.png",
                  get_history=None, set_history=None) -> dict:
    """
    构建完整的工具映射（函数名 → 可调用对象）。
    get_rag: 无参可调用，返回 RAGKnowledgeBase 实例或 None（支持 Web 端延迟加载）。
    test_image: demo_vision 的默认测试图片路径。
    get_history / set_history: 可选，传入后一并注册 summarize_history（需读写 App 的 history）。
    """
    tool_map: dict = {}

    # ---------- 1. 模块级纯工具（tools.py）直接引用 ----------
    tool_map["get_weather"] = get_weather
    tool_map["calculator"] = calculator
    tool_map["web_search"] = web_search
    tool_map["file_read"] = file_read
    tool_map["file_write"] = file_write
    tool_map["sql_query"] = sql_query
    tool_map["run_terminal"] = run_terminal
    tool_map["human_confirm"] = human_confirm
    tool_map["demo_code_run"] = run_code

    # ---------- 2. 闭包工具（绑定 client / get_rag / test_image）----------

    def demo_stream_chat(topic: str) -> str:
        """流式对话演示：边生成边逐字输出"""
        lines = []
        for token in client.chat_stream([{"role": "user", "content": topic}]):
            lines.append(token)
        return "已在上方以流式方式直接展示完整输出。" if lines else "（无流式输出）"

    def demo_reasoning(question: str) -> str:
        return client.chat_with_reasoning([{"role": "user", "content": question}])

    def demo_native_reasoning(question: str) -> str:
        return client.chat_with_native_reasoning([{"role": "user", "content": question}])

    def demo_react(question: str) -> str:
        """ReAct 演示：用一份不含内部演示工具的"干净"工具集让模型边想边做"""
        react_tools = clean_react_tool_map(tool_map)
        return client.chat_with_react(
            [{"role": "user", "content": question}],
            tool_map=react_tools,
        )

    def search_knowledge(question: str) -> str:
        rag = get_rag()
        if rag is None:
            return "知识库不可用（初始化失败），请检查 PDF 与 embedding 模型。"
        return rag.answer(question, client)

    def reflexion_answer(question: str) -> str:
        return client.chat_with_reflexion([{"role": "user", "content": question}])

    def demo_vision(image_path: str = "", question: str = "请描述这张图片的内容") -> str:
        path = image_path.strip() or test_image
        if not path or not os.path.exists(path):
            return f"图片不存在：{path}。可改为不填路径使用测试图，或提供正确的图片路径。"
        return client.analyze_image(path, question)

    def plan_task(task: str) -> str:
        return client.plan_and_execute(task)

    def extract_structured(text: str, fields: str) -> str:
        return client.structured_extract(text, fields)

    def demo_few_shot(question: str, task_type: str = "sentiment") -> str:
        """Prompt Engineering 演示：Few-shot 让模型理解任务模式"""
        template = FEW_SHOT_TEMPLATES.get(task_type, FEW_SHOT_TEMPLATES["sentiment"])
        example_blocks = []
        for i, (inp, out) in enumerate(template["examples"], 1):
            example_blocks.append(f"[示例 {i}]\n输入：{inp}\n输出：{out}")
        few_shot_prompt = (
            f"你是一个擅长 Prompt Engineering 的助手。\n"
            f"通过以下示例，你需要理解任务模式，然后对新问题给出答案。\n\n"
            + "\n\n".join(example_blocks)
            + f"\n\n--- 请理解上述模式，然后回答新问题 ---\n\n"
            + f"输入：{question}\n输出："
        )
        response = client.chat([{"role": "user", "content": few_shot_prompt}], temperature=0.1)
        return (
            f"=== Few-shot 提示词设计 ===\n"
            f"  任务类型: {template['name']}\n"
            f"  给了模型 {len(template['examples'])} 个「输入→输出」示例\n"
            f"=== 模型回答 ===\n{response}"
        )

    def run_multi_agent_tool(task: str) -> str:
        return run_multi_agent(client, task)

    tool_map["demo_stream_chat"] = demo_stream_chat
    tool_map["demo_reasoning"] = demo_reasoning
    tool_map["demo_native_reasoning"] = demo_native_reasoning
    tool_map["demo_react"] = demo_react
    tool_map["search_knowledge"] = search_knowledge
    tool_map["reflexion_answer"] = reflexion_answer
    tool_map["demo_vision"] = demo_vision
    tool_map["plan_task"] = plan_task
    tool_map["extract_structured"] = extract_structured
    tool_map["demo_few_shot"] = demo_few_shot
    tool_map["run_multi_agent"] = run_multi_agent_tool

    # summarize_history 需要读写 App 自身的 history，仅在提供访问器时注册
    if get_history is not None:
        tool_map["summarize_history"] = make_summarize_history(
            client, get_history, set_history
        )

    return tool_map


def make_summarize_history(client, get_history, set_history) -> callable:
    """
    生成一个 summarize_history 工具闭包。
    get_history / set_history 用于在 App 状态上读写 history（CLI / Web 各自的存储不同）。
    """
    def summarize_history() -> str:
        old = get_history()
        old_len = len(old)
        old_chars = sum(len(m["content"]) for m in old)
        new = client.summarize_messages(old, keep_recent=4)
        set_history(new)
        new_len = len(new)
        new_chars = sum(len(m["content"]) for m in new)
        return (
            f"✅ 对话历史摘要压缩完成！\n"
            f"  消息条数: {old_len} → {new_len}\n"
            f"  字符总数: {old_chars} → {new_chars}\n"
            f"  保留了最近 4 条完整消息，较早历史已浓缩为一段摘要"
        )

    return summarize_history
