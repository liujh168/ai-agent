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

import logging
import os

from ai_agent.multi_agent import run_multi_agent
from ai_agent.tooling import ToolExecutor, ToolPolicy
from ai_agent.tools import (
    FEW_SHOT_TEMPLATES,
    calculator,
    file_read,
    file_write,
    get_weather,
    human_confirm,
    run_code,
    run_terminal,
    sql_query,
    web_search,
)
from ai_agent.tools.wordpress import wp_publish as _wp_publish_impl

log = logging.getLogger(__name__)

# ============================================================
# 系统提示词（自然语言 → 工具路由的"真相源"）
# ============================================================
# BASE_SYSTEM_PROMPT: 原始静态版本，不包含任何 Skill 信息
# build_system_prompt(): 运行时合成完整版本（base + Skill 列表简述）

BASE_SYSTEM_PROMPT = (
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
    "- 用户要求【投稿/发布到博客/发 WordPress 文章/上传到博客】时，调用 wp_publish（优先传 md_file 文件路径，自动解析 Markdown→HTML 和 Front Matter；可选 status 默认 draft 草稿）；\n"
    "- 用户要求【规划任务/拆解任务/分步骤完成复杂任务/写大纲并展开/规划并写一篇文章】时，调用 plan_task；\n"
    "- 用户要求【提取结构化信息/抽取字段/生成 JSON 数据/结构化输出】时，调用 extract_structured；\n"
    "- 执行敏感/破坏性操作前（删除覆盖文件、写入重要数据、SQL DDL/DML、终端写入命令），必须先调用 human_confirm 让用户确认；\n"
    "- run_terminal 的安全闸门是「默认禁用 + 每次执行前人工确认」；其内部的关键词拦截只是辅助提示，"
    "可被命令拼接、编码等手法绕过，不能当作防线——敏感命令一律先 human_confirm 让用户确认；\n"
    "- sql_query demo 模式只允许 SELECT/PRAGMA/EXPLAIN/WITH，DROP/DELETE/UPDATE 会被阻断；\n"
    "- calculator 使用 AST 白名单，不接受 eval/exec/__import__ 等任意代码；\n"
    "- 用户询问【天气】时，调用 get_weather；\n"
    "- 用户要求【计算】时，调用 calculator；\n"
    "- 普通聊天直接回答，不需要调用工具。"
)

# 向后兼容别名：旧代码引用 SYSTEM_PROMPT 时行为不变（没有 Skill 增强）
SYSTEM_PROMPT = BASE_SYSTEM_PROMPT


