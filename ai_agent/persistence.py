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
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Optional


DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ai_agent_history.db")


@dataclass
class Session:
    """会话元数据"""
    id: str
    title: str
    mode: str = "chat"          # chat / react / reflexion / multi_agent / ...
    created_at: int = 0
    updated_at: int = 0


class ChatStore:
    """SQLite 会话持久化存储。支持多 session、按 updated_at 排序、懒加载。"""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._ensure_dir()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _ensure_dir(self) -> None:
        d = os.path.dirname(os.path.abspath(self.db_path))
        if d:
            os.makedirs(d, exist_ok=True)

    def _init_schema(self) -> None:
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

            CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, timestamp);
            """
        )
        self.conn.commit()

    # ---------- Session 级 ----------

    def create_session(self, title: str = "新会话", mode: str = "chat", session_id: Optional[str] = None) -> str:
        """创建新会话，返回 session_id"""
        sid = session_id or uuid.uuid4().hex[:12]
        now = int(time.time())
        self.conn.execute(
            "INSERT INTO sessions(id, title, mode, created_at, updated_at) VALUES(?, ?, ?, ?, ?)",
            (sid, title, mode, now, now),
        )
        self.conn.commit()
        return sid

    def get_session(self, session_id: str) -> Optional[Session]:
        row = self.conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not row:
            return None
        return Session(id=row["id"], title=row["title"], mode=row["mode"],
                       created_at=row["created_at"], updated_at=row["updated_at"])

    def list_sessions(self, limit: int = 50) -> list[Session]:
        """按 updated_at 倒序列出会话"""
        rows = self.conn.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [Session(**dict(r)) for r in rows]

    def rename_session(self, session_id: str, title: str) -> bool:
        cur = self.conn.execute(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
            (title, int(time.time()), session_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def delete_session(self, session_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        # SQLite ON DELETE CASCADE 会自动删 messages
        self.conn.commit()
        return cur.rowcount > 0

    def touch_session(self, session_id: str) -> None:
        """更新 updated_at 时间戳"""
        self.conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (int(time.time()), session_id),
        )
        self.conn.commit()

    # ---------- Message 级 ----------

    def append(self, session_id: str, role: str, content: str,
               tool_calls: Optional[list] = None) -> None:
        """追加一条消息到指定会话，同时更新 session 的 updated_at"""
        tool_json = json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None
        self.conn.execute(
            "INSERT INTO messages(session_id, role, content, tool_calls, timestamp) VALUES(?, ?, ?, ?, ?)",
            (session_id, role, content, tool_json, int(time.time())),
        )
        self.touch_session(session_id)

    def load_messages(self, session_id: str, include_system: bool = False) -> list[dict]:
        """加载会话的全部消息 → [{"role": ..., "content": ...}, ...]"""
        rows = self.conn.execute(
            "SELECT role, content, tool_calls FROM messages WHERE session_id = ? ORDER BY timestamp ASC",
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
        row = self.conn.execute(
            "SELECT COUNT(*) as c FROM messages WHERE session_id = ?", (session_id,)
        ).fetchone()
        return row["c"]

    def clear_messages(self, session_id: str) -> None:
        """清空会话的所有消息（保留会话本身）"""
        self.conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        self.touch_session(session_id)

    # ---------- 便捷封装 ----------

    def auto_title(self, session_id: str, first_user_msg: str) -> None:
        """根据首条用户消息自动生成会话标题（取前 20 字）"""
        title = first_user_msg[:20].replace("\n", " ") or "新会话"
        self.rename_session(session_id, title)

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
