"""ReAct — Reasoning + Acting（文本式推理轨迹可见）"""

from __future__ import annotations

import json
import re

# ReAct 输出解析正则
REACT_THOUGHT_RE = re.compile(
    r"Thought\s*[:：]\s*(.+?)(?=\n\s*(?:Action|Final)\s*[:：]|$)", re.DOTALL | re.IGNORECASE
)
REACT_ACTION_RE = re.compile(r"Action\s*[:：]\s*(\w+)", re.IGNORECASE)
REACT_ACTION_INPUT_RE = re.compile(
    r"Action\s*Input\s*[:：]\s*(.+?)(?=\n\s*(?:Observation|Final|Thought|Action)\s*[:：]|$)",
    re.DOTALL | re.IGNORECASE,
)
REACT_FINAL_RE = re.compile(r"Final\s*Answer\s*[:：]\s*(.+)$", re.DOTALL | re.IGNORECASE)


def parse_react_output(raw: str) -> dict:
    """
    解析一轮 ReAct 输出（纯函数，便于单测）。
    返回 {"final": str|None, "thought": str|None, "action": str|None, "action_input": str|None}
    """
    final_m = REACT_FINAL_RE.search(raw)
    thought_m = REACT_THOUGHT_RE.search(raw)
    action_m = REACT_ACTION_RE.search(raw)
    input_m = REACT_ACTION_INPUT_RE.search(raw)
    return {
        "final": final_m.group(1).strip() if final_m else None,
        "thought": thought_m.group(1).strip() if thought_m else None,
        "action": action_m.group(1).strip() if action_m else None,
        "action_input": input_m.group(1).strip() if input_m else None,
    }


def run_react(
    client,
    messages: list[dict],
    tool_map: dict,
    max_iterations: int = 6,
    temperature: float = 0.5,
    max_tokens: int = 2048,
    on_event=None,
) -> str:
    """
    ReAct（Reasoning + Acting）模式：
    让模型交替输出 Thought（思考） → Action（工具名） → Action Input（参数），
    我们执行工具后把 Observation（结果）喂回，直到模型给出 Final Answer。

    与 chat_with_tools 的核心区别：
    chat_with_tools 用原生 Function Calling，思考过程是黑盒；
    ReAct 用文本式 Prompt 技巧，每一步 Thought 都打印出来，
    让学习者看清 Agent 是"边想边做"还是"先想好再做"。
    """
    client.ensure_model_loaded()

    def emit(line: str, kind: str = "info") -> None:
        if on_event is not None:
            on_event(line, kind)
        else:
            print(line)

    # 构造工具描述文本（ReAct 用自然语言告诉模型有哪些工具）
    tool_descriptions = ""
    for name, func in tool_map.items():
        doc = (func.__doc__ or "无描述").strip().split("\n")[0]
        tool_descriptions += f"- {name}: {doc}\n"

    react_system = (
        "你是一个会边想边做的 Agent。面对用户问题，你会交替进行：\n"
        "  1. Thought（思考）：用一句话写下你现在在想什么、打算做什么\n"
        "  2. Action（行动）：选择要用的工具名\n"
        "  3. Action Input（行动输入）：给工具的参数，用合法 JSON 格式\n"
        "  4. 收到 Observation（观察结果）后，继续下一轮 Thought → Action\n"
        "  5. 当信息足够时，给出 Final Answer（最终回答）\n\n"
        "严格按以下格式输出（每次只输出一个完整 Thought + Action + Action Input，或一个 Final Answer）：\n"
        "  Thought: 你的思考\n"
        "  Action: 工具名\n"
        '  Action Input: {"key": "value"}\n'
        "  ... （我们会返回 Observation）\n"
        "  ... （继续循环）\n"
        "  Final Answer: 完整回答用户的问题\n\n"
        "可用工具：\n" + tool_descriptions
    )

    history = [{"role": "system", "content": react_system}] + list(messages)

    emit("\n╭──────── ReAct 推理轨迹 ────────", "divider")
    for iteration in range(1, max_iterations + 1):
        emit(f"│\n│  🔄 第 {iteration} 轮")
        response = client.create_chat_completion(
            model=client.model,
            messages=history,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        raw = response.choices[0].message.content or ""
        history.append({"role": "assistant", "content": raw})

        parsed = parse_react_output(raw)

        # ---- 尝试解析 Final Answer ----
        if parsed["final"] is not None:
            final = parsed["final"]
            emit(f"│  ✅ Final Answer: {final[:120]}{'…' if len(final) > 120 else ''}", "final")
            emit(f"╰──────── 共 {iteration} 轮 ────────\n", "divider")
            return final

        # ---- 解析 Thought / Action / Action Input ----
        if parsed["thought"]:
            emit(f"│  💭 Thought: {parsed['thought']}", "thought")

        if parsed["action"] is not None:
            action_name = parsed["action"]
            args_str = parsed["action_input"] or "{}"

            if action_name not in tool_map:
                obs = f"❌ 未知工具: {action_name}。可用工具: {', '.join(tool_map.keys())}"
            else:
                try:
                    args = json.loads(args_str) if args_str else {}
                except json.JSONDecodeError:
                    obs = f"❌ Action Input 不是合法 JSON，收到的内容：{args_str}"
                else:
                    emit(f"│  🔧 Action: {action_name}", "action")
                    emit(f"│  📥 Input:  {args_str}", "action")
                    try:
                        tool_result = str(tool_map[action_name](**args))
                    except Exception as e:
                        tool_result = f"工具执行异常: {type(e).__name__}: {e}"
                    obs = tool_result
                    # 截断过长结果，避免上下文爆炸
                    if len(obs) > 800:
                        obs = obs[:800] + f"\n... [结果过长已截断，总长 {len(obs)} 字符]"

            emit(f"│  👁️ Observation: {obs[:200]}{'…' if len(obs) > 200 else ''}", "observation")
            history.append({"role": "user", "content": f"Observation: {obs}"})
            continue

        # 如果既没 Final Answer 也没 Action，说明模型跑偏了
        emit(f"│  ⚠️ 模型未按 ReAct 格式输出，原始内容: {raw[:200]}...", "error")
        history.append(
            {
                "role": "user",
                "content": (
                    "上一次输出未识别到 Thought/Action/Action Input 或 Final Answer。"
                    "请严格按格式重新输出，先写 Thought，再写 Action 和 Action Input，"
                    "或者如果已能回答则写 Final Answer。"
                ),
            }
        )
        continue

    emit(f"╰──────── 已达最大迭代 {max_iterations} 轮 ────────\n", "divider")
    # 返回最后一轮模型说的东西（即使不是 Final Answer）
    return history[-1]["content"] if history else "已达最大迭代次数"
