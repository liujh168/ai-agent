"""SQL 查询工具：基于 sqlite3 的演示 Agent"""

import logging
import sqlite3
from pathlib import Path

from .fs import resolve_workspace_path

log = logging.getLogger(__name__)


def _seed_demo_database(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                age INTEGER,
                city TEXT,
                joined_at TEXT
            );
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY,
                user_id INTEGER,
                product TEXT,
                amount REAL,
                quantity INTEGER,
                ordered_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            INSERT OR IGNORE INTO users (id, name, age, city, joined_at) VALUES
                (1, '张三', 28, '北京', '2024-03-15'),
                (2, '李四', 35, '上海', '2023-11-08'),
                (3, '王五', 22, '深圳', '2025-01-20'),
                (4, '赵六', 41, '北京', '2022-07-30'),
                (5, '钱七', 30, '广州', '2024-06-12');
            INSERT OR IGNORE INTO orders (id, user_id, product, amount, quantity, ordered_at) VALUES
                (1, 1, '笔记本', 5999.00, 1, '2025-02-01'),
                (2, 1, '鼠标', 199.00, 2, '2025-02-01'),
                (3, 2, '显示器', 2499.00, 1, '2025-03-10'),
                (4, 3, '键盘', 399.00, 1, '2025-01-25'),
                (5, 4, '笔记本', 6999.00, 1, '2024-12-01'),
                (6, 5, '耳机', 899.00, 1, '2025-04-05'),
                (7, 2, '耳机', 699.00, 2, '2025-03-12');
            """
        )
        conn.commit()
    finally:
        conn.close()


def sql_query(
    db_path: str,
    sql: str,
    demo_seed: bool = True,
    workspace_root: str | None = None,
) -> str:
    """
    SQLite 查询工具。首次调用可带 demo_seed=True 自动建表+示例数据。
    之后传入任意 SQL 查询语句，返回格式化结果。
    """
    abs_path, error = resolve_workspace_path(db_path, workspace_root)
    if error or abs_path is None:
        return error or "安全限制：路径解析失败"
    abs_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        should_seed = demo_seed and (not abs_path.exists() or abs_path.stat().st_size == 0)
        if should_seed:
            _seed_demo_database(abs_path)

        if not abs_path.exists():
            return f"数据库不存在: {abs_path}。设置 demo_seed=True 可创建示例数据库。"

        sql_stripped = sql.strip().rstrip(";")
        uri = abs_path.as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            tables = [r[0] for r in cur.fetchall()]

            if not sql_stripped:
                return f"数据库 {abs_path} 可用，表: {', '.join(tables) or '(空)'}\n请提供 SQL 语句。"

            cur.execute(sql_stripped)
            rows = cur.fetchmany(31)
            col_names = [d[0] for d in cur.description] if cur.description else []

            header = " | ".join(col_names) if col_names else "(无列)"
            sep = "-+-".join("-" * max(6, len(c)) for c in col_names) if col_names else ""
            lines = [header, sep] if col_names else []
            for row in rows[:30]:
                lines.append(" | ".join(str(row[i]) for i in range(len(col_names))))
            if len(rows) > 30:
                lines.append("\n... [结果超过 30 行，仅显示前 30 行]")

            return (
                f"=== {abs_path} ===\n"
                f"可用表: {', '.join(tables) or '(空)'}\n"
                f"执行: {sql_stripped}\n\n"
                + "\n".join(lines)
                + f"\n\n共 {'>30' if len(rows) > 30 else len(rows)} 行结果"
            )
        finally:
            conn.close()
    except (sqlite3.Error, OSError, ValueError) as e:
        log.warning("SQL 执行失败（%s）: %s", abs_path, e)
        return f"SQL 执行失败 ({type(e).__name__}): {e}\n数据库路径: {abs_path}"
