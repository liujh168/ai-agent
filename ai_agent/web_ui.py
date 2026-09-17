"""Gradio Web UI with per-browser session state and typed Agent events."""

from __future__ import annotations

import html
import os
import queue
import sys
import threading
import time
from typing import Any

try:
    import gradio as gr
except ImportError:
    print("错误：未安装 gradio。请先执行 pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_agent.app_core import (
    TOOL_COUNT,
    build_system_prompt,
    build_tool_executor,
    build_tool_map,
    clean_react_tool_map,
    filter_tool_schemas,
    get_tool_schemas,
)
from ai_agent.client import LMStudioClient
from ai_agent.config import Config
from ai_agent.multi_agent import run_multi_agent
from ai_agent.persistence import DEFAULT_DB_PATH, ChatStore
from ai_agent.rag import RAGKnowledgeBase

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


SessionState = dict[str, Any]


class AgentApp:
    """Shared services plus per-request state passed through Gradio State."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH, disable_dangerous: bool = False):
        self.cfg = Config.from_env()
        self.project_root = os.path.abspath(self.cfg.project_root)
        self.workspace_root = os.path.abspath(self.cfg.workspace_root)
        self.client = LMStudioClient(
            self.cfg.base_url,
            self.cfg.api_key,
            self.cfg.model,
            timeout=self.cfg.request_timeout,
        )
        self.store = ChatStore(db_path)
        self.rag: RAGKnowledgeBase | None = None
        self._rag_lock = threading.Lock()
        self.skill_registry = None
        self._state_local = threading.local()
        self.test_image = os.path.join(self.project_root, "_test_chart.png")
        self.disable_dangerous = disable_dangerous
        self.tool_map: dict = {}
        self.mcp_manager = None
        self.tool_executor = None
        self._register_tools()

    def new_state(self, mode: str = "chat", session_id: str | None = None) -> SessionState:
        return {
            "session_id": session_id,
            "mode": mode,
            "history": [{"role": "system", "content": build_system_prompt(self.skill_registry)}],
            "trace_buffer": [],
        }

    def ensure_state(self, state: SessionState | None) -> SessionState:
        if not isinstance(state, dict):
            return self.new_state()
        state.setdefault("session_id", None)
        state.setdefault("mode", "chat")
        state.setdefault(
            "history",
            [{"role": "system", "content": build_system_prompt(self.skill_registry)}],
        )
        state.setdefault("trace_buffer", [])
        return state

    def _register_tools(self) -> None:
        self.mcp_manager = self._init_mcp()
        self.tool_map = build_tool_map(
            self.client,
            lambda: self.rag,
            self.test_image,
            get_history=self._get_active_history,
            set_history=self._set_active_history,
            mcp_manager=self.mcp_manager,
            workspace_root=self.workspace_root,
        )
        self.tool_map["human_confirm"] = self._web_human_confirm
        self._register_skills()

        allow_write = not self.disable_dangerous
        allow_mcp = bool(self.cfg.mcp_allowed_tools) and not self.disable_dangerous
        self.tool_executor = build_tool_executor(
            self.tool_map,
            allow_write=allow_write,
            allow_execute=False,
            allow_mcp=allow_mcp,
            confirm_write=False,
            confirm_execute=False,
            confirm_mcp=False,
            mcp_allowlist=self.cfg.mcp_allowed_tools,
        )

    def _get_active_history(self):
        return getattr(self._state_local, "history", [])

    def _set_active_history(self, history) -> None:
        self._state_local.history = history

    def _register_skills(self) -> None:
        skill_dir = self.cfg.skill_dir
        if not skill_dir:
            return
        abs_dir = os.path.join(self.project_root, skill_dir)
        if not os.path.isdir(abs_dir):
            return
        try:
            from ai_agent.skill import SkillRegistry

            self.skill_registry = SkillRegistry(abs_dir, list(self.tool_map.keys()))
        except Exception as exc:
            print(f"  ⚠️ SkillRegistry 初始化失败: {exc}")
            self.skill_registry = None

    def _init_mcp(self):
        try:
            from ai_agent.mcp_client import create_mcp_manager

            if not self.cfg.mcp_servers:
                return None
            return create_mcp_manager(list(self.cfg.mcp_servers))
        except Exception as exc:
            print(f"  ⚠️ MCP 初始化失败: {exc}")
            return None

    @staticmethod
    def _web_human_confirm(action: str) -> str:
        return "⚠️ Web 模式不允许模型自行批准敏感操作。受限写操作只能写入配置的工作区，执行型工具始终禁用。"

    def is_mcp_tool(self, name: str) -> bool:
        return bool(self.mcp_manager and self.mcp_manager.is_mcp_tool(name))

    def init_rag(self) -> None:
        if self.rag is not None:
            return
        with self._rag_lock:
            if self.rag is not None:
                return
            try:
                self.rag = RAGKnowledgeBase(
                    base_url=self.cfg.base_url,
                    embed_model=self.cfg.embed_model,
                    chunk_size=self.cfg.chunk_size,
                    chunk_overlap=self.cfg.chunk_overlap,
                    cache_file=os.path.join(self.project_root, self.cfg.rag_cache_file),
                    request_timeout=self.cfg.request_timeout,
                )
                self.rag.build([os.path.join(self.project_root, "docs/通用Agent时代价值迁移.md")])
            except Exception as exc:
                print(f"RAG 初始化失败: {exc}")
                self.rag = None

    def trace(self, state: SessionState, text: str, kind: str = "info") -> None:
        trace_buffer = state.setdefault("trace_buffer", [])
        trace_buffer.append({"text": text, "kind": kind, "ts": time.time()})
        if len(trace_buffer) > 500:
            del trace_buffer[:-500]

    def trace_html(self, state: SessionState) -> str:
        colors = {
            "info": "#888888",
            "tool_call": "#1a73e8",
            "tool_call_mcp": "#7b1fa2",
            "tool_result": "#2e7d32",
            "thought": "#9c27b0",
            "action": "#ff6f00",
            "observation": "#546e7a",
            "agent": "#d32f2f",
            "error": "#c62828",
            "divider": "#555555",
        }
        parts = ['<div class="trace-box">']
        for entry in state.get("trace_buffer", []):
            text = html.escape(str(entry.get("text", ""))).replace("\n", "<br>")
            kind = entry.get("kind", "info")
            styles = [f"color:{colors.get(kind, '#cccccc')}"]
            if kind == "thought":
                styles.append("font-style:italic")
            if kind in {"error", "agent"}:
                styles.append("font-weight:bold")
            if kind == "divider":
                styles.append("border-top:1px dashed #444;margin:4px 0")
            parts.append(
                f'<pre style="margin:2px 0;font-family:monospace;font-size:13px;{";".join(styles)}">'
                f"{text}</pre>"
            )
        parts.append("</div>")
        return "\n".join(parts)

    def _trace_event(self, state: SessionState, event) -> None:
        if event.kind == "route_selected":
            mode = event.payload.get("mode")
            skill = event.payload.get("skill")
            text = f"路由: {mode}" + (f" / {skill}" if skill else "")
            self.trace(state, text, "info")
        elif event.kind == "tool_started":
            name = event.payload.get("name", "")
            arguments = event.payload.get("arguments", "")
            kind = "tool_call_mcp" if self.is_mcp_tool(name) else "tool_call"
            self.trace(state, f"🔧 [{name}] {arguments}", kind)
        elif event.kind == "tool_finished":
            content = str(event.payload.get("content", ""))
            preview = content[:300] + ("…" if len(content) > 300 else "")
            kind = "tool_result" if event.payload.get("ok") else "error"
            self.trace(state, f"  ↳ {preview}", kind)
        elif event.kind == "error":
            self.trace(state, f"❌ {event.payload.get('message', '未知错误')}", "error")

    def _tool_schemas(self) -> list[dict]:
        schemas = get_tool_schemas(self.mcp_manager)
        return filter_tool_schemas(schemas, self.tool_executor.policy)

    def _run_chat(self, state: SessionState, user_text: str, event_queue: queue.Queue) -> None:
        self._set_active_history(state["history"])

        def on_event(event):
            event_queue.put(("event", event))

        reply = self.client.chat_with_tools(
            messages=state["history"],
            tools=self._tool_schemas(),
            tool_map=self.tool_map,
            tool_executor=self.tool_executor,
            skill_registry=self.skill_registry,
            mcp_manager=self.mcp_manager,
            on_event=on_event,
            session_id=state.get("session_id"),
        )
        state["history"] = self._get_active_history()
        event_queue.put(("done", reply))

    def _run_react(self, state: SessionState, user_text: str, event_queue: queue.Queue) -> None:
        def on_trace(line: str, kind: str) -> None:
            event_queue.put(("trace", (line, kind)))

        reply = self.client.chat_with_react(
            [{"role": "user", "content": user_text}],
            tool_map=clean_react_tool_map(self.tool_map),
            on_event=on_trace,
        )
        event_queue.put(("done", reply))

    def _run_multi_agent(self, state: SessionState, user_text: str, event_queue: queue.Queue) -> None:
        def on_trace(line: str) -> None:
            event_queue.put(("trace", (line, "agent")))

        reply = run_multi_agent(
            self.client,
            user_text,
            max_rounds=2,
            max_tokens=2048,
            trace_callback=on_trace,
        )
        event_queue.put(("done", reply))

    def _maybe_compress(self, state: SessionState) -> None:
        total_chars = sum(len(m.get("content") or "") for m in state["history"])
        if total_chars <= 5000:
            return
        compressed = self.client.summarize_messages(state["history"], keep_recent=4)
        state["history"] = compressed
        session_id = state.get("session_id")
        if session_id:
            self.store.clear_messages(session_id)
            for message in compressed[1:]:
                self.store.append(
                    session_id,
                    message["role"],
                    message.get("content", ""),
                    message.get("tool_calls"),
                )

    def send(self, user_text: str, history: list[dict], state: SessionState | None):
        state = self.ensure_state(state)
        if not user_text.strip():
            yield self.trace_html(state), history or [], "", state
            return

        self.init_rag()
        if state.get("session_id") is None:
            state["session_id"] = self.store.create_session(
                title="新会话",
                mode=state.get("mode", "chat"),
            )

        session_id = state["session_id"]
        state["trace_buffer"] = []
        self.trace(
            state,
            f"📥 用户输入: {user_text[:100]}{'…' if len(user_text) > 100 else ''}",
        )
        self.store.append(session_id, "user", user_text)
        if self.store.count_messages(session_id) <= 1:
            self.store.auto_title(session_id, user_text)
        state["history"].append({"role": "user", "content": user_text})

        gradio_hist = list(history or []) + [{"role": "user", "content": user_text}]
        mode = state.get("mode", "chat")
        event_queue: queue.Queue = queue.Queue()
        result_holder: dict[str, str | None] = {"reply": None, "error": None}

        def worker():
            try:
                if mode == "react":
                    self._run_react(state, user_text, event_queue)
                elif mode == "multi_agent":
                    self._run_multi_agent(state, user_text, event_queue)
                else:
                    self._run_chat(state, user_text, event_queue)
            except Exception as exc:
                result_holder["error"] = f"{type(exc).__name__}: {exc}"
                event_queue.put(("error", result_holder["error"]))

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        while thread.is_alive() or not event_queue.empty():
            changed = False
            while not event_queue.empty():
                item_type, payload = event_queue.get_nowait()
                changed = True
                if item_type == "event":
                    self._trace_event(state, payload)
                elif item_type == "trace":
                    line, kind = payload
                    self.trace(state, line, kind)
                elif item_type == "done":
                    result_holder["reply"] = payload
                elif item_type == "error":
                    self.trace(state, f"❌ {payload}", "error")
            if changed or thread.is_alive():
                yield self.trace_html(state), gradio_hist, "", state
            thread.join(timeout=0.15)

        if result_holder["error"]:
            reply = f"[异常] {result_holder['error']}"
        else:
            reply = result_holder["reply"] or "(无输出)"

        state["history"].append({"role": "assistant", "content": reply})
        self.store.append(session_id, "assistant", reply)
        try:
            self._maybe_compress(state)
        except Exception as exc:
            self.trace(state, f"历史压缩失败: {exc}", "error")
        gradio_hist.append({"role": "assistant", "content": reply})
        yield self.trace_html(state), gradio_hist, "", state

    def new_session(self, state: SessionState | None) -> tuple[str, list, SessionState]:
        state = self.ensure_state(state)
        mode = state.get("mode", "chat")
        session_id = self.store.create_session(title="新会话", mode=mode)
        state = self.new_state(mode=mode, session_id=session_id)
        self.trace(state, f"✨ 已创建新会话 {session_id}", "info")
        return self.trace_html(state), [], state

    def switch_session(
        self,
        session_id: str,
        state: SessionState | None,
    ) -> tuple[str, list[tuple], SessionState, Any]:
        state = self.ensure_state(state)
        session = self.store.get_session(session_id) if session_id else None
        if session is None:
            return self.trace_html(state), [], state, gr.update()
        saved = self.store.load_messages(session.id)
        state = self.new_state(mode=session.mode, session_id=session.id)
        state["history"].extend(saved)
        self.trace(state, f"✅ 已恢复会话 {session.id}（{len(saved)} 条消息）", "info")
        gradio_hist = [
            {"role": message["role"], "content": message["content"]}
            for message in saved
            if message["role"] in {"user", "assistant"}
        ]
        return self.trace_html(state), gradio_hist, state, gr.update(value=session.mode)

    def list_sessions(self) -> list[tuple[str, str, str]]:
        sessions = self.store.list_sessions(limit=20)
        return [
            (session.id, session.title, f"[{session.mode}] {self.store.count_messages(session.id)}条")
            for session in sessions
        ]

    def set_mode(self, mode: str, state: SessionState | None) -> tuple[str, SessionState]:
        state = self.ensure_state(state)
        state["mode"] = mode
        if state.get("session_id"):
            self.store.set_session_mode(state["session_id"], mode)
        self.trace(state, f"切换模式 → {mode}", "info")
        return self.trace_html(state), state

    def clear(self, state: SessionState | None) -> tuple[str, list, SessionState]:
        state = self.ensure_state(state)
        session_id = state.get("session_id")
        if session_id:
            self.store.clear_messages(session_id)
        mode = state.get("mode", "chat")
        state = self.new_state(mode=mode, session_id=session_id)
        self.trace(state, "已清空当前会话", "info")
        return self.trace_html(state), [], state


def build_ui(app: AgentApp) -> gr.Blocks:
    css = """
    .trace-box {
        background: #1a1a2e;
        border-radius: 8px;
        padding: 10px 12px;
        min-height: 180px;
        max-height: 400px;
        overflow-y: auto;
        font-family: 'IBM Plex Mono', 'Cascadia Code', Consolas, monospace;
    }
    .trace-box pre {
        white-space: pre-wrap;
        word-break: break-all;
    }
    """

    with gr.Blocks(title="AI Agent 学习演示 · Web UI", css=css) as demo:
        mcp_count = app.mcp_manager.total_tools if app.mcp_manager else 0
        mcp_note = f" · MCP 远端 {mcp_count}" if mcp_count else ""
        gr.Markdown(
            "# AI Agent 学习演示 · Web UI\n"
            f"基于 LM Studio · {TOOL_COUNT + mcp_count} 种能力{mcp_note} · 事件驱动运行循环\n"
            "会话状态按浏览器隔离；执行型工具默认关闭。"
        )

        session_state = gr.State(app.new_state())

        with gr.Row():
            with gr.Column(scale=1, min_width=280):
                gr.Markdown("### ⚙️ 模式选择")
                mode_radio = gr.Radio(
                    choices=[
                        ("普通对话 (Function Calling)", "chat"),
                        ("ReAct 边想边做", "react"),
                        ("多 Agent 协作", "multi_agent"),
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
                gr.Textbox(
                    value="北京天气怎么样？顺便算一下 123*456",
                    label="示例",
                    lines=3,
                )
                with gr.Row():
                    btn_example1 = gr.Button("天气+计算")
                    btn_example2 = gr.Button("ReAct 推荐")
                with gr.Row():
                    btn_example3 = gr.Button("多 Agent 写文")
                    btn_example4 = gr.Button("SQL 查询")

                gr.Markdown(
                    "### 安全边界\n"
                    "- 文件操作限制在项目工作区\n"
                    "- SQL 始终以只读连接执行\n"
                    "- Web 不开放代码和终端执行\n"
                    "- 公网分享模式关闭文件写入和 MCP"
                )

            with gr.Column(scale=3):
                chatbot = gr.Chatbot(height=380, type="messages")
                trace_output = gr.HTML(
                    value='<div class="trace-box" style="color:#666">等待请求...</div>',
                    label="执行轨迹",
                )
                with gr.Row():
                    msg_input = gr.Textbox(
                        label="输入",
                        placeholder="在这里输入你的问题...",
                        lines=2,
                        scale=4,
                    )
                    with gr.Column(scale=1):
                        btn_send = gr.Button("发送", variant="primary")
                        btn_clear = gr.Button("清空对话")

        def on_mode_change(mode_value, state):
            return app.set_mode(mode_value, state)

        def on_session_list_refresh():
            sessions = app.list_sessions()
            choices = [(f"{title}  {info}", session_id) for session_id, title, info in sessions]
            return gr.update(choices=choices or [("暂无历史会话", "")])

        def on_session_select(session_id, state):
            return app.switch_session(session_id, state)

        def on_new_session(state):
            trace, history, new_state = app.new_session(state)
            return trace, history, new_state, on_session_list_refresh()

        def on_send(user_text, history, state):
            yield from app.send(user_text or "", history or [], state)

        def on_clear(state):
            return app.clear(state)

        mode_radio.change(
            on_mode_change,
            [mode_radio, session_state],
            [trace_output, session_state],
        )
        btn_refresh_sessions.click(
            on_session_list_refresh,
            None,
            session_radio,
        )
        session_radio.select(
            on_session_select,
            [session_radio, session_state],
            [trace_output, chatbot, session_state, mode_radio],
        )
        btn_new_session.click(
            on_new_session,
            session_state,
            [trace_output, chatbot, session_state, session_radio],
        )
        btn_send.click(
            on_send,
            [msg_input, chatbot, session_state],
            [trace_output, chatbot, msg_input, session_state],
        )
        msg_input.submit(
            on_send,
            [msg_input, chatbot, session_state],
            [trace_output, chatbot, msg_input, session_state],
        )
        btn_clear.click(
            on_clear,
            session_state,
            [trace_output, chatbot, session_state],
        )

        btn_example1.click(lambda: "北京天气怎么样？顺便算一下 123*456", None, msg_input)
        btn_example2.click(
            lambda: "用 ReAct 方式查北京天气，再推荐一本 Agent 书",
            None,
            msg_input,
        )
        btn_example3.click(
            lambda: "用多 Agent 协作写一篇关于 AI Agent 的科普文章",
            None,
            msg_input,
        )
        btn_example4.click(
            lambda: "用 sql_query 查询每个城市有多少用户",
            None,
            msg_input,
        )
        demo.load(on_session_list_refresh, None, session_radio)

    return demo


def main() -> None:
    import argparse

    from ai_agent.console import configure_utf8_output

    configure_utf8_output()
    parser = argparse.ArgumentParser(description="AI Agent Web UI")
    parser.add_argument("--host", default="127.0.0.1", help="绑定地址")
    parser.add_argument("--port", type=int, default=7860, help="端口")
    parser.add_argument("--share", action="store_true", help="生成公网分享链接")
    args = parser.parse_args()

    app = AgentApp(disable_dangerous=args.share)
    print("\n🚀 启动 AI Agent Web UI")
    print(f"   地址: http://{args.host}:{args.port}")
    print(f"   共享: {'是' if args.share else '否'}")
    print(f"   数据库: {app.store.db_path}")
    print()

    demo = build_ui(app)
    demo.launch(
        server_name=args.host,
        server_port=args.port,
        share=args.share,
        inbrowser=False,
    )


if __name__ == "__main__":
    main()
