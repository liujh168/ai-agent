"""
ai_agent.web_render — Web UI 呈现层（纯函数，无 Gradio / 网络 / App 状态依赖）
==========================================================================
从 web_ui.py 抽出的全部 HTML 渲染逻辑：LLM 调用检查器面板、执行轨迹、
JSON 语法高亮、复制按钮脚本，以及呈现层与 AgentApp 共享的 SessionState 结构契约。
输入是 SessionState / LLMCall，输出是 HTML 字符串；AgentApp 只负责状态装配与事件流。
"""

from __future__ import annotations

import html
import json
import re
from typing import Any, TypedDict

from ai_agent import llm_trace
from ai_agent.llm_trace import LLMCall

# 面板里保留的最近调用条数：跨轮保留历史，超长会话不至于让 DOM 无限膨胀
MAX_LLM_CALLS = 120


class SessionState(TypedDict, total=False):
    """Gradio State 里保存的单个浏览器会话状态（AgentApp 与呈现层共享的结构契约）。"""

    session_id: str | None
    mode: str
    history: list[dict]
    trace_buffer: list[dict]
    llm_calls: list[LLMCall]
    llm_revision: int
    llm_round: int
    llm_round_queries: dict[int, str]
    llm_dropped: int
    system_prompt: str
    params: dict[str, Any]


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


def copyable_pre(inner_html: str, css_class: str = "code-block") -> str:
    """带「📋 复制」按钮的代码块；复制逻辑由 web_ui.build_ui 注入的全局脚本统一处理。"""
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


def highlight_json(text: str) -> str:
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


# ============================================================
# LLM API 调用检查器面板
# ============================================================


def llm_panel_html(state: SessionState) -> str:
    calls = state.get("llm_calls") or []
    parts = [_llm_stats_html(llm_trace.usage_totals(calls))]
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
        parts.append(_llm_groups_html(state, calls))
    return '<div class="llm-panel">' + "".join(parts) + "</div>"


def _llm_groups_html(state: SessionState, calls: list[LLMCall]) -> str:
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
            cards.append(_llm_card_html(serial, call, expanded=group_index == last_group))
        query = html.escape(str(queries.get(round_no, "")))
        title = f"第 {round_no} 轮" if round_no else "未分组调用"
        subtitle = f" · {query}" if query else ""
        header = f"{title}{subtitle} · {len(group_calls)} 次调用"
        if group_index == last_group:
            html_parts.append(
                f'<div class="llm-round-head current">{html.escape(header)}</div>' + "".join(cards)
            )
        else:
            html_parts.append(
                '<details class="llm-round">'
                f"<summary>{html.escape(header)}</summary>" + "".join(cards) + "</details>"
            )
    return '<div class="llm-rounds">' + "".join(html_parts) + "</div>"


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


def _llm_card_html(index: int, call: LLMCall, expanded: bool = False) -> str:
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
        _llm_params_html(call),
        '<div class="llm-cols">',
        '<div class="llm-col"><div class="llm-sec-title">① 输入 · 发给模型的内容</div>',
        _messages_html(call.messages),
        _tools_html(call),
        "</div>",
        '<div class="llm-col"><div class="llm-sec-title">② 输出 · 模型返回的内容</div>',
        _output_html(call),
        "</div>",
        "</div>",
        '<div class="llm-sec">',
        _collapsible("请求 JSON（等价于 HTTP body）", llm_trace.request_json(call), json_body=True),
        _collapsible("响应 JSON", llm_trace.response_json(call), json_body=True),
        _collapsible("等价 API 调用代码（可复制运行）", llm_trace.request_code(call)),
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


def _llm_params_html(call: LLMCall) -> str:
    rows = [("model", call.model), ("base_url", call.base_url)]
    rows.extend((key, value) for key, value in call.params.items())
    cells = "".join(
        f'<div class="param"><span class="param-k">{html.escape(str(key))}</span>'
        f'<span class="param-v">{html.escape(str(value))}</span></div>'
        for key, value in rows
    )
    return f'<div class="llm-params">{cells}</div>'


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
            + copyable_pre(html.escape(body), "msg-body")
            + "</div>"
        )
    return '<div class="msg-list">' + "".join(blocks) + "</div>"


def _tools_html(call: LLMCall) -> str:
    if not call.tools:
        return '<div class="llm-none">（本次请求未携带 tools，模型无法进行 Function Calling）</div>'
    names = call.tool_names
    preview = "、".join(names[:8]) + ("…" if len(names) > 8 else "")
    return _collapsible(
        f"tools · 工具 schema（{len(call.tools)} 个）",
        f"可用工具：{preview}\n\n" + json.dumps(call.tools, ensure_ascii=False, indent=2),
    )


def _output_html(call: LLMCall) -> str:
    if call.status == "error":
        return f'<div class="llm-error">调用失败：{html.escape(str(call.error or "未知错误"))}</div>'

    parts = []
    if call.reasoning_content:
        parts.append(
            '<div class="out-block"><div class="out-title">reasoning_content · 模型内部推理</div>'
            + copyable_pre(html.escape(call.reasoning_content), "msg-body")
            + "</div>"
        )
    if call.content:
        parts.append(
            '<div class="out-block"><div class="out-title">content · 给用户的回答</div>'
            + copyable_pre(html.escape(call.content), "msg-body")
            + "</div>"
        )

    if call.tool_calls:
        parts.append(
            '<div class="out-block"><div class="out-title">'
            f"tool_calls · 模型要求执行的 {len(call.tool_calls)} 个工具</div>"
            + "".join(_tool_call_html(tool_call) for tool_call in call.tool_calls)
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
        + copyable_pre(highlight_json(arguments), "tc-args")
        + "</div>"
    )


def _collapsible(title: str, body: str, json_body: bool = False) -> str:
    content = highlight_json(body) if json_body else html.escape(body)
    return (
        '<details class="llm-more">'
        f"<summary>{html.escape(title)}</summary>"
        + copyable_pre(content, "code-block")
        + "</details>"
    )


# ============================================================
# 执行轨迹
# ============================================================


def trace_html(trace_buffer: list[dict]) -> str:
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
    for entry in trace_buffer:
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
