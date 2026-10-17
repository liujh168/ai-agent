"""
LM Studio 本地 LLM API 客户端
===============================
负责所有与 LM Studio OpenAI 兼容 API 直接交互的逻辑。
RAGKnowledgeBase 已拆分到 ai_agent/rag.py；
演示级 Agent 算法（ReAct / 思维链 / 反思 / 规划 / 结构化提取）在 ai_agent/demos/，
本类的同名方法只是保持 API 不变的薄委托。
"""

import base64
import logging
import time
from collections.abc import Callable

import requests
from openai import OpenAI

from ai_agent import llm_trace
from ai_agent.demos import (
    run_native_reasoning,
    run_plan_and_execute,
    run_react,
    run_reasoning,
    run_reflexion,
    run_structured_extract,
)
from ai_agent.llm_trace import LLMCall
from ai_agent.tools.fs import resolve_workspace_path

log = logging.getLogger("Agent")

# LLM 调用观察者签名：(阶段, 快照)。阶段为 "request"（即将发出请求）、
# "stream"（流式增量，节流到 STREAM_NOTIFY_INTERVAL）或 "response"（已收到响应/失败）。
LLMObserver = Callable[[str, LLMCall], None]

# 流式增量通知的最小间隔（秒）：避免每个 token 都触发一次 UI 重绘
STREAM_NOTIFY_INTERVAL = 0.12


# ============================================================
# LM Studio 客户端
# ============================================================


