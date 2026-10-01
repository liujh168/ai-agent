"""CLI 侧的 LLM API 调用展示 —— 用 rich 把每次调用的输入输出打印到终端。

与 Web UI 的「API 调用检查器」共用同一条采集链路（``LMStudioClient.on_llm_call``
观察者 + ``llm_trace.LLMCall`` 快照），本模块只做终端渲染，不参与采集。
"""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console, Group
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from ai_agent import llm_trace
from ai_agent.llm_trace import LLMCall

ROLE_STYLE = {
    "system": "yellow",
    "user": "blue",
    "assistant": "green",
    "tool": "magenta",
}

# 面板里单段文本的展示上限：超出部分截断，完整内容可用 /calls json 查看
PANEL_TEXT_CHAR_LIMIT = 2000

_console = Console()


def get_console() -> Console:
    """CLI 共用的 rich Console（跟随 configure_utf8_output 之后的 stdout）。"""
    return _console


class CallRecorder:
    """``LMStudioClient`` 的观察者：收集一轮对话里的所有 LLM 调用快照。"""

    def __init__(self) -> None:
        self._calls: list[LLMCall] = []

    def __call__(self, phase: str, call: LLMCall) -> None:
        """实现观察者协议：按 id 去重更新（request / stream / response 各阶段）。"""
        for index, existing in enumerate(self._calls):
            if existing.id == call.id:
                self._calls[index] = call
                return
        self._calls.append(call)

    def reset(self) -> None:
        self._calls = []

    @property
    def calls(self) -> list[LLMCall]:
        return list(self._calls)

    def summary_line(self) -> Text:
        """一轮结束后的单行摘要：调用次数、token 用量、耗时。"""
        totals = llm_trace.usage_totals(self._calls)
        approx = "≈" if totals["estimated"] else ""
        text = Text()
        text.append("  ── LLM 调用 ", style="dim")
        text.append(f"{totals['calls']} 次", style="bold cyan")
        text.append(" · 输入 ", style="dim")
        text.append(f"{approx}{totals['prompt_tokens']:,}", style="cyan")
        text.append(" tok · 输出 ", style="dim")
        text.append(f"{approx}{totals['completion_tokens']:,}", style="cyan")
        text.append(" tok · 耗时 ", style="dim")
        text.append(f"{totals['latency_ms'] / 1000:.1f}s", style="cyan")
        if totals["failed"]:
            text.append(f" · {totals['failed']} 次失败", style="bold red")
        if totals["estimated"]:
            text.append("（≈ 为字符估算）", style="dim")
        text.append(" · 输入 /calls 查看完整输入输出 ──", style="dim")
        return text


def render_calls(console: Console, calls: list[LLMCall], show_json: bool = False) -> None:
    """把一轮里的每次调用渲染成面板：参数、消息、输出、用量；可选打印完整 JSON。"""
    if not calls:
        console.print("  [dim]还没有 LLM 调用记录。先发一条消息。[/dim]")
        return
    for index, call in enumerate(calls, 1):
        console.print(_render_call(index, call))
        if show_json:
            console.print(
                Panel(
                    Syntax(llm_trace.request_json(call), "json", word_wrap=True),
                    title="请求 JSON（等价于 HTTP body）",
                    border_style="blue",
                )
            )
            console.print(
                Panel(
                    Syntax(llm_trace.response_json(call), "json", word_wrap=True),
                    title="响应 JSON",
                    border_style="green",
                )
            )
            console.print(
                Panel(
                    Syntax(llm_trace.request_code(call), "python", word_wrap=True),
                    title="等价 API 调用代码（可复制运行）",
                    border_style="magenta",
                )
            )


def _render_call(index: int, call: LLMCall) -> Panel:
    status_text = {
        "pending": "⏳ 生成中",
        "ok": f"✅ {call.finish_reason or 'ok'}",
        "error": "❌ 调用失败",
    }.get(call.status, call.status)
    title = f"#{index} {call.label} · {status_text} · {call.latency_ms / 1000:.2f}s"
    border = {"pending": "yellow", "ok": "green", "error": "red"}.get(call.status, "white")

    sections: list[Any] = [_render_params(call), Text(""), _render_messages(call)]
    tools_text = _render_tools(call)
    if tools_text is not None:
        sections.extend([Text(""), tools_text])
    sections.extend([Text(""), _render_output(call)])
    return Panel(
        Group(*sections),
        title=title,
        border_style=border,
        padding=(0, 1),
    )


