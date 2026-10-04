"""
本地 AI Agent 交互式演示 — CLI 主入口
====================================
架构：
  ai_agent/bootstrap.py        → AgentServices（client/RAG/MCP/Skill/tool_map 共享装配）
  ai_agent/session_service.py  → SessionService（会话业务规则，与 Web UI 共用）
  ai_agent/app_core.py         → 提示词 / 工具 schema / 执行器工厂（单一真相源）
  main.py                      → AgentDemoApp（CLI I/O 薄壳 + 交互循环）

运行：python main.py  或  python -m ai_agent
依赖：pip install requests openai pypdf pillow python-dotenv
可选：pip install ddgs（未配置 BOCHA_API_KEY 时的 DuckDuckGo 降级）
      在 .env 中设置 BOCHA_API_KEY=xxx（博查 AI 搜索，国内稳定）
"""

import logging
import os
import time

from ai_agent.app_core import (
    SYSTEM_PROMPT as _CORE_SYSTEM_PROMPT,
)
from ai_agent.app_core import (
    TOOL_COUNT,
    TOOL_SCHEMAS,
    build_system_prompt,
    build_tool_executor,
    ensure_test_image,
    filter_tool_schemas,
    get_tool_schemas,
)
from ai_agent.bootstrap import AgentServices, resolve_dangerous_tools
from ai_agent.cli_trace import CallRecorder, get_console, render_calls
from ai_agent.config import Config, load_env
from ai_agent.console import configure_utf8_output
from ai_agent.persistence import DEFAULT_DB_PATH, ChatStore
from ai_agent.session_service import SessionService

# ---------- 加载 .env 环境变量（幂等；未安装 python-dotenv 时静默跳过） ----------
load_env()

# ---------- 日志配置 ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("Agent")
configure_utf8_output()


# ============================================================
# 交互式 Agent 演示
# ============================================================


