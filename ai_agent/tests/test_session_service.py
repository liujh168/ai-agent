"""SessionService 业务规则与 CLI 会话命令薄壳的测试。"""

import os
import tempfile

from ai_agent.persistence import ChatStore
from ai_agent.session_service import SessionService


class _FakeClient:
    """compress_and_persist_history 只需要 summarize_messages。"""

    def summarize_messages(self, history, keep_recent=4):
        return [history[0], {"role": "assistant", "content": "摘要"}] + history[-keep_recent:]


def _make_service(temp_dir):
    store = ChatStore(os.path.join(temp_dir, "svc.db"))
    return SessionService(store)


def test_append_user_message_auto_titles_first_message():
    with tempfile.TemporaryDirectory() as temp_dir:
        svc = _make_service(temp_dir)
        sid = svc.create()
        svc.append_user_message(sid, "北京天气怎么样？顺便算一下 123*456")
        assert svc.get(sid).title == "北京天气怎么样？顺便算一下 123*456"[:20]

        # 第二条用户消息不再改标题
        svc.append_user_message(sid, "再算算 7*8")
        assert svc.get(sid).title != "再算算 7*8"
        svc.store.close()


def test_restore_history_prepends_system_prompt():
    with tempfile.TemporaryDirectory() as temp_dir:
        svc = _make_service(temp_dir)
        sid = svc.create()
        svc.append_user_message(sid, "你好")
        svc.append_assistant_message(sid, "你好！有什么可以帮你？")

        history = svc.restore_history(sid, "SYSTEM")
        assert history[0] == {"role": "system", "content": "SYSTEM"}
        assert [m["role"] for m in history[1:]] == ["user", "assistant"]
        svc.store.close()


def test_compress_if_needed_respects_threshold_and_rewrites_store():
    with tempfile.TemporaryDirectory() as temp_dir:
        svc = _make_service(temp_dir)
        sid = svc.create()
        history = [{"role": "system", "content": "sys"}]
        for i in range(6):
            history.append({"role": "user", "content": "x" * 100 + str(i)})
            svc.append_user_message(sid, "x" * 100 + str(i))

        # 未超阈值：原样返回，不压缩
        same, total, did = svc.compress_if_needed(_FakeClient(), history, sid, max_chars=10**9)
        assert did is False and same is history and total > 600

        # 超阈值：压缩并回写存储（存储里的消息被摘要替换）
        compressed, before, did = svc.compress_if_needed(_FakeClient(), history, sid, max_chars=10)
        assert did is True and before > 600
        assert compressed[1] == {"role": "assistant", "content": "摘要"}
        stored = svc.store.load_messages(sid)
        assert stored[0]["content"] == "摘要"
        svc.store.close()


def test_cli_session_command_dispatch(monkeypatch, capsys):
    """CLI 斜杠命令是 SessionService 之上的薄壳：分发、状态切换与退出语义。"""
    from main import AgentDemoApp

    with tempfile.TemporaryDirectory() as temp_dir:
        app = AgentDemoApp(db_path=os.path.join(temp_dir, "cli.db"))
        try:
            # 非命令输入返回 None（交给对话循环）
            assert app._try_session_command("你好") is None

            # /new 创建会话并切换为当前会话
            assert app._try_session_command("/new") == ""
            first_id = app.session_id
            assert first_id is not None
            assert app.history[0]["role"] == "system"

            # /rename + /current 反映同一份状态
            app._try_session_command("/rename 我的测试会话")
            app._try_session_command("/current")
            assert "我的测试会话" in capsys.readouterr().out

            # /sessions 能列出刚创建的会话
            app._try_session_command("/sessions")
            assert "我的测试会话" in capsys.readouterr().out

            # /delete 需要确认；确认删除当前会话后 session_id 被清空
            monkeypatch.setattr("builtins.input", lambda *_a: "y")
            app._try_session_command("/delete 1")
            assert app.session_id is None
            assert svc_list_empty(app)

            # /quit 传递退出语义
            assert app._try_session_command("/quit") == "quit"

            # 未知命令不崩溃
            assert app._try_session_command("/nope") == ""
            assert "未知命令" in capsys.readouterr().out
        finally:
            app.store.close()


def svc_list_empty(app) -> bool:
    return app.sessions.list(limit=20) == []


def test_cli_dangerous_tools_flag_precedence():
    """危险工具开关：CLI flag 显式覆盖 > 配置默认值；force_disable 优先级最高。"""
    from ai_agent.bootstrap import resolve_dangerous_tools
    from ai_agent.config import Config

    cfg_off = Config(enable_dangerous_tools=False)
    cfg_on = Config(enable_dangerous_tools=True)
    assert resolve_dangerous_tools(cfg_off) is False
    assert resolve_dangerous_tools(cfg_on) is True
    assert resolve_dangerous_tools(cfg_off, cli_flag=True) is True
    assert resolve_dangerous_tools(cfg_on, cli_flag=False) is False
    assert resolve_dangerous_tools(cfg_on, cli_flag=True, force_disable=True) is False


def test_resolve_rag_cache_path_handles_absolute():
    """cache_file 为绝对路径时不再拼 project_root（修复 Web 端历史 bug）。"""
    from ai_agent.bootstrap import resolve_rag_cache_path
    from ai_agent.config import Config

    cfg = Config(rag_cache_file="_rag_cache.json")
    assert resolve_rag_cache_path(cfg, "/proj") == os.path.join("/proj", "_rag_cache.json")

    abs_path = os.path.abspath("custom_cache.json")
    cfg_abs = Config(rag_cache_file=abs_path)
    assert resolve_rag_cache_path(cfg_abs, "/proj") == abs_path
