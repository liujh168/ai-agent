"""
ai_agent.config — 集中配置
=========================
把散落在 client.py / rag.py / main.py 中的 base_url / model / embed_model /
chunk_size 等配置项集中到一个 dataclass，统一从环境变量读取，便于复用与评测。
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    """运行时配置（不可变）。默认值可直接被环境变量覆盖。"""

    base_url: str = "http://localhost:1234/v1"
    api_key: str = "lm-studio"
    model: str = "auto"
    embed_model: str = "text-embedding-nomic-embed-text-v1.5"
    chunk_size: int = 500
    chunk_overlap: int = 50
    rag_cache_file: str = "_rag_cache.pkl"

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            base_url=os.environ.get("LM_STUDIO_BASE_URL", cls.base_url),
            api_key=os.environ.get("LM_STUDIO_API_KEY", cls.api_key),
            model=os.environ.get("LM_STUDIO_MODEL", cls.model),
            embed_model=os.environ.get("EMBED_MODEL", cls.embed_model),
            chunk_size=int(os.environ.get("RAG_CHUNK_SIZE", cls.chunk_size)),
            chunk_overlap=int(os.environ.get("RAG_CHUNK_OVERLAP", cls.chunk_overlap)),
            rag_cache_file=os.environ.get("RAG_CACHE_FILE", cls.rag_cache_file),
        )
