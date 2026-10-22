#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
上下文压缩 × RAG 检索演示（零依赖，纯标准库）
对应课件：压缩 × RAG 实验。embedding 用「主题维度示意向量」代替（网页模拟器同款），
让大家看清两件事：① 压缩如何改写历史结构 ② 余弦相似度检索如何选出 top-k。
运行：python context_rag_demo.py
"""
import math

TOKENS_PER_ROUND = 400
THRESHOLD = 6000
KEEP_ROUNDS = 4

# ── 实验一：上下文压缩 ─────────────────────────────
def compress_demo(rounds: int) -> None:
    print(f"\n=== 实验一：{rounds} 轮对话 × {TOKENS_PER_ROUND} tokens = {rounds * TOKENS_PER_ROUND} tokens ===")
    if rounds * TOKENS_PER_ROUND <= THRESHOLD:
        print(f"未超过阈值 {THRESHOLD}：历史原样发送，不压缩。")
        return
    print(f"超过阈值 {THRESHOLD} → 触发压缩：")
    print(f"  第 1~{rounds - KEEP_ROUNDS} 轮 → LLM 生成摘要（1 条占位消息，≈500 tokens）")
    print(f"  最近 {KEEP_ROUNDS} 轮原文保留")
    after = min(rounds, KEEP_ROUNDS) * TOKENS_PER_ROUND + 500
    print(f"  压缩前 {rounds * TOKENS_PER_ROUND} tokens → 压缩后 ≈{after} tokens"
          f"（省 {rounds * TOKENS_PER_ROUND - after} tokens）")
    print("  ⚠️ 代价：早期细节被二次概括，可能失真 —— 这正是 RAG 要补的短板")

# ── 实验二：RAG 向量检索（主题维度示意向量）────────────
TOPICS = ["压缩", "RAG", "工具", "安全", "会话", "评测", "MCP", "部署"]

CHUNKS = [
    ("上下文压缩：当历史 token 超过阈值，LLM 总结旧对话为摘要，只保留最近几轮原文。", [1.0, .15, 0, 0, .5, 0, 0, 0]),
    ("RAG 检索：文档切块后 embedding 成向量，提问按余弦相似度取 top-k 塞进上下文。", [.2, 1.0, .3, 0, 0, 0, 0, 0]),
    ("工具调用安全：五级风险分类，L1 自动执行，L3 写操作人工确认，L5 直接拒绝。", [0, 0, .8, 1.0, 0, .1, 0, 0]),
    ("多 Agent 协作：Planner 拆任务、Executor 执行、Critic 评审打回。", [0, 0, .4, 0, 0, .3, 0, .1]),
    ("MCP 接入：连接外部工具服务器，支持 HTTP 与 stdio 传输，工具带 mcp_ 前缀注册。", [0, .1, .5, 0, 0, 0, 1.0, 0]),
    ("会话持久化：历史按 session_id 存入 SQLite，切换会话即恢复上下文。", [.3, 0, 0, 0, 1.0, 0, 0, .2]),
]

LEXICON = {"压缩": 0, "摘要": 0, "历史": 0, "检索": 1, "rag": 1, "文档": 1,
           "工具": 2, "调用": 2, "安全": 3, "风险": 3, "会话": 4, "持久化": 4,
           "评测": 5, "mcp": 6, "服务器": 6, "部署": 7, "发布": 7}

def query_vec(text: str) -> list[float]:
    q = [0.0] * len(TOPICS)
    for kw, dim in LEXICON.items():
        if kw in text.lower():
            q[dim] += 1
    return q

def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return dot / ((math.sqrt(sum(x * x for x in a)) or 1) * (math.sqrt(sum(y * y for y in b)) or 1))

def rag_demo(question: str) -> None:
    print(f"\n=== 实验二：RAG 检索「{question}」 ===")
    q = query_vec(question)
    hits = [TOPICS[i] for i, v in enumerate(q) if v > 0]
    print(f"问题命中主题维度：{hits if hits else '（无）'}")
    ranked = sorted(((cosine(q, vec), text) for text, vec in CHUNKS), reverse=True)
    for rank, (score, text) in enumerate(ranked[:3], 1):
        mark = " ✓ 入选" if score > 0.01 else ""
        print(f"  top-{rank}  [{score:.3f}]{mark}  {text[:38]}…")
    print("（得分 = 点积 / 模长，即余弦相似度：只看方向，不看大小）")

if __name__ == "__main__":
    print("=== 上下文压缩 × RAG 检索演示（向量为主题示意，非真实 embedding）===")
    compress_demo(4)
    compress_demo(24)
    rag_demo("上下文压缩什么时候触发？")
    rag_demo("工具调用的安全怎么保证？")
