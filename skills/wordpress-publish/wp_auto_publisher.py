#!/usr/bin/env python3
"""
WordPress 自动发布脚本
功能：读取本地 Markdown 文件，定时自动发布到 WordPress
依赖：pip install requests markdown schedule python-frontmatter
"""

import os
import sys
import json
import base64
import logging
import time
import re
import random
import subprocess
from pathlib import Path
from datetime import datetime

import requests
import markdown
import frontmatter
import schedule

# ─────────────────────────────────────────────
#  ① 配置区（修改这里）
#    敏感配置（URL/用户名/密码）请在 .env 文件中设置
# ─────────────────────────────────────────────

def _load_dotenv(dotenv_path: str = ".env") -> dict:
    """从 .env 文件加载环境变量（不依赖第三方库）"""
    env = {}
    path = Path(dotenv_path)
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    env[key.strip()] = value.strip()
    return env


_env = _load_dotenv()


def _load_external_llm_config() -> dict | None:
    """从外部配置文件自动发现 LLM 设置（如 claude_code DeepSeek 配置）"""
    ds_config_dir = Path(os.environ.get("USERPROFILE", "")) / ".claude_code"
    config_path = ds_config_dir / "config.json"
    key_path = ds_config_dir / "api_key.secure"

    if not config_path.exists():
        return None

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return None

    model = cfg.get("ANTHROPIC_MODEL", "")
    base_url = (cfg.get("ANTHROPIC_BASE_URL", "") or "").replace("/anthropic", "/v1")

    api_key = ""
    if key_path.exists():
        try:
            api_key = _decrypt_dpapi_file(str(key_path))
        except Exception:
            pass

    if not api_key and not base_url:
        return None

    return {
        "api_base": base_url or "https://api.deepseek.com/v1",
        "api_key": api_key,
        "model": _map_ds_model(model),
        "max_tokens": 1000,
        "timeout": 60,
    }


def _map_ds_model(anthro_model: str) -> str:
    """将 Anthropic 格式的 DeepSeek 模型名映射到 OpenAI 格式"""
    return "deepseek-chat"


def _decrypt_dpapi_file(key_path: str) -> str:
    """通过 PowerShell 子进程解密 DPAPI 加密的 API Key"""
    ps_script = (
        "$encrypted = [Console]::In.ReadToEnd();"
        "try {"
        "  $secure = ConvertTo-SecureString -String $encrypted -ErrorAction Stop;"
        "  $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure);"
        "  $key = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr);"
        "  [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr);"
        "  Write-Output $key;"
        "} catch { Write-Output '' }"
    )
    with open(key_path, "r", encoding="utf-8") as f:
        encrypted = f.read()
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_script],
        input=encrypted, capture_output=True, text=True, timeout=15,
    )
    return r.stdout.strip()


# ─────────────────────────────────────────────
#  ① 配置区（修改这里）
#    敏感配置（URL/用户名/密码）请在 .env 文件中设置
# ─────────────────────────────────────────────

CONFIG = {
    # WordPress 站点地址（末尾不加斜杠）
    "wp_url": _env.get("WP_URL", "https://example.com"),

    # WordPress 用户名
    "wp_user": _env.get("WP_USER", ""),

    # 应用程序密码
    # 获取方式：WP 后台 → 用户 → 编辑 → 应用程序密码 → 新增
    "wp_app_password": _env.get("WP_APP_PASSWORD", ""),

    # 本地 Markdown 文件目录
    "posts_dir": "./posts",

    # 已发布记录文件（防止重复发布）
    "published_log": "./published.json",

    # 定时发布时间（24小时制，每天几点发）
    "schedule_time": "09:00",

    # 发布间隔模式：
    #   "daily"   → 每天固定时间发一篇
    #   "interval"→ 每隔 N 分钟发一篇（适合批量导入）
    "schedule_mode": "daily",

    # interval 模式下的间隔分钟数
    "interval_minutes": 60,

    # 默认发布状态：publish（立即）/ draft（草稿）/ future（定时）
    "default_status": "publish",

    # 是否随机选文章（True=随机，False=按文件名字母顺序）
    "random_pick": False,

    # 自动在第 N 段后插入 <!--more--> 阅读更多标记（0 = 关闭）
    # 文件中已手动写了 <!--more--> 时，此设置自动跳过
    "auto_more_paragraphs": 1,

    # 摘要生成模式：
    #   "infer"  → 提取正文第一段为摘要（默认）
    #   "llm"    → 调用大模型自动生成摘要（需在 .env 配置 LLM_API_KEY）
    "excerpt_mode": "llm",

    # 请求超时秒数
    "timeout": 30,
}

