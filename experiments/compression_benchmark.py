"""上下文压缩效果对照实验：压缩前后的字符/token 消耗对比。

对照组设计：
  A. 原始长对话历史（不压缩）
  B. summarize_messages() 摘要压缩（client.py:240）
  C. 摘要失败时的硬截断回退（client.py:282-293）

运行：
  .venv/Scripts/python experiments/compression_benchmark.py            # 自动检测 LM Studio，可用则真实摘要，否则模拟摘要
  .venv/Scripts/python experiments/compression_benchmark.py --llm      # 强制真实 LLM 摘要（需 LM Studio 运行中）
  .venv/Scripts/python experiments/compression_benchmark.py --rounds 60  # 合成对话轮数（默认 40）

说明：本项目用"字符数"作为上下文度量（config.py history_max_chars，默认 5000），
因为本地模型 tokenizer 不统一；中文场景下 1 字符 ≈ 1~2 token，可作粗略换算。
"""

import argparse
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from ai_agent.client import LMStudioClient

SYSTEM_PROMPT = "你是一个乐于助人的中文 AI 助手，正在和用户讨论一个 AI Agent 教学项目。"

# 合成对话素材：覆盖"事实 / 决定 / 待续事项"，让摘要任务有真实内容可压
_TOPICS = [
    (
        "Function Calling 的循环是怎么退出的？",
        "当模型回复里没有 tool_calls 字段时循环退出，把 content 作为最终答案返回。",
    ),
    (
        "工具执行出错会崩溃吗？",
        "不会。ToolExecutor 会把异常包成 ok=False 的 ToolResult，错误文本回喂给模型让它自我修正。",
    ),
    ("RAG 的分块参数怎么定的？", "chunk_size=500、overlap=50。重叠是为了不让一句话被切断在两个 chunk 里。"),
    (
        "历史压缩的触发条件是什么？",
        "history 总字符数超过 history_max_chars（默认 5000）就触发，保留最近 4 条完整消息。",
    ),
    (
        "评测框架有多少条 gold case？",
        "EVAL_CASES 里有 20 条，覆盖路由、对话、ReAct、安全边界、反思等能力维度。",
    ),
    (
        "Skill 为什么是 Markdown 文件？",
        "Markdown 对人和模型都可读，frontmatter 放元数据，正文直接当 prompt 用，零解析成本。",
    ),
    (
        "安全防御分几层？",
        "三层：AST 白名单拦表达式、ToolPolicy 策略层拦危险工具、human_confirm 让人类最终拍板。",
    ),
    (
        "embedding 模型和聊天模型是同一个吗？",
        "不是。embedding 走 /embeddings 端点，本项目默认用 nomic-embed-text，聊天模型另配。",
    ),
    ("会话历史存在哪里？", "SQLite 数据库 ai_agent_history.db，压缩后会清空旧记录并重写压缩结果。"),
    (
        "为什么评测要在临时目录里跑？",
        "防止 case 之间通过文件系统互相污染，每轮评测都从干净的 workspace 开始。",
    ),
]


def build_synthetic_history(rounds: int) -> list[dict]:
    """构造一段确定性的长对话历史：system + rounds 轮 user/assistant 问答。"""
    history = [{"role": "system", "content": SYSTEM_PROMPT}]
    for i in range(rounds):
        question, answer = _TOPICS[i % len(_TOPICS)]
        history.append({"role": "user", "content": f"第 {i + 1} 个问题：{question}"})
        history.append(
            {
                "role": "assistant",
                "content": (
                    f"{answer}（补充说明第 {i + 1} 轮：这条结论来自项目的真实实现，"
                    "你可以在对应源码文件里找到它。如果要做实验验证，建议先跑一遍 demo "
                    "再对照文档，避免只看结论不理解过程。）"
                ),
            }
        )
    return history


def total_chars(messages: list[dict]) -> int:
    return sum(len(m.get("content") or "") for m in messages)


def banner(title: str) -> None:
    print("\n" + "=" * 56)
    print(f"  {title}")
    print("=" * 56)


