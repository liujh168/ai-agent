"""
本地 AI Agent 交互式演示 — 主入口
================================
架构：
  ai_agent/client.py   → LMStudioClient（与 LM Studio API 直接交互）
  ai_agent/rag.py      → RAGKnowledgeBase（PDF/MD/TXT 向量化检索）
  ai_agent/tools/      → 10 个模块级工具函数 + 9 个 TOOL schema
  main.py              → AgentDemoApp（初始化 + 工具注册 + 交互循环）

运行：python main.py  或  python -m ai_agent
依赖：pip install requests openai pypdf pillow python-dotenv
可选：pip install ddgs（未配置 BOCHA_API_KEY 时的 DuckDuckGo 降级）
      在 .env 中设置 BOCHA_API_KEY=xxx（博查 AI 搜索，国内稳定）
"""

# ---------- 加载 .env 环境变量 ----------
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # 未安装 python-dotenv 时跳过（仍可通过系统环境变量配置）

from ai_agent.client import LMStudioClient
from ai_agent.rag import RAGKnowledgeBase
from ai_agent.persistence import ChatStore, DEFAULT_DB_PATH
from ai_agent.multi_agent import run_multi_agent
from ai_agent.tools import (
    run_code, get_weather, calculator, web_search,
    file_read, file_write, sql_query, run_terminal, human_confirm,
    WEATHER_TOOL, CALCULATOR_TOOL, WEB_SEARCH_TOOL,
    FILE_READ_TOOL, FILE_WRITE_TOOL, SQL_QUERY_TOOL,
    RUN_TERMINAL_TOOL, HUMAN_CONFIRM_TOOL,
    DEMO_FEW_SHOT_TOOL, SUMMARIZE_TOOL,
    FEW_SHOT_TEMPLATES,
)

import os
import logging
import time

# ---------- 日志配置 ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("Agent")


# ============================================================
# 交互式 Agent 演示
# ============================================================

