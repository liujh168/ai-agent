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
        self._completions = _FakeCompletions(responses)

    def ensure_model_loaded(self):
        return self.model

    def create_chat_completion(self, **kwargs):
        return self._completions.create(**kwargs)


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


def test_network_tools_classified_and_gated():
    policy = ToolPolicy()
    for name in ("wp_publish", "web_search", "demo_vision"):
        assert policy.classify(name) == "network"
    assert policy.classify("file_read") == "read"

    # 默认放行、无需确认（eval 路径依赖该默认行为）
    allowed = ToolExecutor({"web_search": lambda query: "ok"}).execute("web_search", {"query": "x"})
    assert allowed.ok is True

    # allow_network=False 时直接拒绝
    denied = ToolExecutor(
        {"wp_publish": lambda **kwargs: "published"},
        policy=ToolPolicy(allow_network=False),
    ).execute("wp_publish", {"title": "t"})
    assert denied.ok is False
    assert denied.risk == "network"

    # confirm_network=True 且用户拒绝时不执行
    calls = []
    unconfirmed = ToolExecutor(
        {"wp_publish": lambda **kwargs: calls.append(kwargs) or "published"},
        policy=ToolPolicy(confirm_network=True),
        confirm=lambda *_args: False,
    ).execute("wp_publish", {"title": "t"})
    assert unconfirmed.ok is False
    assert calls == []

    approved = ToolExecutor(
        {"wp_publish": lambda **kwargs: "published"},
        policy=ToolPolicy(confirm_network=True),
        confirm=lambda *_args: True,
    ).execute("wp_publish", {"title": "t"})
    assert approved.ok is True


def test_filter_tool_schemas_hides_network_when_disallowed():
    from ai_agent.app_core import TOOL_SCHEMAS, filter_tool_schemas

    visible = {s["function"]["name"] for s in filter_tool_schemas(TOOL_SCHEMAS, ToolPolicy())}
    assert "wp_publish" in visible and "web_search" in visible

    hidden = {
        s["function"]["name"] for s in filter_tool_schemas(TOOL_SCHEMAS, ToolPolicy(allow_network=False))
    }
    assert "wp_publish" not in hidden
    assert "web_search" not in hidden
    assert "demo_vision" not in hidden
    assert "file_read" in hidden


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


def test_mcp_send_error_branches(monkeypatch):
    """_send 的三类失败：网络异常 / HTTP 非 2xx / JSON-RPC error 载荷，都应抛 MCPError"""
    import pytest
    import requests

    from ai_agent.mcp_client import MCPError

    client = MCPClient("https://example.invalid/mcp")

    def _raise_conn(*_args, **_kwargs):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(requests, "post", _raise_conn)
    with pytest.raises(MCPError, match="HTTP 请求失败"):
        client._send("ping", {})

    monkeypatch.setattr(
        requests,
        "post",
        lambda *_a, **_k: SimpleNamespace(status_code=500, text="boom", headers={}),
    )
    with pytest.raises(MCPError, match="HTTP 500"):
        client._send("ping", {})

    monkeypatch.setattr(
        requests,
        "post",
        lambda *_a, **_k: SimpleNamespace(
            status_code=200,
            text='{"jsonrpc":"2.0","id":1,"error":{"code":-32601,"message":"method not found"}}',
            headers={"Content-Type": "application/json"},
        ),
    )
    with pytest.raises(MCPError, match="method not found"):
        client._send("ping", {})


def test_mcp_manager_connect_failure_placeholder(monkeypatch):
    """连接失败的 server 以 None 占位保持索引对齐，其余 server 不受影响，close_all 不炸"""
    from ai_agent.mcp_client import MCPError

    def _fail_init(self):
        raise MCPError(code=-1, message="unreachable")

    monkeypatch.setattr(MCPClient, "initialize", _fail_init)
    manager = MCPManager(["https://bad.example/mcp"])
    manager.connect_all()
    assert manager._clients == [None]
    assert manager.total_tools == 0
    assert manager.tool_schemas == []
    assert any("❌" in e for e in manager.connect_errors)
    manager.close_all()  # None 占位下不应抛异常

    monkeypatch.undo()
    monkeypatch.setattr(MCPClient, "initialize", lambda self: None)
    monkeypatch.setattr(MCPClient, "list_tools", lambda self: [])
    monkeypatch.setattr(MCPClient, "close", lambda self: None)
    manager = MCPManager(["https://ok.example/mcp"])
    manager.connect_all()
    assert manager._clients[0] is not None
    assert manager._clients[0].server_index == 0
    manager.close_all()


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