def build_system_prompt(skill_registry=None) -> str:
    """
    运行时合成完整 system prompt：BASE_SYSTEM_PROMPT + 已加载 Skill 的简述列表。
    Skill 的详细 instructions 只在 Skill 激活时才注入（AgentRuntime 激活时替换 system prompt），
    这里只列出名字和简述，节省 token。
    skill_registry: SkillRegistry 实例，None 或空则返回纯 BASE_SYSTEM_PROMPT。
    """
    prompt = BASE_SYSTEM_PROMPT
    if skill_registry is not None and skill_registry.count > 0:
        skill_section = "\n\n## 可用的专业能力（Skills）\n\n"
        skill_section += "下面这些是预定义的复合工作流（多步编排 + 专属 Prompt），"
        skill_section += "当用户请求匹配时会自动激活。\n\n"
        for s in skill_registry.list():
            kws = ", ".join(s.trigger_keywords[:3])
            skill_section += f"- 【{s.name}】{s.description}（关键词: {kws}）\n"
        prompt += skill_section
    return prompt


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
                    "sql": {
                        "type": "string",
                        "description": "SQL 语句，如 SELECT * FROM users WHERE city='北京'",
                    },
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
                    "command": {
                        "type": "string",
                        "description": "要执行的命令，如 Get-Date、dir、python --version",
                    },
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
                    "question": {
                        "type": "string",
                        "description": "需要用 few-shot 方式回答的问题，如做情感分类、翻译、信息抽取等",
                    },
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
            "parameters": {
                "type": "object",
                "properties": {"topic": {"type": "string", "description": "流式演示的话题"}},
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "demo_reasoning",
            "description": "演示思维链推理（提示词引导，逐步展示推理过程），适合逻辑题、数学题",
            "parameters": {
                "type": "object",
                "properties": {"question": {"type": "string", "description": "需要推理的问题"}},
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "demo_native_reasoning",
            "description": "演示原生推理模式（通过 reasoning_effort 参数触发模型内置思考，qwen3 等模型支持）",
            "parameters": {
                "type": "object",
                "properties": {"question": {"type": "string", "description": "需要推理的问题"}},
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "demo_react",
            "description": "演示 ReAct（Reasoning + Acting）模式：让模型边想边做，交替输出 Thought→Action→Action Input，执行后把 Observation 喂回，直到 Final Answer。教学用途：展示 Agent 的思考轨迹和工具选择过程，与 Function Calling 黑盒模式形成对比",
            "parameters": {
                "type": "object",
                "properties": {"question": {"type": "string", "description": "需要 ReAct 方式回答的问题"}},
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "demo_code_run",
            "description": "代码执行 Agent：执行 Python 代码并返回输出/错误。当用户要求写代码运行、编程计算题、或想验证代码时调用。返回错误时可修正后再次调用",
            "parameters": {
                "type": "object",
                "properties": {"code": {"type": "string", "description": "要执行的 Python 代码"}},
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": "RAG 知识库问答：基于项目内 PDF/MD/TXT 资料检索并回答。当用户询问 AI Agent 概念、原理、资料内容时调用",
            "parameters": {
                "type": "object",
                "properties": {"question": {"type": "string", "description": "需要查询的问题"}},
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reflexion_answer",
            "description": "反思与自我修正：先作答→自我批评→修正，多轮迭代提升答案质量。适合需要高准确率的复杂问题",
            "parameters": {
                "type": "object",
                "properties": {"question": {"type": "string", "description": "需要严谨回答的问题"}},
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "demo_vision",
            "description": "图像识别：分析本地图片（描述内容/OCR/理解图表）。不填 image_path 时使用自动生成的测试图表",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_path": {"type": "string", "description": "图片路径（可选，默认测试图）"},
                    "question": {"type": "string", "description": "针对图片的问题"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_task",
            "description": "任务规划分解（Planner-Executor）：把复杂任务拆解为有序子任务并逐个执行完成。当用户要求规划任务、拆解任务、分步骤完成复杂任务、写文章大纲并展开时调用",
            "parameters": {
                "type": "object",
                "properties": {"task": {"type": "string", "description": "需要规划并执行的任务"}},
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_structured",
            "description": "结构化输出：从文本中按指定字段提取数据，输出格式化 JSON。当用户要求提取结构化信息、抽取字段、生成 JSON 数据时调用",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "要提取信息的源文本"},
                    "fields": {"type": "string", "description": "逗号分隔的字段名，如：姓名,年龄,城市"},
                },
                "required": ["text", "fields"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_multi_agent",
            "description": "多 Agent 协作演示：Planner（规划）→ Executor（执行）→ Critic（评审）三角色分工协作。当用户提到多 Agent、多智能体、Planner-Executor-Critic、角色协作、分工协作时调用。适合复杂任务分解、多步骤产出、需要质量把关的场景",
            "parameters": {
                "type": "object",
                "properties": {"task": {"type": "string", "description": "需要多 Agent 协作完成的复杂任务"}},
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wp_publish",
            "description": "把 Markdown 文件或 HTML 内容发布到 WordPress 博客。当用户要求投稿、发布到博客、发 WordPress 文章、上传到博客时调用。推荐优先用 md_file 传文件路径（工具自动解析 Front Matter + Markdown→HTML + 分类/标签推断）；也可直接传 title + content_html（HTML 格式正文）",
            "parameters": {
                "type": "object",
                "properties": {
                    "md_file": {
                        "type": "string",
                        "description": "本地 Markdown 文件路径（推荐）。工具会自动解析 Front Matter 和 Markdown→HTML",
                    },
                    "title": {
                        "type": "string",
                        "description": "文章标题（纯文本）。md_file 模式下会从文件自动提取",
                    },
                    "content_html": {
                        "type": "string",
                        "description": "文章正文（HTML 格式，WordPress 原生支持）",
                    },
                    "status": {
                        "type": "string",
                        "description": "发布状态。默认 draft（草稿，人工审阅后手动发布）。用户明确说'直接发布'时才用 publish",
                        "enum": ["draft", "publish", "pending", "private", "future"],
                    },
                    "categories": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "分类名列表（不存在会自动创建）",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "标签名列表（不存在会自动创建）",
                    },
                    "excerpt": {
                        "type": "string",
                        "description": "文章摘要（不填自动生成）",
                    },
                },
                "required": [],
            },
        },
    },
]


# 实际注册的工具数量（自动同步，banner / Web 标题 / README 都引用它，避免文案漂移）
# 注意：这是**本地工具**数量。MCP 远端工具在运行时通过 build_tool_map(mcp_manager=...) 追加
TOOL_COUNT = len(TOOL_SCHEMAS)


def get_tool_schemas(mcp_manager=None) -> list[dict]:
    """运行时合成最终的 TOOL_SCHEMAS（本地 + 可选 MCP 远端）"""
    schemas = list(TOOL_SCHEMAS)
    if mcp_manager is not None and mcp_manager.total_tools > 0:
        schemas.extend(mcp_manager.tool_schemas)
    return schemas


# ============================================================
# 工具注册工厂（CLI 与 Web UI 共用，消除重复的闭包实现）
# ============================================================

# ReAct 演示时用不到、且会形成递归的内部演示工具
_REACT_EXCLUDE = {
    "demo_stream_chat",
    "demo_reasoning",
    "demo_native_reasoning",
    "demo_react",
    "demo_code_run",
    "reflexion_answer",
    "demo_vision",
    "plan_task",
    "summarize_history",
    "demo_few_shot",
    "extract_structured",
}


def clean_react_tool_map(tool_map: dict) -> dict:
    """给 ReAct 演示用的"干净"工具集：去掉内部演示工具，但保留 search_knowledge（它调用 RAG，不递归）"""
    cleaned = {k: v for k, v in tool_map.items() if k not in _REACT_EXCLUDE}
    if "search_knowledge" in tool_map:
        cleaned["search_knowledge"] = tool_map["search_knowledge"]
    return cleaned


def build_tool_map(
    client,
    get_rag,
    test_image: str = "_test_chart.png",
    get_history=None,
    set_history=None,
    mcp_manager=None,
    workspace_root: str | None = None,
) -> dict:
    """
    构建完整的工具映射（函数名 → 可调用对象）。
    get_rag: 无参可调用，返回 RAGKnowledgeBase 实例或 None（支持 Web 端延迟加载）。
    test_image: demo_vision 的默认测试图片路径。
    get_history / set_history: 可选，传入后一并注册 summarize_history（需读写 App 的 history）。
    mcp_manager: 可选 MCPManager，传入后自动追加远端 MCP 工具。
    """
    tool_map: dict = {}
    workspace_root = workspace_root or os.getcwd()

    tool_map.update(_build_workspace_tools(workspace_root))
    tool_map.update(_build_demo_tools(client, get_rag, test_image, workspace_root, tool_map))
    tool_map.update(_build_wp_tools(workspace_root))

    # summarize_history 需要读写 App 自身的 history，仅在提供访问器时注册
    if get_history is not None:
        tool_map["summarize_history"] = make_summarize_history(client, get_history, set_history)

    # MCP 远端工具（可选，追加在本地工具之后）
    if mcp_manager is not None:
        tool_map.update(mcp_manager.make_callables())

    return tool_map


def _build_workspace_tools(workspace_root: str) -> dict:
    """模块级纯工具 + 绑定 workspace_root 的文件/SQL 包装"""
    tools: dict = {
        "get_weather": get_weather,
        "calculator": calculator,
        "web_search": web_search,
    }

    def workspace_file_read(path: str, max_chars: int = 3000) -> str:
        return file_read(path, max_chars=max_chars, workspace_root=workspace_root)

    def workspace_file_write(path: str, content: str, mode: str = "write") -> str:
        return file_write(path, content, mode=mode, workspace_root=workspace_root)

    workspace_file_read.__name__ = "file_read"
    workspace_file_write.__name__ = "file_write"
    workspace_file_read.__doc__ = file_read.__doc__
    workspace_file_write.__doc__ = file_write.__doc__
    tools["file_read"] = workspace_file_read
    tools["file_write"] = workspace_file_write

    def workspace_sql_query(db_path: str, sql: str, demo_seed: bool = True) -> str:
        return sql_query(db_path, sql, demo_seed=demo_seed, workspace_root=workspace_root)

    workspace_sql_query.__name__ = "sql_query"
    workspace_sql_query.__doc__ = sql_query.__doc__
    tools["sql_query"] = workspace_sql_query
    tools["run_terminal"] = run_terminal
    tools["human_confirm"] = human_confirm
    tools["demo_code_run"] = run_code
    return tools


def _build_demo_tools(client, get_rag, test_image: str, workspace_root: str, tool_map: dict) -> dict:
    """绑定 client / get_rag / test_image 的演示闭包工具。demo_react 在运行时读取最终 tool_map。"""

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
        return client.analyze_image(path, question, workspace_root=workspace_root)

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
            "你是一个擅长 Prompt Engineering 的助手。\n"
            "通过以下示例，你需要理解任务模式，然后对新问题给出答案。\n\n"
            + "\n\n".join(example_blocks)
            + "\n\n--- 请理解上述模式，然后回答新问题 ---\n\n"
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
        # Executor 可用的工具集：复用 ReAct 的"干净"过滤（剔除内部演示工具），
        # 再去掉 run_multi_agent 自身，避免 Executor 递归触发多 Agent 流水线
        executor_map = clean_react_tool_map(tool_map)
        executor_map.pop("run_multi_agent", None)
        executor_schemas = [
            s for s in TOOL_SCHEMAS if s["function"]["name"] in executor_map
        ]
        return run_multi_agent(client, task, tools=executor_schemas, tool_map=executor_map)

    return {
        "demo_stream_chat": demo_stream_chat,
        "demo_reasoning": demo_reasoning,
        "demo_native_reasoning": demo_native_reasoning,
        "demo_react": demo_react,
        "search_knowledge": search_knowledge,
        "reflexion_answer": reflexion_answer,
        "demo_vision": demo_vision,
        "plan_task": plan_task,
        "extract_structured": extract_structured,
        "demo_few_shot": demo_few_shot,
        "run_multi_agent": run_multi_agent_tool,
    }


def _build_wp_tools(workspace_root: str) -> dict:
    def wp_publish_tool(
        md_file: str = "",
        title: str = "",
        content_html: str = "",
        status: str = "draft",
        categories: list | None = None,
        tags: list | None = None,
        excerpt: str = "",
    ) -> str:
        return _wp_publish_impl(
            md_file=md_file,
            title=title,
            content_html=content_html,
            status=status,
            categories=categories,
            tags=tags,
            excerpt=excerpt,
            cfg_workspace_root=workspace_root,
        )

    return {"wp_publish": wp_publish_tool}


def build_tool_executor(
    tool_map: dict,
    *,
    allow_write: bool = True,
    allow_execute: bool = False,
    allow_mcp: bool = True,
    allow_network: bool = True,
    confirm_write: bool = False,
    confirm_execute: bool = True,
    confirm_mcp: bool = True,
    confirm_network: bool = False,
    mcp_allowlist: tuple[str, ...] = (),
    confirm=None,
) -> ToolExecutor:
    """Build the policy-enforcing executor used by CLI and Web."""
    requested_mcp = frozenset(item for item in mcp_allowlist if item)
    policy = ToolPolicy(
        allow_write=allow_write,
        allow_execute=allow_execute,
        allow_mcp=allow_mcp,
        allow_network=allow_network,
        confirm_write=confirm_write,
        confirm_execute=confirm_execute,
        confirm_mcp=confirm_mcp,
        confirm_network=confirm_network,
        mcp_allowlist=requested_mcp or None,
    )
    return ToolExecutor(tool_map, policy=policy, confirm=confirm)


def filter_tool_schemas(tool_schemas: list[dict], policy: ToolPolicy) -> list[dict]:
    """Hide schemas that the executor would categorically deny."""
    filtered = []
    for schema in tool_schemas:
        name = schema.get("function", {}).get("name", "")
        risk = policy.classify(name)
        if risk == "write" and not policy.allow_write:
            continue
        if risk == "execute" and not policy.allow_execute:
            continue
        if risk == "network" and not policy.allow_network:
            continue
        if risk == "mcp":
            if not policy.allow_mcp:
                continue
            if policy.mcp_allowlist is not None and name not in policy.mcp_allowlist:
                continue
        filtered.append(schema)
    return filtered


def validate_tool_schemas(tool_schemas: list[dict], tool_map: dict | None = None) -> None:
    """
    启动时校验工具 schema 是否为合法的 OpenAI function calling 格式：
    type 必须是 "function"，function 下必须有非空 name/description，
    parameters 必须是 object 类型、properties 必须是 dict，
    required 中声明的字段必须存在于 properties 中，工具名不得重复。
    传入 tool_map 时额外检查：每个 schema 声明的工具都有对应的 callable 注册。
    不合法时抛出 ValueError，指明具体工具名与问题。
    """
    problems: list[str] = []
    seen: set[str] = set()
    for schema in tool_schemas:
        name = "<未命名>"
        if isinstance(schema, dict) and isinstance(schema.get("function"), dict):
            name = schema["function"].get("name") or name
        prefix = f"工具 {name!r}"
        if not isinstance(schema, dict) or schema.get("type") != "function":
            problems.append(f"{prefix}：schema 必须是 type='function' 的对象")
            continue
        func = schema.get("function")
        if not isinstance(func, dict):
            problems.append(f"{prefix}：缺少 function 字段")
            continue
        if not isinstance(func.get("name"), str) or not func["name"].strip():
            problems.append(f"{prefix}：function.name 缺失或为空")
        elif func["name"] in seen:
            problems.append(f"{prefix}：工具名重复定义")
        else:
            seen.add(func["name"])
        if not isinstance(func.get("description"), str) or not func["description"].strip():
            problems.append(f"{prefix}：function.description 缺失或为空")
        params = func.get("parameters")
        if not isinstance(params, dict) or params.get("type") != "object":
            problems.append(f"{prefix}：parameters 必须是 type='object' 的 JSON Schema")
            continue
        properties = params.get("properties", {})
        if not isinstance(properties, dict):
            problems.append(f"{prefix}：parameters.properties 必须是对象")
            continue
        required = params.get("required", [])
        if not isinstance(required, list):
            problems.append(f"{prefix}：parameters.required 必须是数组")
            continue
        for field in required:
            if field not in properties:
                problems.append(f"{prefix}：required 中的字段 {field!r} 未在 properties 中定义")
    if tool_map is not None:
        for name in seen:
            if name not in tool_map:
                problems.append(f"工具 {name!r}：schema 已声明但未注册对应 callable")
            elif not callable(tool_map[name]):
                problems.append(f"工具 {name!r}：注册对象不可调用")
    if problems:
        raise ValueError("工具 schema 校验失败：\n- " + "\n- ".join(problems))


def make_summarize_history(client, get_history, set_history) -> callable:
    """
    生成一个 summarize_history 工具闭包。
    get_history / set_history 用于在 App 状态上读写 history（CLI / Web 各自的存储不同）。
    """

    def summarize_history() -> str:
        old = get_history()
        old_len = len(old)
        old_chars = sum(len(m.get("content") or "") for m in old)
        new = client.summarize_messages(old, keep_recent=4)
        set_history(new)
        new_len = len(new)
        new_chars = sum(len(m.get("content") or "") for m in new)
        return (
            f"✅ 对话历史摘要压缩完成！\n"
            f"  消息条数: {old_len} → {new_len}\n"
            f"  字符总数: {old_chars} → {new_chars}\n"
            f"  保留了最近 4 条完整消息，较早历史已浓缩为一段摘要"
        )

    return summarize_history


def compress_and_persist_history(client, history: list[dict], store, session_id: str | None) -> list[dict]:
    """摘要压缩 history，并把压缩结果重写进持久化存储（CLI / Web 共用）。返回压缩后的 history。"""
    compressed = client.summarize_messages(history, keep_recent=4)
    if session_id:
        store.clear_messages(session_id)
        # 重写当前 history（去掉 system）
        for message in compressed[1:]:
            store.append(
                session_id,
                message["role"],
                message.get("content", ""),
                message.get("tool_calls"),
            )
    return compressed


def ensure_test_image(path: str) -> bool:
    """生成图像识别演示用的测试图表（已存在则跳过）。返回 True 表示本次新建。"""
    if os.path.exists(path):
        log.debug("测试图片已存在，跳过生成: %s", path)
        return False
    try:
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (640, 400), "white")
        draw = ImageDraw.Draw(img)
        data = [("AI Agent", 120), ("RAG", 200), ("Agentic", 160), ("Multi-Agent", 90)]
        x = 60
        for label, h in data:
            draw.rectangle([x, 400 - h, x + 120, 400], fill="steelblue")
            draw.text((x + 20, 400 - h - 25), label, fill="black")
            x += 140
        draw.text((20, 20), "2026 AI Field Attention Survey", fill="black")
        img.save(path)
        return True
    except (ImportError, OSError, ValueError) as exc:
        log.warning("测试图片生成失败: %s", exc)
        return False
