"""
ai_agent.skill — 声明式 Skill 加载、匹配与执行
==============================================
Skill 是 Tool 的上层抽象：声明式 Markdown 文件 = 场景化 System Prompt + 工具子集过滤 + 多轮执行循环。

目录结构:
    skills/
        example_plan.md      ← 每个 Skill 一个 md 文件

文件格式 (Markdown + YAML Frontmatter):
    ---
    name: plan_and_write
    description: 把复杂任务拆解为有序子任务并逐个完成
    trigger_keywords: [规划, 拆解, 分步骤, plan]
    required_tools: [search_knowledge, web_search, file_write]
    max_rounds: 5
    ---

    ## 你是一个专业任务规划者
    ... (正文就是注入的 system prompt) ...

核心数据流:
    启动 → SkillRegistry 扫描 skills/*.md → 解析元数据 + 正文
    用户输入 → registry.match(text) → 返回 SkillDef | None
    命中 → client.chat_with_skill(messages, skill, tool_map) → 多轮执行
    未命中 → 走原有 chat_with_tools（行为完全不变）
"""

from __future__ import annotations

import glob
import logging
import os
import re
from dataclasses import dataclass

log = logging.getLogger("Agent")


# ============================================================
# SkillDef — 解析后的 Skill 结构化表示
# ============================================================


@dataclass
class SkillDef:
    """一个已解析的 Skill 定义"""

    name: str
    description: str
    trigger_keywords: list[str]
    required_tools: list[str]
    instructions: str  # Frontmatter 下面的 Markdown 正文 → system prompt
    max_rounds: int = 3
    entry_mode: str = "auto"  # auto / tool_call
    enabled: bool = True
    source_file: str = ""


# ============================================================
# Frontmatter 解析（轻量正则，不引入 PyYAML 依赖）
# ============================================================

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


def _parse_value(raw: str) -> any:
    """把 YAML-ish 的值转成 Python 对象。支持字符串、整数、布尔、内联列表 [a, b, c]"""
    raw = raw.strip()
    # 内联列表 [a, b, c]
    if raw.startswith("[") and raw.endswith("]"):
        items = [s.strip().strip("'\"") for s in raw[1:-1].split(",") if s.strip()]
        return items
    # 裸列表（每行一个 - xxx）：调用方传的是整个 Frontmatter 块，我们用 _parse_frontmatter 处理
    # 这里只处理单行值
    if raw.lower() in ("true", "yes"):
        return True
    if raw.lower() in ("false", "no"):
        return False
    # 整数
    try:
        return int(raw)
    except ValueError:
        pass
    # 字符串（去掉引号）
    return raw.strip("'\"")


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    raise ValueError(f"应为字符串列表，实际为 {type(value).__name__}")


def _parse_frontmatter(fm_text: str) -> dict:
    """
    解析简化版 YAML Frontmatter。
    支持 key: value（单行值）和 key:\n  - item1\n  - item2（列表值）。
    不支持嵌套字典、多行字符串、缩进层级等复杂 YAML 特性 — Skill 的元数据用不到。
    """
    result = {}
    lines = fm_text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        # 跳过空行和注释
        if not line.strip() or line.strip().startswith("#"):
            i += 1
            continue

        # 列表项：以 "key:" 开头，下一行开始是缩进 + "- xxx"
        key_match = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*$", line)
        if key_match:
            key = key_match.group(1)
            items = []
            i += 1
            while i < len(lines):
                item_line = lines[i]
                item_match = re.match(r"^\s*-\s*(.+?)\s*$", item_line)
                if not item_match:
                    break
                items.append(item_match.group(1).strip("'\""))
                i += 1
            result[key] = items
            continue

        # 内联列表 key: [a, b, c] 或 单行值 key: value
        kv_match = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*(.*)$", line)
        if kv_match:
            key = kv_match.group(1)
            val = _parse_value(kv_match.group(2))
            result[key] = val

        i += 1

    return result


# ============================================================
# SkillRegistry — 发现、加载、匹配 Skill 的核心管理器
# ============================================================


