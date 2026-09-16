"""
AI Agent 学习演示 — Gradio Web UI
==================================
与 CLI (main.py) 等价的 Web 演示界面。

UI 结构：
┌──────────────────────────────────────────────────────────┐
│ 🤖 AI Agent 学习演示（基于 LM Studio · 自然语言路由）      │
├───────────────┬──────────────────────────────────────────┤
│               │  💬 对话区（Gradio Chatbot）                │
│ 📁 会话列表    │                                          │
│               │  ──── 工具调用轨迹 ────                    │
│ ⚙️ 模式选择    │  🔧 [get_weather] 北京 → 晴天            │
│ · 普通对话     │  🔧 [calculator] 123*456 → 56088         │
│ · ReAct 模式   │  ──── ReAct 推理轨迹 ────                │
│ · 多 Agent     │  💭 Thought: 查天气...                    │
│               │  🔧 Action: get_weather                    │
│ 📝 快捷示例    │  👁️ Observation: 晴天 25°C               │
│ · 北京天气+算  │                                          │
│ · ReAct 推荐   │  🎯 助手回复                             │
│ · 多 Agent写   │                                          │
│               │                                          │
├───────────────┴──────────────────────────────────────────┤
│  [输入框]                         [发送] [清空] [新会话]     │
└──────────────────────────────────────────────────────────┘

运行：python -m ai_agent.web_ui
依赖：pip install gradio（已安装）
"""

from __future__ import annotations

import json
import os
import queue
import re
import sys
import threading
import time
import traceback

try:
    import gradio as gr
except ImportError:
    print("错误：未安装 gradio。请先执行 pip install gradio", file=sys.stderr)
    sys.exit(1)

# 让 main.py 同级的包可被导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_agent.client import LMStudioClient
from ai_agent.rag import RAGKnowledgeBase
from ai_agent.multi_agent import run_multi_agent
from ai_agent.persistence import ChatStore, DEFAULT_DB_PATH
from ai_agent.config import Config
from ai_agent.app_core import (
    SYSTEM_PROMPT as _CORE_SYSTEM_PROMPT,
    TOOL_SCHEMAS,
    TOOL_COUNT,
    build_tool_map,
    clean_react_tool_map,
    get_tool_schemas,
)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ============================================================
# 全局状态（单例）
# ============================================================

# 与 CLI 共用同一份系统提示词（app_core 单一真相源），保证路由一致
SYSTEM_PROMPT = _CORE_SYSTEM_PROMPT