class LMStudioClient:
    """LM Studio API 客户端，自动检测模型是否已加载"""

    def __init__(
        self,
        base_url: str = "http://localhost:1234/v1",
        api_key: str = "lm-studio",
        model: str = "auto",
        timeout: float = 120.0,
        client=None,
        on_llm_call: LLMObserver | None = None,
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.client = client or OpenAI(
            base_url=self.base_url,
            api_key=api_key,
            timeout=timeout,
        )
        self._model_checked = False  # 只在首次调用时打印状态
        self._model_cached_name = None  # 首次 ensure_model_loaded 成功后缓存，跳过后续 HTTP 检查
        # LLM API 输入输出观察者：Web UI / CLI 用它把每次请求与响应呈现给学习者
        self.on_llm_call: LLMObserver | None = on_llm_call

    def check_server_status(self) -> dict:
        """检测 LM Studio 服务是否运行，以及模型是否已加载"""
        result = {
            "server_running": False,
            "model_loaded": False,
            "model_name": None,
            "error": None,
            "models": [],
        }
        try:
            resp = requests.get(f"{self.base_url}/models", timeout=5)
            if resp.status_code == 200:
                result["server_running"] = True
                data = resp.json()
                models = data.get("data", [])
                model_ids = [
                    str(item.get("id")) for item in models if isinstance(item, dict) and item.get("id")
                ]
                result["models"] = model_ids

                chat_models = [
                    model_id for model_id in model_ids if not self._looks_like_embedding_model(model_id)
                ]
                if self.model != "auto":
                    if self.model in model_ids:
                        result["model_loaded"] = True
                        result["model_name"] = self.model
                    elif chat_models:
                        result["error"] = (
                            f"已配置模型 {self.model!r} 未加载；当前聊天模型: {', '.join(chat_models)}"
                        )
                    else:
                        result["error"] = "未发现可用的聊天模型（只发现了 embedding 模型）"
                elif chat_models:
                    result["model_loaded"] = True
                    result["model_name"] = chat_models[0]
                elif model_ids:
                    result["error"] = "未发现可用的聊天模型（只发现了 embedding 模型）"
            else:
                result["error"] = f"服务器返回状态码 {resp.status_code}"
        except requests.ConnectionError:
            result["error"] = "无法连接到 LM Studio 服务，请确认 LM Studio 已启动并开启本地服务"
        except requests.Timeout:
            result["error"] = "连接 LM Studio 超时"
        except Exception as e:
            result["error"] = str(e)
        return result

    @staticmethod
    def _looks_like_embedding_model(model_name: str) -> bool:
        lowered = model_name.lower()
        return "embed" in lowered or "embedding" in lowered

    def ensure_model_loaded(self) -> str:
        """确保模型已加载，未加载则抛出异常。成功后缓存结果，后续调用直接返回省掉 HTTP 健康检查。"""
        if self._model_cached_name is not None:
            return self._model_cached_name
        status = self.check_server_status()
        if not status["server_running"]:
            raise RuntimeError(status["error"])
        if not status["model_loaded"]:
            raise RuntimeError(status["error"] or "LM Studio 服务已运行，但未加载可用的聊天模型。")
        if self.model == "auto":
            self.model = status["model_name"]
        if not self._model_checked:
            target = f" → {self.model}" if self.model != "auto" else " (auto)"
            log.info("服务运行中，已加载模型: %s%s", status["model_name"], target)
            self._model_checked = True
        self._model_cached_name = status["model_name"]
        return status["model_name"]

    # ============================================================
    # LLM 调用追踪 —— 所有 API 请求的唯一收口点
    # ============================================================

    def _notify_llm(self, phase: str, call: LLMCall) -> None:
        """通知观察者；观察者自身的异常绝不允许影响正常的模型调用。"""
        observer = self.on_llm_call
        if observer is None:
            return
        try:
            observer(phase, call)
        except Exception:  # noqa: BLE001 - 观测失败不能拖垮主流程
            log.debug("LLM 调用观察者异常", exc_info=True)

    def _begin_trace(
        self,
        label: str,
        *,
        messages: list[dict] | None,
        tools: list[dict] | None = None,
        params: dict | None = None,
        stream: bool = False,
    ) -> LLMCall:
        """建立快照并立即广播 request 阶段（UI 可以先把"输入"显示出来）。"""
        call = llm_trace.begin_call(
            label=label,
            model=self.model,
            messages=messages,
            tools=tools,
            params=params,
            base_url=self.base_url,
            stream=stream,
        )
        self._notify_llm("request", call)
        return call

    def _finish_trace(self, call: LLMCall, response=None, error: BaseException | None = None) -> None:
        if error is not None:
            llm_trace.apply_error(call, error)
        else:
            llm_trace.apply_response(call, response)
        self._notify_llm("response", call)

    def _create_completion(self, *, label: str, stream: bool = False, **kwargs):
        """统一的 ``chat.completions.create`` 入口：先记录输入，再记录输出。"""
        request = dict(kwargs)
        stream = bool(request.pop("stream", stream))
        call = self._begin_trace(
            label,
            messages=request.get("messages"),
            tools=request.get("tools"),
            params={k: v for k, v in request.items() if k not in {"model", "messages", "tools"}},
            stream=stream,
        )
        try:
            response = self.client.chat.completions.create(stream=stream, **request)
        except Exception as exc:
            self._finish_trace(call, error=exc)
            raise
        if stream:
            # 流式：快照在迭代过程中持续更新，结束时才回填完成状态
            return self._wrap_stream(call, response)
        self._finish_trace(call, response=response)
        return response

    def _wrap_stream(self, call: LLMCall, stream):
        """包装流式迭代器：逐 chunk 合并进快照，并按间隔广播 "stream" 阶段。

        快照（``call.content`` 等）在生成过程中持续增长，Web UI 因此可以实时
        展示输出；通知做了节流，避免每个 token 都触发一次界面重绘。
        """
        last_notify = 0.0
        try:
            for chunk in stream:
                if llm_trace.append_stream_chunk(call, chunk):
                    now = time.monotonic()
                    if now - last_notify >= STREAM_NOTIFY_INTERVAL:
                        last_notify = now
                        self._notify_llm("stream", call)
                yield chunk
        except Exception as exc:
            self._finish_trace(call, error=exc)
            raise
        llm_trace.apply_stream_end(call)
        self._notify_llm("response", call)

    def stream_chat_completion(self, label: str | None = None, **kwargs):
        """流式发起调用，并把分片聚合成与非流式响应同形态的对象。

        返回值的 ``choices[0].message`` 带有 ``content`` / ``reasoning_content`` /
        ``tool_calls`` 和 ``model_dump()``，``AgentRuntime`` 用 ``stream=True``
        时可以直接按非流式响应的方式消费，工具循环语义完全一致。
        """
        request = dict(kwargs)
        request["stream"] = True
        stream = self._create_completion(label=label or "chat.completions (stream)", **request)
        return _aggregate_stream(stream)

    def summarize_messages(self, messages: list[dict], keep_recent: int = 4) -> list[dict]:
        """长对话摘要压缩：保留最近 N 条完整消息，较早历史交给 LLM 做摘要"""
        if len(messages) <= keep_recent + 2:
            return messages

        system_msg = messages[0] if messages and messages[0]["role"] == "system" else None
        boundary = max(1 if system_msg else 0, len(messages) - keep_recent)
        while boundary > (1 if system_msg else 0) and messages[boundary].get("role") != "user":
            boundary -= 1
        recent = messages[boundary:]
        to_summarize = messages[1:boundary] if system_msg else messages[:boundary]

        # 清洗掉 tool_calls / tool 类型的噪声消息（纯对话摘要）
        clean = [m for m in to_summarize if m.get("role") in ("user", "assistant") and m.get("content")]
        history_text = "\n".join(f"[{m['role'].upper()}]: {m['content']}" for m in clean)

        summary = None
        try:
            summary = self.chat(
                [
                    {
                        "role": "user",
                        "content": (
                            "请对以下对话历史做简洁摘要（200-300 字），保留关键事实、决定、已完成任务和"
                            "待续事项，用第三人称叙述，不要省略时间、数字、名字等硬信息。\n\n"
                            f"=== 对话历史 ===\n{history_text}\n\n=== 摘要 ==="
                        ),
                    }
                ],
                temperature=0.1,
                max_tokens=512,
                label="历史摘要压缩",
            )
        except Exception as exc:
            log.warning("历史摘要 LLM 调用失败，回退为硬截断: %s", exc)

        new_history = []
        if system_msg:
            new_history.append(system_msg)
        if summary and summary.strip():
            new_history.append({"role": "assistant", "content": f"【历史摘要】{summary}"})
        else:
            # 摘要失败/为空 → 硬截断回退：丢弃较早历史，只留占位说明，保证会话不崩溃
            if summary is not None:
                log.warning("历史摘要返回空内容，回退为硬截断")
            new_history.append(
                {
                    "role": "assistant",
                    "content": (
                        f"【历史已截断】较早的 {len(to_summarize)} 条消息因摘要失败被直接丢弃，"
                        "仅保留最近的对话内容。"
                    ),
                }
            )
        new_history.extend(recent)
        return new_history

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        stream: bool = False,
        label: str = "chat",
    ):
        """发送聊天请求，自动检测模型加载状态"""
        self.ensure_model_loaded()
        response = self._create_completion(
            label=label,
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
        )
        if stream:
            return response
        return response.choices[0].message.content

    def chat_stream(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 2048):
        """流式聊天，逐 token 输出（快照由 _wrap_stream 在迭代过程中持续更新）"""
        self.ensure_model_loaded()
        stream = self._create_completion(
            label="chat_stream",
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )

        def _generator():
            for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    yield delta.content

        return _generator()

    def create_chat_completion(self, label: str | None = None, **kwargs):
        """公开委托：转发到底层 OpenAI chat.completions.create（AgentRuntime 等内部组件使用）

        ``label`` 只用于 UI 展示本次调用的用途（如"Agent 循环 · 第 2 轮"），不会发给模型。
        """
        return self._create_completion(label=label or "chat.completions", **kwargs)

    def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_map: dict,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        max_iterations: int = 5,
        skill_registry=None,
        mcp_manager=None,
        tool_executor=None,
        on_event=None,
        session_id: str | None = None,
        stream: bool = False,
    ):
        """Delegate the tool loop to :class:`AgentRuntime` and return final text."""
        from ai_agent.runtime import AgentRuntime

        final = None
        runtime = AgentRuntime(self, tool_executor=tool_executor)
        for event in runtime.run(
            messages,
            tools,
            tool_map,
            session_id=session_id,
            max_iterations=max_iterations,
            temperature=temperature,
            max_tokens=max_tokens,
            skill_registry=skill_registry,
            mcp_manager=mcp_manager,
            stream=stream,
        ):
            if on_event is not None:
                on_event(event)
            if event.kind == "final":
                final = event.payload.get("content", "")
        return final or "未获得模型输出。"

    # ============================================================
    # 演示算法薄委托 — 实现见 ai_agent/demos/
    # ============================================================

    def chat_with_react(
        self,
        messages: list[dict],
        tool_map: dict,
        max_iterations: int = 6,
        temperature: float = 0.5,
        max_tokens: int = 2048,
        on_event=None,
    ) -> str:
        """ReAct（Reasoning + Acting）模式：文本式 Thought/Action/Observation 循环"""
        return run_react(
            self,
            messages,
            tool_map,
            max_iterations=max_iterations,
            temperature=temperature,
            max_tokens=max_tokens,
            on_event=on_event,
        )

    def chat_with_reasoning(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096):
        """思维链（Chain-of-Thought）：提示词引导模型逐步展示推理过程"""
        return run_reasoning(self, messages, temperature=temperature, max_tokens=max_tokens)

    def chat_with_native_reasoning(
        self,
        messages: list[dict],
        reasoning_effort: str = "high",
        temperature: float = 0.7,
        max_tokens: int = 8192,
    ):
        """原生 reasoning_effort 模式（qwen3 等推理模型）"""
        return run_native_reasoning(
            self,
            messages,
            reasoning_effort=reasoning_effort,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def chat_with_reflexion(
        self, messages: list[dict], rounds: int = 3, temperature: float = 0.7, max_tokens: int = 2048
    ):
        """反思与自我修正：迭代多轮 作答 → 自我批评 → 修正"""
        return run_reflexion(self, messages, rounds=rounds, temperature=temperature, max_tokens=max_tokens)

    def analyze_image(
        self,
        image_path: str,
        question: str = "请描述这张图片的内容",
        max_tokens: int = 1024,
        workspace_root: str | None = None,
    ):
        """图像识别：本地图片 base64 传给多模态模型（路径限制在工作区内）"""
        safe_path, error = resolve_workspace_path(image_path, workspace_root)
        if error:
            return error
        self.ensure_model_loaded()
        mime_map = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }
        ext = str(safe_path).lower()
        ext = next((k for k in mime_map if ext.endswith(k)), ".png")
        mime = mime_map[ext]
        with open(safe_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        response = self._create_completion(
            label="图像识别",
            model=self.model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": question},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    ],
                }
            ],
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content

    def plan_and_execute(
        self, task: str, max_subtasks: int = 3, temperature: float = 0.7, max_tokens: int = 2048
    ) -> str:
        """任务规划分解（Planner-Executor）：先规划后执行，逐个子任务完成"""
        return run_plan_and_execute(
            self, task, max_subtasks=max_subtasks, temperature=temperature, max_tokens=max_tokens
        )

    def structured_extract(
        self, text: str, fields: str, temperature: float = 0.0, max_tokens: int = 1024
    ) -> str:
        """结构化输出：按字段提取 JSON，带围栏清洗 + 失败重试（强化 prompt）"""
        return run_structured_extract(self, text, fields, temperature=temperature, max_tokens=max_tokens)