class SkillRegistry:
    """运行时扫描并管理所有 Skill 定义。加载失败的 Skill 会打印警告但不阻塞启动。"""

    def __init__(self, skill_dir: str, available_tools: list[str] | None = None):
        """
        skill_dir:  扫描哪个目录下的 *.md 文件
        available_tools: 当前 tool_map 里已有的工具名列表（可选，用于校验 required_tools）
        """
        self.skills: dict[str, SkillDef] = {}
        self._errors: list[str] = []

        if available_tools is None:
            available_tools = []

        if not os.path.isdir(skill_dir):
            log.info("Skill 目录不存在（%s），跳过 Skill 加载", skill_dir)
            return

        self._load_all(skill_dir, set(available_tools))
        log.info("SkillRegistry 加载完成：%d 个 Skill 可用，%d 个错误", len(self.skills), len(self._errors))

    # ---------- 加载 ----------

    def _load_all(self, skill_dir: str, available_tools: set[str]) -> None:
        for md_path in sorted(glob.glob(os.path.join(skill_dir, "*.md"))):
            try:
                skill = self._parse_md(md_path, available_tools)
                if skill is None:
                    continue
                if skill.name in self.skills:
                    self._errors.append(f"⚠️ 重复 Skill 名: {skill.name}（来自 {md_path}，已覆盖之前的）")
                self.skills[skill.name] = skill
            except Exception as e:
                err = f"❌ 加载 Skill 失败 {md_path}: {type(e).__name__}: {e}"
                self._errors.append(err)
                log.warning(err)

    def _parse_md(self, md_path: str, available_tools: set[str]) -> SkillDef | None:
        """从一个 .md 文件解析 SkillDef"""
        with open(md_path, encoding="utf-8") as f:
            content = f.read()

        match = _FM_RE.match(content)
        if not match:
            self._errors.append(f"⚠️ {md_path} 缺少 Frontmatter（--- ... ---），跳过")
            return None

        fm_raw, body = match.groups()
        fm = _parse_frontmatter(fm_raw)

        # 必填字段校验
        if "name" not in fm:
            self._errors.append(f"⚠️ {md_path} 缺少必填字段 'name'，跳过")
            return None

        name = str(fm["name"]).strip()
        description = str(fm.get("description", "")).strip()
        try:
            trigger_keywords = _as_list(fm.get("trigger_keywords", []))
            required_tools = _as_list(fm.get("required_tools", []))
        except ValueError as exc:
            self._errors.append(f"⚠️ {md_path} 列表字段无效: {exc}，跳过")
            return None
        try:
            max_rounds = int(fm.get("max_rounds", 3))
        except (TypeError, ValueError):
            self._errors.append(f"⚠️ {md_path} 的 max_rounds 必须是整数，跳过")
            return None
        entry_mode = str(fm.get("entry_mode", "auto")).strip().lower()
        enabled = bool(fm.get("enabled", True))

        if not name:
            self._errors.append(f"⚠️ {md_path} 的 name 不能为空，跳过")
            return None
        if max_rounds <= 0:
            self._errors.append(f"⚠️ Skill {name} 的 max_rounds 必须大于 0，跳过")
            return None
        if entry_mode not in {"auto", "tool_call"}:
            self._errors.append(f"⚠️ Skill {name} 的 entry_mode 必须是 auto/tool_call，跳过")
            return None
        if not body.strip():
            self._errors.append(f"⚠️ Skill {name} 缺少 instructions 正文，跳过")
            return None

        if not enabled:
            log.info("Skill %s 已禁用（enabled=false），跳过", name)
            return None

        # 校验 required_tools 存在性
        if available_tools:
            missing = [t for t in required_tools if t not in available_tools]
            if missing:
                log.warning("Skill %s 声明了不存在的工具: %s，会跳过这些", name, missing)
                required_tools = [t for t in required_tools if t in available_tools]

        skill = SkillDef(
            name=name,
            description=description,
            trigger_keywords=trigger_keywords,
            required_tools=required_tools,
            instructions=body.strip(),
            max_rounds=max_rounds,
            entry_mode=entry_mode,
            enabled=True,
            source_file=md_path,
        )

        log.info(
            "✅ 加载 Skill: %s（关键词=%s, 工具=%s, 来源=%s）",
            name,
            trigger_keywords,
            required_tools,
            os.path.basename(md_path),
        )
        return skill

    # ---------- 匹配 ----------

    def match(self, user_input: str) -> SkillDef | None:
        """
        关键词预筛：用户输入 → 匹配哪个 Skill？

        算法:
        1. 对每个 Skill 的 trigger_keywords 做子串匹配（不区分大小写）
        2. 分数 = 命中关键词的总长度 / 用户输入长度（长关键词、多关键词命中 → 高分）
        3. 取分数最高的；score > 0.3 才算有效命中（避免单字误触）

        后面可以升级为 LLM meta-routing（让 LLM 从候选中选），现在先用简单可靠的关键词。
        """
        if not self.skills or not user_input:
            return None

        best_score = 0.0
        best_skill = None
        user_lower = user_input.lower()
        input_len = max(len(user_input), 1)

        for skill in self.skills.values():
            matched_keywords = []
            for kw in skill.trigger_keywords:
                if kw and kw.lower() in user_lower:
                    matched_keywords.append(kw.lower())
            if not matched_keywords:
                continue
            covered_positions: set[int] = set()
            for keyword in matched_keywords:
                start = 0
                while True:
                    found = user_lower.find(keyword, start)
                    if found < 0:
                        break
                    covered_positions.update(range(found, found + len(keyword)))
                    start = found + 1
            hit_chars = len(covered_positions)
            score = hit_chars / input_len
            # 额外奖励：有多个关键词同时命中（避免多个短关键词和一个长关键词分数相当）
            hit_count = len(set(matched_keywords))
            if hit_count >= 2:
                score *= 1.3
            # 阈值：至少 score > 0.3 才算有效命中（防止单字/短关键词误触）
            if score > best_score and score > 0.3:
                best_score = score
                best_skill = skill

        if best_skill is not None:
            log.info("Skill 匹配: '%s' → %s (score=%.3f)", user_input[:60], best_skill.name, best_score)
            return best_skill
        return None

    # ---------- 查询 ----------

    def list(self) -> list[SkillDef]:
        """返回所有已加载的 Skill（UI/banner 展示用）"""
        return list(self.skills.values())

    @property
    def count(self) -> int:
        return len(self.skills)

    @property
    def errors(self) -> list[str]:
        return list(self._errors)

    def get(self, name: str) -> SkillDef | None:
        return self.skills.get(name)


# ============================================================
# 工厂函数（入口调用，可选依赖保护）
# ============================================================


def create_skill_registry(skill_dir: str, available_tools: list[str] | None = None) -> SkillRegistry | None:
    """
    安全创建 SkillRegistry：目录不存在返回 None，加载失败也返回可用实例（0 个 Skill）。
    和 create_mcp_manager() 同风格。
    """
    if not skill_dir:
        return None
    registry = SkillRegistry(skill_dir, available_tools or [])
    return registry if registry.count > 0 or registry.errors else None