def _render_params(call: LLMCall) -> Text:
    text = Text()
    params = [("model", call.model), ("base_url", call.base_url), *call.params.items()]
    if call.stream:
        params.append(("stream", True))
    text.append("参数  ", style="dim")
    text.append("  ".join(f"{key}={value}" for key, value in params), style="cyan")
    return text


def _render_messages(call: LLMCall) -> Group:
    blocks: list[Any] = [Text("① 输入 · 发给模型的内容", style="bold blue")]
    if not call.messages:
        blocks.append(Text("  （本次请求没有携带消息）", style="dim"))
    for message in call.messages:
        role = str(message.get("role", "?"))
        style = ROLE_STYLE.get(role, "white")
        content = message.get("content")
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False, indent=2)
        header = Text()
        header.append(f"  [{role}]", style=f"bold {style}")
        header.append(f"  {len(content):,} 字符", style="dim")
        extras = []
        if message.get("tool_call_id"):
            extras.append(f"tool_call_id={message['tool_call_id']}")
        if message.get("tool_calls"):
            extras.append("tool_calls=" + json.dumps(message["tool_calls"], ensure_ascii=False))
        if extras:
            header.append("  " + " · ".join(extras), style="dim")
        blocks.append(header)
        blocks.append(Text(_indent(_clip(content)), style=style))
    return Group(*blocks)


def _render_tools(call: LLMCall) -> Text | None:
    if not call.tools:
        return Text("  （本次请求未携带 tools，模型无法进行 Function Calling）", style="dim")
    names = call.tool_names
    preview = "、".join(names[:10]) + ("…" if len(names) > 10 else "")
    text = Text()
    text.append(f"  tools · {len(call.tools)} 个工具 schema", style="bold cyan")
    text.append(f"（{preview}）", style="dim")
    text.append("  ——完整 schema 见 /calls json", style="dim")
    return text


def _render_output(call: LLMCall) -> Group:
    blocks: list[Any] = [Text("② 输出 · 模型返回的内容", style="bold green")]
    if call.status == "error":
        blocks.append(Text(f"  调用失败：{call.error or '未知错误'}", style="bold red"))
        return Group(*blocks)
    if call.reasoning_content:
        blocks.append(Text("  [reasoning_content · 模型内部推理]", style="bold magenta"))
        blocks.append(Text(_indent(_clip(call.reasoning_content)), style="magenta"))
    if call.content:
        blocks.append(Text("  [content · 给用户的回答]", style="bold green"))
        blocks.append(Text(_indent(_clip(call.content))))
    if call.tool_calls:
        blocks.append(Text(f"  [tool_calls · 模型要求执行的 {len(call.tool_calls)} 个工具]", style="bold cyan"))
        for tool_call in call.tool_calls:
            arguments = str(tool_call.get("arguments", ""))
            try:
                arguments = json.dumps(json.loads(arguments), ensure_ascii=False)
            except (json.JSONDecodeError, TypeError):
                pass
            blocks.append(Text(f"    🔧 {tool_call.get('name', '')}({arguments})", style="cyan"))
    if call.status == "pending":
        if len(blocks) == 1:
            blocks.append(Text("  等待模型返回…", style="dim"))
        else:
            blocks.append(Text("  ⏳ 正在生成…", style="yellow"))
        return Group(*blocks)
    if len(blocks) == 1:
        blocks.append(Text("  （content 为空）", style="dim"))
    usage = call.usage_with_fallback()
    approx = "≈" if usage["estimated"] else ""
    blocks.append(
        Text(
            f"  finish_reason={call.finish_reason} · "
            f"prompt {approx}{usage['prompt_tokens']:,} · "
            f"completion {approx}{usage['completion_tokens']:,} · "
            f"total {approx}{usage['total_tokens']:,}",
            style="dim",
        )
    )
    return Group(*blocks)


def _clip(text: str) -> str:
    if len(text) <= PANEL_TEXT_CHAR_LIMIT:
        return text
    return text[:PANEL_TEXT_CHAR_LIMIT] + f"\n…（已截断，共 {len(text):,} 字符；/calls json 看完整内容）"


def _indent(text: str) -> str:
    return "\n".join("    " + line for line in text.splitlines())