# ─────────────────────────────────────────────
#  ② 日志配置
# ─────────────────────────────────────────────

class SafeStreamHandler(logging.StreamHandler):
    def emit(self, record):
        try:
            msg = self.format(record)
            self.stream.write(msg + self.terminator)
            self.flush()
        except UnicodeEncodeError:
            msg = self.format(record).encode("ascii", errors="replace").decode()
            self.stream.write(msg + self.terminator)
            self.flush()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        SafeStreamHandler(sys.stdout),
        logging.FileHandler("wp_publisher.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
#  ③ Markdown 文件格式说明
# ─────────────────────────────────────────────
"""
每篇 Markdown 文件支持 YAML Front Matter，示例：

---
title: "我的第一篇文章"
date: "2025-03-20T10:00:00"   # 可选，留空=立即发布
status: "publish"              # 可选，覆盖全局 default_status
categories:                    # 可选
  - 技术
  - Python
tags:                          # 可选
  - 自动化
  - WordPress
excerpt: "文章摘要，不超过200字"  # 可选
featured_image: "./images/cover.jpg"  # 可选，本地图片路径
---

# 正文从这里开始

文章内容支持完整的 Markdown 语法。
"""


# ─────────────────────────────────────────────
#  ④ 工具函数
# ─────────────────────────────────────────────

def _infer_excerpt(content: str, max_len: int = 200) -> str:
    """从正文提取第一段作为摘要"""
    text = content

    # 去掉标题行
    text = re.sub(r"^#{1,6}\s+.*$", "", text, flags=re.MULTILINE)

    # 去掉代码块
    text = re.sub(r"```[\s\S]*?```", "", text)

    # 去掉图片
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)

    # 链接保留文字
    text = re.sub(r"\[([^\]]*?)\]\(.*?\)", r"\1", text)

    # 去掉行内代码
    text = re.sub(r"`([^`]*)`", r"\1", text)

    # 去掉粗体/斜体标记
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}([^_]+)_{1,3}", r"\1", text)

    # 去掉 > 引用和 - * 列表标记
    text = re.sub(r"^[>\-*+]\s+", "", text, flags=re.MULTILINE)

    # 合并空白行，取第一段
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    first_para = paragraphs[0] if paragraphs else ""

    if not first_para:
        return ""

    return first_para[:max_len] + ("…" if len(first_para) > max_len else "")


def _llm_generate_excerpt(content: str, title: str = "", max_len: int = 200) -> str:
    """调用 LLM（OpenAI 兼容 API）生成文章摘要，失败返回空字符串"""
    profile = _env.get("LLM_PROFILE", "").strip()

    if profile == "auto":
        ext = _load_external_llm_config()
        if ext:
            api_base = ext["api_base"]
            api_key = ext["api_key"]
            model = ext["model"]
            max_tokens = ext["max_tokens"]
            timeout = ext["timeout"]
            log.info("  [LLM] 自动发现外部配置 (DeepSeek)")
        else:
            profile = "local"

    if profile != "auto":
        prefix = {"local": "LLM_LOCAL_", "ds": "LLM_DS_"}.get(profile, "LLM_")
        api_base = _env.get(f"{prefix}API_BASE", "").strip()
        api_key = _env.get(f"{prefix}API_KEY", "").strip()
        model = _env.get(f"{prefix}MODEL", "gpt-4o-mini").strip()
        max_tokens = int(_env.get(f"{prefix}MAX_TOKENS", "3000"))
        timeout = int(_env.get(f"{prefix}TIMEOUT", "300"))

    lang = _env.get("LLM_LANG", "zh").strip()

    if not api_base:
        return ""

    if lang == "zh":
        prompt = f"""请为以下文章生成一个约150字的摘要。要求：
- 概括文章的核心观点和内容，不要照抄开头
- 让读者产生阅读兴趣
- 直接输出摘要，不要加任何前缀说明

文章标题：{title}

文章内容：
{content[:4000]}"""
    else:
        prompt = f"""Generate a concise summary (about 100 words) for the following article.
- Capture the core ideas, don't just repeat the first paragraph
- Make it engaging for readers
- Output only the summary, no prefix

Title: {title}

Content:
{content[:4000]}"""

    try:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        r = requests.post(
            f"{api_base}/chat/completions",
            headers=headers,
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.6,
            },
            timeout=timeout,
        )
        r.raise_for_status()
        summary = r.json()["choices"][0]["message"]["content"].strip()
        log.info("  [LLM] 摘要生成成功")
        return summary[:max_len] + ("…" if len(summary) > max_len else "")
    except Exception as e:
        log.warning(f"  [LLM] 摘要生成失败：{e}")
        return ""


