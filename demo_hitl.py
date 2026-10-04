"""Human-in-the-loop 模拟演示：直接驱动项目真实的 ToolExecutor / human_confirm。

不依赖 LLM：用脚本模拟「模型发起工具调用」，用预置答案模拟「人类在终端输入」。
运行: python demo_hitl.py
"""

import builtins
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from ai_agent.tooling import ToolExecutor, ToolPolicy
from ai_agent.tools.system import human_confirm, run_terminal


class FakeHuman:
    """模拟坐在终端前的人：按队列依次回答，并把交互打印出来。"""

    def __init__(self, answers):
        self._answers = iter(answers)

    def input(self, prompt=""):
        try:
            answer = next(self._answers)
        except StopIteration:
            answer = "n"
        print(f"{prompt}{answer}   ←（模拟人类输入）")
        return answer


def banner(title):
    print("\n" + "=" * 56)
    print(f"  {title}")
    print("=" * 56)


# ---------- 场景 1：基础设施级确认闸门（ToolExecutor + confirm 回调） ----------
banner("场景 1：run_terminal 触发执行前确认，人类先拒绝、后批准")

human = FakeHuman(["n", "y"])  # 第一次拒绝，第二次批准


def cli_confirm(name, risk, arguments):
    """与 main.py:_confirm_tool 等价的确认回调"""
    print(f"\n[安全确认] 工具 {name}（风险: {risk}）")
    print(f"  参数: {arguments}")
    reply = human.input("  允许执行？输入 y/yes 确认: ").strip().lower()
    return reply in {"y", "yes", "ok", "确认", "执行"}


policy = ToolPolicy(allow_execute=True, confirm_execute=True)  # CLI --enable-dangerous-tools 同款
executor = ToolExecutor({"run_terminal": run_terminal}, policy=policy, confirm=cli_confirm)

print("\n>>> 模拟模型第 1 次调用 run_terminal('Get-Date')")
r1 = executor.execute("run_terminal", {"command": "Get-Date"})
print(f"[Tool Result] ok={r1.ok}\n{r1.content}")

print("\n>>> 人类改主意了，模型重试同一个调用")
r2 = executor.execute("run_terminal", {"command": "Get-Date"})
print(f"[Tool Result] ok={r2.ok}\n{r2.content[:300]}")

# ---------- 场景 2：策略硬拦截（不需要人类，直接拒） ----------
banner("场景 2：未开启危险工具时，策略层直接硬拦截（人类无机会确认）")

strict = ToolExecutor({"run_terminal": run_terminal}, policy=ToolPolicy(allow_execute=False))
r3 = strict.execute("run_terminal", {"command": "Get-Date"})
print(f"[Tool Result] ok={r3.ok}\n{r3.content}")

# ---------- 场景 3：模型驱动的 human_confirm 工具 ----------
banner("场景 3：模型主动调用 human_confirm 工具，人类批准后再执行敏感操作")

human2 = FakeHuman(["y"])
orig_input = builtins.input
builtins.input = human2.input  # human_confirm 内部用的是 builtins.input
try:
    print("\n>>> 模拟模型调用 human_confirm(action='删除 demo.db 中的所有测试数据')")
    result = human_confirm("删除 demo.db 中的所有测试数据")
finally:
    builtins.input = orig_input
print(f"[Tool Result] {result}")
print(">>> 模型看到 [已确认]，才会继续调用真正的删除工具")

banner("演示结束")
print("要点：闸门在 ToolExecutor.execute()（tooling.py:67-93），")
print("确认回调由使用方注入（main.py:181）；human_confirm 则是模型可主动调用的工具（tools/system.py:198）。")
