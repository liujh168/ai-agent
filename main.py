"""
本地 AI Agent 交互式演示 — 主入口
================================
架构：
  ai_agent/client.py   → LMStudioClient（与 LM Studio API 直接交互）
  ai_agent/rag.py      → RAGKnowledgeBase（PDF/MD/TXT 向量化检索）
  ai_agent/tools/      → 10 个模块级工具函数 + 9 个 TOOL schema
  main.py              → AgentDemoApp（初始化 + 工具注册 + 交互循环）

运行：python main.py  或  python -m ai_agent
依赖：pip install requests openai pypdf pillow python-dotenv
可选：pip install ddgs（未配置 BOCHA_API_KEY 时的 DuckDuckGo 降级）
      在 .env 中设置 BOCHA_API_KEY=xxx（博查 AI 搜索，国内稳定）
"""

# ---------- 加载 .env 环境变量 ----------
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass  # 未安装 python-dotenv 时跳过（仍可通过系统环境变量配置）

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
    build_tool_map,
    filter_tool_schemas,
)
from ai_agent.client import LMStudioClient
from ai_agent.config import Config
from ai_agent.console import configure_utf8_output
from ai_agent.persistence import DEFAULT_DB_PATH, ChatStore
from ai_agent.rag import RAGKnowledgeBase

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

    # RAG 知识库扫描目录：这些目录下的 .md / .txt / .pdf 文件会被递归扫描
    # 不存在的目录会被跳过。也可通过 Config.rag_directories 覆盖。
    RAG_DIRECTORIES = [
        "docs/knowledge",
        "data",
    ]
    TEST_IMAGE = "_test_chart.png"
    EXIT_WORDS = ("exit", "quit", "q", "退出")

    # 长对话压缩阈值：history 总字符数超这个值就自动摘要压缩
    HISTORY_MAX_CHARS = 5000

    # 系统提示词与工具 schema 统一引用 app_core（单一真相源）
    SYSTEM_PROMPT = _CORE_SYSTEM_PROMPT
    TOOLS = TOOL_SCHEMAS

    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
        enable_dangerous_tools: bool | None = None,
    ):
        self.cfg = Config.from_env()
        self.project_root = os.path.abspath(self.cfg.project_root)
        self.workspace_root = os.path.abspath(self.cfg.workspace_root)
        self.enable_dangerous_tools = (
            self.cfg.enable_dangerous_tools if enable_dangerous_tools is None else enable_dangerous_tools
        )
        self.client = LMStudioClient(
            self.cfg.base_url,
            self.cfg.api_key,
            self.cfg.model,
            timeout=self.cfg.request_timeout,
        )
        self.rag: RAGKnowledgeBase | None = None
        self.tool_map: dict = {}
        self.tool_executor = None
        self.skill_registry = None  # 可选 SkillRegistry（运行时扫描 skills/ 目录）
        self.history: list[dict] = []
        # ---------- 对话持久化 ----------
        self.store = ChatStore(db_path)
        self.session_id: str | None = None

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

    def _build_knowledge_base(self) -> None:
        """构建 RAG 知识库索引（首次向量化较慢，之后走缓存）"""
        print("\n构建 RAG 知识库索引（首次需向量化全部片段，可能较慢）...")
        try:
            self.rag = RAGKnowledgeBase(
                base_url=self.cfg.base_url,
                embed_model=self.cfg.embed_model,
                chunk_size=self.cfg.chunk_size,
                chunk_overlap=self.cfg.chunk_overlap,
                cache_file=(
                    self.cfg.rag_cache_file
                    if os.path.isabs(self.cfg.rag_cache_file)
                    else os.path.join(self.project_root, self.cfg.rag_cache_file)
                ),
                request_timeout=self.cfg.request_timeout,
            )
            # 扫描目录下的全部 .md / .txt / .pdf 文件
            rag_dirs = list(self.cfg.rag_directories) if self.cfg.rag_directories else self.RAG_DIRECTORIES
            rag_paths: list[str] = []
            for rel_dir in rag_dirs:
                abs_dir = os.path.join(self.project_root, rel_dir)
                if not os.path.isdir(abs_dir):
                    continue
                for root, _, files in os.walk(abs_dir):
                    for fname in files:
                        fpath = os.path.join(root, fname)
                        ext = os.path.splitext(fname)[1].lower()
                        if ext in (".md", ".txt", ".pdf"):
                            rag_paths.append(fpath)
            if rag_paths:
                n_chunks = self.rag.build(rag_paths)
                print(f"[OK] 知识库就绪：{len(rag_paths)} 个文件 → {n_chunks} 个文本片段")
            else:
                print("[WARN] RAG 扫描目录为空或不存在，跳过初始化")
                self.rag = None
        except Exception as e:
            print(f"[WARN] RAG 初始化失败（{e}），知识库问答将不可用")
            self.rag = None

    def _create_test_image(self) -> None:
        """用 Pillow 生成一张测试图表，供图像识别演示（已存在则跳过）"""
        image_path = os.path.join(self.project_root, self.TEST_IMAGE)
        if os.path.exists(image_path):
            log.debug("测试图片已存在，跳过生成")
            return
        try:
            from PIL import Image, ImageDraw

            img = Image.new("RGB", (640, 400), "white")
            draw = ImageDraw.Draw(img)
            data = [("AI Agent", 120), ("RAG", 200), ("Agentic", 160), ("Multi-Agent", 90)]
            x = 60
            for label, h in data:
                draw.rectangle([x, 400 - h, x + 120, 400], fill="steelblue")
                draw.text((x + 20, 400 - h - 25), label, fill="black")
                x += 140
            draw.text((20, 20), "2026 AI Field Attention Survey", fill="black")
            img.save(image_path)
            print(f"[OK] 已生成测试图片 {image_path} 供图像识别演示")
        except Exception as e:
            print(f"[WARN] 测试图片生成失败（{e}），图像识别演示将不可用")

    def _register_tools(self) -> None:
        """注册演示工具（统一调用 app_core.build_tool_map 构建工具映射）"""
        self.tool_map = build_tool_map(
            self.client,
            lambda: self.rag,
            os.path.join(self.project_root, self.TEST_IMAGE),
            get_history=lambda: self.history,
            set_history=lambda h: setattr(self, "history", h),
            workspace_root=self.workspace_root,
        )
        self.tool_executor = build_tool_executor(
            self.tool_map,
            allow_write=True,
            allow_execute=self.enable_dangerous_tools,
            allow_mcp=True,
            confirm_write=True,
            confirm_execute=True,
            confirm_mcp=True,
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

    def _register_skills(self) -> None:
        """加载声明式 Skill（可选，skills/ 目录不存在则跳过）"""
        skill_dir = self.cfg.skill_dir
        if not skill_dir:
            return
        # 解析为相对 main.py 所在目录的绝对路径
        abs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), skill_dir)
        if not os.path.isdir(abs_dir):
            log.info("Skill 目录不存在（%s），跳过 Skill 加载", abs_dir)
            return
        try:
            from ai_agent.skill import SkillRegistry

            available = list(self.tool_map.keys())
            self.skill_registry = SkillRegistry(abs_dir, available)
        except Exception as e:
            log.warning("SkillRegistry 初始化失败: %s", e)
            self.skill_registry = None

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
        print("  - exit / quit / 退出  结束程序")
        print()

    def run(self) -> None:
        """启动交互式 Agent 演示"""
        self.check_server()
        self._build_knowledge_base()
        self._create_test_image()
        self._register_tools()
        self._register_skills()  # ← 新增：加载声明式 Skill（可选）
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
            # 持久化用户消息
            if self.session_id:
                self.store.append(self.session_id, "user", user_input)
                # 自动生成标题（首条用户消息）
                if self.store.count_messages(self.session_id) <= 1:
                    self.store.auto_title(self.session_id, user_input)

            try:
                print("\n助手> ", end="", flush=True)
                t0 = time.time()
                reply = self.client.chat_with_tools(
                    messages=self.history,
                    tools=filter_tool_schemas(self.TOOLS, self.tool_executor.policy),
                    tool_map=self.tool_map,
                    skill_registry=self.skill_registry,  # ← 新增：让 Skill 路由生效
                    tool_executor=self.tool_executor,
                    on_event=self._on_agent_event,
                    session_id=self.session_id,
                )
                dt = time.time() - t0
                print(reply)
                log.info("交互完成 (%.1fs, history=%d msg)", dt, len(self.history))

                self.history.append({"role": "assistant", "content": reply})
                # 持久化助手回复
                if self.session_id:
                    self.store.append(self.session_id, "assistant", reply)

                # ===== 长对话自动摘要压缩 =====
                total_chars = sum(len(m.get("content") or "") for m in self.history)
                if total_chars > self.HISTORY_MAX_CHARS:
                    print(
                        f"\n[系统] 对话历史已达 {total_chars} 字符（阈值 {self.HISTORY_MAX_CHARS}），自动压缩中..."
                    )
                    self.history = self.client.summarize_messages(self.history, keep_recent=4)
                    now_chars = sum(len(m.get("content") or "") for m in self.history)
                    print(f"[系统] 压缩完成，剩余 {now_chars} 字符（保留最近 4 条完整消息）")
                    # 压缩后清除持久化，只保存最近完整消息 + 新摘要
                    if self.session_id:
                        self.store.clear_messages(self.session_id)
                        # 重写当前 history（去掉 system）
                        for m in self.history[1:]:
                            self.store.append(
                                self.session_id, m["role"], m.get("content", ""), m.get("tool_calls")
                            )
            except RuntimeError as e:
                print(f"[ERROR] {e}")
                self.history.pop()

        # 关闭持久化连接
        self.store.close()

    # ---------- 持久化辅助 ----------

    def _handle_session_select(self) -> None:
        """启动时显示会话列表让用户选择，或创建新会话"""
        sessions = self.store.list_sessions(limit=10)
        if sessions:
            print(f"\n📁 已保存 {len(sessions)} 个历史会话：")
            for i, s in enumerate(sessions, 1):
                mode_tag = f"[{s.mode}]" if s.mode != "chat" else ""
                print(f"   {i}. {s.title} {mode_tag} ({self.store.count_messages(s.id)} 条消息)")
            print("   n. 新建会话")

            while True:
                choice = input("\n选择会话编号直接恢复，或按 Enter / n 新建: ").strip().lower()
                if choice == "" or choice == "n":
                    break
                try:
                    idx = int(choice) - 1
                    if 0 <= idx < len(sessions):
                        sid = sessions[idx].id
                        saved = self.store.load_messages(sid)
                        print(f"\n✅ 已恢复会话「{sessions[idx].title}」（{len(saved)} 条历史消息）")
                        # 恢复时把 system prompt + 历史消息拼回去
                        self.history = [
                            {"role": "system", "content": build_system_prompt(self.skill_registry)}
                        ] + saved
                        self.session_id = sid
                        return
                except ValueError:
                    pass
                print("   输入无效，请重试")

        # 创建新会话
        sid = self.store.create_session(title="新会话", mode="chat")
        self.session_id = sid
        print(f"\n✨ 已创建新会话（ID: {sid}）")
        print("   提示：输入 /sessions 管理历史，输入 /save 手动保存当前标题")

    def _try_session_command(self, text: str) -> str | None:
        """
        识别 / 开头的持久化命令。
        返回 None 表示不是命令；返回 'quit' 表示退出；返回 "" 表示已处理。
        """
        if not text.startswith("/"):
            return None
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd in ("/quit", "/exit"):
            return "quit"
        elif cmd == "/help":
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
  /help            显示此帮助
  /quit            退出程序
