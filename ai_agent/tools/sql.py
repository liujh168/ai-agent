"""SQL 查询工具：基于 sqlite3 的演示 Agent"""
import os
import sqlite3


def sql_query(db_path: str, sql: str, demo_seed: bool = True) -> str:
    """
    SQLite 查询工具。首次调用可带 demo_seed=True 自动建表+示例数据。
    之后传入任意 SQL 查询语句，返回格式化结果。
    """
    norm = os.path.normpath(db_path)
    if ".." in norm.split(os.sep):
        return f"安全限制：禁止访问包含 '..' 的路径 {db_path}"

    abs_path = os.path.abspath(norm)
    db_dir = os.path.dirname(abs_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    try:
        file_existed = os.path.exists(abs_path)
        conn = sqlite3.connect(abs_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        if demo_seed and not file_existed:
            cur.executescript("""
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
            """)
            cur.executescript("""
                INSERT OR IGNORE INTO users (id, name, age, city, joined_at) VALUES
                    (1, '张三', 28, '北京', '2024-03-15'),
                    (2, '李四', 35, '上海', '2023-11-08'),
                    (3, '王五', 22, '深圳', '2025-01-20'),
                    (4, '赵六', 41, '北京', '2022-07-30'),
                    (5, '钱七', 30, '广州', '2024-06-12');
                INSERT OR IGNORE INTO orders (id, user_id, product, amount, quantity, ordered_at) VALUES
                    (1, 1, '笔记本', 5999.00, 1, '2025-02-01'),
                    (2, 1, '鼠标',   199.00, 2, '2025-02-01'),
                    (3, 2, '显示器', 2499.00, 1, '2025-03-10'),
                    (4, 3, '键盘',   399.00, 1, '2025-01-25'),
                    (5, 4, '笔记本', 6999.00, 1, '2024-12-01'),
                    (6, 5, '耳机',   899.00, 1, '2025-04-05'),
                    (7, 2, '耳机',   699.00, 2, '2025-03-12');
            """)
            conn.commit()

        cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [r[0] for r in cur.fetchall()]

        sql_stripped = sql.strip().rstrip(";")
        if not sql_stripped:
            return f"数据库 {norm} 可用，表: {', '.join(tables) or '(空)'}\n请提供 SQL 语句。"

        first_word = sql_stripped.split()[0].upper()
        _READ_ONLY = {"SELECT", "PRAGMA", "EXPLAIN", "WITH"}
        if first_word not in _READ_ONLY:
            conn.close()
            return (
                f"⚠️ 安全限制：demo 模式下 sql_query 只允许 SELECT/PRAGMA/EXPLAIN/WITH。\n"
                f"拒绝执行的语句：{sql_stripped[:100]}\n"
                f"如需写入，请先 human_confirm 后手动执行。"
            )

        cur.execute(sql_stripped)
        rows = cur.fetchall()
        col_names = [d[0] for d in cur.description] if cur.description else []

        header = " | ".join(col_names) if col_names else "(无列)"
        sep = "-+-".join("-" * max(6, len(c)) for c in col_names) if col_names else ""
        lines = [header, sep] if col_names else []
        for row in rows:
            lines.append(" | ".join(str(row[i]) for i in range(len(col_names))))
        if len(rows) > 30:
            lines.append(f"\n... [还有 {len(rows) - 30} 行，仅显示前 30 行]")

        conn.close()
        return (
            f"=== {norm} ===\n"
            f"可用表: {', '.join(tables) or '(空)'}\n"
            f"执行: {sql_stripped}\n\n"
            + "\n".join(lines)
            + f"\n\n共 {len(rows)} 行结果"
        )
    except Exception as e:
        return f"SQL 执行失败 ({type(e).__name__}): {e}\n数据库路径: {abs_path}"
