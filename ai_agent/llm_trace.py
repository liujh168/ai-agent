"""LLM API 调用快照 —— 记录每次 ``chat.completions.create`` 的输入与输出。

为什么单独一层
--------------
Function Calling / ReAct / 多 Agent / 思维链 / 反思 / 规划 / 结构化提取
最终都会落到 ``LMStudioClient`` 上发起 HTTP 请求。把快照逻辑收在这一个模块里，
``LMStudioClient`` 只要在发起调用前后各通知一次，就能让**所有模式**的 API
输入输出都被同一条链路捕获，不存在某个演示模式"看不到请求"的遗漏。

快照是只读的展示副本
--------------------
:func:`sanitize_messages` 会做深拷贝，并把 base64 图片等大体积内容替换成占位符。
真正的请求始终使用调用方传入的原始 ``messages`` 对象，快照绝不回流进请求，
因此不存在"截图把图片换掉导致模型收不到图"的风险。
"""

from __future__ import annotations

import copy
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# 单条消息在「等价 API 调用代码」里内联展示的字符上限（超出部分标注已截断）
CODE_MESSAGE_CHAR_LIMIT = 200


@dataclass
class LLMCall:
    """一次 LLM API 调用的输入输出快照（可变：请求阶段创建，响应阶段回填）。"""

    id: str
    label: str
    model: str
    base_url: str = ""
    messages: list[dict] = field(default_factory=list)
    tools: list[dict] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    stream: bool = False
    started_at: float = field(default_factory=time.time)
    # Web UI 按「第几轮用户提问」给调用分组；0 表示未分组（CLI 等场景）
    round_no: int = 0

    # ---- 响应阶段回填 ----
    status: str = "pending"  # pending | ok | error
    finished_at: float | None = None
    content: str = ""
    reasoning_content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def latency_ms(self) -> float:
        """本次调用耗时（毫秒）；未结束时按"到此刻为止"计算。"""
        end = self.finished_at if self.finished_at is not None else time.time()
        return round((end - self.started_at) * 1000, 1)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def tool_names(self) -> list[str]:
        names = []
        for schema in self.tools:
            function = schema.get("function") if isinstance(schema, dict) else None
            if isinstance(function, dict) and function.get("name"):
                names.append(str(function["name"]))
        return names

    def usage_with_fallback(self) -> dict[str, Any]:
        """返回 token 用量；服务端未返回 usage 时用字符数做粗略估算。

        返回字典的 ``estimated`` 键标识该数字是估算值，UI 需要如实标注，
        不能把估算值当成真实计费数据展示。
        """
        prompt = _as_int(self.usage.get("prompt_tokens"))
        completion = _as_int(self.usage.get("completion_tokens"))
        if prompt or completion:
            return {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": _as_int(self.usage.get("total_tokens")) or (prompt + completion),
                "estimated": False,
            }
        prompt = sum(estimate_tokens(_message_text(message)) for message in self.messages)
        completion = estimate_tokens(self.content) + estimate_tokens(self.reasoning_content)
        completion += sum(estimate_tokens(str(call.get("arguments", ""))) for call in self.tool_calls)
        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
            "estimated": True,
        }


# ============================================================
# 采集：由 LMStudioClient 在调用前后调用
# ============================================================


def begin_call(
    *,
    label: str,
    model: str,
    messages: list[dict] | None = None,
    tools: list[dict] | None = None,
    params: dict[str, Any] | None = None,
    base_url: str = "",
    stream: bool = False,
) -> LLMCall:
    """创建一次调用的快照（此时只有输入，还没有输出）。"""
    return LLMCall(
        id=uuid.uuid4().hex[:12],
        label=label or "LLM 调用",
        model=model,
        base_url=base_url,
        messages=sanitize_messages(messages or []),
        tools=copy.deepcopy(list(tools or [])),
        params=dict(params or {}),
        stream=stream,
    )


def apply_response(call: LLMCall, response: Any) -> LLMCall:
    """把 OpenAI SDK 的响应对象回填进快照。"""
    choice = _first_choice(response)
    message = getattr(choice, "message", None)
    call.finish_reason = _as_text(getattr(choice, "finish_reason", None)) or None
    call.content = _as_text(getattr(message, "content", "") or "")
    call.reasoning_content = _as_text(getattr(message, "reasoning_content", None) or "")
    call.tool_calls = _dump_tool_calls(getattr(message, "tool_calls", None))
    call.usage = _dump_usage(getattr(response, "usage", None))
    call.status = "ok"
    call.finished_at = time.time()
    return call


