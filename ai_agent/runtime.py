"""Single event-producing tool loop shared by CLI and Web surfaces."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from ai_agent.events import AgentEvent
from ai_agent.tooling import ToolExecution, ToolExecutor, ToolPolicy


class AgentRuntime:
    """Run an LLM tool-calling loop and emit typed events."""

    def __init__(self, client, tool_executor: ToolExecutor | None = None):
        self.client = client
        self.tool_executor = tool_executor

    def run(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_map: dict,
        *,
        session_id: str | None = None,
        max_iterations: int = 5,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        skill_registry=None,
        mcp_manager=None,
        stream: bool = False,
    ) -> Iterator[AgentEvent]:
        self.client.ensure_model_loaded()
        history = list(messages)
        sequence = 0

        def emit(kind: str, payload: dict[str, Any]) -> AgentEvent:
            nonlocal sequence
            sequence += 1
            return AgentEvent(
                kind=kind,
                payload=payload,
                session_id=session_id,
                sequence=sequence,
            )

        selected_skill = None
        if skill_registry is not None:
            last_user = next(
                (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
                "",
            )
            if last_user:
                selected_skill = skill_registry.match(last_user)

        active_map = dict(tool_map)
        active_tools = list(tools)
        if selected_skill is not None:
            from ai_agent.app_core import get_tool_schemas

            history = self._apply_skill_prompt(history, selected_skill.instructions)
            required = set(selected_skill.required_tools)
            active_map = {name: fn for name, fn in tool_map.items() if not required or name in required}
            active_tools = [
                schema for schema in get_tool_schemas(mcp_manager) if schema["function"]["name"] in active_map
            ]
            max_iterations = selected_skill.max_rounds

        executor = self.tool_executor or ToolExecutor(active_map, ToolPolicy())
        if executor.tool_map is not active_map:
            executor = ToolExecutor(active_map, executor.policy, executor.confirm)

        yield emit(
            "run_started",
            {
                "max_iterations": max_iterations,
                "tool_count": len(active_map),
                "skill": selected_skill.name if selected_skill else None,
            },
        )
        yield emit(
            "route_selected",
            {
                "mode": "skill" if selected_skill else "tools",
                "skill": selected_skill.name if selected_skill else None,
            },
        )

        for iteration in range(1, max_iterations + 1):
            try:
                label = f"Agent 循环 · 第 {iteration}/{max_iterations} 轮"
                if stream:
                    # 流式：快照边生成边更新（UI 实时可见），聚合结果与非流式同形态
                    response = self.client.stream_chat_completion(
                        label=label,
                        model=self.client.model,
                        messages=history,
                        tools=active_tools,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                else:
                    response = self.client.create_chat_completion(
                        label=label,
                        model=self.client.model,
                        messages=history,
                        tools=active_tools,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                choice = response.choices[0]
            except Exception as exc:
                message = f"模型调用失败: {type(exc).__name__}: {exc}"
                yield emit("error", {"message": message, "iteration": iteration})
                yield emit("final", {"content": message})
                return

            finish_reason = getattr(choice, "finish_reason", None)
            message = choice.message
            if finish_reason == "tool_calls":
                history.append(message.model_dump())
                for tool_call in message.tool_calls or []:
                    name = tool_call.function.name
                    raw_args = tool_call.function.arguments
                    yield emit(
                        "tool_started",
                        {"name": name, "arguments": raw_args, "iteration": iteration},
                    )

                    try:
                        arguments = json.loads(raw_args) if raw_args else {}
                        if not isinstance(arguments, dict):
                            raise ValueError("工具参数必须是 JSON 对象")
                    except (json.JSONDecodeError, ValueError) as exc:
                        result = ToolExecution(
                            False,
                            f"错误：无法解析参数 JSON（{exc}），请按 schema 重新调用。",
                            executor.policy.classify(name),
                            str(exc),
                        )
                    else:
                        result = executor.execute(name, arguments)

                    history.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result.content,
                        }
                    )
                    yield emit(
                        "tool_finished",
                        {
                            "name": name,
                            "ok": result.ok,
                            "risk": result.risk,
                            "content": result.content,
                            "error": result.error,
                            "iteration": iteration,
                        },
                    )
                    yield emit(
                        "observation",
                        {"name": name, "content": result.content, "iteration": iteration},
                    )
                continue

            content = getattr(message, "content", None) or ""
            if finish_reason in {"length", "content_filter", None}:
                yield emit(
                    "error",
                    {
                        "message": f"模型未正常结束（finish_reason={finish_reason}）",
                        "iteration": iteration,
                    },
                )
            yield emit("final", {"content": content})
            return

        content = f"已达到最大工具调用迭代次数（{max_iterations}）。"
        yield emit("error", {"message": content})
        yield emit("final", {"content": content})

    @staticmethod
    def _apply_skill_prompt(messages: list[dict], instructions: str) -> list[dict]:
        result: list[dict] = []
        replaced = False
        for message in messages:
            if message.get("role") == "system" and not replaced:
                result.append({"role": "system", "content": instructions})
                replaced = True
            else:
                result.append(message)
        if not replaced:
            result.insert(0, {"role": "system", "content": instructions})
        return result
