#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
极简 MCP Client（stdio 传输，零依赖）
以子进程方式拉起 mcp_server.py，走完一次完整会话：
initialize → initialized → tools/list → tools/call(add) → tools/call(缺参错误) → ping → shutdown/exit
"""
import json
import subprocess
import sys

C_BLUE = "\033[94m"    # 客户端发出的请求
C_GREEN = "\033[92m"   # 收到的响应
C_YELLOW = "\033[93m"  # 通知
C_GRAY = "\033[90m"    # 说明
C_END = "\033[0m"


def show(direction: str, payload: dict, color: str) -> None:
    kind = "通知" if "id" not in payload else ("响应" if "result" in payload or "error" in payload else "请求")
    print(f"{color}{direction} [{kind}] {json.dumps(payload, ensure_ascii=False)}{C_END}")


def main() -> None:
    # 1. 启动 Server 子进程：stdin=写、stdout=读，stderr 透传看 server 日志
    proc = subprocess.Popen(
        [sys.executable, "mcp_server.py"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=None, text=True, encoding="utf-8",
    )
    next_id = 0

    def send(payload: dict) -> None:
        proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        proc.stdin.flush()

    def request(method: str, params: dict = None) -> dict:
        """发请求并阻塞等待同 id 的响应 —— 这就是 JSON-RPC 的配对逻辑"""
        nonlocal next_id
        next_id += 1
        send({"jsonrpc": "2.0", "id": next_id, "method": method, "params": params or {}})
        show("C->S", {"jsonrpc": "2.0", "id": next_id, "method": method, "params": params or {}}, C_BLUE)
        while True:
            resp = json.loads(proc.stdout.readline())
            show("S->C", resp, C_GREEN)
            if resp.get("id") == next_id:
                return resp
            # 收到的是别人的消息/通知：真实 Client 这里要分发给对应处理者

    def notify(method: str) -> None:
        send({"jsonrpc": "2.0", "method": method})
        show("C->S", {"jsonrpc": "2.0", "method": method}, C_YELLOW)

    print(f"{C_GRAY}== 步骤 0：以子进程方式启动 Server（stdio 传输）=={C_END}")

    print(f"{C_GRAY}== 步骤 1-2：initialize 握手 =={C_END}")
    init = request("initialize", {
        "protocolVersion": "2025-06-18",
        "capabilities": {"roots": {"listChanged": True}, "sampling": {}},
        "clientInfo": {"name": "demo-host", "version": "1.0.0"},
    })
    assert "error" not in init, "握手失败"

    print(f"{C_GRAY}== 步骤 3：握手完成通知（无 id，不等响应）=={C_END}")
    notify("notifications/initialized")

    print(f"{C_GRAY}== 步骤 4-5：发现工具有哪些 =={C_END}")
    tools = request("tools/list")["result"]["tools"]
    print(f"{C_GRAY}   可用工具：{[t['name'] for t in tools]}{C_END}")

    print(f"{C_GRAY}== 步骤 6-7：调用 add(2, 3) =={C_END}")
    request("tools/call", {"name": "add", "arguments": {"a": 2, "b": 3}})

    print(f"{C_GRAY}== 步骤 8：故意少传参数 b，观察 isError 机制 =={C_END}")
    request("tools/call", {"name": "add", "arguments": {"a": 2}})

    print(f"{C_GRAY}== 步骤 10：ping 保活，然后优雅关闭 =={C_END}")
    request("ping")
    request("shutdown")
    notify("notifications/exit")
    proc.wait(timeout=5)
    print(f"{C_GRAY}Server 进程已退出（returncode={proc.returncode}），会话结束。{C_END}")


if __name__ == "__main__":
    main()