class AgentDemoApp:
    """交互式 Agent 演示应用：初始化环境、注册工具并运行自然语言交互循环。"""

    # RAG 知识库数据源：支持 PDF / MD / TXT 三种格式
    # 不存在的文件会在 build 时自动跳过
    RAG_FILES = [
        "data/AI-Agents-in-Depth-zh-CN.pdf",
        "data/Hello-Agents-V1.0.3-20260717.pdf",
        "docs/通用Agent时代价值迁移.md",
    ]
    TEST_IMAGE = "_test_chart.png"
    EXIT_WORDS = ("exit", "quit", "q", "退出")

    # 长对话压缩阈值：history 总字符数超这个值就自动摘要压缩
    HISTORY_MAX_CHARS = 5000

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
        "- 执行敏感/破坏性操作前（删除覆盖文件、写入重要数据、SQL DDL/DML、终端写入命令），必须先调用 human_confirm 让用户确认；\n"
        "- run_terminal 内部有关键词安全拦截（Remove-Item/Set-Content/Stop-Computer 等 PowerShell 原生命令会被阻断），若被拦截请改用 human_confirm 或普通文件操作工具；\n"
        "- sql_query demo 模式只允许 SELECT/PRAGMA/EXPLAIN/WITH，DROP/DELETE/UPDATE 会被阻断；\n"
        "- calculator 使用 AST 白名单，不接受 eval/exec/__import__ 等任意代码；\n"
        "- 用户询问【天气】时，调用 get_weather；\n"
        "- 用户要求【计算】时，调用 calculator；\n"
        "- 普通聊天直接回答，不需要调用工具。"
    )

    TOOLS = [
        WEATHER_TOOL, CALCULATOR_TOOL, WEB_SEARCH_TOOL,
        FILE_READ_TOOL, FILE_WRITE_TOOL, SQL_QUERY_TOOL,
        RUN_TERMINAL_TOOL, HUMAN_CONFIRM_TOOL,
        DEMO_FEW_SHOT_TOOL, SUMMARIZE_TOOL,
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

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.client = LMStudioClient()
        self.rag: RAGKnowledgeBase | None = None
        self.tool_map: dict = {}
        self.history: list[dict] = []
        # ---------- 对话持久化 ----------
        self.store = ChatStore(db_path)
        self.session_id: str | None = None

    # ---------- 初始化步骤 ----------

    def check_server(self) -> None:
        """检测 LM Studio 服务与模型加载状态"""
        print("=" * 50)
        print("检测 LM Studio 服务状态...")
        status = self.client.check_server_status()
        print(f"  服务运行: {status['server_running']}")
        print(f"  模型已加载: {status['model_loaded']}")
        if status["model_name"]:
            print(f"  模型名称: {status['model_name']}")
        if status["error"]:
            print(f"  错误: {status['error']}")
        print("=" * 50)

    def _build_knowledge_base(self) -> None:
        """构建 RAG 知识库索引（首次向量化较慢，之后走缓存）"""
        print("\n构建 RAG 知识库索引（首次需向量化全部片段，可能较慢）...")
        try:
            self.rag = RAGKnowledgeBase()
            n_chunks = self.rag.build(self.RAG_FILES)
            print(f"[OK] 知识库就绪：{n_chunks} 个文本片段")
        except Exception as e:
            print(f"[WARN] RAG 初始化失败（{e}），知识库问答将不可用")
            self.rag = None

    def _create_test_image(self) -> None:
        """用 Pillow 生成一张测试图表，供图像识别演示（已存在则跳过）"""
        if os.path.exists(self.TEST_IMAGE):
            log.debug("测试图片已存在，跳过生成")
            return
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
            draw.text((20, 20), "2026 年 AI 领域关注度调查", fill="black")
            img.save(self.TEST_IMAGE)
            print(f"[OK] 已生成测试图片 {self.TEST_IMAGE} 供图像识别演示")
        except Exception as e:
            print(f"[WARN] 测试图片生成失败（{e}），图像识别演示将不可用")

    def _register_tools(self) -> None:
        """注册演示工具（闭包绑定 client / rag / 测试图片）"""
        # ---------- 1. 模块级纯工具（tools.py）直接引用 ----------
        self.tool_map["get_weather"] = get_weather
        self.tool_map["calculator"] = calculator
        self.tool_map["web_search"] = web_search
        self.tool_map["file_read"] = file_read
        self.tool_map["file_write"] = file_write
        self.tool_map["sql_query"] = sql_query
        self.tool_map["run_terminal"] = run_terminal
        self.tool_map["human_confirm"] = human_confirm
        self.tool_map["demo_code_run"] = run_code

        # ---------- 2. 闭包工具（绑定 self.client / self.rag / self.TEST_IMAGE）----------

        def demo_stream_chat(topic: str) -> str:
            """流式对话演示：边生成边逐字输出"""
            print("\n[流式演示输出] ", end="", flush=True)
            for token in self.client.chat_stream([{"role": "user", "content": topic}]):
                print(token, end="", flush=True)
            print("\n[流式输出结束]")
            return "已在上方以流式方式直接展示完整输出。"

        def demo_reasoning(question: str) -> str:
            return self.client.chat_with_reasoning([{"role": "user", "content": question}])

        def demo_native_reasoning(question: str) -> str:
            return self.client.chat_with_native_reasoning([{"role": "user", "content": question}])

        def demo_react(question: str) -> str:
            """ReAct 演示：用完整工具集让模型边想边做，推理轨迹可见"""
            # 为 ReAct 准备一份不含内部演示工具的"干净"工具集
            # （避免模型在 ReAct 内部又调用 demo_code_run 等形成递归）
            react_tools = {
                k: v for k, v in self.tool_map.items()
                if k not in ("demo_stream_chat", "demo_reasoning", "demo_native_reasoning",
                              "demo_react", "demo_code_run", "reflexion_answer",
                              "demo_vision", "plan_task", "summarize_history",
                              "demo_few_shot", "extract_structured", "search_knowledge")
            }
            # 但 search_knowledge 对 ReAct 很有价值，让我们保留它（它调用 RAG，不形成递归）
            react_tools = {**react_tools, "search_knowledge": search_knowledge}
            return self.client.chat_with_react(
                [{"role": "user", "content": question}],
                tool_map=react_tools,
            )

        def search_knowledge(question: str) -> str:
            if self.rag is None:
                return "知识库不可用（初始化失败），请检查 PDF 与 embedding 模型。"
            return self.rag.answer(question, self.client)

        def reflexion_answer(question: str) -> str:
            return self.client.chat_with_reflexion([{"role": "user", "content": question}])

        def demo_vision(image_path: str = "", question: str = "请描述这张图片的内容") -> str:
            path = image_path.strip() or self.TEST_IMAGE
            if not path or not os.path.exists(path):
                return f"图片不存在：{path}。可改为不填路径使用测试图，或提供正确的图片路径。"
            return self.client.analyze_image(path, question)

        def plan_task(task: str) -> str:
            return self.client.plan_and_execute(task)

        def summarize_history() -> str:
            old_len = len(self.history)
            old_chars = sum(len(m["content"]) for m in self.history)
            self.history = self.client.summarize_messages(self.history, keep_recent=4)
            new_len = len(self.history)
            new_chars = sum(len(m["content"]) for m in self.history)
            return (
                f"✅ 对话历史摘要压缩完成！\n"
                f"  消息条数: {old_len} → {new_len}\n"
                f"  字符总数: {old_chars} → {new_chars}\n"
                f"  保留了最近 4 条完整消息，较早历史已浓缩为一段摘要"
            )

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
            print(f"\n[Few-shot 提示词结构预览]")
            print(f"  任务类型: {template['name']} — {template['desc']}")
            print(f"  示例数量: {len(template['examples'])} 个")
            print()
            response = self.client.chat([{"role": "user", "content": few_shot_prompt}], temperature=0.1)
            return (
                f"=== Few-shot 提示词设计 ===\n"
                f"  任务类型: {template['name']}\n"
                f"  给了模型 {len(template['examples'])} 个「输入→输出」示例\n"
                f"=== 模型回答 ===\n{response}"
            )

        def extract_structured(text: str, fields: str) -> str:
            return self.client.structured_extract(text, fields)

        def run_multi_agent_tool(task: str) -> str:
            return run_multi_agent(self.client, task)

        # ---------- 注册闭包 ----------
        self.tool_map["demo_stream_chat"] = demo_stream_chat
        self.tool_map["demo_reasoning"] = demo_reasoning
        self.tool_map["demo_native_reasoning"] = demo_native_reasoning
        self.tool_map["demo_react"] = demo_react
        self.tool_map["search_knowledge"] = search_knowledge
        self.tool_map["reflexion_answer"] = reflexion_answer
        self.tool_map["demo_vision"] = demo_vision
        self.tool_map["plan_task"] = plan_task
        self.tool_map["summarize_history"] = summarize_history
        self.tool_map["demo_few_shot"] = demo_few_shot
        self.tool_map["extract_structured"] = extract_structured
        self.tool_map["run_multi_agent"] = run_multi_agent_tool

        log.info("工具注册完成，共 %d 个工具", len(self.tool_map))

    def _print_banner(self) -> None:
        """打印启动 banner 与示例"""
        print()
        print("╔═══════════════════════════════════════════════╗")
        print("║   🤖 本地 AI Agent 交互式演示                 ║")
        print("║   基于 LM Studio · 20 种能力 · 自然语言路由     ║")
        print("╚═══════════════════════════════════════════════╝")
        print("  可用示例：")
        print("  - 介绍一下你自己")
        print("  - 北京天气怎么样？123*456 等于多少？")
        print("  - 用 Few-shot 做情感分类：'这段影评怎么样？'")
        print("  - 用 sql_query 查一下：每个城市有多少用户")
        print("  - 把这段话保存为 poem.txt：床前明月光...")
        print("  - 执行终端命令：Get-Date")
        print("  - 对话太长了，帮我压缩一下历史")
        print("  - 用反思方式解答：三个连续奇数之和为 45")
        print("  - 识别一下测试图表：_test_chart.png 展示的是什么数据？")
        print("  - 帮我规划并写一篇关于 AI Agent 的科普文章")
        print("  - 思维链解答：一个人花8元买鸡，9元卖出，10元买回，11元卖出，赚了多少")
        print("  - 用 ReAct 模式解答：北京现在天气怎么样，天气好的话帮我推荐几本 Agent 好书")
        print("  - 用多 Agent 协作写一篇关于「AI Agent」的科普文章（Planner+Executor+Critic）")
        print("  - exit / quit / 退出  结束程序")
        print()

    def run(self) -> None:
        """启动交互式 Agent 演示"""
        self.check_server()
        self._build_knowledge_base()
        self._create_test_image()
        self._register_tools()
        self._print_banner()

        self.history = [{"role": "system", "content": self.SYSTEM_PROMPT}]

        # ---------- 会话管理 ----------
        self._handle_session_select()

        while True:
            try:
                user_input = input("\n你> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见！")
                break

            if not user_input:
                continue

            # ---------- 持久化命令 ----------
            cmd_result = self._try_session_command(user_input)
            if cmd_result is not None:
                if cmd_result == "quit":
                    print("再见！")
                    break
                continue

            if user_input.lower() in self.EXIT_WORDS:
                print("再见！")
                break

            self.history.append({"role": "user", "content": user_input})
            # 持久化用户消息
            if self.session_id:
                self.store.append(self.session_id, "user", user_input)
                # 自动生成标题（首条用户消息）
                if self.store.count_messages(self.session_id) <= 1:
                    self.store.auto_title(self.session_id, user_input)

            try:
                print("\n助手> ", end="", flush=True)
                t0 = time.time()
                reply = self.client.chat_with_tools(
                    messages=self.history,
                    tools=self.TOOLS,
                    tool_map=self.tool_map,
                )
                dt = time.time() - t0
                print(reply)
                log.info("交互完成 (%.1fs, history=%d msg)", dt, len(self.history))

                self.history.append({"role": "assistant", "content": reply})
                # 持久化助手回复
                if self.session_id:
                    self.store.append(self.session_id, "assistant", reply)

                # ===== 长对话自动摘要压缩 =====
                total_chars = sum(len(m["content"]) for m in self.history)
                if total_chars > self.HISTORY_MAX_CHARS:
                    print(f"\n[系统] 对话历史已达 {total_chars} 字符（阈值 {self.HISTORY_MAX_CHARS}），自动压缩中...")
                    self.history = self.client.summarize_messages(self.history, keep_recent=4)
                    now_chars = sum(len(m["content"]) for m in self.history)
                    print(f"[系统] 压缩完成，剩余 {now_chars} 字符（保留最近 4 条完整消息）")
                    # 压缩后清除持久化，只保存最近完整消息 + 新摘要
                    if self.session_id:
                        self.store.clear_messages(self.session_id)
                        # 重写当前 history（去掉 system）
                        for m in self.history[1:]:
                            self.store.append(self.session_id, m["role"], m.get("content", ""),
                                              m.get("tool_calls"))
            except RuntimeError as e:
                print(f"[ERROR] {e}")
                self.history.pop()

        # 关闭持久化连接
        self.store.close()

    # ---------- 持久化辅助 ----------

    def _handle_session_select(self) -> None:
        """启动时显示会话列表让用户选择，或创建新会话"""
        sessions = self.store.list_sessions(limit=10)
        if sessions:
            print(f"\n📁 已保存 {len(sessions)} 个历史会话：")
            for i, s in enumerate(sessions, 1):
                mode_tag = f"[{s.mode}]" if s.mode != "chat" else ""
                print(f"   {i}. {s.title} {mode_tag} ({self.store.count_messages(s.id)} 条消息)")
            print(f"   n. 新建会话")

            while True:
                choice = input("\n选择会话编号直接恢复，或按 Enter / n 新建: ").strip().lower()
                if choice == "" or choice == "n":
                    break
                try:
                    idx = int(choice) - 1
                    if 0 <= idx < len(sessions):
                        sid = sessions[idx].id
                        saved = self.store.load_messages(sid)
                        print(f"\n✅ 已恢复会话「{sessions[idx].title}」"
                              f"（{len(saved)} 条历史消息）")
                        # 恢复时把 system prompt + 历史消息拼回去
                        self.history = [{"role": "system", "content": self.SYSTEM_PROMPT}] + saved
                        self.session_id = sid
                        return
                except ValueError:
                    pass
                print("   输入无效，请重试")

        # 创建新会话
        sid = self.store.create_session(title="新会话", mode="chat")
        self.session_id = sid
        print(f"\n✨ 已创建新会话（ID: {sid}）")
        print(f"   提示：输入 /sessions 管理历史，输入 /save 手动保存当前标题")

    def _try_session_command(self, text: str) -> str | None:
        """
        识别 / 开头的持久化命令。
        返回 None 表示不是命令；返回 'quit' 表示退出；返回 "" 表示已处理。
        """
        if not text.startswith("/"):
            return None
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd in ("/quit", "/exit"):
            return "quit"
        elif cmd == "/help":
            print("""
可用命令：
  /sessions        列出所有历史会话
  /load <编号>     加载指定会话（按 /sessions 的编号）
  /new             创建新会话
  /save [标题]     保存当前会话（可附带新标题）
  /rename <标题>   重命名当前会话
  /delete <编号>   删除指定会话
  /clear           清空当前会话的消息历史
  /current         显示当前会话信息
  /db              显示数据库路径
  /help            显示此帮助
  /quit            退出程序
""")
            return ""
        elif cmd == "/sessions":
            sessions = self.store.list_sessions(limit=20)
            if not sessions:
                print("  暂无历史会话")
            else:
                print(f"\n📁 历史会话（共 {len(sessions)} 个）：")
                for i, s in enumerate(sessions, 1):
                    cur_tag = " ← 当前" if s.id == self.session_id else ""
                    mode_tag = f"[{s.mode}]" if s.mode != "chat" else ""
                    print(f"   {i}. {s.title} {mode_tag} "
                          f"({self.store.count_messages(s.id)} 条){cur_tag}")
            return ""
        elif cmd == "/load":
            sessions = self.store.list_sessions(limit=20)
            if not sessions:
                print("  暂无历史会话")
                return ""
            try:
                idx = int(arg) - 1
                if 0 <= idx < len(sessions):
                    sid = sessions[idx].id
                    saved = self.store.load_messages(sid)
                    print(f"  ✅ 已加载「{sessions[idx].title}」（{len(saved)} 条消息）")
                    self.history = [{"role": "system", "content": self.SYSTEM_PROMPT}] + saved
                    self.session_id = sid
                    return ""
            except ValueError:
                pass
            print("  用法: /load <编号>（编号来自 /sessions）")
            return ""
        elif cmd == "/new":
            sid = self.store.create_session(title="新会话", mode="chat")
            self.session_id = sid
            self.history = [{"role": "system", "content": self.SYSTEM_PROMPT}]
            print(f"  ✨ 已创建新会话（ID: {sid}）")
            return ""
        elif cmd == "/save":
            if not self.session_id:
                print("  当前无活动会话")
                return ""
            title = arg or self.store.get_session(self.session_id).title
            self.store.rename_session(self.session_id, title)
            print(f"  💾 已保存为「{title}」")
            return ""
        elif cmd == "/rename":
            if not self.session_id:
                print("  当前无活动会话")
                return ""
            if not arg:
                print("  用法: /rename <新标题>")
                return ""
            self.store.rename_session(self.session_id, arg)
            print(f"  ✏️ 已重命名为「{arg}」")
            return ""
        elif cmd == "/delete":
            sessions = self.store.list_sessions(limit=20)
            try:
                idx = int(arg) - 1
                if 0 <= idx < len(sessions):
                    sid = sessions[idx].id
                    confirm = input(f"  确认删除「{sessions[idx].title}」？(y/N): ").strip().lower()
                    if confirm == "y":
                        self.store.delete_session(sid)
                        if sid == self.session_id:
                            self.session_id = None
                            self.history = [{"role": "system", "content": self.SYSTEM_PROMPT}]
                        print(f"  🗑️ 已删除")
                    else:
                        print("  已取消")
                    return ""
            except ValueError:
                pass
            print("  用法: /delete <编号>")
            return ""
        elif cmd == "/clear":
            if self.session_id:
                self.store.clear_messages(self.session_id)
                print("  🧹 已清空当前会话的消息历史（会话本身保留）")
            else:
                print("  当前无活动会话")
            return ""
        elif cmd == "/current":
            if self.session_id:
                s = self.store.get_session(self.session_id)
                print(f"  当前会话: {s.title} [{s.mode}]")
                print(f"  消息数: {self.store.count_messages(self.session_id)}")
            else:
                print("  当前无活动会话")
            return ""
        elif cmd == "/db":
            print(f"  数据库路径: {self.store.db_path}")
            print(f"  绝对路径:   {os.path.abspath(self.store.db_path)}")
            return ""
        else:
            print(f"  未知命令：{cmd}，输入 /help 查看所有命令")
            return ""


if __name__ == "__main__":
    AgentDemoApp().run()
