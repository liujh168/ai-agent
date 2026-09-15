"""
批量运行 main.py banner 中的全部示例
用法: python run_all_examples.py
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_agent.client import LMStudioClient
from ai_agent.app_core import SYSTEM_PROMPT, TOOL_SCHEMAS, build_tool_map

# ============================================================
# 示例定义
# ============================================================

EXAMPLES = [
    {
        "name": "1. 普通对话 · 自我介绍",
        "query": "介绍一下你自己",
        "expect_tool": False,
    },
    {
        "name": "2. 多工具路由 · 天气+计算器",
        "query": "北京天气怎么样？123*456 等于多少？",
        "expect_tool": True,
    },
    {
        "name": "3. Few-shot · 情感分类",
        "query": "用 Few-shot 做情感分类：'这段影评怎么样？'",
        "expect_tool": True,
    },
    {
        "name": "4. SQL 查询 · 统计用户",
        "query": "用 sql_query 查一下：每个城市有多少用户",
        "expect_tool": True,
    },
    {
        "name": "5. 文件写入 · 保存诗歌",
        "query": "把这段话保存为 poem.txt：床前明月光，疑是地上霜。举头望明月，低头思故乡。",
        "expect_tool": True,
    },
    {
        "name": "6. 终端命令 · 获取日期",
        "query": "执行终端命令：Get-Date",
        "expect_tool": True,
    },
    {
        "name": "7. 反思修正 · 数学题",
        "query": "用反思方式解答：三个连续奇数之和为 45",
        "expect_tool": True,
    },
    {
        "name": "8. 图像识别 · 测试图表",
        "query": "识别一下测试图表：_test_chart.png 展示的是什么数据？",
        "expect_tool": True,
    },
    {
        "name": "9. 任务规划 · 写科普文章",
        "query": "帮我规划并写一篇关于 AI Agent 的科普文章",
        "expect_tool": True,
    },
    {
        "name": "10. 思维链推理 · 买卖鸡问题",
        "query": "思维链解答：一个人花8元买鸡，9元卖出，10元买回，11元卖出，赚了多少",
        "expect_tool": True,
    },
    {
        "name": "11. ReAct 模式 · 天气+推荐",
        "query": "用 ReAct 模式解答：北京现在天气怎么样，天气好的话帮我推荐几本 Agent 好书",
        "expect_tool": True,
    },
    {
        "name": "12. 多 Agent 协作 · 写科普文章",
        "query": "用多 Agent 协作写一篇关于「AI Agent」的科普文章（Planner+Executor+Critic）",
        "expect_tool": True,
    },
    {
        "name": "13. 结构化提取 + 计算器 + 流式 · 综合",
        "query": "用流式方式讲个笑话",
        "expect_tool": True,
    },
]


def main():
    print("=" * 70)
    print("🤖 批量运行 AI Agent 全部示例")
    print("=" * 70)

    client = LMStudioClient()
    tool_map = build_tool_map(client, lambda: None, "_test_chart.png")

    # 确保测试图片存在
    if not os.path.exists("_test_chart.png"):
        print("\n[准备] 生成测试图片 _test_chart.png ...")
        try:
            from PIL import Image, ImageDraw
            img = Image.new("RGB", (640, 400), "white")
            draw = ImageDraw.Draw(img)
            data = [("AI Agent", 120), ("RAG", 200), ("Agentic", 160), ("Multi-Agent", 90)]
            x = 60
            for label, h in data:
                draw.rectangle([x, 400 - h, x + 120, 400], fill="steelblue")
                draw.text((x + 20, 400 - h - 25), label, fill="black")
                x += 140
            draw.text((20, 20), "2026 AI Field Attention Survey", fill="black")
            img.save("_test_chart.png")
            print("[OK] 测试图片已生成")
        except Exception as e:
            print(f"[WARN] 测试图片生成失败: {e}")

    results = []
    passed = 0
    failed = 0

    for i, ex in enumerate(EXAMPLES, 1):
        print()
        print(f"{'─' * 70}")
        print(f"▶ [{i}/{len(EXAMPLES)}] {ex['name']}")
        print(f"  输入: {ex['query']}")
        print(f"{'─' * 70}")

        history = [{"role": "system", "content": SYSTEM_PROMPT}]
        history.append({"role": "user", "content": ex["query"]})

        t0 = time.time()
        try:
            reply = client.chat_with_tools(history, TOOL_SCHEMAS, tool_map)
            dt = time.time() - t0
            # 截断过长输出
            display = reply if len(reply) <= 300 else reply[:300] + f"... [截断，总长 {len(reply)} 字符]"
            print(f"  ✅ 通过 ({dt:.1f}s)")
            print(f"  输出预览: {display}")
            results.append((ex["name"], "✅ PASS", f"{dt:.1f}s"))
            passed += 1
        except Exception as e:
            dt = time.time() - t0
            print(f"  ❌ 失败 ({dt:.1f}s): {type(e).__name__}: {e}")
            results.append((ex["name"], f"❌ FAIL: {type(e).__name__}", f"{dt:.1f}s"))
            failed += 1

    # ============================================================
    # 汇总报告
    # ============================================================
    print()
    print("=" * 70)
    print(f"📊 运行结果汇总: {passed}/{len(EXAMPLES)} 通过, {failed} 失败")
    print("=" * 70)
    print(f"  {'示例':<35s} {'结果':<25s} {'耗时':>8s}")
    print(f"  {'─' * 68}")
    for name, status, dt in results:
        print(f"  {name:<35s} {status:<25s} {dt:>8s}")
    print()
    if failed == 0:
        print("🎉 全部示例通过！项目运行正常。")
    else:
        print(f"⚠️  有 {failed} 个示例失败，请检查上方错误信息。")


if __name__ == "__main__":
    main()
