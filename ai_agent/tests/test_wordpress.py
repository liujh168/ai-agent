"""wp_publish 工具的单元测试（全部 mock，不触网）"""

import os
import tempfile

from ai_agent.tools import wordpress
from ai_agent.tools.wordpress import _resolve_wp_user, wp_publish


class _FakeWPClient:
    """记录构造凭证与 publish_post 收到的 post_data"""

    last: dict | None = None

    def __init__(self, url, user, password):
        _FakeWPClient.last = {"url": url, "user": user, "password": password}

    def publish_post(self, post_data):
        _FakeWPClient.last["post_data"] = dict(post_data)
        return {"id": 42, "link": "http://wp.example.com/?p=42"}


def _patch_wp(monkeypatch, post_data=None):
    """把 wp_auto_publisher 的文件解析与网络发布全部换成假实现"""
    monkeypatch.setattr(wordpress, "WordPressClient", _FakeWPClient)
    monkeypatch.setattr(
        wordpress,
        "parse_markdown_file",
        lambda path: dict(post_data) if post_data else {"title": "T", "content": "<p>C</p>"},
    )
    monkeypatch.setattr(wordpress, "_infer_excerpt", lambda html: "自动摘要")
    monkeypatch.setattr(wordpress, "_ensure_wp_env_loaded", lambda: None)
    for var in ("WP_URL", "WP_USER", "WP_APP_PASSWORD"):
        monkeypatch.delenv(var, raising=False)


class TestResolveWpUser:
    def test_explicit_user_wins(self):
        assert _resolve_wp_user("alice", "bob xxxx xxxx") == "alice"

    def test_parse_from_app_password(self):
        assert _resolve_wp_user("", "bob xxxx xxxx xxxx") == "bob"
        assert _resolve_wp_user("  ", "bob xxxx") == "bob"

    def test_unresolvable(self):
        assert _resolve_wp_user("", "xxxx") == ""
        assert _resolve_wp_user("", "") == ""


class TestWpPublishFlow:
    def test_missing_credentials(self, monkeypatch):
        _patch_wp(monkeypatch)
        result = wp_publish(title="t", content_html="<p>x</p>")
        assert "凭证未配置" in result

    def test_unresolvable_user(self, monkeypatch):
        _patch_wp(monkeypatch)
        result = wp_publish(
            title="t",
            content_html="<p>x</p>",
            cfg_wp_url="http://wp.example.com",
            cfg_wp_app_password="xxxx",  # 无用户名段
        )
        assert "无法解析" in result

    def test_direct_content_mode(self, monkeypatch):
        """title + content_html 模式：状态白名单、摘要推断、凭证从 cfg_* 注入"""
        _patch_wp(monkeypatch)
        result = wp_publish(
            title="  我的标题  ",
            content_html="<p>正文</p>",
            status="bogus",  # 非白名单 → 回落 draft
            categories=["AI"],
            tags=["agent"],
            cfg_wp_url="http://wp.example.com",
            cfg_wp_app_password="bob xxxx xxxx",
        )
        assert "发布成功" in result
        assert "Post ID: 42" in result

        call = _FakeWPClient.last
        assert call["url"] == "http://wp.example.com"
        assert call["user"] == "bob"  # 从 app password 解析
        post = call["post_data"]
        assert post["title"] == "我的标题"
        assert post["status"] == "draft"
        assert post["categories"] == ["AI"]
        assert post["tags"] == ["agent"]
        assert post["excerpt"] == "自动摘要"

    def test_md_file_mode(self, monkeypatch):
        """md_file 模式：解析结果可被 status/categories/tags/excerpt 覆盖"""
        _patch_wp(monkeypatch, post_data={"title": "旧标题", "content": "<p>旧</p>", "status": "draft"})
        with tempfile.TemporaryDirectory() as td:
            md_path = os.path.join(td, "post.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write("---\ntitle: 旧标题\n---\n正文\n")

            result = wp_publish(
                md_file="post.md",
                status="publish",
                categories=["博客"],
                tags=["t1"],
                excerpt="手动摘要",
                cfg_wp_url="http://wp.example.com",
                cfg_wp_user="alice",
                cfg_wp_app_password="alice xxxx",
                cfg_workspace_root=td,
            )
        assert "发布成功（状态: publish）" in result
        post = _FakeWPClient.last["post_data"]
        assert post["title"] == "旧标题"
        assert post["status"] == "publish"
        assert post["categories"] == ["博客"]
        assert post["excerpt"] == "手动摘要"

    def test_md_file_outside_workspace_blocked(self, monkeypatch):
        _patch_wp(monkeypatch)
        with tempfile.TemporaryDirectory() as td:
            result = wp_publish(
                md_file="../../../etc/passwd",
                cfg_wp_url="http://wp.example.com",
                cfg_wp_user="alice",
                cfg_wp_app_password="alice xxxx",
                cfg_workspace_root=td,
            )
        assert "安全限制" in result
