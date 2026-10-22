#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FC 循环 + 安全门演示（零依赖，Python 3.8+）
对应课件：Function Calling 循环与安全门。LLM 用脚本桩代替，重点看：
  1. function calling 循环如何运转（tool_calls → 执行 → role:tool 回写 → 再循环）
  2. 五级风险分类如何拦截危险工具（L3 写操作弹人工确认）
运行：python fc_safety_demo.py
"""
import json

RISK_LEVELS = {
    "add": ("L1", "只读低风险，自动执行"),
    "get_weather": ("L1", "只读低风险，自动执行"),
    "read_file": ("L2", "只读敏感，自动执行+留痕"),
    "write_file": ("L3", "写操作，人工确认"),
    "http_request": ("L4", "外发网络，人工确认+审计"),
    "run_python": ("L5", "高危执行，直接拒绝"),
}

# 脚本化的假 LLM：按轮次依次返回预定响应（真实项目里是 HTTP 请求）
SCRIPTED_LLM = [
    {"content": None, "tool_calls": [
        {"id": "call_01", "name": "write_file",
         "arguments": {"path": "report_backup.txt", "content": "<report.txt 的内容>"}}]},
    {"content": None, "tool_calls": [
        {"id": "call_02", "name": "add", "arguments": {"a": 2, "b": 3}}]},
    {"content": "已完成：report.txt 已复制为 report_backup.txt；2+3=5。", "tool_calls": []},
]


def execute_tool(name, args):
    """真实执行工具（demo 里做了无害化简）"""
    if name == "write_file":
        return f"ok: 已写入 {args['path']}（{len(args['content'])} 字节）"
    if name == "add":
        return str(args["a"] + args["b"])
    raise ValueError(f"未知工具 {name}")


def run(max_iterations=5):
    messages = [{"role": "user", "content": "把 report.txt 复制为 report_backup.txt，再算一下 2+3"}]
    for i in range(1, max_iterations + 1):
        print(f"\n===== 第 {i} 轮循环 =====")
        print(f"[->LLM] messages 共 {len(messages)} 条")
        resp = SCRIPTED_LLM[i - 1]  # llm.chat(messages, tools=...) 的替身

        if not resp["tool_calls"]:
            print(f"[LLM->] finish_reason=stop，最终回答：{resp['content']}")
            return

        for call in resp["tool_calls"]:
            level, desc = RISK_LEVELS[call["name"]]
            print(f"[LLM->] tool_call: {call['name']}({json.dumps(call['arguments'], ensure_ascii=False)})")
            print(f"[安全门] {call['name']} → {level}（{desc}）", end="")

            # —— 安全门：按风险等级决定执行策略 ——
            if level == "L5":
                print(" → 🚫 直接拒绝")
                result = f"Error: {call['name']} 在 Web 模式下被禁用"
            elif level in ("L3", "L4"):
                ans = input(" → ⏸ 需人工确认，输入 y 放行，其他拒绝：")
                if ans.strip().lower() == "y":
                    result = execute_tool(call["name"], call["arguments"])
                    print(f"[执行] 已放行：{result}")
                else:
                    result = "Error: 操作被用户拒绝（安全确认未通过）"
                    print(f"[执行] 已拒绝：{result}")
            else:
                result = execute_tool(call["name"], call["arguments"])
                print(f" → ✅ 自动执行：{result}")

            # —— 结果回写历史（模型靠它“看到”执行结果）——
            messages.append({"role": "assistant", "tool_calls": [call]})
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
            print(f"[回写] role=tool → {result}")

    print("\n⚠️ 达到 max_iterations，强制退出循环（保险丝熔断）")


if __name__ == "__main__":
    print("=== FC 循环 + 安全门演示（LLM 为脚本桩）===")
    print("提示：write_file 会请求人工确认，试试输入 y 和 n 观察后续差异")
    run()