class AgentApp:
    """Web UI 后端：包装 Client + Tool_map + 持久化 + 模式切换"""

    def __init__(self, db_path: str = DEFAULT_DB_PATH, disable_dangerous: bool = False):
        self.cfg = Config.from_env()
        self.client = LMStudioClient(self.cfg.base_url, self.cfg.api_key, self.cfg.model)
        self.rag: RAGKnowledgeBase | None = None
        self.store = ChatStore(db_path)
        self.session_id: str | None = None
        self.history: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.mode: str = "chat"  # chat / react / multi_agent
        # 工具调用轨迹 + ReAct 轨迹（用于 UI 实时展示）
        # 每条: {"text": str, "kind": str, "ts": float}
        # kind 取值: info / tool_call / tool_result / thought / action /
        #           observation / agent / error / divider
        self.trace_buffer: list[dict] = []
        # 测试图片（demo_vision 默认图），与 CLI 的 TEST_IMAGE 对应
        self.test_image: str = "_test_chart.png"
        # 公网分享（--share）时关闭高危工具（任意代码/命令执行）
        self.disable_dangerous: bool = disable_dangerous

        # 注册工具（与 CLI 共用 build_tool_map，保证能力一致）
        self.tool_map: dict = {}
        self._register_tools()

    # ---------- 工具注册 ----------

    def _register_tools(self) -> None:
        # 初始化 MCP 远端工具（可选，不配置则跳过）
        self.mcp_manager = self._init_mcp()

        self.tool_map = build_tool_map(
            self.client, lambda: self.rag, self.test_image,
            get_history=lambda: self.history,
            set_history=lambda h: setattr(self, "history", h),
            mcp_manager=self.mcp_manager,
        )
        # Web 模式不支持终端交互式人工确认 → 替换为安全提示，避免 input() 卡死 Gradio 线程
        self.tool_map["human_confirm"] = self._web_human_confirm
        # 公网分享时关闭高危工具（任意代码 / 命令执行）
        if self.disable_dangerous:
            for name in ("demo_code_run", "run_terminal"):
                self.tool_map.pop(name, None)

    def _init_mcp(self):
        """安全初始化 MCPManager：无配置返回 None，连接失败也不崩"""
        try:
            from ai_agent.mcp_client import create_mcp_manager
            servers = self.cfg.mcp_servers
            if not servers:
                return None
            mgr = create_mcp_manager(list(servers))
            return mgr  # 即使连接失败（total_tools=0）也返回，UI 显示状态
        except Exception as e:
            print(f"  ⚠️ MCP 初始化失败: {e}")
            return None

    def _is_mcp_tool(self, name: str) -> bool:
        """判断工具名是否来自 MCP 远端（trace 颜色区分用）"""
        return (self.mcp_manager is not None
                and self.mcp_manager.is_mcp_tool(name))

    def _web_human_confirm(self, action: str) -> str:
        """Web 模式下的安全替代：不阻塞 Gradio 线程，仅给出提示"""
        return (
            "⚠️ Web 模式下不支持终端交互式人工确认。"
            "如确需执行敏感/破坏性操作，请在本地 CLI 环境运行。"
        )

    # ---------- RAG 初始化 ----------

    def init_rag(self):
        if self.rag is None:
            try:
                self.rag = RAGKnowledgeBase(
                    base_url=self.cfg.base_url,
                    embed_model=self.cfg.embed_model,
                    chunk_size=self.cfg.chunk_size,
                    chunk_overlap=self.cfg.chunk_overlap,
                    cache_file=self.cfg.rag_cache_file,
                )
                self.rag.build(["docs/通用Agent时代价值迁移.md"])
            except Exception as e:
                print(f"RAG 初始化失败: {e}")

    # ---------- 发送消息（核心入口 · generator 流式更新）----------

    def send(self, user_text: str, history: list[tuple]):
        """
        Gradio Chatbot generator 回调。
        每 yield 一次，Gradio 就更新一次 UI。
        返回: yield (trace_text, new_history, input_clear)
        """
        if not user_text.strip():
            yield self.trace_html(), history, ""
            return

        # 初始化 RAG（延迟加载）
        self.init_rag()

        # 创建新会话或使用当前会话
        if self.session_id is None:
            self.session_id = self.store.create_session(title="新会话", mode=self.mode)

        self.trace_buffer = []
        self.trace(f"📥 用户输入: {user_text[:100]}{'…' if len(user_text) > 100 else ''}", kind="info")

        # 持久化用户消息
        self.store.append(self.session_id, "user", user_text)
        if self.store.count_messages(self.session_id) <= 1:
            self.store.auto_title(self.session_id, user_text)

        # 追加到 history（OpenAI 格式）
        self.history.append({"role": "user", "content": user_text})

        # Gradio history 先占位（用户消息 + 空回复）
        gradio_hist = list(history or []) + [(user_text, "")]

        # ---------- 多 Agent 模式：后台线程 + queue 逐步刷新 ----------
        if self.mode == "multi_agent":
            self.trace("🤖 模式: 多 Agent 协作（Planner → Executor → Critic）", kind="divider")
            yield self.trace_html(), gradio_hist, ""

            trace_q: queue.Queue = queue.Queue()
            result_holder: dict = {"reply": None, "error": None}

            def worker():
                def on_trace(line):
                    trace_q.put(line)
                try:
                    result_holder["reply"] = run_multi_agent(
                        self.client, user_text,
                        max_rounds=2, max_tokens=2048,
                        trace_callback=on_trace,
                    )
                except Exception as e:
                    result_holder["error"] = f"{type(e).__name__}: {e}"

            t = threading.Thread(target=worker, daemon=True)
            t.start()

            # 轮询 queue + yield
            while t.is_alive() or not trace_q.empty():
                # 把 queue 里所有新行都取出来
                got_new = False
                while not trace_q.empty():
                    try:
                        line = trace_q.get_nowait()
                        # 启发式：多 Agent 消息通常带角色标签
                        if any(tag in line for tag in ("[Planner]", "[Executor]", "[Critic]", "Planner:", "Executor:", "Critic:")):
                            self.trace(line, kind="agent")
                        elif "✗" in line or "错误" in line or "Error" in line:
                            self.trace(line, kind="error")
                        else:
                            self.trace(line, kind="info")
                        got_new = True
                    except queue.Empty:
                        break
                if got_new or t.is_alive():
                    yield self.trace_html(), gradio_hist, ""
                t.join(timeout=0.3)

            # 最终
            if result_holder["error"]:
                reply = f"[异常] {result_holder['error']}"
                self.history.pop()
            else:
                reply = result_holder["reply"] or "(无输出)"

            self.history.append({"role": "assistant", "content": reply})
            self.store.append(self.session_id, "assistant", reply)
            gradio_hist[-1] = (user_text, reply)
            yield self.trace_html(), gradio_hist, ""
            return

        # ---------- 其他模式：同步跑完 yield 最终结果 ----------
        try:
            if self.mode == "react":
                reply = self._react_mode(user_text)
            else:
                reply = self._chat_mode()
        except RuntimeError as e:
            reply = f"[错误] {e}"
            self.history.pop()
        except Exception as e:
            reply = f"[异常] {type(e).__name__}: {e}\n\n{traceback.format_exc()[:500]}"

        self.history.append({"role": "assistant", "content": reply})
        self.store.append(self.session_id, "assistant", reply)
        gradio_hist[-1] = (user_text, reply)
        yield self.trace_html(), gradio_hist, ""

    def _chat_mode(self) -> str:
        """普通对话模式 — chat_with_tools，拦截工具调用展示到 trace"""
        self.trace("🤖 模式: 普通对话 + Function Calling", kind="divider")

        # 创建一个带 hook 的 tool_map，拦截调用展示
        hooked_map = {}
        for name, func in self.tool_map.items():
            # 跳过非 tool_map 条目（如 human_confirm 的 Web 版本）
            def make_hook(n, fn):
                def hook(**kwargs):
                    call_str = f"🔧 [{n}] " + ", ".join(f"{k}={v}" for k, v in kwargs.items())
                    # 区分本地 vs MCP 工具
                    if self._is_mcp_tool(n):
                        self.trace(call_str + "  ← MCP 远端", kind="tool_call_mcp")
                    else:
                        self.trace(call_str, kind="tool_call")
                    t0 = time.time()
                    result = fn(**kwargs)
                    dt = time.time() - t0
                    preview = str(result)[:150]
                    self.trace(f"  ↳ 结果 ({dt*1000:.0f}ms): {preview}{'…' if len(str(result)) > 150 else ''}", kind="tool_result")
                    return result
                hook.__name__ = n
                return hook
            hooked_map[name] = make_hook(name, func)

        # 合成完整 tool schemas（本地 + MCP 远端），高危工具过滤
        all_schemas = get_tool_schemas(self.mcp_manager)
        tools = [
            s for s in all_schemas
            if not (self.disable_dangerous and s["function"]["name"] in ("demo_code_run", "run_terminal"))
        ]

        try:
            reply = self.client.chat_with_tools(
                messages=self.history,
                tools=tools,
                tool_map=hooked_map,
            )
        except RuntimeError as e:
            self.trace(f"❌ 错误: {e}", kind="error")
            raise

        # 长对话自动压缩
        total_chars = sum(len(m["content"]) for m in self.history)
        if total_chars > 5000:
            self.trace(f"[系统] 对话过长 ({total_chars} 字符)，自动压缩...", kind="info")
            self.history = self.client.summarize_messages(self.history, keep_recent=4)
            # 压缩后回写数据库，保证会话恢复时历史一致（与 CLI 行为对齐）
            if self.session_id:
                self.store.clear_messages(self.session_id)
                for m in self.history[1:]:
                    self.store.append(
                        self.session_id, m["role"], m.get("content", ""),
                        m.get("tool_calls"),
                    )

        return reply

    def _react_mode(self, user_text: str) -> str:
        """ReAct 模式 — 拦截输出中的 Thought/Action/Observation 展示"""
        self.trace("🤖 模式: ReAct（边想边做）", kind="divider")
        self.trace("━━━━━━━━━━━━━━━ ReAct 推理轨迹 ━━━━━━━━━━━━━━━━━", kind="divider")

        # 用纯文本式 ReAct，拦截它的 print 输出到 trace
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            reply = self.client.chat_with_react(
                [{"role": "user", "content": user_text}],
                tool_map=clean_react_tool_map(self.tool_map),
            )
        # 把 print 输出也拼到 trace（启发式判断 kind）
        for line in buf.getvalue().splitlines():
            stripped = line.strip()
            if stripped.startswith("Thought"):
                self.trace(line, kind="thought")
            elif stripped.startswith("Action"):
                self.trace(line, kind="action")
            elif stripped.startswith("Observation"):
                self.trace(line, kind="observation")
            elif "✗" in line or "错误" in line or "Error" in line:
                self.trace(line, kind="error")
            else:
                self.trace(line, kind="info")

        self.trace("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", kind="divider")
        return reply

    def _multi_agent_mode(self, user_text: str) -> str:
        """已废弃：多 Agent 模式现在在 send() generator 里直接处理"""
        raise RuntimeError("不要直接调用 _multi_agent_mode，请通过 send()")

    def trace(self, text: str, kind: str = "info") -> None:
        """追加一行到 trace buffer。kind 控制颜色：
        info / tool_call / tool_result / thought / action /
        observation / agent / error / divider
        """
        self.trace_buffer.append({"text": text, "kind": kind, "ts": time.time()})

    def trace_html(self) -> str:
        """将结构化 trace_buffer 渲染为彩色 HTML"""
        _KIND_COLORS = {
            "info":        "#888888",
            "tool_call":   "#1a73e8",
            "tool_call_mcp": "#7b1fa2",   # 紫色 - MCP 远端工具
            "tool_result": "#2e7d32",
            "thought":     "#9c27b0",
            "action":      "#ff6f00",
            "observation": "#546e7a",
            "agent":       "#d32f2f",
            "error":       "#c62828",
            "divider":     "#555555",
        }
        _KIND_ITALIC = {"thought"}
        _KIND_BOLD   = {"error", "agent"}

        parts = ['<div class="trace-box">']
        for entry in self.trace_buffer:
            text = entry["text"]
            kind = entry.get("kind", "info")
            color = _KIND_COLORS.get(kind, "#cccccc")
            style_bits = [f"color:{color}"]
            if kind in _KIND_ITALIC:
                style_bits.append("font-style:italic")
            if kind in _KIND_BOLD:
                style_bits.append("font-weight:bold")
            # divider 加虚线边框
            if kind == "divider":
                style_bits.append("border-top:1px dashed #444;margin:4px 0")
            # 转义 HTML
            safe = (text
                    .replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                    .replace("\n", "<br>"))
            parts.append(f'<pre style="margin:2px 0;font-family:monospace;font-size:13px;{";".join(style_bits)}">{safe}</pre>')
        parts.append("</div>")
        return "\n".join(parts)

    def trace_text(self) -> str:
        """纯文本版本（供 console / 兼容路径使用）"""
        return "\n".join(e["text"] for e in self.trace_buffer)

    # ---------- 会话管理 ----------

    def new_session(self) -> tuple[str, list[tuple]]:
        """创建新会话"""
        self.session_id = self.store.create_session(title="新会话", mode=self.mode)
        self.history = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.trace_buffer = []
        self.trace(f"✨ 已创建新会话 {self.session_id}", kind="info")
        return self.trace_html(), []

    def switch_session(self, session_id: str) -> tuple[str, list[tuple]]:
        """切换到指定会话"""
        if not session_id:
            return "", []
        saved = self.store.load_messages(session_id)
        self.session_id = session_id
        self.history = [{"role": "system", "content": SYSTEM_PROMPT}] + saved
        self.trace_buffer = []
        self.trace(f"✅ 已恢复会话 {session_id}（{len(saved)} 条消息）", kind="info")
        # 转换为 Gradio history 格式
        gradio_hist = []
        for i in range(0, len(saved) - 1, 2):
            if saved[i]["role"] == "user" and saved[i + 1]["role"] == "assistant":
                gradio_hist.append((saved[i]["content"], saved[i + 1]["content"]))
        return self.trace_html(), gradio_hist

    def list_sessions(self) -> list[tuple[str, str, str]]:
        """返回 [(session_id, title, info_str)]"""
        sessions = self.store.list_sessions(limit=20)
        return [(s.id, s.title, f"[{s.mode}] {self.store.count_messages(s.id)}条") for s in sessions]

    def set_mode(self, mode: str) -> None:
        self.mode = mode


