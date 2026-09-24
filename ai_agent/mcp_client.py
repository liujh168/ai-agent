"""
ai_agent.mcp_client — 极简 MCP HTTP/Streamable HTTP 客户端
==========================================================
零外部新增依赖（只用项目已有的 requests）。

支持 MCP Streamable HTTP Transport（官方推荐，POST JSON-RPC 请求）。
不支持 STDIO / SSE（需要子进程或特殊流处理），后续可扩展。

用法:
    from ai_agent.mcp_client import MCPClient, MCPManager

    # 单 server
    c = MCPClient("https://mcp.example.com/tools")
    c.initialize()
    tools = c.list_tools()         # → [MCPTool, ...]
    result = c.call_tool("calc", {"a": 1, "b": 2})  # → str

    # 多 server 聚合
    mgr = MCPManager(["https://mcp.example.com/tools", ...])
    mgr.connect_all()
    schemas = mgr.tool_schemas()          # → OpenAI function schemas
    tool_map = mgr.build_tool_callables() # → {name: callable}
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import requests

log = logging.getLogger(__name__)

# ============================================================
# 数据结构
# ============================================================


@dataclass
class MCPTool:
    """MCP Server 返回的工具描述"""

    name: str
    description: str = ""
    input_schema: dict = field(default_factory=dict)
    server_url: str = ""  # 来源 server
    server_index: int = 0  # MCPManager 内部索引


@dataclass
class MCPError(Exception):
    """MCP 协议级错误"""

    code: int
    message: str
    data: Any = None

    def __str__(self) -> str:
        return f"MCPError({self.code}: {self.message})"


# ============================================================
# 单个 Server 的客户端
# ============================================================


class MCPClient:
    """连接到一个 MCP Server（Streamable HTTP）"""

    def __init__(
        self,
        url: str,
        timeout: float = 15.0,
        extra_headers: dict | None = None,
        protocol_version: str = "2025-06-18",
    ):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.protocol_version = protocol_version
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Accept-Encoding": "identity",  # 简化处理，避免 gzip 解码问题
        }
        if extra_headers:
            self.headers.update(extra_headers)

        self._session_id: str | None = None  # MCP Streamable HTTP 会话
        self._request_id: int = 0
        self._lock = threading.Lock()
        self._connected: bool = False
        self._server_info: dict = {}
        self._tools: list[MCPTool] = []
        self.server_index: int = -1  # 由 MCPManager.connect_all 分配

    @property
    def tools(self) -> list[MCPTool]:
        """最近一次 list_tools() 拉到的工具列表"""
        return list(self._tools)

    # ---------- 底层 JSON-RPC ----------

    def _send(self, method: str, params: dict | None = None) -> Any:
        """发送一个 JSON-RPC 请求，返回 result 或抛 MCPError / requests 异常"""
        with self._lock:
            self._request_id += 1
            req_id = self._request_id

        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {},
        }

        # Streamable HTTP: 如果已有 session_id，放进 headers
        hdrs = dict(self.headers)
        if self._session_id:
            hdrs["Mcp-Session-Id"] = self._session_id
        if self.protocol_version:
            hdrs["Mcp-Protocol-Version"] = self.protocol_version

        try:
            resp = requests.post(
                self.url,
                data=json.dumps(payload),
                headers=hdrs,
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise MCPError(code=-1, message=f"HTTP 请求失败: {e}") from e

        # 提取 session_id（首次响应会返回）
        new_sid = resp.headers.get("Mcp-Session-Id")
        if new_sid:
            self._session_id = new_sid

        if resp.status_code not in (200, 202, 204):
            raise MCPError(
                code=resp.status_code,
                message=f"HTTP {resp.status_code}: {resp.text[:200]}",
            )

        data = self._parse_response(resp)

        # SSE 格式会有多行（data: {...}），取第一条完整 JSON-RPC
        if isinstance(data, list):
            data = data[0] if data else {}

        if "error" in data:
            err = data["error"]
            raise MCPError(
                code=err.get("code", -1),
                message=err.get("message", "未知错误"),
                data=err.get("data"),
            )

        return data.get("result")

    def _notify(self, method: str, params: dict | None = None) -> None:
        """Send a JSON-RPC notification without an id."""
        hdrs = dict(self.headers)
        if self._session_id:
            hdrs["Mcp-Session-Id"] = self._session_id
        if self.protocol_version:
            hdrs["Mcp-Protocol-Version"] = self.protocol_version
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
        }
        try:
            resp = requests.post(
                self.url,
                data=json.dumps(payload),
                headers=hdrs,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise MCPError(code=-1, message=f"HTTP notification 失败: {exc}") from exc
        new_sid = resp.headers.get("Mcp-Session-Id")
        if new_sid:
            self._session_id = new_sid
        if resp.status_code not in (200, 202, 204):
            raise MCPError(
                code=resp.status_code,
                message=f"notification HTTP {resp.status_code}: {resp.text[:200]}",
            )

    @staticmethod
    def _parse_response(resp) -> dict:
        text = resp.text.strip()
        if not text:
            return {}
        content_type = resp.headers.get("Content-Type", "")
        if "text/event-stream" in content_type or text.startswith(("event:", "data:")):
            for line in text.splitlines():
                if line.startswith("data:"):
                    payload = line[5:].strip()
                    if not payload:
                        continue
                    try:
                        return json.loads(payload)
                    except json.JSONDecodeError:
                        continue
            raise MCPError(code=-1, message=f"无法解析 SSE 响应: {text[:200]}")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise MCPError(code=-1, message=f"非 JSON 响应: {text[:200]}") from exc

    # ---------- 高层 API ----------

    def initialize(self) -> dict:
        """MCP 初始化握手"""
        result = self._send(
            "initialize",
            {
                "protocolVersion": self.protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "ai-agent", "version": "1.0"},
            },
        )
        self._connected = True
        self._server_info = result or {}
        negotiated = self._server_info.get("protocolVersion")
        if negotiated:
            self.protocol_version = str(negotiated)

        self._notify("notifications/initialized", {})

        return self._server_info

    def list_tools(self) -> list[MCPTool]:
        """列出 server 上可用的工具"""
        if not self._connected:
            self.initialize()

        result = self._send("tools/list", {})
        raw_tools = (result or {}).get("tools", [])

        tools: list[MCPTool] = []
        for t in raw_tools:
            tools.append(
                MCPTool(
                    name=t["name"],
                    description=t.get("description", ""),
                    input_schema=t.get("inputSchema", {"type": "object", "properties": {}}),
                    server_url=self.url,
                )
            )
        self._tools = tools
        return tools

    def call_tool(self, name: str, arguments: dict | None = None) -> str:
        """调用一个 MCP 工具，返回字符串结果"""
        if not self._connected:
            self.initialize()

        result = self._send(
            "tools/call",
            {
                "name": name,
                "arguments": arguments or {},
            },
        )
        is_error = bool((result or {}).get("isError"))

        # MCP 返回的 content 是 list，每个元素有 type + text
        contents = (result or {}).get("content", [])
        parts = []
        for c in contents:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(c.get("text", ""))
            elif isinstance(c, dict):
                # image / blob 等类型，先输出类型占位
                parts.append(f"[{c.get('type', 'unknown')}: {str(c)[:200]}]")

        content = "\n".join(parts) if parts else "(无输出)"
        return f"[MCP 工具错误] {content}" if is_error else content

    def ping(self) -> bool:
        """健康检查"""
        try:
            self._send("ping", {})
            return True
        except (MCPError, requests.RequestException) as exc:
            log.debug("MCP ping 失败（%s）: %s", self.url, exc)
            return False

    @property
    def connected(self) -> bool:
        return self._connected

    def close(self) -> None:
        """Best-effort MCP Streamable HTTP session termination."""
        if not self._session_id:
            return
        headers = dict(self.headers)
        headers["Mcp-Session-Id"] = self._session_id
        if self.protocol_version:
            headers["Mcp-Protocol-Version"] = self.protocol_version
        try:
            requests.delete(self.url, headers=headers, timeout=self.timeout)
        except requests.RequestException:
            pass
        finally:
            self._session_id = None
            self._connected = False


# ============================================================
# 多 Server 管理器
# ============================================================


class MCPManager:
    """管理多个 MCP Server，合并工具，桥接到本地 tool_map"""

    def __init__(self, server_urls: list[str]):
        self.server_urls = server_urls
        self._clients: list[MCPClient] = []
        self._tool_names: dict[str, tuple[int, str]] = {}  # name → (server_index, original_name)
        self._tool_schemas: list[dict] = []
        self._errors: list[str] = []

    def connect_all(self) -> None:
        """连接所有 server 并 list_tools（同步阻塞）"""
        for idx, url in enumerate(self.server_urls):
            try:
                client = MCPClient(url)
                client.initialize()
                tools = client.list_tools()
                client.server_index = idx

                for t in tools:
                    safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", t.name)
                    final_name = f"mcp_{idx}_{safe_name}"
                    suffix = 2
                    while final_name in self._tool_names:
                        final_name = f"mcp_{idx}_{safe_name}_{suffix}"
                        suffix += 1
                    self._tool_names[final_name] = (idx, t.name)
                    t.server_index = idx
                    t.name = final_name

                self._clients.append(client)
                self._errors.append(f"✅ {url}: {len(tools)} 个工具")
            except Exception as e:
                self._clients.append(None)  # 占位，保持索引对齐
                self._errors.append(f"❌ {url}: {e}")

        # 构建 schema 列表
        self._build_schemas()

    def _build_schemas(self) -> None:
        """把所有 MCP tools 转成 OpenAI function schema 格式"""
        schemas = []
        for idx, client in enumerate(self._clients):
            if client is None:
                continue
            for tool in client.tools:
                schemas.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description or f"MCP 工具: {tool.name}",
                            "parameters": tool.input_schema or {"type": "object", "properties": {}},
                        },
                    }
                )
        self._tool_schemas = schemas

    # ---------- 对外 API ----------

    @property
    def tool_schemas(self) -> list[dict]:
        """可直接拼入 TOOL_SCHEMAS 的 list"""
        return self._tool_schemas

    @property
    def total_tools(self) -> int:
        return len(self._tool_names)

    @property
    def connect_errors(self) -> list[str]:
        """连接过程中的错误/状态信息"""
        return self._errors

    def make_callables(self) -> dict[str, Callable]:
        """生成 {tool_name: callable} 字典，可直接合并到 tool_map"""
        callables: dict[str, Callable] = {}
        for final_name, (server_idx, original_name) in self._tool_names.items():
            client = self._clients[server_idx]
            if client is None:
                continue

            def _make_fn(c: MCPClient, orig: str) -> Callable:
                def fn(**kwargs) -> str:
                    try:
                        return c.call_tool(orig, kwargs)
                    except MCPError as e:
                        return f"[MCP 错误] {e}"
                    except Exception as e:
                        return f"[MCP 调用异常] {type(e).__name__}: {e}"

                fn.__name__ = final_name
                fn.__doc__ = f"MCP tool from {c.url}: {orig}"
                return fn

            callables[final_name] = _make_fn(client, original_name)

        return callables

    def is_mcp_tool(self, name: str) -> bool:
        """判断一个工具名是否来自 MCP（Web UI trace 区分用）"""
        return name in self._tool_names

    def close_all(self) -> None:
        """关闭所有已连接的 MCP 会话（best-effort，入口退出时调用）"""
        for client in self._clients:
            if client is not None:
                client.close()


# ============================================================
# 工厂函数（可选依赖保护）
# ============================================================


def create_mcp_manager(server_urls: list[str]) -> MCPManager | None:
    """安全创建 MCPManager：空列表返回 None，连接失败也返回可用实例（0 工具）"""
    if not server_urls:
        return None
    mgr = MCPManager(server_urls)
    mgr.connect_all()
    return mgr
