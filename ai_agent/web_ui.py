"""Gradio Web UI with per-browser session state and typed Agent events."""

from __future__ import annotations

import html
import inspect
import json
import os
import queue
import re
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

from ai_agent import llm_trace
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

load_env()


SessionState = dict[str, Any]

# 面板里保留的最近调用条数：跨轮保留历史，超长会话不至于让 DOM 无限膨胀
MAX_LLM_CALLS = 120

# 采样参数默认值（Kimi Playground 式可调参数）
DEFAULT_RUN_PARAMS: dict[str, Any] = {
    "temperature": 0.7,
    "max_tokens": 2048,
    "max_iterations": 5,
}

ROLE_LABEL = {
    "system": "system · 系统提示词",
    "user": "user · 用户输入",
    "assistant": "assistant · 模型输出",
    "tool": "tool · 工具返回",
}
ROLE_CLASS = {
    "system": "role-system",
    "user": "role-user",
    "assistant": "role-assistant",
    "tool": "role-tool",
}


def _copyable_pre(inner_html: str, css_class: str = "code-block") -> str:
    """带「📋 复制」按钮的代码块；复制逻辑由 build_ui 注入的全局脚本统一处理。"""
    return (
        '<div class="code-wrap">'
        '<button class="copy-btn" type="button" title="复制到剪贴板">📋 复制</button>'
        f'<pre class="{css_class}">{inner_html}</pre>'
        "</div>"
    )


_JSON_TOKEN_RE = re.compile(
    r'("(?:\\.|[^"\\])*")(\s*:)?'  # 字符串（后跟冒号的是 key）
    r"|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"  # 数字
    r"|\b(true|false|null)\b"  # 字面量
)


def _highlight_json(text: str) -> str:
    """Python 侧 JSON 语法高亮：只给 token 包 <span>，结构字符原样输出（均为 HTML 安全字符）。"""

    def repl(match: re.Match) -> str:
        string, colon, number, literal = match.groups()
        if string is not None:
            cls = "json-key" if colon else "json-str"
            suffix = html.escape(colon) if colon else ""
            return f'<span class="{cls}">{html.escape(string)}</span>{suffix}'
        if number is not None:
            return f'<span class="json-num">{html.escape(number)}</span>'
        return f'<span class="json-bool">{html.escape(literal)}</span>'

    return _JSON_TOKEN_RE.sub(repl, text)