# ============================================================
# 全局单例
# ============================================================
_app = AgentApp()


# ============================================================
# Gradio UI 构建
# ============================================================

def build_ui() -> gr.Blocks:
    _CSS = """
    .trace-box {
        background: #1a1a2e;
        border-radius: 8px;
        padding: 10px 12px;
        min-height: 180px;
        max-height: 400px;
        overflow-y: auto;
        font-family: 'IBM Plex Mono', 'Cascadia Code', Consolas, monospace;
    }
    .trace-box pre {
        white-space: pre-wrap;
        word-break: break-all;
    }
    """
    with gr.Blocks(
        title="AI Agent 学习演示 · Web UI",
        css=_CSS,
    ) as demo:
        # 动态计算工具总数（本地 + 可选 MCP 远端）
        _mgr = _app.mcp_manager
        _mcp_count = _mgr.total_tools if _mgr else 0
        _total = TOOL_COUNT + _mcp_count
        _mcp_note = f" · 🔌 MCP 远端 {_mcp_count}" if _mcp_count > 0 else ""

        gr.Markdown(
            "# 🤖 AI Agent 学习演示 · Web UI\n"
            f"基于 LM Studio 本地大模型 · {_total} 种能力"
            f"{_mcp_note} · 自然语言路由\n"
            "对话自动持久化到 SQLite，支持多会话恢复"
        )

        with gr.Row():
            # ---------- 左侧：控制面板 ----------
            with gr.Column(scale=1, min_width=280):
                gr.Markdown("### ⚙️ 模式选择")
                mode_radio = gr.Radio(
                    choices=[
                        ("💬 普通对话 (Function Calling)", "chat"),
                        ("🔄 ReAct 边想边做", "react"),
                        ("🤝 多 Agent 协作", "multi_agent"),
                    ],
                    value="chat",
                    label="Agent 模式",
                )

                gr.Markdown("### 📁 会话管理")
                session_radio = gr.Radio(
                    choices=[],
                    label="历史会话（点击加载）",
                    interactive=True,
                )
                with gr.Row():
                    btn_new_session = gr.Button("✨ 新会话", variant="primary")
                    btn_refresh_sessions = gr.Button("🔄 刷新列表")

                gr.Markdown("### 📝 快捷示例")
                example_box = gr.Textbox(
                    value="北京天气怎么样？顺便算一下 123*456",
                    label="点击填入示例 →",
                    lines=3,
                )
                with gr.Row():
                    btn_example1 = gr.Button("天气+计算")
                    btn_example2 = gr.Button("ReAct 推荐")
                with gr.Row():
                    btn_example3 = gr.Button("多 Agent 写文")
                    btn_example4 = gr.Button("SQL 查询")

                gr.Markdown("### 💡 教学提示")
                gr.Markdown(
                    "- 左上角切换 Agent 模式\n"
                    "- **普通对话**: Function Calling 黑盒路由\n"
                    "- **ReAct**: Thought→Action→Observation 可见轨迹\n"
                    "- **多 Agent**: Planner→Executor→Critic 分工协作\n"
                    "- 所有消息自动持久化"
                )

            # ---------- 右侧：对话 + 轨迹 ----------
            with gr.Column(scale=3):
                chatbot = gr.Chatbot(height=380)

                trace_output = gr.HTML(
                    value='<div class="trace-box" style="color:#666">等待请求...</div>',
                    label="🔍 执行轨迹（工具调用 / ReAct 推理 / 多 Agent 消息传递）",
                    elem_classes=["trace-box-wrapper"],
                )

                with gr.Row():
                    msg_input = gr.Textbox(
                        label="输入",
                        placeholder="在这里输入你的问题...",
                        lines=2,
                        scale=4,
                    )
                    with gr.Column(scale=1):
                        btn_send = gr.Button("发送 🚀", variant="primary")
                        btn_clear = gr.Button("清空对话")

        # ============================================================
        # 事件绑定（必须在 with Blocks 上下文内）
        # ============================================================

        def on_mode_change(mode_val):
            _app.set_mode(mode_val)
            _app.trace(f"切换模式 → {mode_val}", kind="info")
            return _app.trace_html()

        def on_session_list_refresh():
            sessions = _app.list_sessions()
            if not sessions:
                choices = [("暂无历史会话", "")]
            else:
                choices = [(f"{title}  {info}", sid) for sid, title, info in sessions]
            return gr.update(choices=choices)

        def on_session_select(session_id):
            if session_id:
                trace, hist = _app.switch_session(session_id)
                return trace, hist

        def on_new_session():
            trace, hist = _app.new_session()
            return trace, hist, on_session_list_refresh()

        def on_send(user_text, history):
            yield from _app.send(user_text or "", history or [])

        def on_clear():
            _app.history = [{"role": "system", "content": SYSTEM_PROMPT}]
            _app.trace("已清空对话", kind="info")
            return _app.trace_html(), []

        # 模式切换
        mode_radio.change(on_mode_change, mode_radio, trace_output)

        # 会话管理
        btn_refresh_sessions.click(on_session_list_refresh, None, session_radio)
        session_radio.select(on_session_select, session_radio, [trace_output, chatbot])
        btn_new_session.click(on_new_session, None, [trace_output, chatbot, session_radio])

        # 发送（按钮 + Enter）
        btn_send.click(on_send, [msg_input, chatbot], [trace_output, chatbot, msg_input])
        msg_input.submit(on_send, [msg_input, chatbot], [trace_output, chatbot, msg_input])

        # 清空
        btn_clear.click(on_clear, None, [trace_output, chatbot])

        # 快捷示例
        btn_example1.click(lambda: "北京天气怎么样？顺便算一下 123*456", None, msg_input)
        btn_example2.click(lambda: "用 ReAct 模式解答：北京天气怎么样，如果好的话帮我推荐 3 本 Agent 好书", None, msg_input)
        btn_example3.click(lambda: "用多 Agent 协作写一篇关于 AI Agent 的科普文章", None, msg_input)
        btn_example4.click(lambda: "用 sql_query 查一下数据库里每个城市有多少用户", None, msg_input)

        # 初始会话列表填充
        demo.load(on_session_list_refresh, None, session_radio)

    return demo


# ============================================================
# 入口
# ============================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="AI Agent Web UI")
    parser.add_argument("--host", default="127.0.0.1", help="绑定地址")
    parser.add_argument("--port", type=int, default=7860, help="端口")
    parser.add_argument("--share", action="store_true", help="生成公网分享链接")
    args = parser.parse_args()

    # 公网分享时强制关闭高危工具（任意代码 / 命令执行），避免远程 RCE
    if args.share:
        _app.disable_dangerous = True
        _app._register_tools()
        print("⚠️ 已开启公网分享，已自动禁用 run_code / run_terminal 等高危工具")

    print(f"\n🚀 启动 AI Agent Web UI")
    print(f"   地址: http://{args.host}:{args.port}")
    print(f"   共享: {'是' if args.share else '否'}")
    print(f"   数据库: {_app.store.db_path}")
    print()

    demo = build_ui()

    demo.launch(
        server_name=args.host,
        server_port=args.port,
        share=args.share,
        inbrowser=False,
    )


if __name__ == "__main__":
    main()

