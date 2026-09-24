"""
ai_agent.tools.wordpress — WordPress 博客发布工具
=================================================
薄包装层：import skills/wordpress-publish/wp_auto_publisher.py 的核心实现
（WordPressClient / parse_markdown_file），封装成 Agent Function Calling 工具。

集成原则：不复制 wp_auto_publisher.py 的源码
而是让 sys.path.insert 后直接 import，保持 skills/wordpress-publish/ 作为可移植单元。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import requests

from ai_agent.config import load_env

from .fs import resolve_workspace_path

# ── 把 skills/wordpress-publish/ 加入 import 路径 ──
_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
_WP_PUBLISHER_DIR = os.path.join(_PROJECT_ROOT, "skills", "wordpress-publish")
if _WP_PUBLISHER_DIR not in sys.path and os.path.isdir(_WP_PUBLISHER_DIR):
    sys.path.insert(0, _WP_PUBLISHER_DIR)

try:
    from wp_auto_publisher import (  # noqa: E402  — 必须在 sys.path insert 之后
        WordPressClient,
        _infer_excerpt,
        insert_more_tag,
        parse_markdown_file,
    )

    _WP_AUTO_PUBLISHER_AVAILABLE = True
except ImportError:
    _WP_AUTO_PUBLISHER_AVAILABLE = False
    WordPressClient = None  # type: ignore
    parse_markdown_file = None  # type: ignore
    _infer_excerpt = None  # type: ignore
    insert_more_tag = None  # type: ignore


def _ensure_wp_env_loaded() -> None:
    """
    加载 skills/wordpress-publish/.env 到 os.environ（仅补空，不覆盖已有值）。
    优先级：系统环境变量 > 项目根 .env > skills/wordpress-publish/.env
    """
    if _WP_AUTO_PUBLISHER_AVAILABLE:
        load_env(os.path.join(_WP_PUBLISHER_DIR, ".env"))


def _resolve_wp_user(wp_user: str, wp_app_password: str) -> str:
    """
    解析 WordPress 用户名。
    - 显式 wp_user 优先
    - 否则从 wp_app_password（格式 "username xxxx xxxx xxxx"）里空格拆分取第一段
    """
    if wp_user.strip():
        return wp_user.strip()
    parts = wp_app_password.strip().split(maxsplit=1)
    if len(parts) == 2 and parts[0].strip():
        return parts[0].strip()
    return ""


def wp_publish(
    md_file: str = "",
    title: str = "",
    content_html: str = "",
    status: str = "draft",
    categories: list | None = None,
    tags: list | None = None,
    excerpt: str = "",
    # ── 来自 Config 的凭证（由 build_tool_map 闭包注入，LLM 看不到这些参数） ──
    cfg_wp_url: str = "",
    cfg_wp_user: str = "",
    cfg_wp_app_password: str = "",
    cfg_workspace_root: str = "",
) -> str:
    """
    WordPress 博客发布 Agent 工具。

    两种调用模式（二选一）：
    - md_file 模式：传本地 Markdown 文件路径，工具内部自动解析 Front Matter + md→HTML + 分类/标签推断
    - title + content_html 模式：直接传标题和 HTML 正文，跳过文件解析

    LLM 调用示例：
      wp_publish(md_file="docs/blog/05.md", status="draft")
      wp_publish(title="我的标题", content_html="<h2>...</h2><p>...</p>", status="publish")
    """
    # ── 0. 依赖检查 + 加载 skills/wordpress-publish/.env ──
    if not _WP_AUTO_PUBLISHER_AVAILABLE:
        return (
            "❌ WordPress 发布模块不可用。请确认 skills/wordpress-publish/ 目录存在，"
            "并安装依赖：pip install markdown python-frontmatter"
        )
    _ensure_wp_env_loaded()

    # ── 1. 凭证校验（闭包注入优先，否则 fallback 环境变量） ──
    wp_url = (cfg_wp_url or os.environ.get("WP_URL", "")).strip()
    wp_app_password = (cfg_wp_app_password or os.environ.get("WP_APP_PASSWORD", "")).strip()
    wp_user = _resolve_wp_user(
        cfg_wp_user or os.environ.get("WP_USER", ""),
        wp_app_password,
    )

    if not wp_url or not wp_app_password:
        return (
            "❌ WordPress 凭证未配置。请在 .env 填写 WP_URL 和 WP_APP_PASSWORD"
            "（WP_USER 可选，若不填会从 WP_APP_PASSWORD 格式 'username xxxx xxxx' 解析）"
        )
    if not wp_user:
        return (
            "❌ 无法解析 WordPress 用户名。请在 .env 填写 WP_USER"
            "，或确保 WP_APP_PASSWORD 格式为 'username xxxx xxxx xxxx'"
        )

    # ── 2. 参数白名单 ──
    if status not in ("draft", "publish", "pending", "private", "future"):
        status = "draft"

    # ── 3. 构造 post_data ──
    try:
        if md_file.strip():
            # 模式 A：解析 Markdown 文件（限制在工作区内，防止 LLM 读任意本地文件上传公网）
            safe_path, path_error = resolve_workspace_path(
                md_file.strip(), cfg_workspace_root or None
            )
            if path_error or safe_path is None:
                return f"❌ {path_error or '安全限制：路径解析失败'}"
            # parse_markdown_file 内部做 relative_to(CONFIG["posts_dir"])，
            # 如果 md_file 不在默认的 ./posts 目录下会抛 ValueError，
            # 这里临时把 posts_dir 改成 md_file 的父目录。
            md_path = safe_path
            original_posts_dir = None
            if _WP_AUTO_PUBLISHER_AVAILABLE:
                import wp_auto_publisher as _wp  # noqa: E402

                original_posts_dir = _wp.CONFIG.get("posts_dir")
                _wp.CONFIG["posts_dir"] = str(md_path.parent)
            try:
                post_data = parse_markdown_file(md_path)
            finally:
                if original_posts_dir is not None:
                    _wp.CONFIG["posts_dir"] = original_posts_dir
            post_data["status"] = status
            if categories is not None:
                post_data["categories"] = categories
            if tags is not None:
                post_data["tags"] = tags
            if excerpt.strip():
                post_data["excerpt"] = excerpt.strip()
        elif title.strip() and content_html.strip():
            # 模式 B：直接传内容
            post_data = {
                "title": title.strip(),
                "content": content_html.strip(),
                "status": status,
                "categories": list(categories) if categories else [],
                "tags": list(tags) if tags else [],
                "excerpt": excerpt.strip() or _infer_excerpt(content_html.strip()),
            }
        else:
            return "❌ 参数错误：请提供 md_file（文件路径），或 title + content_html（直接传内容）"
    except FileNotFoundError:
        return f"❌ 文件不存在：{md_file}"
    except Exception as e:
        return f"❌ 解析文件失败：{e}"

    # ── 4. 发布 ──
    try:
        client = WordPressClient(wp_url, wp_user, wp_app_password)
        result = client.publish_post(post_data)
        post_id = result.get("id", "?")
        link = result.get("link", "")
        admin_edit = f"{wp_url.rstrip('/')}/wp-admin/post.php?post={post_id}&action=edit"
        return (
            f"✅ 发布成功（状态: {status}）\n"
            f"   Post ID: {post_id}\n"
            f"   预览链接: {link}\n"
            f"   后台编辑: {admin_edit}"
        )
    except requests.exceptions.HTTPError as e:
        body = e.response.text[:500] if e.response is not None else ""
        return f"❌ 发布失败 HTTP {e.response.status_code if e.response else '?'}: {body}"
    except requests.exceptions.ConnectionError:
        return f"❌ 无法连接到 WordPress 站点：{wp_url}"
    except Exception as e:
        return f"❌ 发布失败：{e}"
