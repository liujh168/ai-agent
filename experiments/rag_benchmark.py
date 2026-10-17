"""RAG 检索方式对照实验：纯 Python 余弦检索 vs FAISS 向量索引。

项目背景：rag.py 的 search_detailed() 有两条检索路径——
  - numpy 路径（默认）：纯 Python 逐向量算余弦相似度，O(N) 全扫描
  - FAISS 路径：chunk 数超过 faiss_threshold（默认 2000）时自动启用 IndexFlatIP
两条路径语义一致（都是余弦相似度 top-k），所以本实验的价值是**耗时对比**，
同时验证两条路径的检索结果一致性。

本实验用确定性的假 embedding 函数（不依赖 LM Studio），纯本地可跑：
  .venv/Scripts/python experiments/rag_benchmark.py                          # N = 500 / 5000
  .venv/Scripts/python experiments/rag_benchmark.py --sizes 1000 10000 --queries 50

faiss 未安装时自动跳过 FAISS 组并提示：pip install -e .[faiss]
"""

import argparse
import hashlib
import random
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from ai_agent.rag import RAGKnowledgeBase, faiss


def banner(title: str) -> None:
    print("\n" + "=" * 56)
    print(f"  {title}")
    print("=" * 56)


def make_fake_embed(dim: int):
    """确定性假 embedding：同一文本永远得到同一向量（md5 作随机种子）。

    真实 embedding 是"语义相近 → 向量相近"，这里只需要"可复现的向量"，
    因为本实验对比的是检索耗时与两条路径的结果一致性，不是检索质量。
    """

    def embed(texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            seed = int(hashlib.md5(text.encode("utf-8")).hexdigest(), 16)
            rng = random.Random(seed)
            vectors.append([rng.gauss(0, 1) for _ in range(dim)])
        return vectors

    return embed


def build_kb(chunks: list[str], embed, faiss_threshold: int, dim: int) -> RAGKnowledgeBase:
    """手动填充 RAGKnowledgeBase（跳过 build() 的文件读取与 HTTP embedding）。"""
    kb = RAGKnowledgeBase(cache_file="__never_used__.json", faiss_threshold=faiss_threshold)
    kb._embed = embed  # 假 embedding，避免 HTTP 调用
    kb.chunks = chunks
    kb.vectors = embed(chunks)
    kb.sources = ["synthetic.md"] * len(chunks)
    kb.chunk_indices = list(range(len(chunks)))
    kb._build_faiss_index()
    return kb


def run_group(n_chunks: int, n_queries: int, dim: int) -> None:
    banner(f"实验组：N = {n_chunks} 个 chunk（向量维度 {dim}，查询 {n_queries} 次）")

    embed = make_fake_embed(dim)
    chunks = [f"这是第 {i} 号知识片段，讲述话题 {i % 50} 的第 {i // 50} 个细节。" for i in range(n_chunks)]
    queries = [f"查询话题 {i % 50} 的相关细节 {i}" for i in range(n_queries)]

    # ---- numpy 路径（faiss_threshold 设为无穷大，强制纯 Python 余弦检索）----
    kb_numpy = build_kb(chunks, embed, faiss_threshold=10**9, dim=dim)
    assert kb_numpy._faiss_index is None
    t0 = time.perf_counter()
    numpy_results = [kb_numpy.search_detailed(q, top_k=5) for q in queries]
    numpy_time = time.perf_counter() - t0
    numpy_ms = numpy_time / n_queries * 1000
    print(f"  numpy 路径（纯 Python 余弦全扫描）: 总耗时 {numpy_time:6.2f}s，平均 {numpy_ms:7.1f} ms/查询")

    # ---- FAISS 路径（faiss_threshold=0，强制启用 IndexFlatIP）----
    if faiss is None:
        print("  FAISS 路径：跳过（faiss 未安装，运行 pip install -e .[faiss] 后重跑）")
        return
    kb_faiss = build_kb(chunks, embed, faiss_threshold=0, dim=dim)
    assert kb_faiss._faiss_index is not None
    t0 = time.perf_counter()
    faiss_results = [kb_faiss.search_detailed(q, top_k=5) for q in queries]
    faiss_time = time.perf_counter() - t0
    faiss_ms = faiss_time / n_queries * 1000
    print(f"  FAISS 路径（IndexFlatIP）:           总耗时 {faiss_time:6.2f}s，平均 {faiss_ms:7.1f} ms/查询")
    print(f"  加速比: {numpy_ms / faiss_ms:.1f}x")

    # ---- 结果一致性：比较两条路径 top-5 的 chunk_index ----
    exact = 0
    overlap_sum = 0
    for rn, rf in zip(numpy_results, faiss_results):
        idx_n = [c.chunk_index for c in rn]
        idx_f = [c.chunk_index for c in rf]
        if idx_n == idx_f:
            exact += 1
        overlap_sum += len(set(idx_n) & set(idx_f))
    print(f"  结果一致性: top-5 完全一致的查询 {exact}/{n_queries}，平均重叠 {overlap_sum / n_queries:.2f}/5")
    print("  （FAISS 用 float32、numpy 路径用 float64，得分有微小舍入差，属预期）")


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG 检索方式对照实验：numpy vs FAISS")
    parser.add_argument(
        "--sizes", type=int, nargs="+", default=[500, 5000], help="chunk 数量档位（默认 500 5000）"
    )
    parser.add_argument("--queries", type=int, default=50, help="每组查询次数（默认 50）")
    parser.add_argument("--dim", type=int, default=384, help="向量维度（默认 384）")
    args = parser.parse_args()

    banner("实验设置")
    print(f"  chunk 档位: {args.sizes}，每组查询 {args.queries} 次，top_k=5")
    print(f"  embedding: 确定性假向量（dim={args.dim}），不依赖 LM Studio")
    print(f"  FAISS 可用: {'是' if faiss is not None else '否（pip install -e .[faiss]）'}")
    print("  注意：生产代码里 FAISS 只在 chunk 数 > faiss_threshold（默认 2000）时启用；")
    print("        本实验通过设置 faiss_threshold=0 / 10**9 强制走某一条路径。")

    for n in args.sizes:
        run_group(n, args.queries, args.dim)

    print("\n要点：检索路径选择在 rag.py:168-176（search_detailed），")
    print("FAISS 索引构建在 rag.py:186-200（_build_faiss_index，阈值 faiss_threshold）。")


if __name__ == "__main__":
    main()
