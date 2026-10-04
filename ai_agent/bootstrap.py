"""
ai_agent.bootstrap — CLI 与 Web UI 共享的服务装配
================================================
把两端 App 类里重复的服务初始化集中到这里，前端只保留各自的 I/O 与
ToolExecutor 策略参数（CLI/Web 的安全策略差异是刻意的，不在此统一）：

  - LMStudioClient 构建
  - RAG 知识库（统一 cache_file 绝对路径处理；lazy 构建、线程安全、只尝试一次）
  - MCP 管理器（可选，配置了 MCP_SERVERS 才启用）
  - SkillRegistry（可选，skills/ 目录不存在则跳过）
  - tool_map 构建（get_history/set_history 访问器由前端注入）
"""

from __future__ import annotations

import logging
import os
import threading

import requests

from ai_agent import app_core
from ai_agent.client import LMStudioClient
from ai_agent.config import Config
from ai_agent.rag import RAGKnowledgeBase, collect_rag_files

log = logging.getLogger(__name__)

# cfg.rag_directories 为空时的回退扫描目录
DEFAULT_RAG_DIRECTORIES = ["docs/knowledge", "data"]


def resolve_dangerous_tools(
    cfg: Config,
    *,
    cli_flag: bool | None = None,
    force_disable: bool = False,
) -> bool:
    """
    统一危险工具（run_code/run_terminal）开关语义，优先级：
    force_disable（如 Web --share）> CLI flag 显式覆盖 > Config.enable_dangerous_tools。
    """
    if force_disable:
        return False
    if cli_flag is not None:
        return bool(cli_flag)
    return cfg.enable_dangerous_tools


def resolve_rag_cache_path(cfg: Config, project_root: str) -> str:
    """rag_cache_file 为绝对路径时原样使用，否则拼到 project_root 下。"""
    if os.path.isabs(cfg.rag_cache_file):
        return cfg.rag_cache_file
    return os.path.join(project_root, cfg.rag_cache_file)


class AgentServices:
    """CLI 与 Web UI 共享的运行时服务集合。构造过程不访问网络（RAG 延迟构建）。"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.project_root = os.path.abspath(cfg.project_root)
        self.workspace_root = os.path.abspath(cfg.workspace_root)
        self.client = LMStudioClient(
            cfg.base_url,
            cfg.api_key,
            cfg.model,
            timeout=cfg.request_timeout,
        )
        self.rag: RAGKnowledgeBase | None = None
        self._rag_lock = threading.Lock()
        self._rag_attempted = False
        self.mcp_manager = self._init_mcp()
        self.skill_registry = None
        self.test_image = os.path.join(self.project_root, "_test_chart.png")

    def _init_mcp(self):
        try:
            from ai_agent.mcp_client import create_mcp_manager

            if not self.cfg.mcp_servers:
                return None
            return create_mcp_manager(list(self.cfg.mcp_servers))
        except Exception as exc:
            print(f"  ⚠️ MCP 初始化失败: {exc}")
            return None

    def ensure_rag(self, verbose: bool = True) -> RAGKnowledgeBase | None:
        """
        幂等构建 RAG 知识库索引（线程安全，失败/空目录只尝试一次）。
        首次向量化较慢，之后走缓存。返回 None 表示知识库不可用。
        """
        if self._rag_attempted:
            return self.rag
        with self._rag_lock:
            if self._rag_attempted:
                return self.rag
            self._rag_attempted = True
            try:
                rag = RAGKnowledgeBase(
                    base_url=self.cfg.base_url,
                    embed_model=self.cfg.embed_model,
                    chunk_size=self.cfg.chunk_size,
                    chunk_overlap=self.cfg.chunk_overlap,
                    cache_file=resolve_rag_cache_path(self.cfg, self.project_root),
                    request_timeout=self.cfg.request_timeout,
                )
                rag_dirs = (
                    list(self.cfg.rag_directories)
                    if self.cfg.rag_directories
                    else DEFAULT_RAG_DIRECTORIES
                )
                rag_paths = collect_rag_files(self.project_root, rag_dirs)
                if rag_paths:
                    n_chunks = rag.build(rag_paths)
                    if verbose:
                        print(f"[OK] 知识库就绪：{len(rag_paths)} 个文件 → {n_chunks} 个文本片段")
                    self.rag = rag
                else:
                    if verbose:
                        print("[WARN] RAG 扫描目录为空或不存在，跳过初始化")
                    self.rag = None
            except (requests.RequestException, RuntimeError, OSError, ValueError, KeyError) as e:
                log.warning("RAG 初始化失败: %s", e)
                if verbose:
                    print(f"[WARN] RAG 初始化失败（{e}），知识库问答将不可用")
                self.rag = None
        return self.rag

    def build_tool_map(self, get_history=None, set_history=None) -> dict:
        """
        构建完整工具映射并加载 Skill（Skill 校验 required_tools 需要完整工具名列表，
        所以 register_skills 在 tool_map 就绪后顺带完成）。
        get_history/set_history：前端注入的 history 访问器，传入后注册 summarize_history。
        """
        tool_map = app_core.build_tool_map(
            self.client,
            lambda: self.rag,
            self.test_image,
            get_history=get_history,
            set_history=set_history,
            mcp_manager=self.mcp_manager,
            workspace_root=self.workspace_root,
        )
        self._register_skills(list(tool_map.keys()))
        return tool_map

    def _register_skills(self, available_tools: list[str]) -> None:
        skill_dir = self.cfg.skill_dir
        if not skill_dir:
            return
        from ai_agent.skill import create_skill_registry

        abs_dir = os.path.join(self.project_root, skill_dir)
        self.skill_registry = create_skill_registry(abs_dir, available_tools)
