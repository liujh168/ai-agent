"""
ai_agent.tools — 工具函数集合（Tool Calling 注册源）
====================================================
按类别拆为子模块：
  demo.py     get_weather, calculator
  system.py   run_code, run_terminal, human_confirm + 安全拦截常量
  fs.py       file_read, file_write
  search.py   web_search（优先博查 AI 搜索，降级 DuckDuckGo）
  sql.py      sql_query

本文件：统一 re-export + 9 个 TOOL schema 常量 + FEW_SHOT_TEMPLATES。
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


WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "查询指定城市的天气",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "城市名称，如北京、上海"},
            },
            "required": ["city"],
        },
    },
}

CALCULATOR_TOOL = {
    "type": "function",
    "function": {
        "name": "calculator",
        "description": "执行数学表达式计算，如 3+5*2",
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "数学表达式"},
            },
            "required": ["expression"],
        },
    },
}

WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "网络搜索工具（优先博查 AI 搜索 API，国内稳定；未配 BOCHA_API_KEY 时降级 DuckDuckGo）。当用户询问最新信息、外部知识、实时数据、当前时间相关问题时调用。注意：这是互联网搜索，不同于 search_knowledge 的本地 PDF 知识库",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "max_results": {"type": "integer", "description": "返回结果数量，默认 3，建议不超过 5"},
            },
            "required": ["query"],
        },
    },
}

FILE_READ_TOOL = {
    "type": "function",
    "function": {
        "name": "file_read",
        "description": "读取本地文本文件内容。当用户要求查看、读取某个文件时调用。自动处理编码，超长文件截断并保留前后上下文",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径，支持相对路径（不能包含 ..）"},
                "max_chars": {"type": "integer", "description": "最大读取字符数，默认 3000"},
            },
            "required": ["path"],
        },
    },
}

FILE_WRITE_TOOL = {
    "type": "function",
    "function": {
        "name": "file_write",
        "description": "写入本地文本文件。当用户要求生成、创建、保存文件时调用。覆盖或追加模式",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径，支持相对路径（不能包含 ..）"},
                "content": {"type": "string", "description": "要写入的文本内容"},
                "mode": {
                    "type": "string",
                    "description": "写入模式：write 覆盖（默认）/ append 追加",
                    "enum": ["write", "append"],
                },
            },
            "required": ["path", "content"],
        },
    },
}

SQL_QUERY_TOOL = {
    "type": "function",
    "function": {
        "name": "sql_query",
        "description": "SQLite 数据库查询。首次调用带 demo_seed=True 会自动创建 users/orders 示例表和数据；之后可执行任意 SQL 查询。返回格式化表格结果",
        "parameters": {
            "type": "object",
            "properties": {
                "db_path": {"type": "string", "description": "SQLite 数据库文件路径，如 demo.db"},
                "sql": {
                    "type": "string",
                    "description": "SQL 语句，如 SELECT * FROM users WHERE city='北京'",
                },
                "demo_seed": {
                    "type": "boolean",
                    "description": "是否首次自动建表+示例数据，默认 True（仅新库生效）",
                },
            },
            "required": ["db_path", "sql"],
        },
    },
}

RUN_TERMINAL_TOOL = {
    "type": "function",
    "function": {
        "name": "run_terminal",
        "description": "执行系统终端命令（Windows 下走 PowerShell）。返回 stdout+stderr+exit_code。注意：执行删除、写入、网络相关等敏感命令前，建议先调用 human_confirm 让用户确认",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的命令，如 Get-Date、dir、python --version",
                },
                "timeout": {"type": "integer", "description": "超时秒数，默认 15"},
            },
            "required": ["command"],
        },
    },
}

HUMAN_CONFIRM_TOOL = {
    "type": "function",
    "function": {
        "name": "human_confirm",
        "description": "Human-in-the-loop 人工确认。执行敏感/破坏性操作（删除文件、DDL/DML SQL、终端写入命令、覆盖重要数据）前暂停等待用户输入 y/yes 确认，返回确认或取消结果。这是 Agent 安全流程的关键环节",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "描述即将执行的操作，让用户清楚在确认什么"},
            },
            "required": ["action"],
        },
    },
}

DEMO_FEW_SHOT_TOOL = {
    "type": "function",
    "function": {
        "name": "demo_few_shot",
        "description": "Prompt Engineering 演示：用 Few-shot（输入少量示例让模型理解任务模式）来回答问题。支持两种任务类型：sentiment 情感分类 / polish 文本润色。输出会展示 few-shot 提示词结构和最终答案，帮助用户理解 prompt engineering 原理",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "需要用 few-shot 方式回答的问题，如做情感分类、翻译、信息抽取等",
                },
                "task_type": {
                    "type": "string",
                    "description": "任务类型：sentiment 情感分类（默认）/ polish 文本润色",
                    "enum": ["sentiment", "polish"],
                },
            },
            "required": ["question"],
        },
    },
}

SUMMARIZE_TOOL = {
    "type": "function",
    "function": {
        "name": "summarize_history",
        "description": "长对话摘要压缩：把较早的对话历史浓缩成一段摘要，只保留最近几条完整消息。当对话话题切换前、或 history 过长担心超上下文窗口时可主动调用",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}