class AgentDemoApp:
    """交互式 Agent 演示应用：初始化环境、注册工具并运行自然语言交互循环。"""

    TEST_IMAGE = "_test_chart.png"
    EXIT_WORDS = ("exit", "quit", "q", "退出")

    # 系统提示词与工具 schema 统一引用 app_core（单一真相源）
    SYSTEM_PROMPT = _CORE_SYSTEM_PROMPT
    TOOLS = TOOL_SCHEMAS

    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
        enable_dangerous_tools: bool | None = None,
    ):
        self.services = AgentServices(Config.from_env())
        self.cfg = self.services.cfg
        self.project_root = self.services.project_root
        self.workspace_root = self.services.workspace_root
        # 危险工具开关：CLI flag 显式覆盖 > ENABLE_DANGEROUS_TOOLS 配置
        self.enable_dangerous_tools = resolve_dangerous_tools(
            self.cfg, cli_flag=enable_dangerous_tools
        )
        self.client = self.services.client
        # 收集每轮的 LLM API 调用快照：回复后打印摘要，/calls 查看完整输入输出
        self.llm_recorder = CallRecorder()
        self.client.on_llm_call = self.llm_recorder
        self.show_llm_summary = True
        self.tool_map: dict = {}
        self.tool_executor = None
        self.history: list[dict] = []
        # ---------- 对话持久化 ----------
        self.store = ChatStore(db_path)
        self.sessions = SessionService(self.store)
        self.session_id: str | None = None

    @property
    def skill_registry(self):
        return self.services.skill_registry

    # ---------- 初始化步骤 ----------

    def check_server(self) -> None:
        """检测 LM Studio 服务与模型加载状态"""
        print("=" * 50)
        print("检测 LM Studio 服务状态...")
        status = self.client.check_server_status()
        print(f"  服务运行: {status['server_running']}")
        print(f"  模型已加载: {status['model_loaded']}")
        if status["model_name"]:
            print(f"  模型名称: {status['model_name']}")
        if status["error"]:
            print(f"  错误: {status['error']}")
        print("=" * 50)

    def _create_test_image(self) -> None:
        """用 Pillow 生成一张测试图表，供图像识别演示（已存在则跳过）"""
        if ensure_test_image(self.services.test_image):
            print(f"[OK] 已生成测试图片 {self.TEST_IMAGE} 供图像识别演示")

    def _register_tools(self) -> None:
        """注册演示工具（tool_map 与 Skill 由共享的 AgentServices 装配）"""
        self.tool_map = self.services.build_tool_map(
            get_history=lambda: self.history,
            set_history=lambda h: setattr(self, "history", h),
        )
        self.tool_executor = build_tool_executor(
            self.tool_map,
            allow_write=True,
            allow_execute=self.enable_dangerous_tools,
            allow_mcp=True,
            confirm_write=True,
            confirm_execute=True,
            confirm_mcp=True,
            confirm_network=True,
            mcp_allowlist=self.cfg.mcp_allowed_tools,
            confirm=self._confirm_tool,
        )
        log.info("工具注册完成，共 %d 个工具", len(self.tool_map))

    def _confirm_tool(self, name: str, risk: str, arguments: dict) -> bool:
        print(f"\n[安全确认] 工具 {name}（风险: {risk}）")
        print(f"  参数: {arguments}")
        try:
            reply = input("  允许执行？输入 y/yes 确认: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return reply in {"y", "yes", "ok", "确认", "执行"}

    @staticmethod
    def _on_agent_event(event) -> None:
        if event.kind == "tool_started":
            print(f"\n[Tool Call] {event.payload['name']}({event.payload['arguments']})")
        elif event.kind == "tool_finished":
            status = "OK" if event.payload["ok"] else "ERROR"
            preview = event.payload["content"][:200]
            print(f"[Tool Result:{status}] {preview}")
        elif event.kind == "error" and event.payload.get("iteration"):
            print(f"[Agent Event] {event.payload['message']}")

    def _print_banner(self) -> None:
        """打印启动 banner 与示例"""
        print()
        print("╔═══════════════════════════════════════════════╗")
        print("║   🤖 本地 AI Agent 交互式演示                 ║")
        print(f"║   基于 LM Studio · {TOOL_COUNT} 种能力 · 自然语言路由     ║")
        print("╚═══════════════════════════════════════════════╝")
        print("  可用示例：")
        print("  - 介绍一下你自己")
        print("  - 北京天气怎么样？123*456 等于多少？")
        print("  - 用 Few-shot 做情感分类：'这段影评怎么样？'")
        print("  - 用 sql_query 查一下：每个城市有多少用户")
        print("  - 把这段话保存为 poem.txt：床前明月光...")
        print("  - 执行终端命令：Get-Date")
        print("  - 对话太长了，帮我压缩一下历史")
        print("  - 用反思方式解答：三个连续奇数之和为 45")
        print("  - 识别一下测试图表：_test_chart.png 展示的是什么数据？")
        print("  - 帮我规划并写一篇关于 AI Agent 的科普文章")
        print("  - 思维链解答：一个人花8元买鸡，9元卖出，10元买回，11元卖出，赚了多少")
        print("  - 用 ReAct 模式解答：北京现在天气怎么样，天气好的话帮我推荐几本 Agent 好书")
        print("  - 用多 Agent 协作写一篇关于「AI Agent」的科普文章（Planner+Executor+Critic）")
        print("  - /calls 查看上一轮 LLM API 调用的完整输入输出（/calls json 附请求/响应 JSON）")
        print("  - exit / quit / 退出  结束程序")
        print()

    def run(self) -> None:
        """启动交互式 Agent 演示"""
        self.check_server()
        print("\n构建 RAG 知识库索引（首次需向量化全部片段，可能较慢）...")
        self.services.ensure_rag()
        self._create_test_image()
        self._register_tools()
        self._print_banner()

        # 用动态 system prompt（包含 Skill 列表简述）
        self.history = [{"role": "system", "content": build_system_prompt(self.skill_registry)}]

        # ---------- 会话管理 ----------
        self._handle_session_select()

        while True:
            try:
                user_input = input("\n你> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见！")
                break

            if not user_input:
                continue

            # ---------- 持久化命令 ----------
            cmd_result = self._try_session_command(user_input)
            if cmd_result is not None:
                if cmd_result == "quit":
                    print("再见！")
                    break
                continue

            if user_input.lower() in self.EXIT_WORDS:
                print("再见！")
                break

            self.history.append({"role": "user", "content": user_input})
            # 持久化用户消息（首条自动生成标题）
            if self.session_id:
                self.sessions.append_user_message(self.session_id, user_input)

            try:
                print("\n助手> ", end="", flush=True)
                t0 = time.time()
                self.llm_recorder.reset()
                reply = self.client.chat_with_tools(
                    messages=self.history,
                    tools=filter_tool_schemas(
                        get_tool_schemas(self.services.mcp_manager),
                        self.tool_executor.policy,
                    ),
                    tool_map=self.tool_map,
                    skill_registry=self.skill_registry,
                    tool_executor=self.tool_executor,
                    on_event=self._on_agent_event,
                    session_id=self.session_id,
                )
                dt = time.time() - t0
                print(reply)
                if self.show_llm_summary:
                    get_console().print(self.llm_recorder.summary_line())
                log.info("交互完成 (%.1fs, history=%d msg)", dt, len(self.history))

                self.history.append({"role": "assistant", "content": reply})
                # 持久化助手回复
                if self.session_id:
                    self.sessions.append_assistant_message(self.session_id, reply)

                # ===== 长对话自动摘要压缩 =====
                compressed_history, before, compressed = self.sessions.compress_if_needed(
                    self.client, self.history, self.session_id, self.cfg.history_max_chars
                )
                if compressed:
                    self.history = compressed_history
                    now_chars = sum(len(m.get("content") or "") for m in self.history)
                    print(
                        f"\n[系统] 对话历史已达 {before} 字符"
                        f"（阈值 {self.cfg.history_max_chars}），"
                        f"已自动压缩为 {now_chars} 字符（保留最近 4 条完整消息）"
                    )
            except RuntimeError as e:
                print(f"[ERROR] {e}")
                self.history.pop()

        # 关闭持久化连接
        self.store.close()

    # ---------- 持久化辅助 ----------

    def _handle_session_select(self) -> None:
        """启动时显示会话列表让用户选择，或创建新会话"""
        sessions = self.sessions.list(limit=10)
        if sessions:
            print(f"\n📁 已保存 {len(sessions)} 个历史会话：")
            for i, s in enumerate(sessions, 1):
                mode_tag = f"[{s.mode}]" if s.mode != "chat" else ""
                print(f"   {i}. {s.title} {mode_tag} ({self.sessions.count_messages(s.id)} 条消息)")
            print("   n. 新建会话")

            while True:
                choice = input("\n选择会话编号直接恢复，或按 Enter / n 新建: ").strip().lower()
                if choice == "" or choice == "n":
                    break
                try:
                    idx = int(choice) - 1
                    if 0 <= idx < len(sessions):
                        sid = sessions[idx].id
                        self.history = self.sessions.restore_history(
                            sid, build_system_prompt(self.skill_registry)
                        )
                        print(
                            f"\n✅ 已恢复会话「{sessions[idx].title}」"
                            f"（{len(self.history) - 1} 条历史消息）"
                        )
                        self.session_id = sid
                        return
                except ValueError:
                    pass
                print("   输入无效，请重试")

        # 创建新会话
        sid = self.sessions.create(mode="chat")
        self.session_id = sid
        print(f"\n✨ 已创建新会话（ID: {sid}）")
        print("   提示：输入 /sessions 管理历史，输入 /save 手动保存当前标题")

    def _try_session_command(self, text: str) -> str | None:
        """
        识别 / 开头的持久化命令（命令表分发）。
        返回 None 表示不是命令；返回 'quit' 表示退出；返回 "" 表示已处理。
        """
        if not text.startswith("/"):
            return None
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        handlers = {
            "/quit": self._cmd_quit,
            "/exit": self._cmd_quit,
            "/help": self._cmd_help,
            "/sessions": self._cmd_sessions,
            "/load": self._cmd_load,
            "/new": self._cmd_new,
            "/save": self._cmd_save,
            "/rename": self._cmd_rename,
            "/delete": self._cmd_delete,
            "/clear": self._cmd_clear,
            "/current": self._cmd_current,
            "/db": self._cmd_db,
            "/calls": self._cmd_calls,
        }
        handler = handlers.get(cmd)
        if handler is None:
            print(f"  未知命令：{cmd}，输入 /help 查看所有命令")
            return ""
        return handler(arg)

    # ---------- 会话命令处理器（签名：(self, arg: str) -> str） ----------

    def _cmd_quit(self, arg: str) -> str:
        return "quit"

    def _cmd_help(self, arg: str) -> str:
        print("""
可用命令：
  /sessions        列出所有历史会话
  /load <编号>     加载指定会话（按 /sessions 的编号）
  /new             创建新会话
  /save [标题]     保存当前会话（可附带新标题）
  /rename <标题>   重命名当前会话
  /delete <编号>   删除指定会话
  /clear           清空当前会话的消息历史
  /current         显示当前会话信息
  /db              显示数据库路径
  /calls [json]    查看上一轮 LLM API 调用的完整输入输出（json 附带请求/响应 JSON）
  /calls on|off    开关每轮回复后的 LLM 调用摘要行
  /help            显示此帮助
  /quit            退出程序
""")
        return ""

    def _cmd_calls(self, arg: str) -> str:
        arg = arg.strip().lower()
        if arg in {"on", "off"}:
            self.show_llm_summary = arg == "on"
            print(f"  LLM 调用摘要行已{'开启' if self.show_llm_summary else '关闭'}")
            return ""
        render_calls(get_console(), self.llm_recorder.calls, show_json=(arg == "json"))
        return ""

    def _cmd_sessions(self, arg: str) -> str:
        sessions = self.sessions.list(limit=20)
        if not sessions:
            print("  暂无历史会话")
        else:
            print(f"\n📁 历史会话（共 {len(sessions)} 个）：")
            for i, s in enumerate(sessions, 1):
                cur_tag = " ← 当前" if s.id == self.session_id else ""
                mode_tag = f"[{s.mode}]" if s.mode != "chat" else ""
                print(
                    f"   {i}. {s.title} {mode_tag} "
                    f"({self.sessions.count_messages(s.id)} 条){cur_tag}"
                )
        return ""

    def _cmd_load(self, arg: str) -> str:
        sessions = self.sessions.list(limit=20)
        if not sessions:
            print("  暂无历史会话")
            return ""
        try:
            idx = int(arg) - 1
            if 0 <= idx < len(sessions):
                sid = sessions[idx].id
                self.history = self.sessions.restore_history(
                    sid, build_system_prompt(self.skill_registry)
                )
                self.session_id = sid
                print(f"  ✅ 已加载「{sessions[idx].title}」（{len(self.history) - 1} 条消息）")
                return ""
        except ValueError:
            pass
        print("  用法: /load <编号>（编号来自 /sessions）")
        return ""

    def _cmd_new(self, arg: str) -> str:
        sid = self.sessions.create(mode="chat")
        self.session_id = sid
        self.history = [{"role": "system", "content": build_system_prompt(self.skill_registry)}]
        print(f"  ✨ 已创建新会话（ID: {sid}）")
        return ""

    def _cmd_save(self, arg: str) -> str:
        if not self.session_id:
            print("  当前无活动会话")
            return ""
        title = arg or self.sessions.get(self.session_id).title
        self.sessions.rename(self.session_id, title)
        print(f"  💾 已保存为「{title}」")
        return ""

    def _cmd_rename(self, arg: str) -> str:
        if not self.session_id:
            print("  当前无活动会话")
            return ""
        if not arg:
            print("  用法: /rename <新标题>")
            return ""
        self.sessions.rename(self.session_id, arg)
        print(f"  ✏️ 已重命名为「{arg}」")
        return ""

    def _cmd_delete(self, arg: str) -> str:
        sessions = self.sessions.list(limit=20)
        try:
            idx = int(arg) - 1
            if 0 <= idx < len(sessions):
                sid = sessions[idx].id
                confirm = input(f"  确认删除「{sessions[idx].title}」？(y/N): ").strip().lower()
                if confirm == "y":
                    self.sessions.delete(sid)
                    if sid == self.session_id:
                        self.session_id = None
                        self.history = [
                            {"role": "system", "content": build_system_prompt(self.skill_registry)}
                        ]
                    print("  🗑️ 已删除")
                else:
                    print("  已取消")
                return ""
        except ValueError:
            pass
        print("  用法: /delete <编号>")
        return ""

    def _cmd_clear(self, arg: str) -> str:
        if self.session_id:
            self.sessions.clear(self.session_id)
            print("  🧹 已清空当前会话的消息历史（会话本身保留）")
        else:
            print("  当前无活动会话")
        return ""

    def _cmd_current(self, arg: str) -> str:
        if self.session_id:
            s = self.sessions.get(self.session_id)
            print(f"  当前会话: {s.title} [{s.mode}]")
            print(f"  消息数: {self.sessions.count_messages(self.session_id)}")
        else:
            print("  当前无活动会话")
        return ""

    def _cmd_db(self, arg: str) -> str:
        print(f"  数据库路径: {self.store.db_path}")
        print(f"  绝对路径:   {os.path.abspath(self.store.db_path)}")
        return ""


def main(argv: list[str] | None = None) -> None:
    """CLI entry point with explicit opt-in for dangerous local tools."""
    import argparse

    parser = argparse.ArgumentParser(description="本地 AI Agent 交互式演示")
    parser.add_argument(
        "--enable-dangerous-tools",
        action="store_true",
        default=None,
        help="允许 run_code/run_terminal，并在每次执行前要求确认",
    )
    args = parser.parse_args(argv)
    AgentDemoApp(enable_dangerous_tools=args.enable_dangerous_tools).run()


if __name__ == "__main__":
    main()