def _infer_categories(file_path: Path) -> list:
    parent = file_path.parent.name
    if parent and parent not in (".", "posts"):
        return [parent]
    return []


def _find_featured_image(file_path: Path) -> str:
    name_no_ext = file_path.stem
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        candidate = file_path.parent / (name_no_ext + ext)
        if candidate.exists():
            return str(candidate)
    return ""


def insert_more_tag(html: str, after_paragraphs: int) -> str:
    """在第 N 个 </p> 后插入 WordPress <!--more--> 标签"""
    if after_paragraphs <= 0:
        return html
    pos, count = 0, 0
    while count < after_paragraphs:
        idx = html.find("</p>", pos)
        if idx == -1:
            return html  # 段落数不足，不插入
        count += 1
        pos = idx + 4
    return html[:pos] + "\n<!--more-->\n" + html[pos:]


def load_published_log(log_path: str) -> set:
    """加载已发布记录"""
    path = Path(log_path)
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("published", []))
    return set()


def save_published_log(log_path: str, published: set):
    """保存已发布记录"""
    path = Path(log_path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {"published": sorted(published), "last_updated": datetime.now().isoformat()},
            f,
            ensure_ascii=False,
            indent=2,
        )


def get_pending_files(posts_dir: str, published: set, random_pick: bool) -> list:
    """获取待发布的 Markdown 文件列表"""
    dir_path = Path(posts_dir)
    if not dir_path.exists():
        log.error(f"文章目录不存在：{posts_dir}")
        return []

    all_files = sorted(dir_path.rglob("*.md"))
    pending = [f for f in all_files if str(f.relative_to(dir_path)) not in published]

    if not pending:
        log.info("📭 所有文章已发布完毕，无待发布文件")
        return []

    if random_pick:
        random.shuffle(pending)

    log.info(f"📂 待发布文件数：{len(pending)}")
    return pending


def parse_markdown_file(file_path: Path) -> dict:
    """
    解析 Markdown 文件，提取 Front Matter 和正文
    返回包含所有字段的字典
    """
    with open(file_path, "r", encoding="utf-8") as f:
        post = frontmatter.load(f)

    # 正文 Markdown → HTML
    md_extensions = [
        "extra",        # 表格、脚注等
        "codehilite",   # 代码高亮
        "toc",          # 目录
        "nl2br",        # 换行转 <br>
        "sane_lists",   # 更合理的列表解析
    ]
    html_content = markdown.markdown(
        post.content,
        extensions=md_extensions,
        extension_configs={
            "codehilite": {"css_class": "highlight", "linenums": False},
            "toc": {"permalink": False},
        },
    )

    # 自动插入 <!--more-->（文件中已手动写了则跳过）
    if CONFIG["auto_more_paragraphs"] > 0 and "<!--more-->" not in post.content:
        html_content = insert_more_tag(html_content, CONFIG["auto_more_paragraphs"])

    # 从文件名推断标题（去掉序号前缀和扩展名）
    filename_title = re.sub(r"^\d+[-_.]?\s*", "", file_path.stem).replace("-", " ").replace("_", " ").title()

    # 自动推断缺失字段
    title = str(post.get("title", "")) or filename_title

    if post.get("excerpt"):
        excerpt = str(post["excerpt"])
    elif CONFIG["excerpt_mode"] == "llm":
        excerpt = _llm_generate_excerpt(post.content, title) or _infer_excerpt(post.content)
    else:
        excerpt = _infer_excerpt(post.content)

    categories = post.get("categories", []) or _infer_categories(file_path)
    tags = post.get("tags", []) or []
    featured_image = post.get("featured_image", None) or _find_featured_image(file_path)

    return {
        "title":          title,
        "content":        html_content,
        "excerpt":        excerpt,
        "status":         str(post.get("status", CONFIG["default_status"])),
        "date":           post.get("date", None),
        "categories":     categories,
        "tags":           tags,
        "featured_image": featured_image,
        "_source_file":   str(file_path.relative_to(CONFIG["posts_dir"])),
    }


