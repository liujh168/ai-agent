"""RAG knowledge base with safe, versioned local caching."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

import requests
from pypdf import PdfReader

log = logging.getLogger("Agent")


@dataclass(frozen=True)
class RetrievedChunk:
    text: str
    score: float
    source: str
    chunk_index: int


class RAGKnowledgeBase:
    """PDF/MD/TXT extraction, embedding, cosine retrieval, and JSON caching."""

    SUPPORTED_EXT = {".pdf", ".md", ".txt"}
    CACHE_VERSION = 2

    def __init__(
        self,
        base_url: str = "http://localhost:1234/v1",
        embed_model: str = "text-embedding-nomic-embed-text-v1.5",
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        cache_file: str = "_rag_cache.json",
        request_timeout: float = 60.0,
    ):
        self.base_url = base_url
        self.embed_model = embed_model
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.cache_file = cache_file
        self.request_timeout = request_timeout
        self.chunks: list[str] = []
        self.vectors: list[list[float]] = []
        self.sources: list[str] = []
        self.chunk_indices: list[int] = []

    def _embed(self, texts: list[str]) -> list[list[float]]:
        resp = requests.post(
            f"{self.base_url}/embeddings",
            json={"model": self.embed_model, "input": texts},
            timeout=self.request_timeout,
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
        """Build or load an index keyed by files and retrieval configuration."""
        valid_paths = []
        for path in file_paths:
            if not os.path.exists(path):
                log.info("RAG 跳过不存在的文件: %s", path)
                continue
            ext = os.path.splitext(path)[1].lower()
            if ext not in self.SUPPORTED_EXT:
                raise ValueError(f"不支持的文件类型 {ext}：{path}（仅支持 {self.SUPPORTED_EXT}）")
            valid_paths.append(path)
        if not valid_paths:
            log.info("RAG 无有效文件，跳过构建")
            return 0

        if self.chunk_size <= 0:
            raise ValueError("chunk_size 必须大于 0")
        if self.chunk_overlap < 0 or self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap 必须在 [0, chunk_size) 范围内")

        key = self._cache_key(valid_paths)
        cached = self._load_cache()
        if self._valid_cache(cached, key):
            self.chunks = cached["chunks"]
            self.vectors = cached["vectors"]
            self.sources = cached["sources"]
            self.chunk_indices = cached["chunk_indices"]
            return len(self.chunks)

        self.chunks = []
        self.vectors = []
        self.sources = []
        self.chunk_indices = []
        for path in valid_paths:
            text = re.sub(r"\s+", " ", self._extract_text(path))
            step = self.chunk_size - self.chunk_overlap
            source = str(Path(path).resolve())
            for index, start in enumerate(range(0, max(1, len(text) - self.chunk_overlap), step)):
                chunk = text[start : start + self.chunk_size]
                if chunk.strip():
                    self.chunks.append(chunk)
                    self.sources.append(source)
                    self.chunk_indices.append(index)

        batch_size = 16
        for start in range(0, len(self.chunks), batch_size):
            self.vectors.extend(self._embed(self.chunks[start : start + batch_size]))
        if len(self.vectors) != len(self.chunks):
            raise RuntimeError(
                f"embedding 返回数量不一致：chunks={len(self.chunks)}, vectors={len(self.vectors)}"
            )
        self._write_cache(key)
        return len(self.chunks)

    def search(self, query: str, top_k: int = 5) -> list[tuple[str, float]]:
        """Backward-compatible tuple search API."""
        return [(item.text, item.score) for item in self.search_detailed(query, top_k)]

    def search_detailed(
        self,
        query: str,
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> list[RetrievedChunk]:
        if not query.strip() or not self.chunks or not self.vectors:
            return []
        query_vector = self._embed([query])[0]
        scored = [(self._cosine(query_vector, vector), i) for i, vector in enumerate(self.vectors)]
        scored.sort(reverse=True)
        return [
            RetrievedChunk(
                text=self.chunks[index],
                score=score,
                source=self.sources[index] if index < len(self.sources) else "",
                chunk_index=self.chunk_indices[index] if index < len(self.chunk_indices) else index,
            )
            for score, index in scored[:top_k]
            if score >= min_score
        ]

    def answer(
        self,
        question: str,
        client,
        top_k: int = 5,
        max_tokens: int = 1024,
        min_score: float = 0.2,
    ) -> str:
        results = self.search_detailed(question, top_k, min_score=min_score)
        if not results:
            return "资料中未找到相关内容。"
        context = "\n\n".join(
            f"[片段{i + 1} | 来源: {Path(item.source).name} | 分块: {item.chunk_index}]\n{item.text}"
            for i, item in enumerate(results)
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "你是知识库问答助手，只能依据提供的资料片段回答。"
                    "资料中没有的信息要明确说明'资料中未找到'。"
                ),
            },
            {"role": "user", "content": f"参考资料：\n{context}\n\n问题：{question}"},
        ]
        return client.chat(messages, max_tokens=max_tokens)

    def _cache_key(self, valid_paths: list[str]) -> str:
        parts = [
            str(self.CACHE_VERSION),
            self.embed_model,
            str(self.chunk_size),
            str(self.chunk_overlap),
        ]
        for path in valid_paths:
            resolved = Path(path).resolve()
            stat = resolved.stat()
            parts.append(f"{resolved}:{stat.st_size}:{stat.st_mtime_ns}")
        return "|".join(parts)

    def _load_cache(self):
        if not os.path.exists(self.cache_file):
            return None
        try:
            with open(self.cache_file, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError, UnicodeError):
            return None

    def _valid_cache(self, cached, key: str) -> bool:
        if not isinstance(cached, dict) or cached.get("version") != self.CACHE_VERSION:
            return False
        if cached.get("key") != key:
            return False
        chunks = cached.get("chunks")
        vectors = cached.get("vectors")
        sources = cached.get("sources")
        indices = cached.get("chunk_indices")
        if not all(isinstance(value, list) for value in (chunks, vectors, sources, indices)):
            return False
        length = len(chunks)
        return length > 0 and len(vectors) == length and len(sources) == length and len(indices) == length

    def _write_cache(self, key: str) -> None:
        cache_path = Path(self.cache_file)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.CACHE_VERSION,
            "key": key,
            "chunks": self.chunks,
            "vectors": self.vectors,
            "sources": self.sources,
            "chunk_indices": self.chunk_indices,
        }
        temp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(temp_path, cache_path)