def apply_stream_end(call: LLMCall) -> LLMCall:
    """流式响应结束：标记完成（流式不采集 token 用量，交由 UI 估算）。"""
    call.status = "ok"
    call.finish_reason = call.finish_reason or "stream"
    call.finished_at = time.time()
    return call


def append_stream_chunk(call: LLMCall, chunk: Any) -> bool:
    """把一个流式 chunk 的增量合并进快照，返回是否有可见内容变化。

    OpenAI 流式协议里 ``delta.tool_calls`` 按 index 分片下发，这里聚合成
    ``call.tool_calls`` 里的完整条目，让 UI 在生成过程中就能看到工具调用。
    """
    changed = False
    choice = _first_choice(chunk)
    if choice is None:
        return False
    delta = getattr(choice, "delta", None)
    content = _as_text(getattr(delta, "content", None) or "")
    if content:
        call.content += content
        changed = True
    reasoning = _as_text(getattr(delta, "reasoning_content", None) or "")
    if reasoning:
        call.reasoning_content += reasoning
        changed = True
    for tool_call in getattr(delta, "tool_calls", None) or []:
        index = _as_int(getattr(tool_call, "index", 0))
        while len(call.tool_calls) <= index:
            call.tool_calls.append({"id": "", "name": "", "arguments": ""})
        entry = call.tool_calls[index]
        call_id = _as_text(getattr(tool_call, "id", "") or "")
        if call_id:
            entry["id"] = call_id
        function = getattr(tool_call, "function", None)
        name = _as_text(getattr(function, "name", "") or "")
        if name:
            entry["name"] += name
        arguments = _as_text(getattr(function, "arguments", "") or "")
        if arguments:
            entry["arguments"] += arguments
        changed = True
    finish_reason = _as_text(getattr(choice, "finish_reason", None) or "")
    if finish_reason:
        call.finish_reason = finish_reason
        changed = True
    return changed


def apply_error(call: LLMCall, error: BaseException) -> LLMCall:
    """调用失败：记下异常类型与消息，供 UI 原样展示。"""
    call.status = "error"
    call.error = f"{type(error).__name__}: {error}"
    call.finish_reason = "error"
    call.finished_at = time.time()
    return call


# ============================================================
# 展示：把快照渲染成 JSON / Python 代码 / 统计数字
# ============================================================


def sanitize_messages(messages: list[dict]) -> list[dict]:
    """深拷贝并净化消息列表：base64 图片等大体积内容替换为占位符。

    注意：返回值仅用于展示，**不会**被送回模型。
    """
    cleaned: list[dict] = []
    for message in messages or []:
        if not isinstance(message, dict):
            cleaned.append({"role": "unknown", "content": str(message)})
            continue
        item = {key: copy.deepcopy(value) for key, value in message.items() if key != "content"}
        item["content"] = _sanitize_content(message.get("content"))
        cleaned.append(item)
    return cleaned


def request_payload(call: LLMCall) -> dict[str, Any]:
    """还原这次调用的请求体（顺序贴近 OpenAI API 文档）。"""
    payload: dict[str, Any] = {"model": call.model, "messages": call.messages}
    if call.tools:
        payload["tools"] = call.tools
    payload.update(call.params)
    if call.stream:
        payload["stream"] = True
    return payload


def request_json(call: LLMCall) -> str:
    return json.dumps(request_payload(call), ensure_ascii=False, indent=2)


def response_payload(call: LLMCall) -> dict[str, Any]:
    """还原这次调用的响应体（与 OpenAI 返回结构对齐，便于对照官方文档）。"""
    if call.status == "error":
        return {"error": {"type": "调用失败", "message": call.error}}
    message: dict[str, Any] = {"role": "assistant", "content": call.content}
    if call.reasoning_content:
        message["reasoning_content"] = call.reasoning_content
    if call.tool_calls:
        message["tool_calls"] = [
            {
                "id": tool_call.get("id", ""),
                "type": "function",
                "function": {
                    "name": tool_call.get("name", ""),
                    "arguments": tool_call.get("arguments", ""),
                },
            }
            for tool_call in call.tool_calls
        ]
    payload: dict[str, Any] = {
        "choices": [{"index": 0, "finish_reason": call.finish_reason, "message": message}]
    }
    if call.usage:
        payload["usage"] = call.usage
    if call.status == "pending":
        payload["note"] = "模型仍在生成中…"
    return payload


def response_json(call: LLMCall) -> str:
    return json.dumps(response_payload(call), ensure_ascii=False, indent=2)