# ─────────────────────────────────────────────
#  ⑤ WordPress API 操作
# ─────────────────────────────────────────────

class WordPressClient:
    def __init__(self, url: str, user: str, password: str, timeout: int = 30):
        self.base = url.rstrip("/") + "/wp-json/wp/v2"
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self.timeout = timeout
        self._term_cache = {}

    def _get(self, endpoint: str, params: dict = None) -> list | dict:
        r = requests.get(
            f"{self.base}/{endpoint}",
            headers=self.headers,
            params=params or {},
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()

    def _post(self, endpoint: str, data: dict) -> dict:
        r = requests.post(
            f"{self.base}/{endpoint}",
            headers=self.headers,
            json=data,
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()

    def test_connection(self) -> bool:
        """测试 API 连接与鉴权"""
        try:
            result = self._get("users/me")
            log.info(f"✅ 连接成功，已登录用户：{result.get('name')} (ID: {result.get('id')})")
            return True
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                log.error("❌ 认证失败，请检查用户名和应用程序密码")
            elif e.response.status_code == 403:
                log.error("❌ 权限不足，该用户没有发布文章权限")
            else:
                log.error(f"❌ HTTP 错误 {e.response.status_code}：{e.response.text[:200]}")
            return False
        except requests.exceptions.ConnectionError:
            log.error(f"❌ 无法连接到站点：{self.base}")
            return False

    def get_or_create_terms(self, names: list, taxonomy: str) -> list:
        """获取或创建分类/标签，返回 ID 列表（内存缓存）"""
        ids = []
        cache = self._term_cache.setdefault(taxonomy, {})
        for name in names:
            name = str(name).strip()
            if not name:
                continue
            key = name.lower()
            if key in cache:
                ids.append(cache[key])
                continue
            results = self._get(taxonomy, {"search": name, "per_page": 5})
            matched = [t for t in results if t["name"].lower() == name.lower()]
            if matched:
                cache[key] = matched[0]["id"]
                ids.append(matched[0]["id"])
            else:
                new_term = self._post(taxonomy, {"name": name})
                cache[key] = new_term["id"]
                ids.append(new_term["id"])
                log.info(f"  📌 新建{taxonomy}：{name} (ID: {new_term['id']})")
        return ids

    def upload_image(self, image_path: str) -> int | None:
        """上传本地图片，返回 media ID"""
        path = Path(image_path)
        if not path.exists():
            log.warning(f"  ⚠️  封面图不存在：{image_path}")
            return None

        mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".png": "image/png", ".gif": "image/gif",
                    ".webp": "image/webp"}
        mime_type = mime_map.get(path.suffix.lower(), "image/jpeg")

        upload_headers = {
            "Authorization": self.headers["Authorization"],
            "Content-Disposition": f'attachment; filename="{path.name}"',
            "Content-Type": mime_type,
        }
        with open(path, "rb") as f:
            r = requests.post(
                f"{self.base}/media",
                headers=upload_headers,
                data=f.read(),
                timeout=self.timeout,
            )
        r.raise_for_status()
        media_id = r.json()["id"]
        log.info(f"  🖼️  封面图上传成功 (media ID: {media_id})")
        return media_id

    def publish_post(self, post_data: dict) -> dict:
        """发布一篇文章"""
        # 处理分类和标签
        cat_ids = self.get_or_create_terms(post_data.get("categories", []), "categories")
        tag_ids = self.get_or_create_terms(post_data.get("tags", []),       "tags")

        # 处理封面图
        featured_media = None
        if post_data.get("featured_image"):
            featured_media = self.upload_image(post_data["featured_image"])

        # 构造请求体
        payload = {
            "title":   post_data["title"],
            "content": post_data["content"],
            "status":  post_data["status"],
        }
        if post_data.get("slug"):
            payload["slug"] = post_data["slug"]
        if post_data.get("excerpt"):
            payload["excerpt"] = post_data["excerpt"]
        if cat_ids:
            payload["categories"] = cat_ids
        if tag_ids:
            payload["tags"] = tag_ids
        if featured_media:
            payload["featured_media"] = featured_media

        # 定时发布：status 须为 "future"，且提供 date（本地时间 ISO 8601）
        if post_data.get("date"):
            payload["status"] = "future"
            payload["date"] = str(post_data["date"])

        result = self._post("posts", payload)
        return result


# ─────────────────────────────────────────────
#  ⑥ 核心任务
# ─────────────────────────────────────────────

def _do_publish(post_data: dict, published: set, rel_path: str) -> bool:
    """发布文章并记录 published.json"""
    client = WordPressClient(
        CONFIG["wp_url"], CONFIG["wp_user"],
        CONFIG["wp_app_password"], CONFIG["timeout"],
    )
    try:
        result = client.publish_post(post_data)
        link = result.get("link", "（无链接）")
        post_id = result.get("id", "?")
        log.info(f"✅ 发布成功！文章 ID: {post_id}")
        log.info(f"   链接：{link}")
        published.add(rel_path)
        save_published_log(CONFIG["published_log"], published)
        return True
    except requests.exceptions.HTTPError as e:
        body = e.response.text[:300] if e.response else ""
        log.error(f"❌ 发布失败 HTTP {e.response.status_code}：{body}")
    except Exception as e:
        log.error(f"❌ 发布失败：{e}")
    return False


def run_publish_job():
    """执行一次发布任务（每次发布一篇）"""
    log.info("=" * 55)
    log.info("🚀 开始执行发布任务")

    published = load_published_log(CONFIG["published_log"])
    pending   = get_pending_files(CONFIG["posts_dir"], published, CONFIG["random_pick"])

    if not pending:
        return

    # 取第一篇
    file_path = pending[0]
    rel_path = str(file_path.relative_to(CONFIG["posts_dir"]))
    log.info(f"📄 准备发布：{rel_path}")

    # 解析 Markdown
    try:
        post_data = parse_markdown_file(file_path)
    except Exception as e:
        log.error(f"❌ 解析文件失败：{rel_path} → {e}")
        return

    # 生成 slug：优先使用文件中定义的，否则自动生成
    if not post_data.get("slug"):
        today = datetime.now().strftime("%Y%m%d")
        seq = len(published) + 1
        post_data["slug"] = f"{today}-{seq:03d}"

    log.info(f"  标题：{post_data['title']}")
    log.info(f"  Slug ：{post_data['slug']}")
    log.info(f"  状态：{post_data['status']}")
    if post_data.get("categories"):
        log.info(f"  分类：{post_data['categories']}")
    if post_data.get("tags"):
        log.info(f"  标签：{post_data['tags']}")

    # 发布
    _do_publish(post_data, published, rel_path)

    log.info("=" * 55)


# ─────────────────────────────────────────────
#  ⑦ 调度器
# ─────────────────────────────────────────────

def start_scheduler():
    mode = CONFIG["schedule_mode"]

    if mode == "daily":
        t = CONFIG["schedule_time"]
        schedule.every().day.at(t).do(run_publish_job)
        log.info(f"⏰ 定时模式：每天 {t} 发布一篇")

    elif mode == "interval":
        mins = CONFIG["interval_minutes"]
        schedule.every(mins).minutes.do(run_publish_job)
        log.info(f"⏰ 间隔模式：每 {mins} 分钟发布一篇")

    else:
        log.error(f"❌ 未知 schedule_mode: {mode}")
        sys.exit(1)

    # 启动时立刻检查一次（可注释掉）
    log.info("⚡ 启动时立即执行一次...")
    run_publish_job()

    log.info("🔄 调度器运行中，按 Ctrl+C 退出\n")
    while True:
        schedule.run_pending()
        time.sleep(30)  # 每 30 秒检查一次待执行任务


# ─────────────────────────────────────────────
#  ⑧ 命令行入口
# ─────────────────────────────────────────────

def cmd_test():
    """测试连接"""
    log.info("🔌 测试 WordPress 连接...")
    client = WordPressClient(
        CONFIG["wp_url"],
        CONFIG["wp_user"],
        CONFIG["wp_app_password"],
        CONFIG["timeout"],
    )
    client.test_connection()


def cmd_list():
    """列出所有待发布文件"""
    published = load_published_log(CONFIG["published_log"])
    pending   = get_pending_files(CONFIG["posts_dir"], published, False)
    if pending:
        log.info("待发布文件列表：")
        for i, f in enumerate(pending, 1):
            log.info(f"  {i:3d}. {f.relative_to(CONFIG['posts_dir'])}")
    total_published = len(published)
    log.info(f"\n已发布：{total_published} 篇，待发布：{len(pending)} 篇")


def cmd_preview(filename: str = None):
    """预览文章转换结果（不实际发布）"""
    published = load_published_log(CONFIG["published_log"])

    if filename:
        file_path = Path(CONFIG["posts_dir"]) / filename
    else:
        pending = get_pending_files(CONFIG["posts_dir"], published, CONFIG["random_pick"])
        if not pending:
            log.info("📭 无待发布文件")
            return
        file_path = pending[0]

    if not file_path.exists():
        log.error(f"文件不存在：{file_path}")
        sys.exit(1)

    post_data = parse_markdown_file(file_path)
    if not post_data.get("slug"):
        today = datetime.now().strftime("%Y%m%d")
        seq = len(published) + 1
        post_data["slug"] = f"{today}-{seq:03d}"

    print("=" * 55)
    print(f"[预览] {post_data.get('_source_file', file_path.name)}")
    print("-" * 55)
    print(f"标题:     {post_data['title']}")
    print(f"Slug:     {post_data['slug']}")
    print(f"状态:     {post_data['status']}")
    if post_data.get('categories'):
        print(f"分类:     {', '.join(post_data['categories'])}")
    if post_data.get('tags'):
        print(f"标签:     {', '.join(post_data['tags'])}")
    if post_data.get('excerpt'):
        print(f"摘要:     {post_data['excerpt'][:100]}{'...' if len(post_data['excerpt']) > 100 else ''}")
    if post_data.get('featured_image'):
        print(f"封面图:   {post_data['featured_image']}")
    if post_data.get('date'):
        print(f"定时:     {post_data['date']}")
    print("-" * 55)
    html = post_data['content']
    print(f"HTML 长度: {len(html)} 字符")
    print(f"包含 more: {'是' if '<!--more-->' in html else '否'}")
    snippet = html[:200].strip()
    print(f"HTML 前200字符: {snippet}...")
    print("=" * 55)


def cmd_publish_now(filename: str = None):
    """立即发布一篇（可指定文件名）"""
    published = load_published_log(CONFIG["published_log"])

    if filename:
        file_path = Path(CONFIG["posts_dir"]) / filename
        if not file_path.exists():
            log.error(f"文件不存在：{file_path}")
            sys.exit(1)

        # 检查是否已发布
        rel = str(file_path.relative_to(CONFIG["posts_dir"]))
        if rel in published:
            log.warning(f"⚠️  文章已发布过：{rel}")
            sys.exit(0)

        post_data = parse_markdown_file(file_path)

        # slug 生成：优先使用文件中定义的，否则自动生成
        if not post_data.get("slug"):
            today = datetime.now().strftime("%Y%m%d")
            seq = len(published) + 1
            post_data["slug"] = f"{today}-{seq:03d}"

        log.info(f"  标题：{post_data['title']}")
        log.info(f"  Slug ：{post_data['slug']}")

        if not _do_publish(post_data, published, rel):
            sys.exit(1)
    else:
        run_publish_job()


def print_help():
    print("""
用法：python wp_auto_publisher.py [命令]

命令：
  （无参数）        启动定时调度器
  test              测试 WordPress API 连接
  list              列出待发布文章
  preview           预览下一篇文章的转换结果（不实际发布）
  preview <文件名>  预览指定文章的转换结果
  publish           立即发布一篇（按配置顺序）
  publish <文件名>  立即发布指定文件

示例：
  python wp_auto_publisher.py
  python wp_auto_publisher.py test
  python wp_auto_publisher.py list
  python wp_auto_publisher.py preview
  python wp_auto_publisher.py preview my-post.md
  python wp_auto_publisher.py publish
  python wp_auto_publisher.py publish 01-my-first-post.md
""")


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args:
        start_scheduler()
    elif args[0] == "test":
        cmd_test()
    elif args[0] == "list":
        cmd_list()
    elif args[0] == "publish":
        cmd_publish_now(args[1] if len(args) > 1 else None)
    elif args[0] == "preview":
        cmd_preview(args[1] if len(args) > 1 else None)
    else:
        print_help()