def make_fake_summarizer(target_len: int = 250):
    """模拟 LLM 摘要：返回一条贴近真实摘要长度（200~300 字）的确定性文本。

    不调用任何 API，只用于展示压缩机制的"字符数效果"——
    真实摘要质量对比请用 --llm 模式（需 LM Studio）。
    """

    def fake_chat(messages, **kwargs):
        body = (
            "用户与助手围绕 AI Agent 教学项目进行了多轮问答，讨论了 Function Calling 循环退出条件、"
            "工具异常兜底、RAG 分块参数（chunk_size=500、overlap=50）、历史压缩阈值（5000 字符）、"
            "评测框架的 gold case 设计、Skill 的 Markdown 化、三层安全防御、embedding 模型选型、"
            "会话持久化到 SQLite、评测隔离等话题。关键数字与决定已保留，暂无待续事项。"
        )
        # 调整到目标长度附近，模拟真实摘要的长度波动
        return (body * 3)[:target_len]

    return fake_chat


def make_failing_summarizer():
    def failing_chat(messages, **kwargs):
        raise RuntimeError("模拟：LM Studio 连接失败")

    return failing_chat


def run_compression(client: LMStudioClient, history: list[dict], label: str) -> dict:
    compressed = client.summarize_messages(history, keep_recent=4)
    return {
        "label": label,
        "messages": len(compressed),
        "chars": total_chars(compressed),
        "preview": compressed[1]["content"] if len(compressed) > 1 else "",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="上下文压缩效果对照实验")
    parser.add_argument("--rounds", type=int, default=40, help="合成对话轮数（默认 40）")
    parser.add_argument("--llm", action="store_true", help="强制使用真实 LLM 摘要（需 LM Studio 运行中）")
    args = parser.parse_args()

    history = build_synthetic_history(args.rounds)
    before_chars = total_chars(history)

    banner(f"实验设置：合成对话 {args.rounds} 轮（{len(history)} 条消息，{before_chars} 字符）")
    print("  压缩算法: client.summarize_messages(keep_recent=4)")
    print("  生产触发条件: history 总字符 > history_max_chars（默认 5000）")

    # ---- 检测 LM Studio ----
    client = LMStudioClient()
    status = client.check_server_status()
    llm_available = status["server_running"] and status["model_loaded"]

    if args.llm and not llm_available:
        print(f"\n❌ --llm 模式要求 LM Studio 运行中：{status['error']}")
        print("   请先启动 LM Studio 并加载聊天模型，或不带 --llm 运行模拟模式。")
        sys.exit(1)

    use_real_llm = llm_available
    results = []

    # ---- 对照组 A：原始历史 ----
    results.append(
        {"label": "A. 不压缩（原始历史）", "messages": len(history), "chars": before_chars, "preview": ""}
    )

    # ---- 对照组 B：摘要压缩 ----
    banner("对照组 B：summarize_messages() 摘要压缩")
    if use_real_llm:
        print(f"  模式：真实 LLM 摘要（模型 {status.get('model_name')}）")
        real_client = LMStudioClient()
        results.append(run_compression(real_client, history, "B. LLM 摘要压缩（真实）"))
    else:
        print("  模式：模拟摘要（LM Studio 未运行，用 250 字符的确定性文本代替 LLM 输出）")
        print("  → 字符数对比仍然真实；摘要质量对比请运行 --llm 模式")
        sim_client = LMStudioClient()
        sim_client.chat = make_fake_summarizer()
        results.append(run_compression(sim_client, history, "B. LLM 摘要压缩（模拟 250 字符摘要）"))

    # ---- 对照组 C：硬截断回退 ----
    banner("对照组 C：摘要 LLM 调用失败 → 硬截断回退（真实代码路径，无需 API）")
    trunc_client = LMStudioClient()
    trunc_client.chat = make_failing_summarizer()
    results.append(run_compression(trunc_client, history, "C. 硬截断回退（摘要失败时）"))

    # ---- 汇总 ----
    banner("结果汇总（字符数 = sum(len(m.content))，中文 1 字符 ≈ 1~2 token）")
    print(f"  {'方案':<34s} {'消息数':>6s} {'字符数':>8s} {'相对原始':>8s}")
    print("  " + "-" * 62)
    for r in results:
        ratio = r["chars"] / before_chars * 100
        print(f"  {r['label']:<34s} {r['messages']:>6d} {r['chars']:>8d} {ratio:>7.1f}%")

    banner("摘要内容预览（对照组 B）")
    preview = results[1]["preview"]
    print(f"  {preview[:200]}{'…' if len(preview) > 200 else ''}")

    print("\n要点：压缩算法在 client.py:240-295；生产触发在 main.py / web_ui.py，")
    print("阈值为 config.py 的 history_max_chars（默认 5000，env HISTORY_MAX_CHARS 可覆盖）。")


if __name__ == "__main__":
    main()
