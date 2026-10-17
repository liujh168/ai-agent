"""
Executor 工具调用测试 — mock client + 工具函数，不依赖真实 LLM
运行：python -m pytest ai_agent/tests/test_executor_tools.py -v
"""

from types import SimpleNamespace

from ai_agent.multi_agent import run_multi_agent
from ai_agent.runtime import AgentRuntime

CALCULATOR_SCHEMA = {
    "type": "function",
    "function": {
        "name": "calculator",
        "description": "执行数学表达式计算",
        "parameters": {
            "type": "object",
            "properties": {"expression": {"type": "string", "description": "数学表达式"}},
            "required": ["expression"],
        },
    },
}


class _FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls

    def model_dump(self):
        return {"role": "assistant", "content": self.content, "tool_calls": self.tool_calls}


def _tool_call_response(name: str, arguments: str):
    tool_call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name=name, arguments=arguments),
    )
    message = _FakeMessage(content=None, tool_calls=[tool_call])
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason="tool_calls", message=message)])


def _text_response(content: str):
    message = _FakeMessage(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=message)])


class FakeClient:
    """按 label 脚本化回复的 client：chat 走纯文本，chat_with_tools 走真实 AgentRuntime FC 循环"""

    model = "fake-model"

    def __init__(self, chat_replies: dict[str, str], fc_script: list | None = None):
        self.chat_replies = chat_replies
        self.fc_script = list(fc_script or [])
        self.chat_calls = []  # (label, messages)
        self.completion_calls = []  # (label, messages)
        self.chat_with_tools_used = False

    def ensure_model_loaded(self):
        pass

    def chat(self, messages, temperature=0.7, max_tokens=2048, label="chat", **kwargs):
        self.chat_calls.append((label, messages))
        for key, reply in self.chat_replies.items():
            if key in label:
                return reply
        raise AssertionError(f"未脚本化的 chat 调用: {label}")

    def create_chat_completion(self, label=None, model=None, messages=None, **kwargs):
        self.completion_calls.append((label, messages))
        assert self.fc_script, f"FC 脚本已耗尽，仍有调用: {label}"
        return self.fc_script.pop(0)

    def chat_with_tools(
        self, messages, tools, tool_map, on_event=None, tool_executor=None, **kwargs
    ):
        self.chat_with_tools_used = True
        final = None
        runtime = AgentRuntime(self, tool_executor=tool_executor)
        for event in runtime.run(messages, tools, tool_map, **kwargs):
            if on_event is not None:
                on_event(event)
            if event.kind == "final":
                final = event.payload.get("content", "")
        return final or "未获得模型输出。"


APPROVE_JSON = '{"decision": "approve", "reason": "没问题"}'


def _make_tool_client(executor_reply: str):
    calls = {"calculator": []}

    def calculator(expression: str) -> str:
        calls["calculator"].append(expression)
        return "计算结果: 42"

    client = FakeClient(
        chat_replies={
            "Planner 规划": "1. 计算 21*2 并给出答案",
            "Critic": APPROVE_JSON,
            "Planner 汇总整合": "最终交付：答案是 42",
        },
        fc_script=[
            _tool_call_response("calculator", '{"expression": "21*2"}'),
            _text_response(executor_reply),
        ],
    )
    return client, calls, {"calculator": calculator}


class TestExecutorToolCalls:
    def test_tool_call_executed_and_result_in_output(self):
        """子任务触发 tool_call：工具被真正执行，结果进入 Executor 产出并被 Critic 看到"""
        client, calls, tool_map = _make_tool_client("子任务产出：答案是 42")
        trace: list[str] = []

        result = run_multi_agent(
            client,
            "帮我算 21*2",
            tools=[CALCULATOR_SCHEMA],
            tool_map=tool_map,
            trace_callback=trace.append,
        )

        assert calls["calculator"] == ["21*2"]
        # 第二轮 FC 请求的 messages 里应带 role=tool 的工具结果
        _, second_messages = client.completion_calls[1]
        tool_msgs = [m for m in second_messages if m.get("role") == "tool"]
        assert any("42" in m["content"] for m in tool_msgs)
        # 工具结果进入 Executor 产出 → Critic 评审时能看到
        critic_labels = [lbl for lbl, _ in client.chat_calls if "Critic 审子任务" in lbl]
        assert critic_labels
        critic_msgs = dict(client.chat_calls)[critic_labels[0]]
        assert any("42" in m["content"] for m in critic_msgs if m["role"] == "user")
        assert result == "最终交付：答案是 42"
        # trace 中可见工具调用轨迹
        assert any("🔧" in line and "calculator" in line for line in trace)

    def test_full_pipeline_with_tools(self):
        """Planner→Executor(带工具)→Critic 整条流水线在 mock 下走通"""
        client, calls, tool_map = _make_tool_client("子任务产出：答案是 42")

        result = run_multi_agent(
            client,
            "帮我算 21*2",
            tools=[CALCULATOR_SCHEMA],
            tool_map=tool_map,
            trace_callback=lambda line: None,
        )

        labels = [lbl for lbl, _ in client.chat_calls]
        assert labels == ["Planner 规划", "Critic 审计划", "Critic 审子任务 1", "Planner 汇总整合"]
        assert client.chat_with_tools_used
        assert len(client.completion_calls) == 2  # tool_call 一轮 + 最终回复一轮
        assert result == "最终交付：答案是 42"


class TestExecutorWithoutTools:
    def test_plain_chat_behavior_unchanged(self):
        """不传 tools/tool_map：Executor 仍走纯 client.chat，行为不变"""
        client = FakeClient(
            chat_replies={
                "Planner 规划": "1. 写一句口号",
                "Critic": APPROVE_JSON,
                "Executor": "口号：好好学习",
                "Planner 汇总整合": "最终交付：好好学习",
            }
        )

        result = run_multi_agent(client, "写一句口号", trace_callback=lambda line: None)

        assert not client.chat_with_tools_used
        labels = [lbl for lbl, _ in client.chat_calls]
        assert labels == [
            "Planner 规划",
            "Critic 审计划",
            "Executor 子任务 1/1",
            "Critic 审子任务 1",
            "Planner 汇总整合",
        ]
        assert result == "最终交付：好好学习"
