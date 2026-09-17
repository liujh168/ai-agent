"""
扩展单元测试 — 覆盖路由修复、共享模块、解析器、安全边界、离线 RAG、配置
运行：python -m pytest ai_agent/tests/ -v
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ai_agent.app_core import (  # noqa: E402
    SYSTEM_PROMPT,
    TOOL_COUNT,
    TOOL_SCHEMAS,
    build_tool_map,
    clean_react_tool_map,
)
from ai_agent.config import Config  # noqa: E402
from ai_agent.eval import EVAL_CASES  # noqa: E402
from ai_agent.multi_agent import _parse_critic_decision, _parse_subtasks  # noqa: E402
from ai_agent.rag import RAGKnowledgeBase  # noqa: E402
from ai_agent.tools import run_code, run_terminal  # noqa: E402

# ============================================================
# 共享模块 / 路由修复
# ============================================================


class TestAppCore:
    def test_system_prompt_routes_plan_and_extract(self):
        """修复点：SYSTEM_PROMPT 必须提示模型调用 plan_task / extract_structured"""
        assert "plan_task" in SYSTEM_PROMPT
        assert "extract_structured" in SYSTEM_PROMPT
        # 这两个是之前漏路由、导致 eval 必失败的"死工具"
        assert "规划任务" in SYSTEM_PROMPT
        assert "结构化" in SYSTEM_PROMPT

    def test_tool_count_consistent(self):
        """TOOL_COUNT 应等于 TOOL_SCHEMAS 实际长度（单一真相源，避免 18/20/21 漂移）"""
        assert TOOL_COUNT == len(TOOL_SCHEMAS) == 21

    def test_tool_schemas_match_build_tool_map(self):
        """TOOL_SCHEMAS 的每一个工具名都能在 build_tool_map 中找到实现"""

        # 用一个假的 client / get_rag 构造工具映射
        class _DummyClient:
            pass

        tool_map = build_tool_map(
            _DummyClient(),
            lambda: None,
            "_test_chart.png",
            get_history=lambda: [],
            set_history=lambda h: None,
        )
        schema_names = {s["function"]["name"] for s in TOOL_SCHEMAS}
        assert schema_names == set(tool_map.keys())

    def test_search_knowledge_unavailable_safe(self):
        """RAG 不可用时返回友好提示而非抛异常"""

        class _DummyClient:
            pass

        tool_map = build_tool_map(_DummyClient(), lambda: None, "_test_chart.png")
        out = tool_map["search_knowledge"]("什么是 ReAct？")
        assert "不可用" in out

    def test_clean_react_tool_map_excludes_internal_demos(self):
        """ReAct 工具集应剔除内部演示工具，但保留 search_knowledge"""

        class _DummyClient:
            pass

        tool_map = build_tool_map(_DummyClient(), lambda: None, "_test_chart.png")
        cleaned = clean_react_tool_map(tool_map)
        for name in (
            "demo_stream_chat",
            "demo_reasoning",
            "demo_native_reasoning",
            "demo_react",
            "demo_code_run",
            "reflexion_answer",
            "demo_vision",
            "plan_task",
            "summarize_history",
            "demo_few_shot",
            "extract_structured",
        ):
            assert name not in cleaned
        assert "search_knowledge" in cleaned


# ============================================================
# 路由修复的端到端校验：eval 用例期望的工具必须真实存在且被提示
# ============================================================


class TestEvalRoutingConsistency:
    def test_expected_tools_all_routable(self):
        schema_names = {s["function"]["name"] for s in TOOL_SCHEMAS}
        for case in EVAL_CASES:
            if case.expected_tool is None:
                continue
            assert case.expected_tool in schema_names, (
                f"评测用例 {case.name} 期望工具 {case.expected_tool} 不在 TOOL_SCHEMAS 中"
            )
            # 系统提示词必须提示模型调用它（否则模型不会路由过去）
            assert case.expected_tool in SYSTEM_PROMPT, (
                f"SYSTEM_PROMPT 未提示调用 {case.expected_tool}，{case.name} 评测将失败"
            )


# ============================================================
# 多 Agent 解析器边界
# ============================================================


class TestMultiAgentParsers:
    def test_critic_pass_explicit(self):
        assert _parse_critic_decision("## 评审结论: ✅通过") is True
        assert _parse_critic_decision("✅ 通过") is True
        assert _parse_critic_decision("整体合格，通过") is True

    def test_critic_fail_explicit(self):
        assert _parse_critic_decision("## 评审结论: ❌不通过") is False
        assert _parse_critic_decision("❌ 不通过") is False
        assert _parse_critic_decision("不通过，需要重做") is False

    def test_critic_fail_in_first_line_wins(self):
        # 首行出现"不通过/❌"应优先判为不通过，避免把"不通过"误判为通过
        text = "❌ 不通过\n理由：缺少步骤"
        assert _parse_critic_decision(text) is False
        text2 = "不通过\n——\n（其它正文）"
        assert _parse_critic_decision(text2) is False

    def test_critic_default_pass(self):
        # 没有明显标记时默认通过，避免卡死循环
        assert _parse_critic_decision("计划看起来不错") is True

    def test_parse_subtasks_chinese_and_western(self):
        plan = "1. 第一步\n2、第二步\n3) 第三步"
        subs = _parse_subtasks(plan)
        assert subs == ["第一步", "第二步", "第三步"]

    def test_parse_subtasks_empty(self):
        assert _parse_subtasks("没有编号的文字") == []


# ============================================================
# 终端安全边界（含绕过变体）
# ============================================================


class TestTerminalSecurity:
    def test_normal_allowed(self):
        r = run_terminal("Write-Host hello", allow_unsafe=True)
        assert "安全拦截" not in r
        assert "hello" in r

    def test_disabled_by_default(self):
        r = run_terminal("Write-Host hello")
        assert "默认关闭" in r

    def test_remove_item_variants_blocked(self):
        for cmd in ("Remove-Item -Recurse -Force C:\\tmp", "Remove-Item file.txt", "Remove-Item *"):
            assert "安全拦截" in run_terminal(cmd, allow_unsafe=True), cmd

    def test_del_variants_blocked(self):
        for cmd in ("del file.txt", "del /s file.txt", "del /q file.txt"):
            assert "安全拦截" in run_terminal(cmd, allow_unsafe=True), cmd

    def test_rm_r_variants_blocked(self):
        for cmd in ("rm -r /tmp/x", "rmdir x", "rd x", "rm /r x"):
            assert "安全拦截" in run_terminal(cmd, allow_unsafe=True), cmd

    def test_format_and_reg_blocked(self):
        for cmd in ("format c:", "format d:", "reg delete HKLM\\x", "cmd /c del x"):
            assert "安全拦截" in run_terminal(cmd, allow_unsafe=True), cmd

    def test_chain_bypass_blocked(self):
        # 危险动词 + 链接符 的链式绕过
        for cmd in ("del x & echo y", "rm x | cat", "Remove-Item y ; echo z"):
            assert "安全拦截" in run_terminal(cmd, allow_unsafe=True), cmd

    def test_set_content_still_blocked(self):
        assert "安全拦截" in run_terminal("Set-Content f.txt hack", allow_unsafe=True)


# ============================================================
# 代码执行（子进程，验证 run_code 真的能跑）
# ============================================================


class TestRunCode:
    def test_executes_python(self):
        r = run_code("print(2 + 3)", allow_unsafe=True)
        assert "5" in r
        assert "退出码" in r

    def test_timeout_or_error_handled(self):
        # 语法错误应被捕获，不抛异常
        r = run_code("this is not valid python !!!", allow_unsafe=True)
        assert "执行失败" in r or "错误" in r


# ============================================================
# 离线 RAG（mock embedding，不依赖 LM Studio）
# ============================================================


class TestRAGOffline:
    def test_build_and_search_without_server(self, monkeypatch):
        # 用确定性伪 embedding，避免真实向量化请求
        monkeypatch.setattr(
            RAGKnowledgeBase,
            "_embed",
            lambda self, texts: [[1.0, 0.0] for _ in texts],
        )
        with tempfile.TemporaryDirectory() as td:
            md = os.path.join(td, "doc.md")
            with open(md, "w", encoding="utf-8") as f:
                f.write("# 标题\n这是一段用于检索的测试文本。\n")
            cache = os.path.join(td, "_rag_cache.json")
            kb = RAGKnowledgeBase(cache_file=cache)
            n = kb.build([md])
            assert n > 0
            results = kb.search("测试文本", top_k=3)
            assert isinstance(results, list)
            assert len(results) > 0
            assert isinstance(results[0], tuple) and len(results[0]) == 2

    def test_extract_text_md(self):
        with tempfile.TemporaryDirectory() as td:
            md = os.path.join(td, "a.md")
            with open(md, "w", encoding="utf-8") as f:
                f.write("hello **world**")
            kb = RAGKnowledgeBase()
            assert "hello" in kb._extract_text(md)


# ============================================================
# 配置模块
# ============================================================


class TestConfig:
    def test_defaults(self):
        cfg = Config.from_env()
        assert cfg.base_url == "http://localhost:1234/v1"
        assert cfg.model == "auto"
        assert cfg.chunk_size == 500

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("LM_STUDIO_BASE_URL", "http://example:1234/v1")
        monkeypatch.setenv("LM_STUDIO_MODEL", "qwen3")
        monkeypatch.setenv("RAG_CHUNK_SIZE", "800")
        cfg = Config.from_env()
        assert cfg.base_url == "http://example:1234/v1"
        assert cfg.model == "qwen3"
        assert cfg.chunk_size == 800


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
