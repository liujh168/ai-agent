"""任务规划分解（Planner-Executor）演示"""

import re


def run_plan_and_execute(
    client, task: str, max_subtasks: int = 3, temperature: float = 0.7, max_tokens: int = 2048
) -> str:
    """任务规划分解（Planner-Executor）：先规划后执行，逐个子任务完成"""
    client.ensure_model_loaded()
    plan_prompt = (
        f"请将下面的任务拆解为最多 {max_subtasks} 个有序子任务，"
        "每行一个，格式为「1. 子任务描述」。只输出子任务清单，不要其他内容。\n\n"
        f"任务：{task}"
    )
    plan_text = (
        client.create_chat_completion(
            label="Plan-and-Execute · 规划",
            model=client.model,
            messages=[{"role": "user", "content": plan_prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        .choices[0]
        .message.content
    )

    subtasks = [
        m.group(1).strip() for m in re.finditer(r"^\s*\d+[.、)]\s*(.+)$", plan_text or "", re.MULTILINE)
    ]
    if not subtasks:
        subtasks = [task]
    subtasks = subtasks[:max_subtasks]

    print(f"\n[任务规划] 将任务拆解为 {len(subtasks)} 个子任务：")
    for i, st in enumerate(subtasks, 1):
        print(f"  {i}. {st}")

    accumulated = ""
    parts = []
    for i, st in enumerate(subtasks, 1):
        prev = accumulated if accumulated else "（暂无）"
        if len(prev) > 600:
            prev = "…（因篇幅较长已截断，保留最近内容）…\n" + prev[-600:]
        exec_prompt = (
            f"整体任务：{task}\n\n当前要完成的子任务：{st}\n\n"
            f"已完成部分：\n{prev}\n\n"
            "请完成这个子任务，输出可以直接合并到最终成果的内容，不要复述整体任务。"
        )
        result = (
            client.create_chat_completion(
                label=f"Plan-and-Execute · 子任务 {i}/{len(subtasks)}",
                model=client.model,
                messages=[{"role": "user", "content": exec_prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            .choices[0]
            .message.content
        )
        print(f"\n[子任务 {i}/{len(subtasks)}] {st}")
        print(result)
        accumulated += f"\n{result}"
        parts.append(f"【子任务 {i}：{st}】\n{result}")

    summary = "\n".join(
        f"【子任务 {i}：{st}】\n{res[:200]}{'…' if len(res) > 200 else ''}"
        for i, (st, res) in enumerate(zip(subtasks, parts), 1)
    )
    return f"已完成 {len(subtasks)} 个子任务（完整内容已在上方直接展示）。\n{summary}"
