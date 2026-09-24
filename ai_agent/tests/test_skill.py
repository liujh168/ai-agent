"""Skill 加载 / Frontmatter 解析 / 关键词匹配的单元测试（不依赖 LM Studio）"""

import os
import tempfile

from ai_agent.skill import SkillRegistry, _parse_frontmatter, _parse_value, create_skill_registry

_SKILL_MD = """---
name: plan_and_write
description: 规划并执行写作任务
trigger_keywords: [规划, 拆解]
required_tools: [file_read, file_write]
max_rounds: 5
---

## 你是规划者
正文指令
"""


def _write_skill(directory: str, filename: str, content: str) -> str:
    path = os.path.join(directory, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


class TestParseValue:
    def test_inline_list(self):
        assert _parse_value("[a, b, 'c']") == ["a", "b", "c"]
        assert _parse_value("[]") == []

    def test_bool(self):
        assert _parse_value("true") is True
        assert _parse_value("Yes") is True
        assert _parse_value("false") is False
        assert _parse_value("no") is False

    def test_int(self):
        assert _parse_value("5") == 5

    def test_string_strips_quotes(self):
        assert _parse_value("'hello'") == "hello"
        assert _parse_value('"world"') == "world"
        assert _parse_value("plan") == "plan"


class TestParseFrontmatter:
    def test_kv_inline_list_and_block_list(self):
        fm = _parse_frontmatter(
            "name: s1\nmax_rounds: 5\ntags: [a, b]\ntrigger_keywords:\n  - 规划\n  - plan\n"
        )
        assert fm["name"] == "s1"
        assert fm["max_rounds"] == 5
        assert fm["tags"] == ["a", "b"]
        assert fm["trigger_keywords"] == ["规划", "plan"]

    def test_comments_and_blank_lines_skipped(self):
        fm = _parse_frontmatter("# 注释\n\nname: s1\n")
        assert fm == {"name": "s1"}


class TestSkillRegistry:
    def test_load_and_match(self):
        with tempfile.TemporaryDirectory() as td:
            _write_skill(td, "a.md", _SKILL_MD)
            registry = SkillRegistry(td, ["file_read", "file_write"])
            assert registry.count == 1

            skill = registry.match("帮我规划并拆解这个任务")
            assert skill is not None
            assert skill.name == "plan_and_write"
            assert skill.max_rounds == 5
            assert skill.instructions.startswith("## 你是规划者")

    def test_missing_frontmatter_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            _write_skill(td, "bad.md", "# 没有 frontmatter\n")
            registry = SkillRegistry(td, [])
            assert registry.count == 0
            assert any("Frontmatter" in e for e in registry.errors)

    def test_missing_name_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            _write_skill(td, "bad.md", "---\ndescription: x\n---\n正文\n")
            registry = SkillRegistry(td, [])
            assert registry.count == 0
            assert any("name" in e for e in registry.errors)

    def test_disabled_skill_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            _write_skill(td, "off.md", _SKILL_MD.replace("max_rounds: 5", "max_rounds: 5\nenabled: false"))
            registry = SkillRegistry(td, [])
            assert registry.count == 0

    def test_invalid_max_rounds_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            _write_skill(td, "bad.md", _SKILL_MD.replace("max_rounds: 5", "max_rounds: 0"))
            registry = SkillRegistry(td, [])
            assert registry.count == 0
            assert any("max_rounds" in e for e in registry.errors)

    def test_unknown_required_tools_filtered(self):
        with tempfile.TemporaryDirectory() as td:
            _write_skill(td, "a.md", _SKILL_MD.replace("[file_read, file_write]", "[file_read, nope]"))
            registry = SkillRegistry(td, ["file_read"])
            assert registry.count == 1
            assert registry.get("plan_and_write").required_tools == ["file_read"]

    def test_match_threshold_rejects_low_coverage(self):
        """关键词覆盖率 <= 0.3 不命中（防单字误触）"""
        with tempfile.TemporaryDirectory() as td:
            _write_skill(td, "a.md", _SKILL_MD)
            registry = SkillRegistry(td, [])
            # "规划" 2 字 / 8 字输入 = 0.25 < 0.3
            assert registry.match("这本书规划得很好") is None
            assert registry.match("北京天气怎么样") is None
            assert registry.match("") is None

    def test_empty_or_missing_dir(self):
        registry = SkillRegistry("/nonexistent/skill/dir", [])
        assert registry.count == 0
        assert registry.match("规划") is None


class TestCreateSkillRegistry:
    def test_empty_dir_returns_none(self):
        assert create_skill_registry("") is None
        assert create_skill_registry("/nonexistent/skill/dir") is None

    def test_loadable_dir_returns_registry(self):
        with tempfile.TemporaryDirectory() as td:
            _write_skill(td, "a.md", _SKILL_MD)
            registry = create_skill_registry(td, ["file_read", "file_write"])
            assert registry is not None
            assert registry.count == 1