def request_code(call: LLMCall) -> str:
    """生成与本次调用等价、可直接复制运行的 Python 代码。"""
    lines = [
        "from openai import OpenAI",
        "",
        "client = OpenAI(",
        f'    base_url="{call.base_url}",',
        '    api_key="lm-studio",',
        ")",
        "",
        "resp = client.chat.completions.create(",
        f'    model="{call.model}",',
        "    messages=[",
    ]
    for message in call.messages:
        lines.append(f"        {_message_literal(message)},")
    lines.append("    ],")
    for key, value in call.params.items():
        lines.append(f"    {key}={value!r},")
    if call.tools:
        lines.append(f"    tools=tools,  # {len(call.tools)} 个工具 schema，见「请求」标签")
    lines.append(")")
    lines.append("")
    lines.append("print(resp.choices[0].message.content)")
    return "\n".join(lines)


def usage_totals(calls: list[LLMCall]) -> dict[str, Any]:
    """汇总一组调用的 token 与耗时，供面板顶部统计条使用。"""
    prompt = completion = total = 0
    latency_ms = 0.0
    estimated = False
    failed = 0
    for call in calls:
        if call.status == "error":
            failed += 1
        usage = call.usage_with_fallback()
        prompt += usage["prompt_tokens"]
        completion += usage["completion_tokens"]
        total += usage["total_tokens"]
        estimated = estimated or usage["estimated"]
        if call.finished_at is not None:
            latency_ms += call.latency_ms
    return {
        "calls": len(calls),
        "failed": failed,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "latency_ms": round(latency_ms, 1),
        "estimated": estimated,
    }


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数：中日韩字符约 1 token/字，其余字符约 4 字符/token。

    仅用于服务端未返回 usage 时的兜底展示。
    """
    if not text:
        return 0
    cjk = sum(1 for char in text if "\u3400" <= char <= "\u9fff" or "\u3040" <= char <= "\u30ff")
    other = len(text) - cjk
    return cjk + (other + 3) // 4


# ============================================================
# 内部辅助
# ============================================================


def _sanitize_content(content: Any) -> Any:
    """净化单条消息的 content：多模态数组里的 base64 数据替换为占位符。"""
    if isinstance(content, list):
        blocks = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "image_url":
                image_url = block.get("image_url")
                url = image_url.get("url", "") if isinstance(image_url, dict) else str(image_url)
                blocks.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": _shorten_data_url(url)},
                    }
                )
            else:
                blocks.append(copy.deepcopy(block))
        return blocks
    if isinstance(content, str):
        return _shorten_data_url(content)
    return copy.deepcopy(content)


def _shorten_data_url(text: str) -> str:
    """把 ``data:image/png;base64,AAAA…`` 折叠成可读的占位说明。"""
    marker = ";base64,"
    index = text.find(marker)
    if not text.startswith("data:") or index < 0:
        return text
    header = text[: index + len(marker)]
    payload = text[index + len(marker) :]
    if len(payload) <= 64:
        return text
    return f"{header}<已省略 {len(payload)} 字符的 base64 图像数据>"


def _message_literal(message: dict, limit: int = CODE_MESSAGE_CHAR_LIMIT) -> str:
    """把一条消息渲染成代码里的字面量（长文本截断并标注）。"""
    parts = [f'"role": {json.dumps(message.get("role", ""), ensure_ascii=False)}']
    content = message.get("content")
    if isinstance(content, str) and len(content) > limit:
        clipped = content[:limit] + f"…（已截断，原文 {len(content)} 字符）"
        parts.append(f'"content": {json.dumps(clipped, ensure_ascii=False)}')
    else:
        parts.append(f'"content": {json.dumps(content, ensure_ascii=False)}')
    if message.get("tool_call_id"):
        parts.append(f'"tool_call_id": {json.dumps(message["tool_call_id"], ensure_ascii=False)}')
    if message.get("tool_calls"):
        parts.append(f'"tool_calls": {json.dumps(message["tool_calls"], ensure_ascii=False)}')
    return "{" + ", ".join(parts) + "}"


def _message_text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") for block in content if isinstance(block, dict)
        )
    return ""


def _first_choice(response: Any) -> Any:
    choices = getattr(response, "choices", None)
    if not choices:
        return None
    return choices[0]


def _dump_tool_calls(tool_calls: Any) -> list[dict]:
    dumped = []
    for tool_call in tool_calls or []:
        function = getattr(tool_call, "function", None)
        dumped.append(
            {
                "id": _as_text(getattr(tool_call, "id", "")),
                "name": _as_text(getattr(function, "name", "")),
                "arguments": _as_text(getattr(function, "arguments", "")),
            }
        )
    return dumped


def _dump_usage(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    if isinstance(usage, dict):
        source: dict[str, Any] = usage
    else:
        source = {
            key: getattr(usage, key, None)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        }
    return {key: value for key, value in source.items() if isinstance(value, (int, float))}


def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else ("" if value is None else str(value))


def _as_int(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) else 0
