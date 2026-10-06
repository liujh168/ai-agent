"""LLM 调用追踪的流式聚合、CLI 记录器与 Web 面板分组渲染的离线测试。"""

from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace

from ai_agent import llm_trace
from ai_agent.cli_trace import CallRecorder
from ai_agent.client import LMStudioClient, _aggregate_stream


def _chunk(content=None, reasoning=None, tool_calls=None, finish_reason=None):
    delta = SimpleNamespace(content=content, reasoning_content=reasoning, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)])


def _tool_call_delta(index=0, call_id=None, name=None, arguments=None):
    return SimpleNamespace(
        index=index,
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


# ============================================================
# 流式聚合
# ============================================================


def test_aggregate_stream_content_shards():
    chunks = [
        _chunk(content="你好"),
        _chunk(content="，世界"),
        _chunk(finish_reason="stop"),
    ]
    response = _aggregate_stream(iter(chunks))
    choice = response.choices[0]
    assert choice.finish_reason == "stop"
    assert choice.message.content == "你好，世界"
    assert choice.message.tool_calls is None
    assert choice.message.model_dump() == {"role": "assistant", "content": "你好，世界"}


def test_aggregate_stream_tool_call_shards():
    chunks = [
        _chunk(tool_calls=[_tool_call_delta(call_id="call_1", name="get_wea")]),
        _chunk(tool_calls=[_tool_call_delta(name="ther", arguments='{"ci')]),
        _chunk(tool_calls=[_tool_call_delta(arguments='ty":"北京"}')]),
        _chunk(finish_reason="tool_calls"),
    ]
    response = _aggregate_stream(iter(chunks))
    choice = response.choices[0]
    assert choice.finish_reason == "tool_calls"
    assert choice.message.content is None
    tool_call = choice.message.tool_calls[0]
    assert tool_call.id == "call_1"
    assert tool_call.function.name == "get_weather"
    assert tool_call.function.arguments == '{"city":"北京"}'
    dumped = choice.message.model_dump()
    assert dumped["content"] is None
    assert dumped["tool_calls"][0]["function"]["name"] == "get_weather"


def test_append_stream_chunk_updates_snapshot():
    call = llm_trace.begin_call(label="t", model="m", messages=[])
    assert llm_trace.append_stream_chunk(call, _chunk(content="你"))
    assert llm_trace.append_stream_chunk(call, _chunk(content="好"))
    llm_trace.append_stream_chunk(
        call, _chunk(tool_calls=[_tool_call_delta(call_id="c1", name="f", arguments='{"a"')])
    )
    llm_trace.append_stream_chunk(call, _chunk(tool_calls=[_tool_call_delta(arguments=":1}")]))
    llm_trace.append_stream_chunk(call, _chunk(finish_reason="tool_calls"))
    assert call.content == "你好"
    assert call.tool_calls == [{"id": "c1", "name": "f", "arguments": '{"a":1}'}]
    assert call.finish_reason == "tool_calls"


class _FakeCompletions:
    def __init__(self, chunks):
        self._chunks = chunks

    def create(self, **kwargs):
        return iter(self._chunks)


class _FakeOpenAI:
    def __init__(self, chunks):
        self.chat = SimpleNamespace(completions=_FakeCompletions(chunks))


def test_wrap_stream_notifies_and_aggregates():
    chunks = [_chunk(content="你"), _chunk(content="好"), _chunk(finish_reason="stop")]
    client = LMStudioClient(client=_FakeOpenAI(chunks))
    seen: list[tuple[str, str]] = []
    client.on_llm_call = lambda phase, call: seen.append((phase, call.content))

    stream = client._create_completion(label="t", model="m", messages=[], stream=True)
    assert [phase for phase, _ in seen] == ["request"]  # 迭代前只有 request

    list(stream)  # 消费完整个流
    phases = [phase for phase, _ in seen]
    assert phases[0] == "request"
    assert phases[-1] == "response"
    assert "stream" in phases  # 增量通知（有节流，次数不多于 chunk 数）
    assert seen[-1][1] == "你好"


def test_wrap_stream_error_marks_call_failed():
    class _Boom:
        def create(self, **kwargs):
            raise ConnectionError("lm studio 挂了")

    client = LMStudioClient(client=SimpleNamespace(chat=SimpleNamespace(completions=_Boom())))
    seen: list[tuple[str, str]] = []
    client.on_llm_call = lambda phase, call: seen.append((phase, call.status))

    try:
        client._create_completion(label="t", model="m", messages=[])
    except ConnectionError:
        pass
    else:  # pragma: no cover
        raise AssertionError("应当抛出 ConnectionError")
    assert seen == [("request", "pending"), ("response", "error")]


# ============================================================
# CLI 记录器
# ============================================================


def _finished_call(label: str, content: str, prompt: int, completion: int) -> llm_trace.LLMCall:
    call = llm_trace.begin_call(label=label, model="m", messages=[{"role": "user", "content": "hi"}])
    call.status = "ok"
    call.content = content
    call.finish_reason = "stop"
    call.finished_at = call.started_at + 1.0
    call.usage = {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}
    return call


def test_call_recorder_dedup_and_reset():
    recorder = CallRecorder()
    call = _finished_call("t", "ok", 10, 5)
    recorder("request", call)
    recorder("stream", call)
    recorder("response", call)
    assert len(recorder.calls) == 1

    summary = recorder.summary_line().plain
    assert "1 次" in summary
    assert "10" in summary and "5" in summary

    recorder.reset()
    assert recorder.calls == []


# ============================================================
# Web 面板：轮次分组与 JSON 高亮
# ============================================================


def test_llm_panel_groups_calls_by_round():
    # web_ui 模块导入时会 load_env()，延迟到用例内导入以免污染 test_core 的配置默认值断言
    from ai_agent.web_ui import AgentApp

    with tempfile.TemporaryDirectory() as temp_dir:
        app = AgentApp(db_path=os.path.join(temp_dir, "web.db"))
        try:
            state = app.new_state()
            state["llm_round"] = 1
            state["llm_round_queries"] = {1: "第一个问题", 2: "第二个问题"}
            AgentApp._apply_llm_call(state, "request", _finished_call("第一轮调用", "答一", 10, 5))
            state["llm_round"] = 2
            AgentApp._apply_llm_call(state, "request", _finished_call("第二轮调用", "答二", 20, 8))

            panel = app.llm_panel_html(state)
            assert "第 1 轮" in panel and "第一个问题" in panel
            assert "第 2 轮" in panel and "第二个问题" in panel
            # 历史轮整体折叠，最新一轮平铺展开
            assert '<details class="llm-round">' in panel
            assert "llm-round-head current" in panel
        finally:
            app.store.close()  # Windows 下 SQLite 连接不关闭会锁住临时目录


def test_clear_llm_calls_resets_rounds():
    from ai_agent.web_ui import AgentApp

    with tempfile.TemporaryDirectory() as temp_dir:
        app = AgentApp(db_path=os.path.join(temp_dir, "web.db"))
        try:
            state = app.new_state()
            state["llm_round"] = 3
            state["llm_round_queries"] = {3: "问题"}
            AgentApp._apply_llm_call(state, "request", _finished_call("t", "ok", 1, 1))

            panel, state = app.clear_llm_calls(state)
            assert state["llm_calls"] == []
            assert state["llm_round"] == 0
            assert "还没有 API 调用" in panel
        finally:
            app.store.close()


def test_highlight_json_escapes_and_colors():
    from ai_agent.web_render import highlight_json

    highlighted = highlight_json('{"key": "<b>", "n": 1, "ok": true}')
    assert '<span class="json-key">' in highlighted
    assert '<span class="json-num">1</span>' in highlighted
    assert '<span class="json-bool">true</span>' in highlighted
    assert "&lt;b&gt;" in highlighted  # 字符串内容必须转义
