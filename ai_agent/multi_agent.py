"""
多 Agent 协作演示 — Planner + Executor + Critic 三角色分工
===========================================================
教学目标：让学习者理解多 Agent 系统的核心思想 —— 不同角色分工协作，
通过显式消息传递（Message Passing）完成单 Agent 难以胜任的复杂任务。

三种角色：
  Planner  — 规划者：把用户的复杂请求拆解为有序子任务清单
  Executor — 执行者：逐个完成 Planner 分配的子任务，输出中间结果
  Critic   — 评审者：审查 Planner 的计划和 Executor 的产出，
            不满意时返回重规划，满意时才批准推进

协作流程：
  User → Planner（出计划）→ Critic（审计划）
  Critic → Planner（不通过，重规划）→ ... → Critic 通过
  Planner → Executor（逐子任务执行） → Critic（审每步产出）
  Critic → Executor（不满意，重做） → ... → Critic 通过
  全部完成 → 汇总最终成果给 User

设计亮点：
  - 每个角色有独立 System Prompt，体现角色人格
  - 显式 show_messages() 展示角色间的消息传递轨迹
  - Critic 不通过时循环重试（最多 rounds 轮）
  - 与 chat_with_tools（单 Agent 自己选工具）形成对比
  - 与 chat_with_react（单 Agent 边想边做）形成对比
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger("Agent")


# ============================================================
# 角色 System Prompt 定义
# ============================================================

ROLE_PROMPTS = {
    "planner": (
        "你是一个严谨的任务规划者（Planner）。"
        "你的职责是把用户请求拆解为 2-4 个有序、可执行的子任务。\n\n"
        "严格按以下格式输出（只输出子任务清单，不要解释）：\n"
        "  1. 第一个子任务的具体描述\n"
        "  2. 第二个子任务的具体描述\n"
        "  3. 第三个子任务的具体描述\n\n"
        "要求：\n"
        "- 每行以数字+点+空格开头（如 '1. '）\n"
        "- 不要用「## 规划思路」「## 子任务清单」等标题\n"
        "- 子任务按依赖关系排序\n"
        "- 最后一个子任务应是整合/交付\n"
        "- 总数 2-4 个，太少是没拆开，太多是拆过细"
    ),
    "executor": (
        "你是一个高效的执行者（Executor）。"
        "你的职责是按照 Planner 分配的子任务逐一完成，输出高质量的中间成果。\n"
        "执行原则：\n"
        "1. 每个子任务独立执行时，要把自己当作解决这个小问题的专家；\n"
        "2. 输出要具体、可直接合并到最终成果，不要写「以下是我的回答」之类的套话；\n"
        "3. 如果需要引用前面子任务的产出，要准确转述，不要臆造；\n"
        "4. Critic 指出你的产出问题时，要针对性修正，不要从头重写。\n\n"
        "输出格式：\n"
        "  ## 子任务 N 产出\n"
        "  （具体内容）\n"
    ),
    "critic": (
        "你是一个严格的质量评审者（Critic）。"
        "你的职责是审查 Planner 的计划和 Executor 的产出，确保质量达标。\n"
        "评审原则：\n"
        "1. 标准：正确性、完整性、可执行性、有无遗漏关键步骤；\n"
        "2. 只在确实发现问题时才提出批评，不要为了批评而批评；\n"
        "3. 批评要具体（指出哪个子任务/哪句话有什么问题），给出改进方向；\n"
        "4. 如果整体合格，明确说「✅ 通过」并简述理由；\n"
        "5. 如果不通过，明确说「❌ 不通过」并列出问题清单。\n\n"
        "输出格式：\n"
        "  ## 评审结论: ✅通过 或 ❌不通过\n"
        "  ## 评审意见\n"
        "  （具体内容）\n"
    ),
}


# ============================================================
# 角色调用器
# ============================================================


@dataclass
class RoleCall:
    """角色间的一次消息传递"""

    from_role: str  # planner / executor / critic / user
    to_role: str  # planner / executor / critic
    content: str
    timestamp: str = ""


def _parse_critic_decision(text: str) -> bool:
    """解析 Critic 的评审结论：True=通过，False=不通过"""
    # 先看明确的结论标记
    if "✅" in text or "通过" in text.split("\n")[0]:
        # 但要确认不是「不通过」
        first_line = text.split("\n")[0] if text else ""
        if "不通过" in first_line or "❌" in first_line:
            return False
        return True
    if "❌" in text or "不通过" in text:
        return False
    # 没明显标记 → 默认通过（避免卡死循环）
    return True


def _parse_subtasks(plan_text: str) -> list[str]:
    """从 Planner 输出中提取子任务清单"""
    import re

    # 匹配「数字. xxx」或「数字、xxx」
    items = re.findall(r"^\s*\d+[.、)]\s*(.+)$", plan_text, re.MULTILINE)
    return [i.strip() for i in items if i.strip()]


# ============================================================
# 多 Agent 协作主流程
# ============================================================


def run_multi_agent(
    client,  # LMStudioClient 实例
    user_task: str,
    max_rounds: int = 2,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    trace_callback=None,  # 可选：每行 print 文本会传给这个 callback
) -> str:
    """
    完整的 Planner → Executor → Critic 三角色协作流程。

    参数:
        client: LMStudioClient 实例
        user_task: 用户的复杂任务
        max_rounds: Critic 不通过时最多重试几轮
        trace_callback: 可选 callable(line)，每行进度文本会被回调
    返回:
        最终汇总的执行结果字符串
    """

    def _emit(line: str) -> None:
        if trace_callback is not None:
            trace_callback(line)
        else:
            print(line, flush=True)

    separator = "\n" + "=" * 50 + "\n"

    _emit(f"\n{separator}")
    _emit("🤝 多 Agent 协作启动（Planner + Executor + Critic）")
    _emit(f"📋 用户任务: {user_task}")
    _emit(separator)

    # ---------- Phase 1: Planner 出计划 ----------
    _emit("\n👤 [Planner] 正在规划...")
    planner_msgs = [
        {"role": "system", "content": ROLE_PROMPTS["planner"]},
        {"role": "user", "content": f"请为下面的任务制定执行计划：\n\n{user_task}"},
    ]
    plan_text = client.chat(
        planner_msgs, temperature=temperature, max_tokens=512, label="Planner 规划"
    )
    _emit(f"\n📊 Planner 输出:\n{plan_text}")

    subtasks = _parse_subtasks(plan_text)
    if not subtasks:
        subtasks = [plan_text.strip()]
        _emit("\n[WARN] 未能解析出子任务清单，将整个计划作为一个子任务执行")
    _emit(f"\n🔍 解析到 {len(subtasks)} 个子任务:")
    for i, st in enumerate(subtasks, 1):
        _emit(f"   {i}. {st[:50]}{'…' if len(st) > 50 else ''}")

    # ---------- Phase 2: Critic 审计划 ----------
    for round_num in range(1, max_rounds + 1):
        _emit(f"\n🔄 [第 {round_num} 轮] Critic 评审计划...")
        critic_msgs = [
            {"role": "system", "content": ROLE_PROMPTS["critic"]},
            {
                "role": "user",
                "content": (
                    f"请评审 Planner 的执行计划。\n\n"
                    f"【原始任务】\n{user_task}\n\n"
                    f"【Planner 的计划】\n{plan_text}\n\n"
                    f"【提取的子任务清单】\n"
                    + "\n".join(f"  {i + 1}. {s}" for i, s in enumerate(subtasks))
                    + "\n\n请给出评审结论。"
                ),
            },
        ]
        critic_reply = client.chat(
            critic_msgs, temperature=temperature, max_tokens=512, label="Critic 审计划"
        )
        _emit(f"\n📝 Critic 评审:\n{critic_reply}")

        if _parse_critic_decision(critic_reply):
            _emit("\n✅ Critic 通过了 Planner 的计划！")
            break

        _emit("\n❌ Critic 不通过，让 Planner 根据批评意见重规划...")
        planner_msgs = [
            {"role": "system", "content": ROLE_PROMPTS["planner"]},
            {"role": "user", "content": f"请为下面的任务制定执行计划：\n\n{user_task}"},
            {"role": "assistant", "content": plan_text},
            {
                "role": "user",
                "content": (
                    f"Critic 的评审意见（不通过）：\n\n{critic_reply}\n\n"
                    "请根据以上批评意见重新规划，输出完整的新计划。"
                ),
            },
        ]
        plan_text = client.chat(
            planner_msgs, temperature=temperature, max_tokens=512, label="Planner 重规划"
        )
        subtasks = _parse_subtasks(plan_text) or [plan_text.strip()]
        _emit(f"\n📊 Planner 重新输出:\n{plan_text}")
        _emit(f"\n🔍 新解析到 {len(subtasks)} 个子任务")
    else:
        _emit(f"\n[WARN] Critic 连续 {max_rounds} 轮不通过，将按当前计划继续执行")

    # ---------- Phase 3: Executor 逐个子任务执行 + Critic 审产出 ----------
    all_outputs = []
    accumulated_context = f"原始任务：{user_task}\n\n执行计划：\n{plan_text}"

    for i, subtask in enumerate(subtasks, 1):
        _emit(f"\n{'─' * 40}")
        _emit(f"⚙️  [Executor] 执行子任务 {i}/{len(subtasks)}: {subtask[:60]}...")

        executor_msgs = [
            {"role": "system", "content": ROLE_PROMPTS["executor"]},
            {
                "role": "user",
                "content": (
                    f"整体任务回顾：{user_task}\n"
                    f"已完成的前序子任务产出：\n{accumulated_context}\n"
                    f"当前子任务：{i}. {subtask}\n\n"
                    "请完成这个子任务，输出具体成果。"
                ),
            },
        ]
        exec_output = client.chat(
            executor_msgs,
            temperature=temperature,
            max_tokens=max_tokens,
            label=f"Executor 子任务 {i}/{len(subtasks)}",
        )
        _emit(f"\n📄 Executor 子任务 {i} 产出:\n{exec_output[:300]}{'…' if len(exec_output) > 300 else ''}")

        for round_num in range(1, max_rounds + 1):
            _emit(f"\n🔍 Critic 评审子任务 {i} 产出（第 {round_num} 轮）...")
            critic_msgs = [
                {"role": "system", "content": ROLE_PROMPTS["critic"]},
                {
                    "role": "user",
                    "content": (
                        f"请评审 Executor 对「子任务 {i}: {subtask}」的执行产出。\n\n"
                        f"【整体任务】{user_task}\n"
                        f"【当前子任务】{subtask}\n"
                        f"【Executor 产出】\n{exec_output}\n\n"
                        "请给出评审结论。"
                    ),
                },
            ]
            critic_reply = client.chat(
                critic_msgs,
                temperature=temperature,
                max_tokens=512,
                label=f"Critic 审子任务 {i}",
            )
            _emit(f"   Critic: {critic_reply[:200]}{'…' if len(critic_reply) > 200 else ''}")

            if _parse_critic_decision(critic_reply):
                _emit(f"   ✅ Critic 通过了子任务 {i}")
                break
            _emit("   ❌ Critic 不通过，让 Executor 根据批评意见修正...")
            executor_msgs = [
                {"role": "system", "content": ROLE_PROMPTS["executor"]},
                {
                    "role": "user",
                    "content": (
                        f"整体任务：{user_task}\n当前子任务：{i}. {subtask}\n\n"
                        f"你之前的产出：\n{exec_output}\n\n"
                        f"Critic 的批评意见（不通过）：\n{critic_reply}\n\n"
                        "请针对性修正，输出改进后的完整成果。"
                    ),
                },
            ]
            exec_output = client.chat(
                executor_msgs,
                temperature=temperature,
                max_tokens=max_tokens,
                label=f"Executor 修正子任务 {i}",
            )
            _emit(f"   Executor 修正后产出: {exec_output[:200]}…")
        else:
            _emit(f"   [WARN] Critic 连续 {max_rounds} 轮不通过，保留当前产出继续推进")

        all_outputs.append((i, subtask, exec_output))
        accumulated_context += f"\n\n【子任务 {i} 完成】{subtask}\n{exec_output}"
        # 截断：后序子任务只需看最近 800 字 + 开头 400 字的原始任务/计划摘要
        if len(accumulated_context) > 1200:
            accumulated_context = (
                accumulated_context[:400]
                + "\n…（前序产出已截断，保留最近内容）…\n"
                + accumulated_context[-800:]
            )

    # ---------- Phase 4: 汇总 ----------
    _emit(f"\n{separator}")
    _emit(f"📦 汇总全部 {len(all_outputs)} 个子任务的最终产出...")
    summary_parts = []
    for i, st, out in all_outputs:
        summary_parts.append(f"【子任务 {i}：{st}】\n{out}")
    final_summary = "\n\n".join(summary_parts)

    final_integrate_msgs = [
        {"role": "system", "content": ROLE_PROMPTS["planner"]},
        {
            "role": "user",
            "content": (
                f"以下是针对任务「{user_task}」的 {len(subtasks)} 个子任务执行产出，"
                "请整合为一份连贯的最终交付物给用户。可以调整结构、合并相关内容，"
                "但不要丢弃任何关键成果。\n\n" + final_summary
            ),
        },
    ]
    integrated = client.chat(
        final_integrate_msgs, temperature=temperature, max_tokens=max_tokens, label="Planner 汇总整合"
    )

    _emit(f"\n{separator}")
    _emit(f"🎯 最终交付（已整合）:\n{integrated}")
    _emit(separator)

    return integrated
