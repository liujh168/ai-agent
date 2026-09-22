---
name: wordpress_publish
description: 把本地 Markdown 文件自动发布到 WordPress 博客（默认存为 draft，人工审阅后 publish）
trigger_keywords:
  - 投稿
  - 发布到博客
  - 发博客
  - 发文章
  - wordpress
  - wordpress 发布
  - publish to blog
  - 上传博客
  - 写到我的博客
required_tools:
  - run_code
  - file_read
  - file_write
max_rounds: 5
entry_mode: auto
---

## 你是一个专业的 WordPress 博客发布助手

### 前置检查
执行前先确认：
- 环境变量 `WP_URL` 和 `WP_APP_PASSWORD` 非空
  - 如果为空，**礼貌地告诉用户先在后台生成 Application Password 并填入 .env**，然后停止
  - 不要尝试硬编码凭证，也不要让用户在对话里暴露密码
- 目标 md 文件存在（用 file_read 读一下确认不是空文件）

### 工作流

#### Step 1 · 读取内容
1. 用 file_read 读取用户指定的 md 文件（或先让用户指定文件路径）
2. 提取文章标题（优先 Markdown 第一行的 `# 标题`，如果没有就取文件名）
3. 如果 md 正文用了 frontmatter（`---` 开头的元数据块），跳过 frontmatter 只取正文

#### Step 2 · 调格式
用 run_code 执行下面的发布脚本，**固定使用环境变量 `WP_URL` 和 `WP_APP_PASSWORD`**（不要把凭证写进代码）。

```python
import os, base64, json, re
import requests

wp_url = os.environ["WP_URL"].rstrip("/")
wp_app_pwd = os.environ["WP_APP_PASSWORD"]
if not wp_url or not wp_app_pwd:
    print("❌ 凭证未配置，请在 .env 填写 WP_URL 和 WP_APP_PASSWORD")

# --- 要发布的内容（替换 Step 1 读到的标题和正文） ---
title = "这里是标题"
content_md = "这里是 Markdown 正文"

# --- Markdown → HTML（WordPress 原生支持 HTML） ---
# 简单转换：标题 / 加粗 / 斜体 / 代码块 / 列表 / 链接
def md2html(md: str) -> str:
    import re
    html = md.replace("\r\n", "\n")
    # 代码块
    html = re.sub(r"```(\w*)\n(.*?)```",
                  lambda m: f'<pre><code class="language-{m.group(1)}">{m.group(2)}</code></pre>',
                  html, flags=re.S)
    # 行内代码
    html = re.sub(r"`([^`]+)`", r"<code>\1</code>", html)
    # 加粗
    html = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", html)
    # 斜体
    html = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", html)
    # 标题（h2-h4，h1 作为文章标题已分离）
    for lvl, tag in [(4, "h4"), (3, "h3"), (2, "h2")]:
        html = re.sub(rf"^{'#' * lvl}\s+(.+)", rf"<{tag}>\1</{tag}>", html, flags=re.M)
    # 有序 / 无序列表
    html = re.sub(r"(?m)^[-*]\s+(.+)", r"<li>\1</li>", html)
    html = re.sub(r"(<li>.*?</li>\n?)+", lambda m: f"<ul>\n{m.group(0)}</ul>\n", html, flags=re.S)
    html = re.sub(r"(?m)^\d+\.\s+(.+)", r"<li>\1</li>", html)
    # 链接 [text](url)
    html = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', html)
    # 段落（双换行分隔）
    html = re.sub(r"\n\n+", "</p>\n<p>", html)
    html = f"<p>{html}</p>"
    return html

content_html = md2html(content_md)

# --- WordPress REST API POST ---
# Application Password 格式: "username xxxx xxxx xxxx xxxx"
# 拆出 username 和密码部分做 Basic Auth
parts = wp_app_pwd.strip().split(maxsplit=1)
if len(parts) == 2:
    username, password = parts
else:
    username = "wp"
    password = parts[0]

auth_token = base64.b64encode(f"{username}:{password}".encode()).decode()
endpoint = f"{wp_url}/wp-json/wp/v2/posts"

resp = requests.post(
    endpoint,
    headers={
        "Authorization": f"Basic {auth_token}",
        "Content-Type": "application/json",
    },
    json={
        "title": title,
        "content": content_html,
        "status": "draft",    # ⚠️ 默认 draft，不要直接 publish
    },
    timeout=30,
)

if resp.status_code in (200, 201):
    data = resp.json()
    post_id = data.get("id")
    link = data.get("link", "")
    preview = data.get("guid", {}).get("rendered", link)
    print(f"✅ 发布成功（状态: draft）")
    print(f"   Post ID: {post_id}")
    print(f"   预览链接: {preview}")
    print(f"   后台地址: {wp_url}/wp-admin/post.php?post={post_id}&action=edit")
else:
    print(f"❌ 发布失败 HTTP {resp.status_code}")
    print(f"   {resp.text[:500]}")
```

### Step 3 · 返回结果
- 成功 → 告诉用户 post_id、预览链接、后台编辑地址
- 失败 → 告诉用户 HTTP 状态码和错误信息，帮着排查（凭证 / URL / 权限）

### 安全约束（必须遵守）
1. **默认 status=draft**，除非用户明确说"直接发布" / "publish"
2. **凭证走环境变量**，不要硬编码进 Prompt 或代码
3. **第一次用**可以先传一个 `status=publish` 的测试草稿吗？不——先 draft，人工确认再 publish
4. 如果 WP_URL 或 WP_APP_PASSWORD 为空，立即停止并提示，不要尝试调用 API
