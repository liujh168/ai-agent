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


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"环境变量 {name} 需要整数值，当前为 {raw!r}，请修正 .env 后重试") from None


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"环境变量 {name} 需要数值，当前为 {raw!r}，请修正 .env 后重试") from None


_loaded_env_files: set[str] = set()


def load_env(path: str | None = None) -> bool:
    """
    加载 .env 到 os.environ（幂等；不覆盖已有变量，优先级：系统环境变量 > .env）。
    path 默认为项目根目录的 .env。未安装 python-dotenv 时静默跳过并返回 False。
    """
    dotenv_path = path or os.path.join(_PROJECT_ROOT, ".env")
    if dotenv_path in _loaded_env_files:
        return True
    try:
        from dotenv import load_dotenv
    except ImportError:
        return False
    _loaded_env_files.add(dotenv_path)
    return bool(load_dotenv(dotenv_path, override=False))


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
    # FAISS 可选加速阈值：chunk 数超过该值且安装了 faiss-cpu 时启用 FAISS 索引检索
    rag_faiss_threshold: int = 2000
    project_root: str = _PROJECT_ROOT
    workspace_root: str = _PROJECT_ROOT
    request_timeout: float = 120.0
    enable_dangerous_tools: bool = False
    # 长对话压缩阈值：history 总字符数超过该值时自动摘要压缩
    history_max_chars: int = 5000
    # MCP 服务器列表（可选）。分号分隔；http(s):// 走 HTTP Transport，stdio: 前缀走本地子进程
    #   MCP_SERVERS=https://mcp.example.com/tools;stdio:python -m my_mcp_server
    mcp_servers: tuple = ()
    mcp_allowed_tools: tuple = ()
    # Skill 定义文件目录（可选）。空字符串或目录不存在则跳过 Skill 加载。
    # 支持声明式 Markdown 文件作为复合工作流（Skill = 场景化 Prompt + 工具子集）。
    skill_dir: str = "skills"
    # RAG 知识库扫描目录。Web UI / CLI 启动时递归扫描这些目录下的
    # .md / .txt / .pdf 文件作为向量化知识源。多个目录用分号分隔。
    rag_directories: tuple = ("docs/knowledge",)
    # WordPress REST API 凭证（可选，供 wordpress-publish Skill 使用）。
    # wp_url 填站点根地址，例如 https://example.com
    # wp_app_password 在后台「用户→个人资料」生成的 Application Password，格式如
    #   username xxxx xxxx xxxx xxxx
    wp_url: str = ""
    wp_user: str = ""  # WordPress 用户名。为空时尝试从 wp_app_password 空格分隔解析
    wp_app_password: str = ""

    def validate(self) -> None:
        """启动时配置校验：发现缺失/非法字段立即抛出带中文说明的 ValueError。"""
        problems: list[str] = []
        if not self.base_url.startswith(("http://", "https://")):
            problems.append(
                f"base_url 必须以 http:// 或 https:// 开头，当前为 {self.base_url!r}"
                "（对应环境变量 LM_STUDIO_BASE_URL）"
            )
        if not self.api_key.strip():
            problems.append("api_key 不能为空（对应环境变量 LM_STUDIO_API_KEY）")
        if not self.model.strip():
            problems.append("model 不能为空（对应环境变量 LM_STUDIO_MODEL）")
        if not self.embed_model.strip():
            problems.append("embed_model 不能为空（对应环境变量 EMBED_MODEL）")
        if self.chunk_size <= 0:
            problems.append(f"chunk_size 必须为正整数，当前为 {self.chunk_size}")
        if not 0 <= self.chunk_overlap < self.chunk_size:
            problems.append(
                f"chunk_overlap 必须满足 0 <= chunk_overlap < chunk_size，"
                f"当前 chunk_overlap={self.chunk_overlap}、chunk_size={self.chunk_size}"
            )
        if self.request_timeout <= 0:
            problems.append(f"request_timeout 必须为正数，当前为 {self.request_timeout}")
        if self.history_max_chars <= 0:
            problems.append(f"history_max_chars 必须为正整数，当前为 {self.history_max_chars}")
        if self.rag_faiss_threshold <= 0:
            problems.append(
                f"rag_faiss_threshold 必须为正整数，当前为 {self.rag_faiss_threshold}"
            )
        if problems:
            raise ValueError("配置校验失败，请修正 .env 后重启：\n- " + "\n- ".join(problems))

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
            chunk_size=_env_int("RAG_CHUNK_SIZE", cls.chunk_size),
            chunk_overlap=_env_int("RAG_CHUNK_OVERLAP", cls.chunk_overlap),
            rag_cache_file=os.environ.get("RAG_CACHE_FILE", cls.rag_cache_file),
            rag_faiss_threshold=_env_int("RAG_FAISS_THRESHOLD", cls.rag_faiss_threshold),
            project_root=os.environ.get("PROJECT_ROOT", cls.project_root),
            workspace_root=os.environ.get("AGENT_WORKSPACE_ROOT") or cls.workspace_root,
            request_timeout=_env_float("LM_STUDIO_TIMEOUT", cls.request_timeout),
            history_max_chars=_env_int("HISTORY_MAX_CHARS", cls.history_max_chars),
            enable_dangerous_tools=_env_bool(
                "ENABLE_DANGEROUS_TOOLS",
                cls.enable_dangerous_tools,
            ),
            mcp_servers=servers,
            mcp_allowed_tools=mcp_allowed_tools,
            skill_dir=os.environ.get("SKILL_DIR", cls.skill_dir),
            rag_directories=tuple(
                s.strip()
                for s in os.environ.get("RAG_DIRECTORIES", ";".join(cls.rag_directories)).split(";")
                if s.strip()
            ),
            wp_url=os.environ.get("WP_URL", cls.wp_url),
            wp_user=os.environ.get("WP_USER", cls.wp_user),
            wp_app_password=os.environ.get("WP_APP_PASSWORD", cls.wp_app_password),
        )
