"""
ai_agent.session_service — CLI 与 Web UI 共享的会话业务规则
==========================================================
在 ChatStore（纯存储 CRUD）之上封装两端一致的会话业务规则：
自动命名、恢复会话时拼回 system prompt、历史超长自动摘要压缩。
前端的斜杠命令（CLI）与 Gradio 事件（Web）只是这层之上的 I/O 薄壳。
"""

from __future__ import annotations

from ai_agent.app_core import compress_and_persist_history
from ai_agent.persistence import ChatStore, Session


class SessionService:
    """会话业务规则层。所有方法不做任何终端/Web 输出，返回值供前端渲染。"""

    def __init__(self, store: ChatStore):
        self.store = store

    # ---------- 查询 ----------

    def list(self, limit: int = 20) -> list[Session]:
        return self.store.list_sessions(limit=limit)

    def get(self, session_id: str) -> Session | None:
        return self.store.get_session(session_id) if session_id else None

    def count_messages(self, session_id: str) -> int:
        return self.store.count_messages(session_id)

    # ---------- 会话级操作 ----------

    def create(self, mode: str = "chat", title: str = "新会话") -> str:
        return self.store.create_session(title=title, mode=mode)

    def rename(self, session_id: str, title: str) -> bool:
        return self.store.rename_session(session_id, title)

    def delete(self, session_id: str) -> bool:
        return self.store.delete_session(session_id)

    def delete_all(self) -> int:
        return self.store.delete_all_sessions()

    def clear(self, session_id: str) -> None:
        self.store.clear_messages(session_id)

    def set_mode(self, session_id: str, mode: str) -> bool:
        return self.store.set_session_mode(session_id, mode)

    def restore_history(self, session_id: str, system_prompt: str) -> list[dict]:
        """加载持久化消息并拼回 system prompt（两端恢复会话的统一规则）。"""
        saved = self.store.load_messages(session_id)
        return [{"role": "system", "content": system_prompt}] + saved

    # ---------- 消息级操作 ----------

    def append_user_message(self, session_id: str, text: str) -> None:
        """持久化用户消息；首条用户消息自动生成会话标题。"""
        self.store.append(session_id, "user", text)
        if self.store.count_messages(session_id) <= 1:
            self.store.auto_title(session_id, text)

    def append_assistant_message(self, session_id: str, text: str) -> None:
        self.store.append(session_id, "assistant", text)

    # ---------- 历史压缩 ----------

    def compress_if_needed(
        self,
        client,
        history: list[dict],
        session_id: str | None,
        max_chars: int,
    ) -> tuple[list[dict], int, bool]:
        """
        history 总字符数超过 max_chars 时做摘要压缩并回写存储。
        返回 (history, 压缩前总字符数, 是否发生了压缩)。
        """
        total = sum(len(m.get("content") or "") for m in history)
        if total <= max_chars:
            return history, total, False
        compressed = compress_and_persist_history(client, history, self.store, session_id)
        return compressed, total, True
