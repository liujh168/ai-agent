"""MCPClientSTDIO 离线测试：内嵌假 MCP stdio server 子进程，不依赖网络与真实 server"""

from __future__ import annotations

import gc
import sys
import time
from pathlib import Path

import pytest

from ai_agent.mcp_client import MCPClientSTDIO, MCPError, MCPManager

FAKE_SERVER = """
import json
import sys

print("fake mcp server starting...")  # 非 JSON 噪声行，客户端应跳过
sys.stdout.flush()

TOOLS = [
    {
        "name": "echo",
        "description": "回显 text 参数",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
    }
]

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    if "id" not in req:
        continue  # notification（如 notifications/initialized），无需响应
    method = req.get("method")
    if method == "initialize":
        result = {
            "protocolVersion": "2025-06-18",
            "serverInfo": {"name": "fake-stdio", "version": "0.1"},
            "capabilities": {},
        }
    elif method == "tools/list":
        print("listing tools...")  # 非 JSON 噪声行
        result = {"tools": TOOLS}
    elif method == "tools/call":
        text = req["params"].get("arguments", {}).get("text", "")
        result = {"content": [{"type": "text", "text": "echo:" + text}]}
    elif method == "ping":
        result = {}
    else:
        sys.stdout.write(json.dumps({
            "jsonrpc": "2.0", "id": req["id"],
            "error": {"code": -32601, "message": "method not found"},
        }) + "\\n")
        sys.stdout.flush()
        continue
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result}) + "\\n")
    sys.stdout.flush()
"""


def make_client(**kwargs) -> MCPClientSTDIO:
    return MCPClientSTDIO(sys.executable, ["-c", FAKE_SERVER], **kwargs)


def test_initialize_handshake_skips_noise_lines():
    client = make_client()
    try:
        info = client.initialize()
        assert info["serverInfo"]["name"] == "fake-stdio"
        assert client.connected
        assert client.ping()
    finally:
        client.close()


def test_list_tools():
    client = make_client()
    try:
        tools = client.list_tools()
        assert [t.name for t in tools] == ["echo"]
        assert tools[0].description == "回显 text 参数"
        assert tools[0].input_schema["properties"]["text"]["type"] == "string"
        assert tools[0].server_url == client.url
    finally:
        client.close()


def test_call_tool_returns_text():
    client = make_client()
    try:
        assert client.call_tool("echo", {"text": "你好"}) == "echo:你好"
    finally:
        client.close()


def test_jsonrpc_error_raises_mcp_error():
    client = make_client()
    try:
        client.initialize()
        with pytest.raises(MCPError, match="method not found"):
            client._send("resources/list", {})
    finally:
        client.close()


def test_manager_stdio_spec_registers_prefixed_tools(tmp_path):
    """MCP_SERVERS 条目带 stdio: 前缀时走子进程，工具以 mcp_ 前缀并入 tool_map"""
    script = tmp_path / "fake_server.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    spec = f'stdio:"{Path(sys.executable).as_posix()}" "{script.as_posix()}"'
    manager = MCPManager([spec])
    try:
        manager.connect_all()
        assert manager.total_tools == 1
        callables = manager.make_callables()
        assert set(callables) == {"mcp_0_echo"}
        assert manager.is_mcp_tool("mcp_0_echo")
        assert callables["mcp_0_echo"](text="hi") == "echo:hi"
        assert [s["function"]["name"] for s in manager.tool_schemas] == ["mcp_0_echo"]
    finally:
        manager.close_all()


def test_close_terminates_subprocess():
    client = make_client()
    client.initialize()
    proc = client._proc
    assert proc.poll() is None
    client.close()
    assert proc.poll() is not None
    assert client._proc is None


def test_del_terminates_subprocess():
    client = make_client()
    client.initialize()
    proc = client._proc
    del client
    gc.collect()
    deadline = time.monotonic() + 5
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert proc.poll() is not None