""")
            return ""
        elif cmd == "/sessions":
            sessions = self.store.list_sessions(limit=20)
            if not sessions:
                print("  暂无历史会话")
            else:
                print(f"\n📁 历史会话（共 {len(sessions)} 个）：")
                for i, s in enumerate(sessions, 1):
                    cur_tag = " ← 当前" if s.id == self.session_id else ""
                    mode_tag = f"[{s.mode}]" if s.mode != "chat" else ""
                    print(f"   {i}. {s.title} {mode_tag} ({self.store.count_messages(s.id)} 条){cur_tag}")
            return ""
        elif cmd == "/load":
            sessions = self.store.list_sessions(limit=20)
            if not sessions:
                print("  暂无历史会话")
                return ""
            try:
                idx = int(arg) - 1
                if 0 <= idx < len(sessions):
                    sid = sessions[idx].id
                    saved = self.store.load_messages(sid)
                    print(f"  ✅ 已加载「{sessions[idx].title}」（{len(saved)} 条消息）")
                    self.history = [
                        {"role": "system", "content": build_system_prompt(self.skill_registry)}
                    ] + saved
                    self.session_id = sid
                    return ""
            except ValueError:
                pass
            print("  用法: /load <编号>（编号来自 /sessions）")
            return ""
        elif cmd == "/new":
            sid = self.store.create_session(title="新会话", mode="chat")
            self.session_id = sid
            self.history = [{"role": "system", "content": build_system_prompt(self.skill_registry)}]
            print(f"  ✨ 已创建新会话（ID: {sid}）")
            return ""
        elif cmd == "/save":
            if not self.session_id:
                print("  当前无活动会话")
                return ""
            title = arg or self.store.get_session(self.session_id).title
            self.store.rename_session(self.session_id, title)
            print(f"  💾 已保存为「{title}」")
            return ""
        elif cmd == "/rename":
            if not self.session_id:
                print("  当前无活动会话")
                return ""
            if not arg:
                print("  用法: /rename <新标题>")
                return ""
            self.store.rename_session(self.session_id, arg)
            print(f"  ✏️ 已重命名为「{arg}」")
            return ""
        elif cmd == "/delete":
            sessions = self.store.list_sessions(limit=20)
            try:
                idx = int(arg) - 1
                if 0 <= idx < len(sessions):
                    sid = sessions[idx].id
                    confirm = input(f"  确认删除「{sessions[idx].title}」？(y/N): ").strip().lower()
                    if confirm == "y":
                        self.store.delete_session(sid)
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
        elif cmd == "/clear":
            if self.session_id:
                self.store.clear_messages(self.session_id)
                print("  🧹 已清空当前会话的消息历史（会话本身保留）")
            else:
                print("  当前无活动会话")
            return ""
        elif cmd == "/current":
            if self.session_id:
                s = self.store.get_session(self.session_id)
                print(f"  当前会话: {s.title} [{s.mode}]")
                print(f"  消息数: {self.store.count_messages(self.session_id)}")
            else:
                print("  当前无活动会话")
            return ""
        elif cmd == "/db":
            print(f"  数据库路径: {self.store.db_path}")
            print(f"  绝对路径:   {os.path.abspath(self.store.db_path)}")
            return ""
        else:
            print(f"  未知命令：{cmd}，输入 /help 查看所有命令")
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
