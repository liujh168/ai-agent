"""Gradio Web UI with per-browser session state and typed Agent events.

呈现层（LLM 调用面板 / 执行轨迹 / JSON 高亮的 HTML 渲染）已抽到 ai_agent/web_render.py，
本模块只保留状态装配、事件流与 Gradio 界面接线。样式表外置在 ai_agent/static/web_ui.css。
"""

from __future__ import annotations

import inspect
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

from ai_agent import web_render
from ai_agent.app_core import (
    TOOL_COUNT,
    build_system_prompt,
    build_tool_executor,
    clean_react_tool_map,
    filter_tool_schemas,
    get_tool_schemas,
)
from ai_agent.bootstrap import AgentServices
from ai_agent.config import Config, load_env
from ai_agent.llm_trace import LLMCall
from ai_agent.multi_agent import run_multi_agent
from ai_agent.persistence import DEFAULT_DB_PATH, ChatStore
from ai_agent.session_service import SessionService
from ai_agent.web_render import COPY_BUTTON_HEAD, MAX_LLM_CALLS, SessionState

load_env()


# 采样参数默认值（Kimi Playground 式可调参数）
DEFAULT_RUN_PARAMS: dict[str, Any] = {
    "temperature": 0.7,
    "max_tokens": 2048,
    "max_iterations": 5,
}

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def _load_css() -> str:
    """读取外置样式表（ai_agent/static/web_ui.css）。"""
    with open(os.path.join(_STATIC_DIR, "web_ui.css"), encoding="utf-8") as handle:
        return handle.read()


