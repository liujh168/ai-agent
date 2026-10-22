#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
极简 MCP Server（stdio 传输，零依赖，Python 3.8+）
协议版本：2025-06-18 的核心子集 —— initialize / tools/list / tools/call / ping

运行方式：由 mcp_client.py 以子进程方式拉起，也可手动测试：
  输入一行 JSON-RPC，输出一行 JSON-RPC（换行分隔）
"""
import json
import sys

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "demo-calculator-server", "version": "0.1.0"}
CAPABILITIES = {"tools": {"listChanged": True}}

TOOLS = [
    {
        "name": "add",
        "description": "计算两个数之和",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "number", "description": "加数 a"},
                "b": {"type": "number", "description": "加数 b"},
            },
            "required": ["a", "b"],
        },
    },
    {
        "name": "get_weather",
        "description": "查询城市天气（假数据，仅演示）",
        "inputSchema": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "城市名"}},
            "required": ["city"],
        },
    },
]

_initialized = False  # 握手完成前不处理普通请求（协议要求）


def log(msg: str) -> None:
    """日志走 stderr —— stdout 必须保持纯协议通道"""
    print(f"[server] {msg}", file=sys.stderr, flush=True)


def send(payload: dict) -> None:
    """stdio 传输：一行 JSON + 换行"""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle_request(msg: dict) -> None:
    method, req_id = msg.get("method"), msg.get("id")
    params = msg.get("params") or {}
    log(f"收到请求  id={req_id} method={method}")

    if method == "initialize":
        send({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": CAPABILITIES,
                "serverInfo": SERVER_INFO,
            },
        })
        return

    if not _initialized:
        # 握手未完成：按协议应拒绝其他请求
        send({"jsonrpc": "2.0", "id": req_id,
              "error": {"code": -32002, "message": "Server not initialized"}})
        return

    if method == "ping":
        send({"jsonrpc": "2.0", "id": req_id, "result": {}})

    elif method == "shutdown":
        send({"jsonrpc": "2.0", "id": req_id, "result": None})
        log("收到 shutdown，进入关闭状态（只等 exit 通知）")

    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}})

    elif method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        schema = next((t for t in TOOLS if t["name"] == name), None)
        if schema is None:
            send({"jsonrpc": "2.0", "id": req_id,
                  "error": {"code": -32602, "message": f"Unknown tool: {name}"}})
        else:
            # 业务错误不报错：result.isError = true，把错误喂回给模型自我纠正
            missing = [k for k in schema["inputSchema"]["required"] if k not in args]
            if missing:
                text = f'Error: missing required argument "{missing[0]}"'
                send({"jsonrpc": "2.0", "id": req_id,
                      "result": {"content": [{"type": "text", "text": text}], "isError": True}})
            elif name == "add":
                total = args["a"] + args["b"]
                send({"jsonrpc": "2.0", "id": req_id, "result": {
                    "content": [{"type": "text", "text": str(total)}],
                    "structuredContent": {"sum": total},
                    "isError": False,
                }})
            elif name == "get_weather":
                text = f"{args['city']}：晴，26°C，东南风 2 级（假数据）"
                send({"jsonrpc": "2.0", "id": req_id, "result": {
                    "content": [{"type": "text", "text": text}], "isError": False}})

    else:
        send({"jsonrpc": "2.0", "id": req_id,
              "error": {"code": -32601, "message": f"Method not found: {method}"}})


def main() -> None:
    global _initialized
    log(f"启动，等待 stdin 上的 JSON-RPC 消息（pid={os.getpid()}）")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            send({"jsonrpc": "2.0", "id": None,
                  "error": {"code": -32700, "message": "Parse error"}})
            continue

        if "id" not in msg:  # 通知：无 id，不回应
            log(f"收到通知  method={msg.get('method')}")
            if msg.get("method") == "notifications/initialized":
                _initialized = True
            elif msg.get("method") == "notifications/exit":
                log("收到退出通知，bye")
                sys.exit(0)
        else:
            handle_request(msg)


import os  # noqa: E402  放这里避免干扰阅读主流程

if __name__ == "__main__":
    main()
