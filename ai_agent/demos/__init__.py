"""
ai_agent.demos — 演示算法（ReAct / 思维链 / 反思 / 规划 / 结构化提取）
======================================================================
从 client.py 拆出的演示级 Agent 算法。每个函数第一个参数是 LMStudioClient
（鸭子类型），LMStudioClient 上的同名方法只是薄委托，保持既有 API 不变。
"""

from ai_agent.demos.extract import run_structured_extract
from ai_agent.demos.planner import run_plan_and_execute
from ai_agent.demos.react import (
    REACT_ACTION_INPUT_RE,
    REACT_ACTION_RE,
    REACT_FINAL_RE,
    REACT_THOUGHT_RE,
    parse_react_output,
    run_react,
)
from ai_agent.demos.reasoning import run_native_reasoning, run_reasoning
from ai_agent.demos.reflexion import run_reflexion

__all__ = [
    "REACT_ACTION_INPUT_RE",
    "REACT_ACTION_RE",
    "REACT_FINAL_RE",
    "REACT_THOUGHT_RE",
    "parse_react_output",
    "run_native_reasoning",
    "run_plan_and_execute",
    "run_react",
    "run_reasoning",
    "run_reflexion",
    "run_structured_extract",
]