class AgentApp:
    """Shared services plus per-request state passed through Gradio State."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH, disable_dangerous: bool = False):
        self.services = AgentServices(Config.from_env())
        self.cfg = self.services.cfg
        self.project_root = self.services.project_root
        self.workspace_root = self.services.workspace_root
        self.client = self.services.client
        # 把每次 LLM API 调用的输入输出接到 Web 面板上
        self.client.on_llm_call = self._on_llm_call
        self.store = ChatStore(db_path)
        self.sessions = SessionService(self.store)
        self._state_local = threading.local()
        # --share 公网分享时强制禁用写入、MCP 和外发网络工具
        self.disable_dangerous = disable_dangerous
        self.mcp_manager = self.services.mcp_manager
        self.tool_map: dict = {}
        self.tool_executor = None
        self._register_tools()

    @property
    def rag(self):
        return self.services.rag

    @property
    def skill_registry(self):
        return self.services.skill_registry

    def new_state(self, mode: str = "chat", session_id: str | None = None) -> SessionState:
        prompt = build_system_prompt(self.skill_registry)
        return {
            "session_id": session_id,
            "mode": mode,
            "history": [{"role": "system", "content": prompt}],
            "trace_buffer": [],
            "llm_calls": [],
            "llm_revision": 0,
            "llm_round": 0,
            "llm_round_queries": {},
            "llm_dropped": 0,
            "system_prompt": prompt,
            "params": dict(DEFAULT_RUN_PARAMS),
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
        state.setdefault("llm_calls", [])
        state.setdefault("llm_revision", 0)
        state.setdefault("llm_round", 0)
        state.setdefault("llm_round_queries", {})
        state.setdefault("llm_dropped", 0)
        state.setdefault("system_prompt", self._current_system_prompt(state))
        state.setdefault("params", dict(DEFAULT_RUN_PARAMS))
        return state

    @staticmethod
    def _current_system_prompt(state: SessionState) -> str:
        for message in state.get("history") or []:
            if message.get("role") == "system":
                return str(message.get("content") or "")
        return ""

    def _run_params(self, state: SessionState) -> dict[str, Any]:
        params = dict(DEFAULT_RUN_PARAMS)
        params.update(state.get("params") or {})
        return params

    def _register_tools(self) -> None:
        """注册工具（tool_map / MCP / Skill 由共享的 AgentServices 装配），只保留 Web 侧策略"""
        self.tool_map = self.services.build_tool_map(
            get_history=self._get_active_history,
            set_history=self._set_active_history,
        )
        self.tool_map["human_confirm"] = self._web_human_confirm

        allow_write = not self.disable_dangerous
        allow_mcp = bool(self.cfg.mcp_allowed_tools) and not self.disable_dangerous
        self.tool_executor = build_tool_executor(
            self.tool_map,
            allow_write=allow_write,
            allow_execute=False,
            allow_mcp=allow_mcp,
            allow_network=not self.disable_dangerous,
            confirm_write=False,
            confirm_execute=False,
            confirm_mcp=False,
            mcp_allowlist=self.cfg.mcp_allowed_tools,
        )

    def _get_active_history(self):
        return getattr(self._state_local, "history", [])

    def _set_active_history(self, history) -> None:
        self._state_local.history = history

    @staticmethod
    def _web_human_confirm(action: str) -> str:
        return "⚠️ Web 模式不允许模型自行批准敏感操作。受限写操作只能写入配置的工作区，执行型工具始终禁用。"

    def is_mcp_tool(self, name: str) -> bool:
        return bool(self.mcp_manager and self.mcp_manager.is_mcp_tool(name))

    def trace(self, state: SessionState, text: str, kind: str = "info") -> None:
        trace_buffer = state.setdefault("trace_buffer", [])
        trace_buffer.append({"text": text, "kind": kind, "ts": time.time()})
        if len(trace_buffer) > 500:
            del trace_buffer[:-500]

    # ============================================================
    # LLM API 调用面板
    # ============================================================

    def _on_llm_call(self, phase: str, call: LLMCall) -> None:
        """LMStudioClient 的观察者：把快照转发给当前线程正在跑的那次请求。"""
        sink = getattr(self._state_local, "llm_sink", None)
        if sink is not None:
            sink(phase, call)

    @staticmethod
    def _apply_llm_call(state: SessionState, phase: str, call: LLMCall) -> None:
        """请求阶段新增一张卡片，流式/响应阶段就地更新同一张卡片。"""
        state["llm_revision"] = state.get("llm_revision", 0) + 1
        calls = state.setdefault("llm_calls", [])
        for index, existing in enumerate(calls):
            if existing.id == call.id:
                calls[index] = call
                return
        # 新卡片盖上当前轮次，供面板按「第几轮提问」分组
        call.round_no = state.get("llm_round", 0)
        calls.append(call)
        if len(calls) > MAX_LLM_CALLS:
            overflow = len(calls) - MAX_LLM_CALLS
            del calls[:overflow]
            state["llm_dropped"] = state.get("llm_dropped", 0) + overflow

    def llm_panel_update(self, state: SessionState, last_revision: int) -> tuple[Any, int]:
        """只在调用记录真的变化时才重绘面板。

        面板里有可折叠卡片，如果每次流式 tick 都重绘，用户展开的卡片和滚动位置
        会被反复重置。返回 ``gr.update()`` 表示"这个输出不变"。
        """
        revision = state.get("llm_revision", 0)
        if revision == last_revision:
            return gr.update(), last_revision
        return self.llm_panel_html(state), revision

    def llm_panel_html(self, state: SessionState) -> str:
        return web_render.llm_panel_html(state)

    def trace_html(self, state: SessionState) -> str:
        return web_render.trace_html(state.get("trace_buffer", []))


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
        params = self._run_params(state)

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
            temperature=params["temperature"],
            max_tokens=params["max_tokens"],
            max_iterations=params["max_iterations"],
            stream=True,
        )
        state["history"] = self._get_active_history()
        event_queue.put(("done", reply))

    def _run_react(self, state: SessionState, user_text: str, event_queue: queue.Queue) -> None:
        params = self._run_params(state)

        def on_trace(line: str, kind: str) -> None:
            event_queue.put(("trace", (line, kind)))

        reply = self.client.chat_with_react(
            [{"role": "user", "content": user_text}],
            tool_map=clean_react_tool_map(self.tool_map),
            max_iterations=params["max_iterations"] + 1,
            temperature=params["temperature"],
            max_tokens=params["max_tokens"],
            on_event=on_trace,
        )
        event_queue.put(("done", reply))

    def _run_multi_agent(self, state: SessionState, user_text: str, event_queue: queue.Queue) -> None:
        params = self._run_params(state)

        def on_trace(line: str) -> None:
            event_queue.put(("trace", (line, "agent")))

        # Executor 工具集：剔除内部演示工具与 run_multi_agent 自身（防递归）
        executor_map = clean_react_tool_map(self.tool_map)
        executor_map.pop("run_multi_agent", None)
        executor_schemas = [
            s for s in self._tool_schemas() if s["function"]["name"] in executor_map
        ]
        reply = run_multi_agent(
            self.client,
            user_text,
            max_rounds=2,
            temperature=params["temperature"],
            max_tokens=params["max_tokens"],
            trace_callback=on_trace,
            tools=executor_schemas,
            tool_map=executor_map,
            tool_executor=self.tool_executor,
        )
        event_queue.put(("done", reply))

    def _maybe_compress(self, state: SessionState) -> None:
        compressed, _, did_compress = self.sessions.compress_if_needed(
            self.client,
            state["history"],
            state.get("session_id"),
            self.cfg.history_max_chars,
        )
        if did_compress:
            state["history"] = compressed

    def send(self, user_text: str, history: list[dict], state: SessionState | None):
        state = self.ensure_state(state)
        if not user_text.strip():
            yield self.trace_html(state), self.llm_panel_html(state), history or [], "", state
            return

        self.services.ensure_rag()
        if state.get("session_id") is None:
            state["session_id"] = self.sessions.create(mode=state.get("mode", "chat"))

        session_id = state["session_id"]
        state["trace_buffer"] = []
        # 跨轮保留调用记录：开启新的一轮，面板按轮次分组、历史轮折叠
        state["llm_round"] = state.get("llm_round", 0) + 1
        round_queries = state.setdefault("llm_round_queries", {})
        round_queries[state["llm_round"]] = user_text[:60] + ("…" if len(user_text) > 60 else "")
        state["llm_revision"] = state.get("llm_revision", 0) + 1
        last_llm_revision = -1
        self.trace(
            state,
            f"📥 用户输入: {user_text[:100]}{'…' if len(user_text) > 100 else ''}",
        )
        self.sessions.append_user_message(session_id, user_text)
        state["history"].append({"role": "user", "content": user_text})

        gradio_hist = list(history or []) + [{"role": "user", "content": user_text}]
        mode = state.get("mode", "chat")
        event_queue: queue.Queue = queue.Queue()
        result_holder: dict[str, str | None] = {"reply": None, "error": None}

        def worker():
            # LLM 快照观察者是按线程分发的：这个 worker 线程 = 这一次请求
            self._state_local.llm_sink = lambda phase, call: event_queue.put(("llm", (phase, call)))
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
            finally:
                self._state_local.llm_sink = None

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        # 流式聊天气泡：本轮每次 API 请求开一个临时气泡，增量就地更新，结束后统一收尾
        first_bubble_index: int | None = None
        active_bubble: int | None = None
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
                elif item_type == "llm":
                    phase, call = payload
                    self._apply_llm_call(state, phase, call)
                    if mode == "chat":
                        if phase == "request":
                            if first_bubble_index is None:
                                first_bubble_index = len(gradio_hist)
                            gradio_hist.append({"role": "assistant", "content": ""})
                            active_bubble = len(gradio_hist) - 1
                        elif (
                            phase in {"stream", "response"}
                            and call.content
                            and active_bubble is not None
                        ):
                            gradio_hist[active_bubble] = {
                                "role": "assistant",
                                "content": call.content,
                            }
                elif item_type == "done":
                    result_holder["reply"] = payload
                elif item_type == "error":
                    self.trace(state, f"❌ {payload}", "error")
            if changed or thread.is_alive():
                panel, last_llm_revision = self.llm_panel_update(state, last_llm_revision)
                yield self.trace_html(state), panel, gradio_hist, "", state
            thread.join(timeout=0.15)

        if result_holder["error"]:
            reply = f"[异常] {result_holder['error']}"
        else:
            reply = result_holder["reply"] or "(无输出)"

        state["history"].append({"role": "assistant", "content": reply})
        self.sessions.append_assistant_message(session_id, reply)
        try:
            self._maybe_compress(state)
        except Exception as exc:
            self.trace(state, f"历史压缩失败: {exc}", "error")
        # 去掉流式过程中的临时气泡，用最终回复收尾
        if first_bubble_index is not None:
            del gradio_hist[first_bubble_index:]
        gradio_hist.append({"role": "assistant", "content": reply})
        yield self.trace_html(state), self.llm_panel_html(state), gradio_hist, "", state

    def new_session(self, state: SessionState | None) -> tuple[str, str, list, SessionState]:
        state = self.ensure_state(state)
        mode = state.get("mode", "chat")
        session_id = self.sessions.create(mode=mode)
        state = self.new_state(mode=mode, session_id=session_id)
        self.trace(state, f"✨ 已创建新会话 {session_id}", "info")
        return self.trace_html(state), self.llm_panel_html(state), [], state

    def delete_all_sessions(self, state: SessionState | None) -> tuple[str, str, list, SessionState]:
        state = self.ensure_state(state)
        deleted = self.sessions.delete_all()
        mode = state.get("mode", "chat")
        session_id = self.sessions.create(mode=mode)
        state = self.new_state(mode=mode, session_id=session_id)
        self.trace(state, f"🗑 已删除全部历史会话（{deleted} 个），并创建新会话 {session_id}", "info")
        return self.trace_html(state), self.llm_panel_html(state), [], state

    def delete_session(
        self, session_id: str, state: SessionState | None
    ) -> tuple[Any, Any, Any, SessionState, bool]:
        """删除单个会话；若删的是当前会话则自动切换到新会话（返回最后一项标记是否发生了切换）"""
        state = self.ensure_state(state)
        session = self.sessions.get(session_id)
        if session is None:
            return gr.update(), gr.update(), gr.update(), state, False
        self.sessions.delete(session.id)
        if session.id == state.get("session_id"):
            trace, panel, history, new_state = self.new_session(state)
            return trace, panel, history, new_state, True
        self.trace(state, f"🗑 已删除会话「{session.title}」", "info")
        return self.trace_html(state), gr.update(), gr.update(), state, False

    def switch_session(
        self,
        session_id: str,
        state: SessionState | None,
    ) -> tuple[str, str, list[tuple], SessionState, Any]:
        state = self.ensure_state(state)
        session = self.sessions.get(session_id)
        if session is None:
            return (
                self.trace_html(state),
                self.llm_panel_html(state),
                [],
                state,
                gr.update(),
            )
        state = self.new_state(mode=session.mode, session_id=session.id)
        state["history"] = self.sessions.restore_history(session.id, state["system_prompt"])
        saved = state["history"][1:]
        self.trace(state, f"✅ 已恢复会话 {session.id}（{len(saved)} 条消息）", "info")
        gradio_hist = [
            {"role": message["role"], "content": message["content"]}
            for message in saved
            if message["role"] in {"user", "assistant"}
        ]
        return (
            self.trace_html(state),
            self.llm_panel_html(state),
            gradio_hist,
            state,
            gr.update(value=session.mode),
        )

    def list_sessions(self) -> list[tuple[str, str, str]]:
        sessions = self.sessions.list(limit=20)
        return [
            (
                session.id,
                session.title,
                f"[{session.mode}] {self.sessions.count_messages(session.id)}条",
            )
            for session in sessions
        ]

    def set_mode(self, mode: str, state: SessionState | None) -> tuple[str, SessionState]:
        state = self.ensure_state(state)
        state["mode"] = mode
        if state.get("session_id"):
            self.sessions.set_mode(state["session_id"], mode)
        self.trace(state, f"切换模式 → {mode}", "info")
        return self.trace_html(state), state

    def set_params(
        self,
        temperature: float,
        max_tokens: int,
        max_iterations: int,
        state: SessionState | None,
    ) -> tuple[str, SessionState]:
        state = self.ensure_state(state)
        state["params"] = {
            "temperature": round(float(temperature), 2),
            "max_tokens": int(max_tokens),
            "max_iterations": int(max_iterations),
        }
        self.trace(
            state,
            "⚙️ 采样参数 → "
            f"temperature={state['params']['temperature']}, "
            f"max_tokens={state['params']['max_tokens']}, "
            f"max_iterations={state['params']['max_iterations']}",
            "info",
        )
        return self.trace_html(state), state

    def set_system_prompt(self, text: str, state: SessionState | None) -> tuple[str, SessionState]:
        state = self.ensure_state(state)
        text = (text or "").strip()
        if not text:
            self.trace(state, "系统提示词不能为空，已忽略本次修改", "error")
            return self.trace_html(state), state
        history = list(state.get("history") or [])
        if history and history[0].get("role") == "system":
            history[0] = {"role": "system", "content": text}
        else:
            history.insert(0, {"role": "system", "content": text})
        state["history"] = history
        state["system_prompt"] = text
        self.trace(
            state,
            f"🧩 已更新系统提示词（{len(text)} 字符）——下一次 API 调用的输入会立即反映该改动",
            "info",
        )
        return self.trace_html(state), state

    def reset_system_prompt(self, state: SessionState | None) -> tuple[str, str, SessionState]:
        state = self.ensure_state(state)
        prompt = build_system_prompt(self.skill_registry)
        state["system_prompt"] = prompt
        history = list(state.get("history") or [])
        if history and history[0].get("role") == "system":
            history[0] = {"role": "system", "content": prompt}
        else:
            history.insert(0, {"role": "system", "content": prompt})
        state["history"] = history
        self.trace(state, "♻️ 已恢复默认系统提示词", "info")
        return prompt, self.trace_html(state), state

    def clear(self, state: SessionState | None) -> tuple[str, str, list, SessionState]:
        state = self.ensure_state(state)
        session_id = state.get("session_id")
        if session_id:
            self.sessions.clear(session_id)
        mode = state.get("mode", "chat")
        prompt = state.get("system_prompt") or build_system_prompt(self.skill_registry)
        params = dict(state.get("params") or DEFAULT_RUN_PARAMS)
        state = self.new_state(mode=mode, session_id=session_id)
        state["system_prompt"] = prompt
        state["history"][0] = {"role": "system", "content": prompt}
        state["params"] = params
        self.trace(state, "已清空当前会话", "info")
        return self.trace_html(state), self.llm_panel_html(state), [], state

    def clear_llm_calls(self, state: SessionState | None) -> tuple[str, SessionState]:
        """清空 API 调用检查器的全部记录（不影响对话本身）。"""
        state = self.ensure_state(state)
        state["llm_calls"] = []
        state["llm_round"] = 0
        state["llm_round_queries"] = {}
        state["llm_dropped"] = 0
        state["llm_revision"] = state.get("llm_revision", 0) + 1
        return self.llm_panel_html(state), state


def build_ui(app: AgentApp) -> gr.Blocks:
    css = _load_css()

    default_prompt = app.new_state()["system_prompt"]

    blocks_kwargs: dict[str, Any] = {"title": "AI Agent 学习演示 · Web UI", "css": css}
    # head 注入（Gradio 5.x）用于复制按钮的全局事件委托；旧版本无此参数则降级为无复制按钮
    if "head" in inspect.signature(gr.Blocks.__init__).parameters:
        blocks_kwargs["head"] = COPY_BUTTON_HEAD

    with gr.Blocks(**blocks_kwargs) as demo:
        mcp_count = app.mcp_manager.total_tools if app.mcp_manager else 0
        mcp_note = f" · MCP 远端 {mcp_count}" if mcp_count else ""
        gr.Markdown(
            "# AI Agent 学习演示 · Web UI\n"
            f"基于 LM Studio · {TOOL_COUNT + mcp_count} 种能力{mcp_note} · 事件驱动运行循环\n"
            "右侧「API 调用检查器」跨轮记录每次请求的完整输入（system / user 消息 + 工具 schema + 采样参数）"
            "与模型返回（content / tool_calls / finish_reason / token 用量），流式生成实时可见、输入输出左右对照。"
        )

        session_state = gr.State(app.new_state())
        # 会话列表变更信号：任何增删会话的事件都输出最新列表来触发 @gr.render 重绘
        sessions_data = gr.State(app.list_sessions())

        with gr.Row():
            with gr.Column(scale=1, min_width=270):
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

                with gr.Accordion("🎛️ 采样参数", open=False):
                    temp_slider = gr.Slider(
                        minimum=0.0,
                        maximum=1.5,
                        step=0.05,
                        value=DEFAULT_RUN_PARAMS["temperature"],
                        label="temperature（越高越随机）",
                    )
                    tokens_slider = gr.Slider(
                        minimum=256,
                        maximum=8192,
                        step=256,
                        value=DEFAULT_RUN_PARAMS["max_tokens"],
                        label="max_tokens（单轮输出上限）",
                    )
                    iter_slider = gr.Slider(
                        minimum=1,
                        maximum=10,
                        step=1,
                        value=DEFAULT_RUN_PARAMS["max_iterations"],
                        label="max_iterations（工具循环上限）",
                    )
                    btn_apply_params = gr.Button("应用参数")

                with gr.Accordion("🧩 系统提示词（system message）", open=False):
                    prompt_box = gr.Textbox(
                        value=default_prompt,
                        lines=10,
                        max_lines=18,
                        show_label=False,
                        placeholder="system 消息内容",
                    )
                    with gr.Row():
                        btn_apply_prompt = gr.Button("应用", variant="primary")
                        btn_reset_prompt = gr.Button("恢复默认")
                    gr.Markdown(
                        "<span class='inspector-hint'>system 消息是每次 API 调用的第一条输入，"
                        "改它就能立刻在右侧看到效果。</span>"
                    )

                with gr.Accordion("📁 会话管理", open=False):
                    session_list_container = gr.Group()
                    with gr.Row():
                        btn_new_session = gr.Button("✨ 新会话", variant="primary")
                        btn_refresh_sessions = gr.Button("🔄 刷新列表")
                    btn_delete_all_sessions = gr.Button("🗑 删除全部历史会话", variant="stop")
                    with gr.Column(visible=False) as delete_all_confirm_box:
                        gr.Markdown("⚠️ 确认删除全部历史会话？此操作不可恢复。")
                        with gr.Row():
                            btn_confirm_delete_all = gr.Button("确认删除", variant="stop", size="sm")
                            btn_cancel_delete_all = gr.Button("取消", size="sm")

                gr.Markdown("### 📝 快捷示例")
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
                    "- 公网分享关闭写入、MCP 和外发网络工具"
                )

            with gr.Column(scale=3, min_width=340):
                chatbot = gr.Chatbot(height=520, type="messages")
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

            with gr.Column(scale=3, min_width=420):
                with gr.Tabs():
                    with gr.Tab("🔍 API 调用检查器"):
                        llm_output = gr.HTML(value=app.llm_panel_html(app.new_state()))
                        btn_clear_calls = gr.Button("🗑 清空调用记录", size="sm")
                        gr.Markdown(
                            "<span class='inspector-hint'>"
                            "卡片按「第几轮提问」分组列出本会话的所有 API 调用——历史轮折叠在下方，"
                            "最新一轮展开并支持流式实时查看。展开任意一条可以看到："
                            "**① 输入**（模型真正收到的每一条消息）与 **② 输出**"
                            "（content / tool_calls / finish_reason / token 用量）左右对照，"
                            "下方还有等价的请求 JSON、响应 JSON 和可复制运行的 Python 代码，"
                            "鼠标悬停代码块可一键复制。≈ 表示 token 数为字符估算。"
                            "</span>"
                        )
                    with gr.Tab("🧭 执行轨迹"):
                        trace_output = gr.HTML(
                            value='<div class="trace-box" style="color:#666">等待请求...</div>',
                            label="执行轨迹",
                        )

        def on_mode_change(mode_value, state):
            return app.set_mode(mode_value, state)

        def on_apply_params(temperature, max_tokens, max_iterations, state):
            return app.set_params(temperature, max_tokens, max_iterations, state)

        def on_apply_prompt(text, state):
            return app.set_system_prompt(text, state)

        def on_reset_prompt(state):
            return app.reset_system_prompt(state)

        def load_sessions_data():
            return app.list_sessions()

        def _param_updates(state):
            params = {**DEFAULT_RUN_PARAMS, **(state.get("params") or {})}
            return (
                gr.update(value=params["temperature"]),
                gr.update(value=params["max_tokens"]),
                gr.update(value=params["max_iterations"]),
                gr.update(value=state.get("system_prompt", "")),
            )

        def on_session_select(session_id, state):
            trace, panel, history, new_state, mode_update = app.switch_session(session_id, state)
            return (trace, panel, history, new_state, mode_update, *_param_updates(new_state))

        def on_session_delete(session_id, state):
            trace, panel, history, new_state, switched = app.delete_session(session_id, state)
            param_updates = _param_updates(new_state) if switched else (gr.update(),) * 4
            return (trace, panel, history, new_state, load_sessions_data(), *param_updates)

        def on_new_session(state):
            trace, panel, history, new_state = app.new_session(state)
            return (
                trace,
                panel,
                history,
                new_state,
                load_sessions_data(),
                *_param_updates(new_state),
            )

        def on_delete_all_sessions(state):
            trace, panel, history, new_state = app.delete_all_sessions(state)
            return (
                trace,
                panel,
                history,
                new_state,
                load_sessions_data(),
                *_param_updates(new_state),
                gr.update(visible=False),
            )

        def on_send(user_text, history, state):
            yield from app.send(user_text or "", history or [], state)

        def on_clear(state):
            return app.clear(state)

        mode_radio.change(
            on_mode_change,
            [mode_radio, session_state],
            [trace_output, session_state],
        )
        btn_apply_params.click(
            on_apply_params,
            [temp_slider, tokens_slider, iter_slider, session_state],
            [trace_output, session_state],
        )
        btn_apply_prompt.click(
            on_apply_prompt,
            [prompt_box, session_state],
            [trace_output, session_state],
        )
        btn_reset_prompt.click(
            on_reset_prompt,
            session_state,
            [prompt_box, trace_output, session_state],
        )
        btn_refresh_sessions.click(
            load_sessions_data,
            None,
            sessions_data,
        )
        btn_new_session.click(
            on_new_session,
            session_state,
            [
                trace_output,
                llm_output,
                chatbot,
                session_state,
                sessions_data,
                temp_slider,
                tokens_slider,
                iter_slider,
                prompt_box,
            ],
        )
        btn_delete_all_sessions.click(
            lambda: gr.update(visible=True),
            None,
            delete_all_confirm_box,
        )
        btn_cancel_delete_all.click(
            lambda: gr.update(visible=False),
            None,
            delete_all_confirm_box,
        )
        btn_confirm_delete_all.click(
            on_delete_all_sessions,
            session_state,
            [
                trace_output,
                llm_output,
                chatbot,
                session_state,
                sessions_data,
                temp_slider,
                tokens_slider,
                iter_slider,
                prompt_box,
                delete_all_confirm_box,
            ],
        )
        send_outputs = [trace_output, llm_output, chatbot, msg_input, session_state]
        btn_send.click(
            on_send,
            [msg_input, chatbot, session_state],
            send_outputs,
        )
        msg_input.submit(
            on_send,
            [msg_input, chatbot, session_state],
            send_outputs,
        )
        btn_clear.click(
            on_clear,
            session_state,
            [trace_output, llm_output, chatbot, session_state],
        )
        btn_clear_calls.click(
            app.clear_llm_calls,
            session_state,
            [llm_output, session_state],
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
        def _make_session_load_handler(session_id):
            def _handler(state):
                return on_session_select(session_id, state)

            return _handler

        def _make_session_delete_handler(session_id):
            def _handler(state):
                return on_session_delete(session_id, state)

            return _handler

        with session_list_container:

            @gr.render(inputs=sessions_data)
            def render_sessions(_sessions):
                sessions = app.list_sessions()
                if not sessions:
                    gr.Markdown("暂无历史会话")
                    return
                for session_id, title, info in sessions:
                    with gr.Row():
                        btn_load = gr.Button(f"{title}  {info}", size="sm", scale=6)
                        btn_del = gr.Button("🗑", size="sm", variant="stop", scale=1, min_width=36)
                    btn_load.click(
                        _make_session_load_handler(session_id),
                        session_state,
                        [
                            trace_output,
                            llm_output,
                            chatbot,
                            session_state,
                            mode_radio,
                            temp_slider,
                            tokens_slider,
                            iter_slider,
                            prompt_box,
                        ],
                    )
                    btn_del.click(
                        _make_session_delete_handler(session_id),
                        session_state,
                        [
                            trace_output,
                            llm_output,
                            chatbot,
                            session_state,
                            sessions_data,
                            temp_slider,
                            tokens_slider,
                            iter_slider,
                            prompt_box,
                        ],
                    )

        demo.load(load_sessions_data, None, sessions_data)

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
    try:
        demo.launch(
            server_name=args.host,
            server_port=args.port,
            share=args.share,
            inbrowser=False,
        )
    finally:
        if app.mcp_manager is not None:
            app.mcp_manager.close_all()


if __name__ == "__main__":
    main()
