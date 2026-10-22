#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Skill 路由 + ReAct vs FC 对比演示（零依赖）
对应课件：Skill 路由 / ReAct 对比。LLM 用脚本桩代替，看清三件事：
  1. Skill 关键词路由如何收窄工具集（22 → 5）并注入提示词
  2. ReAct 模式：模型输出 Thought/Action/Input 文本，Agent 用正则解析
  3. FC 模式：模型输出结构化 tool_calls JSON，无需解析
运行：python react_vs_fc_demo.py
"""
import json
import re

# ── 1. Skill 路由：关键词命中 → 收窄工具集 + 注入提示词 ──
SKILLS = {
    "wordpress-publish": {
        "keywords": ["wordpress", "发布", "发文", "博客"],
        "tools": ["read_file", "write_file", "http_request", "wordpress_publish", "get_weather"],
        "prompt": "你是 WordPress 发布助手，按步骤：读取草稿 → 排版 → 发布 → 回报链接。",
    }
}
ALL_TOOLS = [f"tool_{i:02d}" for i in range(22)]  # 全部 22 种能力

def skill_route(user_text: str):
    for name, skill in SKILLS.items():
        if any(k in user_text.lower() for k in skill["keywords"]):
            return name, skill
    return None, None

# ── 2. 两种模式跑同一道题 ─────────────────────────
TASK = "查一下北京天气，再算 3×17"

FAKE_WEATHER = "北京：晴，26°C"
FAKE_TOOLS = {
    "get_weather": lambda args: FAKE_WEATHER,
    "multiply": lambda args: str(args["a"] * args["b"]),
}

# ReAct：模型输出文本，Agent 解析 Thought/Action/Action Input
REACT_SCRIPT = [
    "Thought: 需要先查北京天气\nAction: get_weather\nAction Input: {\"city\": \"北京\"}",
    "Thought: 再算 3×17\nAction: multiply\nAction Input: {\"a\": 3, \"b\": 17}",
    f"Thought: 信息齐全，可以总结\nFinal Answer: {FAKE_WEATHER}；3×17=51",
]
REACT_ACTION_RE = re.compile(r"Action:\s*(\w+)\s*\nAction Input:\s*(\{.*?\})", re.S)

# FC：模型输出结构化 tool_calls
FC_SCRIPT = [
    {"content": None, "tool_calls": [{"id": "c1", "name": "get_weather", "arguments": {"city": "北京"}}]},
    {"content": None, "tool_calls": [{"id": "c2", "name": "multiply", "arguments": {"a": 3, "b": 17}}]},
    {"content": f"{FAKE_WEATHER}；3×17=51。", "tool_calls": []},
]

def run_react():
    print("\n── ReAct 模式（文本协议）──")
    messages = [{"role": "user", "content": TASK}]
    for i, text in enumerate(REACT_SCRIPT, 1):
        print(f"  第{i}轮 模型原文输出：\n    {text.replace(chr(10), chr(10) + '    ')}")
        m = REACT_ACTION_RE.search(text)
        if m:  # Agent 侧解析文本，提取动作
            name, args = m.group(1), json.loads(m.group(2))
            obs = FAKE_TOOLS[name](args)
            messages.append({"role": "user", "content": f"Observation: {obs}"})
            print(f"  Agent 解析 → 执行 {name} → Observation: {obs}")
        elif "Final Answer" in text:
            print("  检测到 Final Answer → 循环结束")

def run_fc():
    print("\n── FC 模式（结构化协议）──")
    for i, resp in enumerate(FC_SCRIPT, 1):
        if resp["tool_calls"]:
            for call in resp["tool_calls"]:
                print(f"  第{i}轮 模型返回 tool_calls: {call['name']}({json.dumps(call['arguments'], ensure_ascii=False)})")
                result = FAKE_TOOLS[call["name"]](call["arguments"])
                print(f"  Agent 直接读取 → 执行 → role=tool 回写: {result}")
        else:
            print(f"  第{i}轮 finish_reason=stop，最终回答：{resp['content']}")

if __name__ == "__main__":
    print("=== Skill 路由 + ReAct / FC 对比演示 ===")
    user = "帮我把这篇文章发布到 WordPress"
    name, skill = skill_route(user)
    print(f"\n── Skill 路由：「{user}」──")
    print(f"  命中 Skill: {name}")
    print(f"  工具集: {len(ALL_TOOLS)} 种 → 收窄为 {len(skill['tools'])} 种 {skill['tools']}")
    print(f"  system prompt 注入: {skill['prompt'][:30]}…")
    run_react()
    run_fc()
    print("\n对照结论：ReAct 靠文本解析（灵活但脆、多耗 token）；FC 靠结构化字段（可靠、省 token）。")
