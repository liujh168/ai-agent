"""
对话持久化模块 — SQLite 多会话管理
=================================
提供完整的 session 级 CRUD：创建 / 列出 / 切换 / 重命名 / 删除会话，
以及自动保存每条用户消息 + 助手回复。

数据库 schema：
  sessions(id TEXT PRIMARY KEY, title TEXT, mode TEXT, created_at INTEGER, updated_at INTEGER)
  messages(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,
           content TEXT, tool_calls TEXT, timestamp INTEGER,
           FOREIGN KEY(session_id) REFERENCES sessions(id))

使用示例：
    db = ChatStore("ai_agent/agent_history.db")
    sid = db.create_session("我的测试会话", mode="react")
    db.append(sid, "user", "北京天气怎么样？")
    db.append(sid, "assistant", "北京今天晴天...")
    history = db.load_messages(sid)
    sessions = db.list_sessions()  # 按 updated_at DESC 返回
    db.delete_session(sid)
"""

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass

log = logging.getLogger(__name__)

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ai_agent_history.db")


@dataclass
class Session:
    """会话元数据"""

    id: str
    title: str
    mode: str = "chat"  # chat / react / reflexion / multi_agent / ...
    created_at: int = 0
    updated_at: int = 0


class ChatStore:
    """SQLite 会话持久化存储。支持多 session、按 updated_at 排序、懒加载。"""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._lock = threading.RLock()
        self._ensure_dir()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self._init_schema()

    def _ensure_dir(self) -> None:
        d = os.path.dirname(os.path.abspath(self.db_path))
        if d:
            os.makedirs(d, exist_ok=True)

    def _init_schema(self) -> None:
        with self._lock:
            cur = self.conn.cursor()
            cur.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id          TEXT PRIMARY KEY,
                    title       TEXT NOT NULL DEFAULT '新会话',
                    mode        TEXT NOT NULL DEFAULT 'chat',
                    created_at  INTEGER NOT NULL,
                    updated_at  INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id  TEXT NOT NULL,
                    role        TEXT NOT NULL,
                    content     TEXT NOT NULL,
                    tool_calls  TEXT,
                    timestamp   INTEGER NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
                DELETE FROM messages
                WHERE session_id NOT IN (SELECT id FROM sessions);
                """
            )
            self.conn.commit()

    # ---------- Session 级 ----------

    def create_session(self, title: str = "新会话", mode: str = "chat", session_id: str | None = None) -> str:
        """创建新会话，返回 session_id"""
        sid = session_id or uuid.uuid4().hex[:12]
        now = int(time.time())
        with self._lock:
            self.conn.execute(
                "INSERT INTO sessions(id, title, mode, created_at, updated_at) VALUES(?, ?, ?, ?, ?)",
                (sid, title, mode, now, now),
            )
            self.conn.commit()
        return sid

    def get_session(self, session_id: str) -> Session | None:
        with self._lock:
            row = self.conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            return None
        return Session(
            id=row["id"],
            title=row["title"],
            mode=row["mode"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_sessions(self, limit: int = 50) -> list[Session]:
        """按 updated_at 倒序列出会话"""
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [Session(**dict(r)) for r in rows]

    def rename_session(self, session_id: str, title: str) -> bool:
        with self._lock:
            cur = self.conn.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                (title, int(time.time()), session_id),
            )
            self.conn.commit()
        return cur.rowcount > 0

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            cur = self.conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self.conn.commit()
        return cur.rowcount > 0

    def delete_all_sessions(self) -> int:
        """删除所有会话，messages 通过 ON DELETE CASCADE 一并清理，返回删除条数"""
        with self._lock:
            cur = self.conn.execute("DELETE FROM sessions")
            self.conn.commit()
        return cur.rowcount

    def touch_session(self, session_id: str) -> None:
        """更新 updated_at 时间戳"""
        with self._lock:
            self.conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (int(time.time()), session_id),
            )
            self.conn.commit()

    def set_session_mode(self, session_id: str, mode: str) -> bool:
        """Update the mode marker for an existing session."""
        with self._lock:
            cur = self.conn.execute(
                "UPDATE sessions SET mode = ?, updated_at = ? WHERE id = ?",
                (mode, int(time.time()), session_id),
            )
            self.conn.commit()
        return cur.rowcount > 0

    # ---------- Message 级 ----------

    def append(self, session_id: str, role: str, content: str, tool_calls: list | None = None) -> None:
        """追加一条消息到指定会话，同时更新 session 的 updated_at"""
        tool_json = json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None
        now = int(time.time())
        with self._lock:
            self.conn.execute(
                "INSERT INTO messages(session_id, role, content, tool_calls, timestamp) VALUES(?, ?, ?, ?, ?)",
                (session_id, role, content, tool_json, now),
            )
            self.conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            self.conn.commit()

    def load_messages(self, session_id: str, include_system: bool = False) -> list[dict]:
        """加载会话的全部消息 → [{"role": ..., "content": ...}, ...]"""
        with self._lock:
            rows = self.conn.execute(
                "SELECT role, content, tool_calls FROM messages WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
        msgs = []
        for r in rows:
            entry = {"role": r["role"], "content": r["content"]}
            if r["tool_calls"]:
                try:
                    entry["tool_calls"] = json.loads(r["tool_calls"])
                except json.JSONDecodeError:
                    pass
            msgs.append(entry)
        if not include_system:
            msgs = [m for m in msgs if m["role"] != "system"]
        return msgs

    def count_messages(self, session_id: str) -> int:
        with self._lock:
            row = self.conn.execute(
                "SELECT COUNT(*) as c FROM messages WHERE session_id = ?", (session_id,)
            ).fetchone()
        return row["c"]

    def clear_messages(self, session_id: str) -> None:
        """清空会话的所有消息（保留会话本身）"""
        with self._lock:
            self.conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            self.conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (int(time.time()), session_id),
            )
            self.conn.commit()

    # ---------- 便捷封装 ----------

    def auto_title(self, session_id: str, first_user_msg: str) -> None:
        """根据首条用户消息自动生成会话标题（取前 20 字）"""
        title = first_user_msg[:20].replace("\n", " ") or "新会话"
        self.rename_session(session_id, title)

    def close(self) -> None:
        with self._lock:
            try:
                self.conn.close()
            except sqlite3.Error as exc:
                log.debug("关闭数据库连接时出错（忽略）: %s", exc)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