# 复制按钮的全局事件委托：注入一次，对所有动态渲染的卡片生效
COPY_BUTTON_HEAD = """<script>
document.addEventListener('click', function (event) {
  var btn = event.target.closest('.copy-btn');
  if (!btn) return;
  var wrap = btn.closest('.code-wrap');
  var pre = wrap ? wrap.querySelector('pre') : null;
  if (!pre) return;
  navigator.clipboard.writeText(pre.innerText).then(function () {
    btn.textContent = '已复制 ✓';
    setTimeout(function () { btn.textContent = '📋 复制'; }, 1200);
  });
});
</script>"""


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
        calls = state.get("llm_calls") or []
        parts = [self._llm_stats_html(llm_trace.usage_totals(calls))]
        if not calls:
            parts.append(
                '<div class="llm-empty">'
                "还没有 API 调用。<br>"
                "发一条消息，这里会立刻出现本次请求的完整输入（system / user 消息 + 工具 schema + 采样参数），"
                "模型返回后同一张卡片会补齐输出。"
                "</div>"
            )
        else:
            dropped = state.get("llm_dropped", 0)
            if dropped:
                parts.append(
                    f'<div class="llm-dropped">更早的 {dropped} 条调用记录已丢弃'
                    f"（面板最多保留 {MAX_LLM_CALLS} 条）</div>"
                )
            parts.append(self._llm_groups_html(state, calls))
        return '<div class="llm-panel">' + "".join(parts) + "</div>"

    def _llm_groups_html(self, state: SessionState, calls: list[LLMCall]) -> str:
        """把调用卡片按轮次分组：历史轮折叠，最新一轮的卡片默认展开。"""
        queries = state.get("llm_round_queries") or {}
        groups: list[tuple[int, list[LLMCall]]] = []
        for call in calls:
            round_no = getattr(call, "round_no", 0)
            if groups and groups[-1][0] == round_no:
                groups[-1][1].append(call)
            else:
                groups.append((round_no, [call]))

        last_group = len(groups) - 1
        serial = 0
        html_parts = []
        for group_index, (round_no, group_calls) in enumerate(groups):
            cards = []
            for call in group_calls:
                serial += 1
                cards.append(
                    self._llm_card_html(serial, call, expanded=group_index == last_group)
                )
            query = html.escape(str(queries.get(round_no, "")))
            title = f"第 {round_no} 轮" if round_no else "未分组调用"
            subtitle = f" · {query}" if query else ""
            header = f"{title}{subtitle} · {len(group_calls)} 次调用"
            if group_index == last_group:
                html_parts.append(
                    f'<div class="llm-round-head current">{html.escape(header)}</div>'
                    + "".join(cards)
                )
            else:
                html_parts.append(
                    '<details class="llm-round">'
                    f"<summary>{html.escape(header)}</summary>"
                    + "".join(cards)
                    + "</details>"
                )
        return '<div class="llm-rounds">' + "".join(html_parts) + "</div>"

    @staticmethod
    def _llm_stats_html(stats: dict[str, Any]) -> str:
        approx = "≈" if stats["estimated"] else ""
        failed = f'<span class="stat stat-bad">{stats["failed"]} 次失败</span>' if stats["failed"] else ""
        hint = '<span class="stat stat-hint">≈ 表示按字符数估算</span>' if stats["estimated"] else ""
        return (
            '<div class="llm-stats">'
            f'<span class="stat">共 <b>{stats["calls"]}</b> 次 API 调用</span>'
            f'<span class="stat">输入 <b>{approx}{stats["prompt_tokens"]:,}</b> tok</span>'
            f'<span class="stat">输出 <b>{approx}{stats["completion_tokens"]:,}</b> tok</span>'
            f'<span class="stat">合计 <b>{approx}{stats["total_tokens"]:,}</b> tok</span>'
            f'<span class="stat">耗时 <b>{stats["latency_ms"] / 1000:.1f}</b> s</span>'
            f"{failed}{hint}"
            "</div>"
        )

    def _llm_card_html(self, index: int, call: LLMCall, expanded: bool = False) -> str:
        usage = call.usage_with_fallback()
        approx = "≈" if usage["estimated"] else ""
        status_text = {
            "pending": "⏳ 生成中…",
            "ok": f"✅ {call.finish_reason or 'ok'}",
            "error": "❌ 调用失败",
        }.get(call.status, call.status)
        open_attr = " open" if expanded else ""

        meta = [
            f'<span class="chip">{html.escape(call.model)}</span>',
            f'<span class="chip">{call.latency_ms / 1000:.2f}s</span>',
            f'<span class="chip">{approx}{usage["total_tokens"]:,} tok</span>',
        ]
        if call.tools:
            meta.append(f'<span class="chip chip-tool">{len(call.tools)} 个工具</span>')
        if call.stream:
            meta.append('<span class="chip">stream</span>')

        body = [
            self._llm_params_html(call),
            '<div class="llm-cols">',
            '<div class="llm-col"><div class="llm-sec-title">① 输入 · 发给模型的内容</div>',
            self._messages_html(call.messages),
            self._tools_html(call),
            "</div>",
            '<div class="llm-col"><div class="llm-sec-title">② 输出 · 模型返回的内容</div>',
            self._output_html(call),
            "</div>",
            "</div>",
            '<div class="llm-sec">',
            self._collapsible("请求 JSON（等价于 HTTP body）", llm_trace.request_json(call), json_body=True),
            self._collapsible("响应 JSON", llm_trace.response_json(call), json_body=True),
            self._collapsible("等价 API 调用代码（可复制运行）", llm_trace.request_code(call)),
            "</div>",
        ]

        return (
            f'<details class="llm-card status-{call.status}"{open_attr}>'
            f'<summary><span class="llm-idx">#{index + 1}</span>'
            f'<span class="llm-label">{html.escape(call.label)}</span>'
            f'<span class="llm-status">{status_text}</span>'
            f'<span class="llm-meta">{"".join(meta)}</span></summary>'
            f'<div class="llm-body">{"".join(body)}</div>'
            "</details>"
        )

    @staticmethod
    def _llm_params_html(call: LLMCall) -> str:
        rows = [("model", call.model), ("base_url", call.base_url)]
        rows.extend((key, value) for key, value in call.params.items())
        cells = "".join(
            f'<div class="param"><span class="param-k">{html.escape(str(key))}</span>'
            f'<span class="param-v">{html.escape(str(value))}</span></div>'
            for key, value in rows
        )
        return f'<div class="llm-params">{cells}</div>'

    @staticmethod
    def _messages_html(messages: list[dict]) -> str:
        if not messages:
            return '<div class="llm-none">（本次请求没有携带消息）</div>'
        blocks = []
        for message in messages:
            role = str(message.get("role", "?"))
            label = ROLE_LABEL.get(role, role)
            css = ROLE_CLASS.get(role, "role-other")
            content = message.get("content")
            if isinstance(content, str):
                body = content
            else:
                body = json.dumps(content, ensure_ascii=False, indent=2)
            extras = []
            if message.get("tool_call_id"):
                extras.append(f'tool_call_id = {message["tool_call_id"]}')
            if message.get("tool_calls"):
                extras.append("tool_calls = " + json.dumps(message["tool_calls"], ensure_ascii=False))
            extra_html = (
                '<div class="msg-extra">' + html.escape(" · ".join(extras)) + "</div>" if extras else ""
            )
            blocks.append(
                f'<div class="msg {css}">'
                f'<div class="msg-head"><span class="msg-role">{html.escape(label)}</span>'
                f'<span class="msg-len">{len(body):,} 字符</span></div>'
                f"{extra_html}"
                + _copyable_pre(html.escape(body), "msg-body")
                + "</div>"
            )
        return '<div class="msg-list">' + "".join(blocks) + "</div>"

    @staticmethod
    def _tools_html(call: LLMCall) -> str:
        if not call.tools:
            return '<div class="llm-none">（本次请求未携带 tools，模型无法进行 Function Calling）</div>'
        names = call.tool_names
        preview = "、".join(names[:8]) + ("…" if len(names) > 8 else "")
        return AgentApp._collapsible(
            f"tools · 工具 schema（{len(call.tools)} 个）",
            f"可用工具：{preview}\n\n" + json.dumps(call.tools, ensure_ascii=False, indent=2),
        )

    @staticmethod
    def _output_html(call: LLMCall) -> str:
        if call.status == "error":
            return f'<div class="llm-error">调用失败：{html.escape(str(call.error or "未知错误"))}</div>'

        parts = []
        if call.reasoning_content:
            parts.append(
                '<div class="out-block"><div class="out-title">reasoning_content · 模型内部推理</div>'
                + _copyable_pre(html.escape(call.reasoning_content), "msg-body")
                + "</div>"
            )
        if call.content:
            parts.append(
                '<div class="out-block"><div class="out-title">content · 给用户的回答</div>'
                + _copyable_pre(html.escape(call.content), "msg-body")
                + "</div>"
            )

        if call.tool_calls:
            parts.append(
                '<div class="out-block"><div class="out-title">'
                f"tool_calls · 模型要求执行的 {len(call.tool_calls)} 个工具</div>"
                + "".join(AgentApp._tool_call_html(tool_call) for tool_call in call.tool_calls)
                + '<div class="llm-note">'
                "工具执行结果会以 <code>role: tool</code> 消息回到下一轮请求的输入里——"
                "在下一次调用的「① 输入」中可以看到它的完整样子。"
                "</div></div>"
            )

        if call.status == "pending":
            if not parts:
                return '<div class="llm-pending">等待模型返回…</div>'
            # 流式进行中：先展示已生成的部分
            parts.append('<div class="llm-pending streaming">⏳ 正在生成…</div>')
            return "".join(parts)

        if not parts:
            parts.append('<div class="llm-none">（content 为空）</div>')

        usage = call.usage_with_fallback()
        approx = "≈" if usage["estimated"] else ""
        parts.append(
            '<div class="out-usage">'
            f'<span class="chip">finish_reason: {html.escape(str(call.finish_reason))}</span>'
            f'<span class="chip">prompt {approx}{usage["prompt_tokens"]:,}</span>'
            f'<span class="chip">completion {approx}{usage["completion_tokens"]:,}</span>'
            f'<span class="chip">total {approx}{usage["total_tokens"]:,}</span>'
            "</div>"
        )
        return "".join(parts)

    @staticmethod
    def _tool_call_html(tool_call: dict) -> str:
        arguments = str(tool_call.get("arguments", ""))
        try:
            arguments = json.dumps(json.loads(arguments), ensure_ascii=False, indent=2)
        except (json.JSONDecodeError, TypeError):
            pass
        return (
            '<div class="tc">'
            f'<div class="tc-head">🔧 <b>{html.escape(str(tool_call.get("name", "")))}</b>'
            f'<span class="tc-id">{html.escape(str(tool_call.get("id", "")))}</span></div>'
            + _copyable_pre(_highlight_json(arguments), "tc-args")
            + "</div>"
        )

    @staticmethod
    def _collapsible(title: str, body: str, json_body: bool = False) -> str:
        content = _highlight_json(body) if json_body else html.escape(body)
        return (
            '<details class="llm-more">'
            f"<summary>{html.escape(title)}</summary>"
            + _copyable_pre(content, "code-block")
            + "</details>"
        )

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

        reply = run_multi_agent(
            self.client,
            user_text,
            max_rounds=2,
            temperature=params["temperature"],
            max_tokens=params["max_tokens"],
            trace_callback=on_trace,
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
    css = """
    /* ---------- 执行轨迹 ---------- */
    .trace-box {
        background: #1a1a2e;
        border-radius: 8px;
        padding: 10px 12px;
        min-height: 180px;
        max-height: 560px;
        overflow-y: auto;
        font-family: 'IBM Plex Mono', 'Cascadia Code', Consolas, monospace;
    }
    .trace-box pre {
        white-space: pre-wrap;
        word-break: break-all;
    }

    /* ---------- LLM API 调用检查器 ---------- */
    .llm-panel {
        background: #0f1117;
        border: 1px solid #232735;
        border-radius: 10px;
        padding: 10px;
        max-height: 620px;
        overflow-y: auto;
        font-family: 'IBM Plex Mono', 'Cascadia Code', Consolas, monospace;
        font-size: 12.5px;
        line-height: 1.6;
        color: #d7dae0;
    }
    .llm-stats {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        padding: 4px 0 10px;
        margin-bottom: 10px;
        border-bottom: 1px solid #232735;
        position: sticky;
        top: -10px;
        z-index: 5;
        background: #0f1117;
    }
    .llm-stats .stat {
        background: #1a1f2e;
        border: 1px solid #2a3145;
        border-radius: 999px;
        padding: 2px 10px;
        color: #9aa4b8;
    }
    .llm-stats .stat b { color: #e8ebf0; }
    .llm-stats .stat-bad { border-color: #7f2b2b; color: #ff9c9c; }
    .llm-stats .stat-hint { border-style: dashed; color: #7b8496; }
    .llm-empty, .llm-none, .llm-pending {
        color: #7b8496;
        padding: 12px;
        line-height: 1.9;
        background: #141824;
        border-radius: 8px;
    }
    .llm-card {
        border: 1px solid #2a3145;
        border-radius: 8px;
        margin-bottom: 8px;
        background: #141824;
    }
    .llm-card > summary {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;
        padding: 8px 10px;
        cursor: pointer;
        list-style: none;
    }
    .llm-card > summary::-webkit-details-marker { display: none; }
    .llm-card[open] > summary { border-bottom: 1px solid #2a3145; }
    @keyframes llm-pulse {
        0%, 100% { box-shadow: inset 3px 0 0 #d9a441; }
        50% { box-shadow: inset 3px 0 0 rgba(217, 164, 65, 0.25); }
    }
    .llm-card.status-pending > summary {
        box-shadow: inset 3px 0 0 #d9a441;
        animation: llm-pulse 1.4s ease-in-out infinite;
    }
    .llm-card.status-ok > summary { box-shadow: inset 3px 0 0 #3f9e63; }
    .llm-card.status-error > summary { box-shadow: inset 3px 0 0 #c0392b; }
    .llm-idx { color: #6f7b91; }
    .llm-label { color: #e8ebf0; font-weight: 600; }
    .llm-status { color: #9aa4b8; }
    .llm-meta { margin-left: auto; display: flex; gap: 5px; flex-wrap: wrap; }
    .chip {
        background: #1e2434;
        border: 1px solid #2f374b;
        border-radius: 6px;
        padding: 1px 7px;
        color: #9aa4b8;
        font-size: 11.5px;
    }
    .chip-tool { border-color: #3d5a80; color: #8fb8e8; }
    .llm-body { padding: 10px; }
    .llm-params { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
    .param {
        background: #1a1f2e;
        border: 1px solid #262d3f;
        border-radius: 6px;
        padding: 3px 8px;
    }
    .param-k { color: #7b8496; margin-right: 6px; }
    .param-v { color: #cfd6e4; }
    .llm-sec { margin-bottom: 12px; }
    .llm-sec-title { color: #8fb8e8; font-weight: 600; margin: 6px 0; }
    .msg-list { display: flex; flex-direction: column; gap: 6px; }
    .msg {
        border: 1px solid #262d3f;
        border-left-width: 3px;
        border-radius: 6px;
        background: #11151f;
    }
    .msg-head {
        display: flex;
        gap: 8px;
        padding: 4px 8px;
        border-bottom: 1px solid #1d2331;
    }
    .msg-role { font-weight: 600; }
    .msg-len { color: #6f7b91; margin-left: auto; }
    .msg-extra { color: #6f7b91; padding: 2px 8px; word-break: break-all; }
    .msg-body, .code-block, .tc-args {
        margin: 0;
        padding: 8px;
        white-space: pre-wrap;
        word-break: break-word;
        max-height: 280px;
        overflow: auto;
        color: #cfd6e4;
    }
    .role-system { border-left-color: #d9a441; }
    .role-system .msg-role { color: #d9a441; }
    .role-user { border-left-color: #3d8bfd; }
    .role-user .msg-role { color: #6fb0ff; }
    .role-assistant { border-left-color: #3f9e63; }
    .role-assistant .msg-role { color: #62c98a; }
    .role-tool { border-left-color: #9b6bd6; }
    .role-tool .msg-role { color: #b98bea; }
    .out-block {
        border: 1px solid #262d3f;
        border-radius: 6px;
        background: #11151f;
        margin-bottom: 6px;
    }
    .out-title {
        padding: 4px 8px;
        border-bottom: 1px solid #1d2331;
        color: #62c98a;
        font-weight: 600;
    }
    .out-usage { display: flex; gap: 5px; flex-wrap: wrap; margin-top: 6px; }
    .llm-error {
        background: #2a1416;
        border: 1px solid #7f2b2b;
        color: #ff9c9c;
        border-radius: 6px;
        padding: 8px;
    }
    .llm-note { color: #7b8496; padding: 6px 8px; line-height: 1.8; }
    .llm-note code {
        background: #1e2434;
        padding: 0 4px;
        border-radius: 4px;
        color: #b98bea;
    }
    .tc {
        border: 1px solid #2f374b;
        border-radius: 6px;
        background: #11151f;
        margin: 5px 0;
    }
    .tc-head { padding: 4px 8px; border-bottom: 1px solid #1d2331; color: #8fb8e8; }
    .tc-id { color: #6f7b91; margin-left: 8px; font-size: 11.5px; }
    .llm-more { border: 1px dashed #2f374b; border-radius: 6px; margin-top: 6px; }
    .llm-more > summary { padding: 5px 8px; cursor: pointer; color: #9aa4b8; }
    .llm-more[open] > summary { border-bottom: 1px solid #1d2331; }
    .inspector-hint { color: #7b8496; font-size: 12.5px; line-height: 1.8; }

    /* ---------- 轮次分组 ---------- */
    .llm-round {
        border: 1px solid #232735;
        border-radius: 8px;
        margin-bottom: 8px;
        background: #10141d;
    }
    .llm-round > summary {
        padding: 7px 10px;
        cursor: pointer;
        color: #9aa4b8;
        font-size: 12px;
    }
    .llm-round > summary:hover { color: #cfd6e4; }
    .llm-round[open] > summary { border-bottom: 1px dashed #232735; }
    .llm-round-head {
        color: #8fb8e8;
        font-size: 12px;
        font-weight: 600;
        padding: 4px 2px 8px;
    }
    .llm-dropped {
        color: #7b8496;
        font-size: 12px;
        border: 1px dashed #2f374b;
        border-radius: 6px;
        padding: 4px 8px;
        margin-bottom: 8px;
    }

    /* ---------- 输入输出对照双栏 ---------- */
    .llm-cols {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 10px;
        margin-bottom: 12px;
    }
    @media (max-width: 900px) {
        .llm-cols { grid-template-columns: 1fr; }
    }
    .llm-col { min-width: 0; }

    /* ---------- 复制按钮 ---------- */
    .code-wrap { position: relative; }
    .copy-btn {
        position: absolute;
        top: 4px;
        right: 4px;
        z-index: 2;
        background: #1e2434;
        border: 1px solid #2f374b;
        border-radius: 6px;
        color: #9aa4b8;
        font-size: 11px;
        padding: 1px 8px;
        cursor: pointer;
        opacity: 0;
        transition: opacity 0.15s;
    }
    .code-wrap:hover .copy-btn { opacity: 1; }
    .copy-btn:hover { color: #e8ebf0; border-color: #3d5a80; }

    /* ---------- JSON 语法高亮 ---------- */
    .json-key { color: #8fb8e8; }
    .json-str { color: #98c379; }
    .json-num { color: #d19a66; }
    .json-bool { color: #c678dd; }

    /* ---------- 流式生成中 ---------- */
    .llm-pending.streaming {
        margin-top: 6px;
        padding: 4px 8px;
        animation: llm-pulse 1.4s ease-in-out infinite;
    }
    """

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
