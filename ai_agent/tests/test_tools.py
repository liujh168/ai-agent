"""单元测试 — 纯逻辑函数（不依赖 LM Studio）

运行方式：
  pip install pytest
  python -m pytest ai_agent/tests/ -v
"""

import os
import sys
import tempfile

# 确保能 import scripts 包（测试文件在 scripts/tests/ 下，需要项目根进 path）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest

from ai_agent.client import LMStudioClient  # noqa: F401
from ai_agent.tools import (  # noqa: F401
    FEW_SHOT_TEMPLATES,
    calculator,
    file_read,
    file_write,
    run_terminal,
    sql_query,
)

# ============================================================
# calculator — AST 白名单
# ============================================================


class TestCalculator:
    def test_basic_ops(self):
        assert calculator("1 + 2 * 3") == "7"
        assert calculator("(1 + 2) * 3") == "9"
        assert calculator("-5 + 3") == "-2"
        assert calculator("2 ** 10") == "1024"
        assert calculator("10 % 3") == "1"
        assert calculator("6 / 4") == "1.5"

    def test_zero_div(self):
        result = calculator("1/0")
        assert "错误" in result

    def test_malicious_code_blocked(self):
        """AST 白名单必须阻断任意代码执行"""
        for expr in [
            "__import__('os')",
            "eval('1+1')",
            "exec('print(1)')",
            "(lambda x: x+1)(5)",
            "open('/etc/passwd')",
            "print(1)",
        ]:
            result = calculator(expr)
            assert "错误" in result, f"FAIL: {expr!r} 应被阻断，实际返回 {result!r}"


# ============================================================
# file_read / file_write — 路径安全
# ============================================================


class TestFileIO:
    def test_write_and_read(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "test.txt")
            r = file_write(path, "hello world", workspace_root=td)
            assert "成功" in r or path in r
            r = file_read(path, workspace_root=td)
            assert "hello world" in r

    def test_write_append(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "append.txt")
            file_write(path, "a", mode="write", workspace_root=td)
            file_write(path, "b", mode="append", workspace_root=td)
            r = file_read(path, workspace_root=td)
            assert "a" in r and "b" in r

    def test_path_traversal_blocked(self):
        """路径穿越应被阻断"""
        result = file_read("../../../etc/passwd")
        assert "安全" in result or "不存在" in result or "错误" in result

    def test_auto_create_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "sub", "sub2", "x.txt")
            file_write(path, "nested", workspace_root=td)
            assert os.path.exists(path)

    def test_absolute_path_outside_workspace_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            outside = os.path.join(tempfile.gettempdir(), "outside-test.txt")
            result = file_write(outside, "blocked", workspace_root=td)
            assert "安全限制" in result


# ============================================================
# sql_query — SELECT-only enforce
# ============================================================


class TestSQLQuery:
    def setup_method(self):
        self._db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._db.close()

    def teardown_method(self):
        # Windows 下文件可能仍被 Defender / 句柄短暂占用，重试删除
        import time as _t

        for _ in range(5):
            try:
                os.remove(self._db.name)
                break
            except PermissionError:
                _t.sleep(0.2)

    def test_select_ok(self):
        r = sql_query(
            self._db.name,
            "SELECT COUNT(*) FROM users",
            workspace_root=os.path.dirname(self._db.name),
        )
        assert "用户数" in r or "共" in r or "执行" in r or "users" in r.lower()

    def test_drop_blocked(self):
        r = sql_query(
            self._db.name,
            "DROP TABLE users",
            workspace_root=os.path.dirname(self._db.name),
        )
        assert "SQL 执行失败" in r
        assert "readonly" in r.lower()

    def test_delete_blocked(self):
        r = sql_query(
            self._db.name,
            "DELETE FROM users",
            workspace_root=os.path.dirname(self._db.name),
        )
        assert "SQL 执行失败" in r

    def test_update_blocked(self):
        r = sql_query(
            self._db.name,
            "UPDATE users SET age=99",
            workspace_root=os.path.dirname(self._db.name),
        )
        assert "SQL 执行失败" in r

    def test_cte_delete_blocked(self):
        workspace = os.path.dirname(self._db.name)
        sql_query(
            self._db.name,
            "SELECT COUNT(*) FROM users",
            workspace_root=workspace,
        )
        r = sql_query(
            self._db.name,
            "WITH x AS (SELECT 1) DELETE FROM users RETURNING id",
            workspace_root=workspace,
        )
        assert "SQL 执行失败" in r
        check = sql_query(
            self._db.name,
            "SELECT COUNT(*) AS n FROM users",
            workspace_root=workspace,
        )
        assert "5" in check

    def test_pragma_ok(self):
        r = sql_query(
            self._db.name,
            "PRAGMA table_info(users)",
            workspace_root=os.path.dirname(self._db.name),
        )
        assert "安全限制" not in r


# ============================================================
# run_terminal — 危险命令拦截
# ============================================================


class TestTerminal:
    def test_normal_command_allowed(self):
        r = run_terminal("Write-Host hello", allow_unsafe=True)
        assert "安全拦截" not in r
        assert "hello" in r

    def test_remove_item_blocked(self):
        r = run_terminal("Remove-Item -Recurse -Force C:\\tmp", allow_unsafe=True)
        assert "安全拦截" in r

    def test_set_content_blocked(self):
        r = run_terminal("Set-Content file.txt hack", allow_unsafe=True)
        assert "安全拦截" in r

    def test_stop_computer_blocked(self):
        r = run_terminal("Stop-Computer -Force", allow_unsafe=True)
        assert "安全拦截" in r


# ============================================================
# RAG _cosine — 纯函数
# ============================================================


class TestCosine:
    def test_same_direction(self):
        from ai_agent.rag import RAGKnowledgeBase

        kb = RAGKnowledgeBase()
        assert abs(kb._cosine([1, 0], [1, 0]) - 1.0) < 1e-9

    def test_orthogonal(self):
        from ai_agent.rag import RAGKnowledgeBase

        kb = RAGKnowledgeBase()
        assert abs(kb._cosine([1, 0], [0, 1])) < 1e-9

    def test_zero_vector(self):
        from ai_agent.rag import RAGKnowledgeBase

        kb = RAGKnowledgeBase()
        assert kb._cosine([0, 0], [1, 2]) == 0.0


# ============================================================
# FEW_SHOT_TEMPLATES — 结构校验
# ============================================================


class TestFewShotTemplates:
    def test_all_templates_have_required_keys(self):
        for key, tpl in FEW_SHOT_TEMPLATES.items():
            assert "name" in tpl
            assert "desc" in tpl
            assert "examples" in tpl
            assert len(tpl["examples"]) >= 2
            for inp, out in tpl["examples"]:
                assert isinstance(inp, str) and isinstance(out, str)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