# ============================================================
# 流式分片聚合 —— 把 stream chunks 还原成非流式响应的形态
# ============================================================


class _AggregatedToolFunction:
    def __init__(self):
        self.name = ""
        self.arguments = ""


class _AggregatedToolCall:
    def __init__(self):
        self.id = ""
        self.type = "function"
        self.function = _AggregatedToolFunction()

    def model_dump(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "function": {"name": self.function.name, "arguments": self.function.arguments},
        }


class _AggregatedMessage:
    def __init__(self):
        self.role = "assistant"
        self.content: str | None = ""
        self.reasoning_content = ""
        self.tool_calls: list[_AggregatedToolCall] | None = []

    def model_dump(self) -> dict:
        data: dict = {"role": self.role, "content": self.content or None}
        if self.tool_calls:
            data["tool_calls"] = [tool_call.model_dump() for tool_call in self.tool_calls]
        return data


class _AggregatedChoice:
    def __init__(self):
        self.index = 0
        self.finish_reason: str | None = None
        self.message = _AggregatedMessage()


class _AggregatedResponse:
    def __init__(self, choice: _AggregatedChoice):
        self.choices = [choice]


def _aggregate_stream(stream) -> _AggregatedResponse:
    """消费一个流式迭代器，把 content / tool_calls 分片聚合成完整响应。"""
    choice = _AggregatedChoice()
    tool_calls: list[_AggregatedToolCall] = []
    for chunk in stream:
        chunk_choices = getattr(chunk, "choices", None)
        if not chunk_choices:
            continue
        delta = chunk_choices[0].delta
        content = getattr(delta, "content", None)
        if content:
            choice.message.content = (choice.message.content or "") + content
        reasoning = getattr(delta, "reasoning_content", None)
        if reasoning:
            choice.message.reasoning_content += reasoning
        for tool_call in getattr(delta, "tool_calls", None) or []:
            index = int(getattr(tool_call, "index", 0) or 0)
            while len(tool_calls) <= index:
                tool_calls.append(_AggregatedToolCall())
            aggregated = tool_calls[index]
            if getattr(tool_call, "id", None):
                aggregated.id = tool_call.id
            function = getattr(tool_call, "function", None)
            if function is not None:
                if getattr(function, "name", None):
                    aggregated.function.name += function.name
                if getattr(function, "arguments", None):
                    aggregated.function.arguments += function.arguments
        if chunk_choices[0].finish_reason:
            choice.finish_reason = chunk_choices[0].finish_reason
    choice.message.tool_calls = tool_calls or None
    if not choice.message.content:
        choice.message.content = None
    return _AggregatedResponse(choice)
