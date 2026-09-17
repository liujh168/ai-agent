import json
import os
import tempfile
from types import SimpleNamespace

from ai_agent.mcp_client import MCPClient, MCPManager, MCPTool
from ai_agent.persistence import ChatStore
from ai_agent.rag import RAGKnowledgeBase
from ai_agent.runtime import AgentRuntime
from ai_agent.tooling import ToolExecutor, ToolPolicy


class _FakeCompletions:
    def __init__(self, responses):
        self.responses = list(responses)

    def create(self, **_kwargs):
        return self.responses.pop(0)


class _FakeClient:
    def __init__(self, responses):
        self.model = "fake-model"
        self.client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions(responses)))

    def ensure_model_loaded(self):
        return self.model


def _tool_call(name: str, arguments: str):
    return SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _response(finish_reason: str, content=None, tool_calls=None):
    message = SimpleNamespace(
        content=content,
        tool_calls=tool_calls,
        model_dump=lambda: {
            "role": "assistant",
            "content": content,
            "tool_calls": [],
        },
    )
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish_reason, message=message)])


def test_runtime_emits_tool_and_final_events():
    client = _FakeClient(
        [
            _response(
                "tool_calls",
                tool_calls=[_tool_call("echo", '{"value": "x"}')],
            ),
            _response("stop", content="done"),
        ]
    )
    runtime = AgentRuntime(client)
    events = list(
        runtime.run(
            [{"role": "user", "content": "run"}],
            tools=[],
            tool_map={"echo": lambda value: f"echo:{value}"},
        )
    )
    kinds = [event.kind for event in events]
    assert kinds == [
        "run_started",
        "route_selected",
        "tool_started",
        "tool_finished",
        "observation",
        "final",
    ]
    assert events[-1].payload["content"] == "done"
    assert events[3].payload["ok"] is True


def test_runtime_converts_tool_exception_to_observation():
    client = _FakeClient(
        [
            _response(
                "tool_calls",
                tool_calls=[_tool_call("explode", "{}")],
            ),
            _response("stop", content="recovered"),
        ]
    )

    def explode():
        raise RuntimeError("boom")

    events = list(
        AgentRuntime(client).run(
            [{"role": "user", "content": "run"}],
            tools=[],
            tool_map={"explode": explode},
        )
    )
    tool_finished = next(event for event in events if event.kind == "tool_finished")
    assert tool_finished.payload["ok"] is False
    assert "boom" in tool_finished.payload["content"]
    assert events[-1].payload["content"] == "recovered"


def test_tool_executor_denies_execute_by_default_and_confirmation():
    calls = []
    executor = ToolExecutor(
        {"run_code": lambda code, allow_unsafe=False: calls.append((code, allow_unsafe)) or "ok"},
        policy=ToolPolicy(allow_execute=False),
    )
    denied = executor.execute("run_code", {"code": "print(1)"})
    assert denied.ok is False
    assert calls == []

    confirmed = ToolExecutor(
        {"run_code": lambda code, allow_unsafe=False: "executed"},
        policy=ToolPolicy(allow_execute=True, confirm_execute=True),
        confirm=lambda *_args: False,
    ).execute("run_code", {"code": "print(1)"})
    assert confirmed.ok is False


def test_chat_store_cascades_and_orders_messages():
    with tempfile.TemporaryDirectory() as temp_dir:
        store = ChatStore(os.path.join(temp_dir, "history.db"))
        session_id = store.create_session()
        store.append(session_id, "user", "first")
        store.append(session_id, "assistant", "second")
        store.conn.execute(
            "UPDATE messages SET timestamp = 1 WHERE session_id = ?",
            (session_id,),
        )
        store.conn.commit()
        assert [message["content"] for message in store.load_messages(session_id)] == [
            "first",
            "second",
        ]
        assert store.delete_session(session_id) is True
        count = store.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        assert count == 0
        store.close()


def test_rag_cache_is_json_versioned_and_recovers_from_corruption(monkeypatch):
    monkeypatch.setattr(
        RAGKnowledgeBase,
        "_embed",
        lambda self, texts: [[1.0, 0.0] for _ in texts],
    )
    with tempfile.TemporaryDirectory() as temp_dir:
        doc_path = os.path.join(temp_dir, "doc.md")
        cache_path = os.path.join(temp_dir, "cache.json")
        with open(doc_path, "w", encoding="utf-8") as handle:
            handle.write("Agent runtime event trace")

        kb = RAGKnowledgeBase(cache_file=cache_path)
        assert kb.build([doc_path]) > 0
        with open(cache_path, encoding="utf-8") as handle:
            cache = json.load(handle)
        assert cache["version"] == RAGKnowledgeBase.CACHE_VERSION

        with open(cache_path, "w", encoding="utf-8") as handle:
            handle.write("not-json")
        assert kb.build([doc_path]) > 0


def test_mcp_parses_sse_and_namespaces_tools(monkeypatch):
    response = SimpleNamespace(
        text='event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n',
        headers={"Content-Type": "text/event-stream"},
    )
    assert MCPClient._parse_response(response)["result"]["ok"] is True

    monkeypatch.setattr(MCPClient, "initialize", lambda self: {})
    monkeypatch.setattr(
        MCPClient,
        "list_tools",
        lambda self: [
            MCPTool(
                name="same",
                description="test",
                input_schema={"type": "object", "properties": {}},
            )
        ],
    )
    manager = MCPManager(["https://one.example/mcp", "https://two.example/mcp"])
    manager.connect_all()
    assert set(manager._tool_names) == {"mcp_0_same", "mcp_1_same"}


def test_mcp_error_result_is_not_reported_as_success(monkeypatch):
    client = MCPClient("https://example.invalid/mcp")
    client._connected = True
    monkeypatch.setattr(
        client,
        "_send",
        lambda *_args, **_kwargs: {
            "isError": True,
            "content": [{"type": "text", "text": "failed"}],
        },
    )
    assert "MCP 工具错误" in client.call_tool("bad")


def test_web_session_state_is_isolated():
    from ai_agent.web_ui import AgentApp

    with tempfile.TemporaryDirectory() as temp_dir:
        app = AgentApp(db_path=os.path.join(temp_dir, "web.db"))
        first = app.new_state()
        second = app.new_state()
        app.trace(first, "first-only")
        app.set_mode("react", first)

        assert first["trace_buffer"][0]["text"] == "first-only"
        assert second["trace_buffer"] == []
        assert first["mode"] == "react"
        assert second["mode"] == "chat"
        assert first["history"] is not second["history"]
        app.store.close()
