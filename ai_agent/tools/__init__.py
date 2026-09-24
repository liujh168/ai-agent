"""
ai_agent.tools — 工具函数集合（Tool Calling 注册源）
====================================================
按类别拆为子模块：
  demo.py     get_weather, calculator
  system.py   run_code, run_terminal, human_confirm + 安全拦截常量
  fs.py       file_read, file_write
  search.py   web_search（优先博查 AI 搜索，降级 DuckDuckGo）
  sql.py      sql_query

本文件：统一 re-export + FEW_SHOT_TEMPLATES。
工具 schema 的唯一真相源是 app_core.TOOL_SCHEMAS，本文件不再重复定义。
外部通过 `from ai_agent.tools import xxx` 引用。
"""

from .demo import calculator, get_weather
from .fs import file_read, file_write
from .search import web_search
from .sql import sql_query
from .system import _DANGEROUS_CMD_PATTERNS, human_confirm, run_code, run_terminal

__all__ = [
    "_DANGEROUS_CMD_PATTERNS",
    "calculator",
    "file_read",
    "file_write",
    "get_weather",
    "human_confirm",
    "run_code",
    "run_terminal",
    "sql_query",
    "web_search",
]

FEW_SHOT_TEMPLATES = {
    "sentiment": {
        "name": "情感分类",
        "desc": "判断文本的情绪倾向（正面/负面/中性）",
        "examples": [
            (
                '分析下面的影评是正面还是负面？\n"画面很震撼，演员演技在线，故事节奏紧凑，完全被吸引住了。"',
                "正面",
            ),
            (
                '分析下面的影评是正面还是负面？\n"开头还能看，中间拖沓得想快进，结局更是烂尾浪费期待。"',
                "负面",
            ),
            (
                '分析下面的影评是正面还是负面？\n"经典之作，每一遍重看都有新收获，细节处理堪称教科书级别。"',
                "正面",
            ),
        ],
    },
    "polish": {
        "name": "文本润色",
        "desc": "把口语化/不规范的表达润色成更专业、简洁的书面语",
        "examples": [
            (
                '把这段口语润色成正式书面语：\n"那个项目搞砸了，主要是因为大家都不好好干，客户那边也很烦。"',
                "该项目未能按期交付，原因涉及多方：团队执行力度不足，客户需求沟通也存在摩擦空间。",
            ),
            (
                '把这段口语润色成正式书面语：\n"昨天开会说了下季度的事儿，老板挺生气，说我们太慢了。"',
                "昨日召开季度规划会议，管理层对当前进展速度提出明确整改要求。",
            ),
            (
                '把这段口语润色成正式书面语：\n"我觉得这个方案不太行，可能得改改。"',
                "该方案存在可优化空间，建议重新评估核心思路后调整。",
            ),
        ],
    },
}
