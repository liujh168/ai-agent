"""
ai_agent.config — 集中配置
=========================
把散落在 client.py / rag.py / main.py 中的 base_url / model / embed_model /
chunk_size 等配置项集中到一个 dataclass，统一从环境变量读取，便于复用与评测。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parents[1])


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    """运行时配置（不可变）。默认值可直接被环境变量覆盖。"""

    base_url: str = "http://localhost:1234/v1"
    api_key: str = "lm-studio"
    model: str = "auto"
    embed_model: str = "text-embedding-nomic-embed-text-v1.5"
    chunk_size: int = 500
    chunk_overlap: int = 50
    rag_cache_file: str = "_rag_cache.json"
    project_root: str = _PROJECT_ROOT
    workspace_root: str = _PROJECT_ROOT
    request_timeout: float = 120.0
    enable_dangerous_tools: bool = False
    # MCP 服务器 URL 列表（可选）。多个用分号分隔，例如:
    #   MCP_SERVERS=https://mcp.example.com/tools;http://localhost:9000/sse
    mcp_servers: tuple = ()
    mcp_allowed_tools: tuple = ()
    # Skill 定义文件目录（可选）。空字符串或目录不存在则跳过 Skill 加载。
    # 支持声明式 Markdown 文件作为复合工作流（Skill = 场景化 Prompt + 工具子集）。
    skill_dir: str = "skills"

    @classmethod
    def from_env(cls) -> Config:
        raw_mcp = os.environ.get("MCP_SERVERS", "").strip()
        servers = tuple(s.strip() for s in raw_mcp.split(";") if s.strip()) if raw_mcp else ()
        raw_mcp_tools = os.environ.get("MCP_ALLOWED_TOOLS", "").strip()
        mcp_allowed_tools = (
            tuple(s.strip() for s in raw_mcp_tools.split(";") if s.strip()) if raw_mcp_tools else ()
        )
        return cls(
            base_url=os.environ.get("LM_STUDIO_BASE_URL", cls.base_url),
            api_key=os.environ.get("LM_STUDIO_API_KEY", cls.api_key),
            model=os.environ.get("LM_STUDIO_MODEL", cls.model),
            embed_model=os.environ.get("EMBED_MODEL", cls.embed_model),
            chunk_size=int(os.environ.get("RAG_CHUNK_SIZE", cls.chunk_size)),
            chunk_overlap=int(os.environ.get("RAG_CHUNK_OVERLAP", cls.chunk_overlap)),
            rag_cache_file=os.environ.get("RAG_CACHE_FILE", cls.rag_cache_file),
            project_root=os.environ.get("PROJECT_ROOT", cls.project_root),
            workspace_root=os.environ.get("AGENT_WORKSPACE_ROOT") or cls.workspace_root,
            request_timeout=float(os.environ.get("LM_STUDIO_TIMEOUT", cls.request_timeout)),
            enable_dangerous_tools=_env_bool(
                "ENABLE_DANGEROUS_TOOLS",
                cls.enable_dangerous_tools,
            ),
            mcp_servers=servers,
            mcp_allowed_tools=mcp_allowed_tools,
            skill_dir=os.environ.get("SKILL_DIR", cls.skill_dir),
        )
