"""
AI Agent 学习演示 — Gradio Web UI
==================================
与 CLI (main.py) 等价的 Web 演示界面。

UI 结构：
┌──────────────────────────────────────────────────────────┐
│ 🤖 AI Agent 学习演示（基于 LM Studio · 20 种能力）         │
├───────────────┬──────────────────────────────────────────┤
│               │  💬 对话区（Gradio Chatbot）                │
│ 📁 会话列表    │                                          │
│               │  ──── 工具调用轨迹 ────                    │
│ ⚙️ 模式选择    │  🔧 [get_weather] 北京 → 晴天            │
│ · 普通对话     │  🔧 [calculator] 123*456 → 56088         │
│ · ReAct 模式   │  ──── ReAct 推理轨迹 ────                │
│ · 多 Agent     │  💭 Thought: 查天气...                    │
│               │  🔧 Action: get_weather                    │
│ 📝 快捷示例    │  👁️ Observation: 晴天 25°C               │
│ · 北京天气+算  │                                          │
│ · ReAct 推荐   │  🎯 助手回复                             │
│ · 多 Agent写   │                                          │
│               │                                          │
├───────────────┴──────────────────────────────────────────┤
│  [输入框]                         [发送] [清空] [新会话]     │
└──────────────────────────────────────────────────────────┘

运行：python -m ai_agent.web_ui
依赖：pip install gradio（已安装）
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback

try:
    import gradio as gr
except ImportError:
    print("错误：未安装 gradio。请先执行 pip install gradio", file=sys.stderr)
    sys.exit(1)

# 让 main.py 同级的包可被导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_agent.client import LMStudioClient
from ai_agent.rag import RAGKnowledgeBase
from ai_agent.multi_agent import run_multi_agent
from ai_agent.persistence import ChatStore, DEFAULT_DB_PATH
from ai_agent.tools import (
    run_code, get_weather, calculator, web_search,
    file_read, file_write, sql_query, run_terminal, human_confirm,
    WEATHER_TOOL, CALCULATOR_TOOL, WEB_SEARCH_TOOL,
    FILE_READ_TOOL, FILE_WRITE_TOOL, SQL_QUERY_TOOL,
    RUN_TERMINAL_TOOL, HUMAN_CONFIRM_TOOL,
    DEMO_FEW_SHOT_TOOL, SUMMARIZE_TOOL,
    FEW_SHOT_TEMPLATES,
)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ============================================================
# 全局状态（单例）
# ============================================================

SYSTEM_PROMPT = (
    "你是一个交互式演示助手，负责向用户展示 LLM 的各项能力。"
    "根据用户的自然语言请求，选择合适的工具来完成演示：\n"
    "- 用户要求【ReAct/边想边做/思考与行动交替/推理轨迹】时，调用 demo_react；\n"
    "- 用户要求【多 Agent/多智能体/Planner/Executor/Critic/角色协作】时，调用 run_multi_agent；\n"
    "- 用户要求【写代码并运行/编程计算题】时，调用 demo_code_run；\n"
    "- 用户要求【查看文件/读取文件】时，调用 file_read；\n"
    "- 用户要求【创建文件/写入文件】时，调用 file_write；\n"
    "- 用户要求【查数据库/SQL查询】时，调用 sql_query；\n"
    "- 用户询问【AI Agent 概念/资料/知识库】时，调用 search_knowledge；\n"
    "- 用户询问【最新信息/搜索一下】时，调用 web_search；\n"
    "- 用户要求【反思/自我修正】时，调用 reflexion_answer；\n"
    "- 用户询问【天气】时，调用 get_weather；\n"
    "- 用户要求【计算】时，调用 calculator；\n"
    "- 用户要求【Few-shot/示例演示/提示词工程】时，调用 demo_few_shot；\n"
    "- 普通聊天直接回答，不需要调用工具。"
)


class AgentApp:
    """Web UI 后端：包装 Client + Tool_map + 持久化 + 模式切换"""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.client = LMStudioClient()
        self.rag: RAGKnowledgeBase | None = None
        self.store = ChatStore(db_path)
        self.session_id: str | None = None
        self.history: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.mode: str = "chat"  # chat / react / multi_agent
        # 工具调用轨迹 + ReAct 轨迹（用于 UI 实时展示）
        self.trace_buffer: list[str] = []

        # 注册工具（简化版，保留核心演示）
        self.tool_map: dict = {}
        self._register_tools()

    # ---------- 工具注册 ----------

    def _register_tools(self) -> None:
        self.tool_map["get_weather"] = get_weather
        self.tool_map["calculator"] = calculator
        self.tool_map["web_search"] = web_search
        self.tool_map["file_read"] = file_read
        self.tool_map["file_write"] = file_write
        self.tool_map["sql_query"] = sql_query
        self.tool_map["run_terminal"] = run_terminal
        self.tool_map["human_confirm"] = human_confirm
        self.tool_map["demo_code_run"] = run_code

        def search_knowledge(q):
            if self.rag is None:
                return "知识库不可用"
            return self.rag.answer(q, self.client)

        def demo_few_shot(question, task_type="sentiment"):
            template = FEW_SHOT_TEMPLATES.get(task_type, FEW_SHOT_TEMPLATES["sentiment"])
            examples = "\n\n".join(
                f"[示例 {i+1}]\n输入：{inp}\n输出：{out}"
                for i, (inp, out) in enumerate(template["examples"])
            )
            prompt = (
                f"通过以下示例理解任务模式，然后对新问题给出答案。\n\n"
                + examples + f"\n\n输入：{question}\n输出："
            )
            return self.client.chat([{"role": "user", "content": prompt}], temperature=0.1)

        self.tool_map["search_knowledge"] = search_knowledge
        self.tool_map["demo_few_shot"] = demo_few_shot

    # ---------- RAG 初始化 ----------

    def init_rag(self):
        if self.rag is None:
            try:
                self.rag = RAGKnowledgeBase()
                self.rag.build(["docs/通用Agent时代价值迁移.md"])
            except Exception as e:
                print(f"RAG 初始化失败: {e}")

    # ---------- 发送消息（核心入口）----------

    def send(self, user_text: str, history: list[tuple]) -> tuple[str, list[tuple]]:
        """
        Gradio Chatbot 回调。
        history: Gradio 格式 [(user_msg, assistant_msg), ...]
        返回: (trace_text, new_history)
        """
        if not user_text.strip():
            return "", history

        # 初始化 RAG（延迟加载）
        self.init_rag()

        # 创建新会话或使用当前会话
        if self.session_id is None:
            self.session_id = self.store.create_session(title="新会话", mode=self.mode)

        self.trace_buffer = []
        self.trace(f"📥 用户输入: {user_text[:100]}{'…' if len(user_text) > 100 else ''}")

        # 持久化用户消息
        self.store.append(self.session_id, "user", user_text)
        if self.store.count_messages(self.session_id) <= 1:
            self.store.auto_title(self.session_id, user_text)

        # 追加到 history（OpenAI 格式）
        self.history.append({"role": "user", "content": user_text})

        try:
            if self.mode == "react":
                reply = self._react_mode(user_text)
            elif self.mode == "multi_agent":
                reply = self._multi_agent_mode(user_text)
            else:
                reply = self._chat_mode()
        except RuntimeError as e:
            reply = f"[错误] {e}"
            self.history.pop()  # 失败时不保留用户消息
        except Exception as e:
            reply = f"[异常] {type(e).__name__}: {e}\n\n{traceback.format_exc()[:500]}"

        self.history.append({"role": "assistant", "content": reply})
        self.store.append(self.session_id, "assistant", reply)

        # 更新 Gradio history
        history.append((user_text, reply))
        return self.trace_text(), history

    def _chat_mode(self) -> str:
        """普通对话模式 — chat_with_tools，拦截工具调用展示到 trace"""
        self.trace("🤖 模式: 普通对话 + Function Calling")

        # 创建一个带 hook 的 tool_map，拦截调用展示
        hooked_map = {}
        for name, func in self.tool_map.items():
            def make_hook(n, fn):
                def hook(**kwargs):
                    call_str = f"🔧 [{n}] " + ", ".join(f"{k}={v}" for k, v in kwargs.items())
                    self.trace(call_str)
                    t0 = time.time()
                    result = fn(**kwargs)
                    dt = time.time() - t0
                    preview = str(result)[:150]
                    self.trace(f"  ↳ 结果 ({dt*1000:.0f}ms): {preview}{'…' if len(str(result)) > 150 else ''}")
                    return result
                return hook
            hooked_map[name] = make_hook(name, func)

        tools = [
            WEATHER_TOOL, CALCULATOR_TOOL, WEB_SEARCH_TOOL,
            FILE_READ_TOOL, FILE_WRITE_TOOL, SQL_QUERY_TOOL,
            RUN_TERMINAL_TOOL, HUMAN_CONFIRM_TOOL,
            DEMO_FEW_SHOT_TOOL, SUMMARIZE_TOOL,
            {
                "type": "function", "function": {
                    "name": "demo_code_run",
                    "description": "执行 Python 代码并返回输出/错误",
                    "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
                }
            },
            {
                "type": "function", "function": {
                    "name": "search_knowledge",
                    "description": "RAG 知识库问答",
                    "parameters": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]},
                }
            },
        ]

        try:
            reply = self.client.chat_with_tools(
                messages=self.history,
                tools=tools,
                tool_map=hooked_map,
            )
        except RuntimeError as e:
            self.trace(f"❌ 错误: {e}")
            raise

        # 长对话自动压缩
        total_chars = sum(len(m["content"]) for m in self.history)
        if total_chars > 5000:
            self.trace(f"[系统] 对话过长 ({total_chars} 字符)，自动压缩...")
            self.history = self.client.summarize_messages(self.history, keep_recent=4)

        return reply

    def _react_mode(self, user_text: str) -> str:
        """ReAct 模式 — 拦截输出中的 Thought/Action/Observation 展示"""
        self.trace("🤖 模式: ReAct（边想边做）")
        self.trace("━━━━━━━━━━━━━━━ ReAct 推理轨迹 ━━━━━━━━━━━━━━━━━")

        # 用纯文本式 ReAct，拦截它的 print 输出到 trace
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            reply = self.client.chat_with_react(
                [{"role": "user", "content": user_text}],
                tool_map=self.tool_map,
            )
        # 把 print 输出也拼到 trace
        for line in buf.getvalue().splitlines():
            self.trace(line)

        self.trace("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        return reply

    def _multi_agent_mode(self, user_text: str) -> str:
        """多 Agent 模式"""
        self.trace("🤖 模式: 多 Agent 协作（Planner → Executor → Critic）")

        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            reply = run_multi_agent(self.client, user_text)
        for line in buf.getvalue().splitlines():
            self.trace(line)

        return reply

    def trace(self, text: str) -> None:
        """追加一行到 trace buffer"""
        self.trace_buffer.append(text)

    def trace_text(self) -> str:
        return "\n".join(self.trace_buffer)

    # ---------- 会话管理 ----------

    def new_session(self) -> tuple[str, list[tuple]]:
        """创建新会话"""
        self.session_id = self.store.create_session(title="新会话", mode=self.mode)
        self.history = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.trace_buffer = []
        self.trace(f"✨ 已创建新会话 {self.session_id}")
        return self.trace_text(), []

    def switch_session(self, session_id: str) -> tuple[str, list[tuple]]:
        """切换到指定会话"""
        if not session_id:
            return "", []
        saved = self.store.load_messages(session_id)
        self.session_id = session_id
        self.history = [{"role": "system", "content": SYSTEM_PROMPT}] + saved
        self.trace_buffer = []
        self.trace(f"✅ 已恢复会话 {session_id}（{len(saved)} 条消息）")
        # 转换为 Gradio history 格式
        gradio_hist = []
        for i in range(0, len(saved) - 1, 2):
            if saved[i]["role"] == "user" and saved[i + 1]["role"] == "assistant":
                gradio_hist.append((saved[i]["content"], saved[i + 1]["content"]))
        return self.trace_text(), gradio_hist

    def list_sessions(self) -> list[tuple[str, str, str]]:
        """返回 [(session_id, title, info_str)]"""
        sessions = self.store.list_sessions(limit=20)
        return [(s.id, s.title, f"[{s.mode}] {self.store.count_messages(s.id)}条") for s in sessions]

    def set_mode(self, mode: str) -> None:
        self.mode = mode


# ============================================================
# 全局单例
# ============================================================
_app = AgentApp()


# ============================================================
# Gradio UI 构建
# ============================================================

def build_ui() -> gr.Blocks:
    with gr.Blocks(
        title="AI Agent 学习演示 · Web UI",
    ) as demo:
        gr.Markdown(
            "# 🤖 AI Agent 学习演示 · Web UI\n"
            "基于 LM Studio 本地大模型 · 20 种能力 · 自然语言路由\n"
            "对话自动持久化到 SQLite，支持多会话恢复"
        )

        with gr.Row():
            # ---------- 左侧：控制面板 ----------
            with gr.Column(scale=1, min_width=280):
                gr.Markdown("### ⚙️ 模式选择")
                mode_radio = gr.Radio(
                    choices=[
                        ("💬 普通对话 (Function Calling)", "chat"),
                        ("🔄 ReAct 边想边做", "react"),
                        ("🤝 多 Agent 协作", "multi_agent"),
                    ],
                    value="chat",
                    label="Agent 模式",
                )

                gr.Markdown("### 📁 会话管理")
                session_radio = gr.Radio(
                    choices=[],
                    label="历史会话（点击加载）",
                    interactive=True,
                )
                with gr.Row():
                    btn_new_session = gr.Button("✨ 新会话", variant="primary")
                    btn_refresh_sessions = gr.Button("🔄 刷新列表")

                gr.Markdown("### 📝 快捷示例")
                example_box = gr.Textbox(
                    value="北京天气怎么样？顺便算一下 123*456",
                    label="点击填入示例 →",
                    lines=3,
                )
                with gr.Row():
                    btn_example1 = gr.Button("天气+计算")
                    btn_example2 = gr.Button("ReAct 推荐")
                with gr.Row():
                    btn_example3 = gr.Button("多 Agent 写文")
                    btn_example4 = gr.Button("SQL 查询")

                gr.Markdown("### 💡 教学提示")
                gr.Markdown(
                    "- 左上角切换 Agent 模式\n"
                    "- **普通对话**: Function Calling 黑盒路由\n"
                    "- **ReAct**: Thought→Action→Observation 可见轨迹\n"
                    "- **多 Agent**: Planner→Executor→Critic 分工协作\n"
                    "- 所有消息自动持久化"
                )

            # ---------- 右侧：对话 + 轨迹 ----------
            with gr.Column(scale=3):
                chatbot = gr.Chatbot(height=380)

                trace_output = gr.Textbox(
                    label="🔍 执行轨迹（工具调用 / ReAct 推理 / 多 Agent 消息传递）",
                    lines=10,
                    interactive=False,
                    elem_classes=["trace-box"],
                )

                with gr.Row():
                    msg_input = gr.Textbox(
                        label="输入",
                        placeholder="在这里输入你的问题...",
                        lines=2,
                        scale=4,
                    )
                    with gr.Column(scale=1):
                        btn_send = gr.Button("发送 🚀", variant="primary")
                        btn_clear = gr.Button("清空对话")

        # ============================================================
        # 事件绑定（必须在 with Blocks 上下文内）
        # ============================================================

        def on_mode_change(mode_val):
            _app.set_mode(mode_val)
            _app.trace(f"切换模式 → {mode_val}")
            return _app.trace_text()

        def on_session_list_refresh():
            sessions = _app.list_sessions()
            if not sessions:
                choices = [("暂无历史会话", "")]
            else:
                choices = [(f"{title}  {info}", sid) for sid, title, info in sessions]
            return gr.update(choices=choices)

        def on_session_select(session_id):
            if session_id:
                trace, hist = _app.switch_session(session_id)
                return trace, hist

        def on_new_session():
            trace, hist = _app.new_session()
            return trace, hist, on_session_list_refresh()

        def on_send(user_text, history):
            trace, new_hist = _app.send(user_text or "", history or [])
            return trace, new_hist, ""

        def on_clear():
            _app.history = [{"role": "system", "content": SYSTEM_PROMPT}]
            _app.trace("已清空对话")
            return _app.trace_text(), []

        # 模式切换
        mode_radio.change(on_mode_change, mode_radio, trace_output)

        # 会话管理
        btn_refresh_sessions.click(on_session_list_refresh, None, session_radio)
        session_radio.select(on_session_select, session_radio, [trace_output, chatbot])
        btn_new_session.click(on_new_session, None, [trace_output, chatbot, session_radio])

        # 发送（按钮 + Enter）
        btn_send.click(on_send, [msg_input, chatbot], [trace_output, chatbot, msg_input])
        msg_input.submit(on_send, [msg_input, chatbot], [trace_output, chatbot, msg_input])

        # 清空
        btn_clear.click(on_clear, None, [trace_output, chatbot])

        # 快捷示例
        btn_example1.click(lambda: "北京天气怎么样？顺便算一下 123*456", None, msg_input)
        btn_example2.click(lambda: "用 ReAct 模式解答：北京天气怎么样，如果好的话帮我推荐 3 本 Agent 好书", None, msg_input)
        btn_example3.click(lambda: "用多 Agent 协作写一篇关于 AI Agent 的科普文章", None, msg_input)
        btn_example4.click(lambda: "用 sql_query 查一下数据库里每个城市有多少用户", None, msg_input)

        # 初始会话列表填充
        demo.load(on_session_list_refresh, None, session_radio)

    return demo


# ============================================================
# 入口
# ============================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="AI Agent Web UI")
    parser.add_argument("--host", default="127.0.0.1", help="绑定地址")
    parser.add_argument("--port", type=int, default=7860, help="端口")
    parser.add_argument("--share", action="store_true", help="生成公网分享链接")
    args = parser.parse_args()

    print(f"\n🚀 启动 AI Agent Web UI")
    print(f"   地址: http://{args.host}:{args.port}")
    print(f"   共享: {'是' if args.share else '否'}")
    print(f"   数据库: {_app.store.db_path}")
    print()

    demo = build_ui()
    demo.launch(
        server_name=args.host,
        server_port=args.port,
        share=args.share,
    )


if __name__ == "__main__":
    main()
