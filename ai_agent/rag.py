"""
RAG 知识库 — 简易 PDF/MD/TXT → 本地 embedding 向量化 → 余弦相似度检索
"""
import os
import pickle
import re

import requests
from pypdf import PdfReader


class RAGKnowledgeBase:
    """简易 RAG：PDF/MD/TXT 提取 → 本地 embedding 向量化 → 余弦相似度检索"""

    # 支持的文档扩展名
    SUPPORTED_EXT = {".pdf", ".md", ".txt"}

    def __init__(
        self,
        base_url: str = "http://localhost:1234/v1",
        embed_model: str = "text-embedding-nomic-embed-text-v1.5",
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        cache_file: str = "_rag_cache.pkl",
    ):
        self.base_url = base_url
        self.embed_model = embed_model
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.cache_file = cache_file
        self.chunks: list[str] = []
        self.vectors: list[list[float]] = []

    def _embed(self, texts: list[str]) -> list[list[float]]:
        resp = requests.post(
            f"{self.base_url}/embeddings",
            json={"model": self.embed_model, "input": texts}, timeout=60,
        )
        resp.raise_for_status()
        return [item["embedding"] for item in resp.json()["data"]]

    def _extract_text(self, path: str) -> str:
        ext = os.path.splitext(path)[1].lower()
        if ext == ".pdf":
            reader = PdfReader(path)
            parts = []
            for page in reader.pages:
                try:
                    parts.append(page.extract_text() or "")
                except Exception:
                    continue
            return "\n".join(parts)
        if ext in (".md", ".txt"):
            for enc in ("utf-8", "utf-8-sig", "gbk", "latin-1"):
                try:
                    with open(path, encoding=enc) as f:
                        return f.read()
                except UnicodeDecodeError:
                    continue
            raise RuntimeError(f"无法识别文件 {path} 的编码")
        raise RuntimeError(f"不支持的文件类型：{ext}（仅支持 {self.SUPPORTED_EXT}）")

    def _cosine(self, a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(x * x for x in b) ** 0.5
        return dot / (na * nb) if na and nb else 0.0

    def build(self, file_paths: list[str]) -> int:
        """构建索引（带缓存：文件名 + 大小 + mtime）"""
        # 过滤掉不存在的文件，扩展名校验
        valid_paths = []
        for p in file_paths:
            if not os.path.exists(p):
                print(f"  [RAG] 跳过不存在的文件: {p}")
                continue
            ext = os.path.splitext(p)[1].lower()
            if ext not in self.SUPPORTED_EXT:
                raise ValueError(f"不支持的文件类型 {ext}：{p}（仅支持 {self.SUPPORTED_EXT}）")
            valid_paths.append(p)
        if not valid_paths:
            print("  [RAG] 无有效文件跳过构建")
            return 0

        key = "|".join(
            f"{os.path.basename(p)}:{os.path.getsize(p)}:{os.path.getmtime(p):.0f}"
            for p in valid_paths
        )
        if os.path.exists(self.cache_file):
            with open(self.cache_file, "rb") as f:
                cached = pickle.load(f)
            if cached.get("key") == key and cached.get("chunks"):
                self.chunks = cached["chunks"]
                self.vectors = cached["vectors"]
                return len(self.chunks)

        for path in valid_paths:
            text = re.sub(r"\s+", " ", self._extract_text(path))
            step = self.chunk_size - self.chunk_overlap
            self.chunks.extend(
                text[i:i + self.chunk_size]
                for i in range(0, max(1, len(text) - self.chunk_overlap), step)
            )
        self.chunks = [c for c in self.chunks if c.strip()]

        self.vectors = []
        batch = 16
        for i in range(0, len(self.chunks), batch):
            self.vectors.extend(self._embed(self.chunks[i:i + batch]))
        with open(self.cache_file, "wb") as f:
            pickle.dump({"key": key, "chunks": self.chunks, "vectors": self.vectors}, f)
        return len(self.chunks)

    def search(self, query: str, top_k: int = 5) -> list[tuple[str, float]]:
        qv = self._embed([query])[0]
        scored = [(self._cosine(qv, v), i) for i, v in enumerate(self.vectors)]
        scored.sort(reverse=True)
        return [(self.chunks[i], s) for s, i in scored[:top_k]]

    def answer(self, question: str, client, top_k: int = 5, max_tokens: int = 1024) -> str:
        results = self.search(question, top_k)
        context = "\n\n".join(f"[片段{i + 1}]\n{text}" for i, (text, _score) in enumerate(results))
        messages = [
            {"role": "system", "content": (
                "你是知识库问答助手，只能依据提供的资料片段回答。"
                "资料中没有的信息要明确说明'资料中未找到'。"
            )},
            {"role": "user", "content": f"参考资料：\n{context}\n\n问题：{question}"},
        ]
        return client.chat(messages, max_tokens=max_tokens)
