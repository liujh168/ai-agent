# WordPress 自动发布工具

读取本地 Markdown 文件，解析 Front Matter，支持 LLM 自动生成摘要，定时/即时发布到 WordPress。

---

## 快速开始

### 1. 安装依赖

```bash
pip install requests markdown schedule python-frontmatter
```

### 2. 配置文件

#### 2.1 敏感配置：创建 `.env` 文件

在项目根目录创建 `.env`：

```ini
# 必填：WordPress 连接
WP_URL=https://yourblog.com
WP_USER=your_username
WP_APP_PASSWORD=xxxx xxxx xxxx xxxx xxxx xxxx

# 可选：LLM 摘要生成（见下方说明）
LLM_PROFILE=auto
LLM_LANG=zh
```

**获取应用程序密码：**
WP 后台 → 用户 → 编辑 → 滚动到底部 → 应用程序密码 → 输入名称 → 新增 → 复制密码

#### 2.2 常规配置：编辑 `wp_auto_publisher.py`

打开 `wp_auto_publisher.py`，找到顶部的 `CONFIG` 字典，可按需调整：

| 配置项 | 说明 | 默认值 |
|---|---|---|
| `posts_dir` | Markdown 文件目录（支持子目录） | `"./posts"` |
| `schedule_time` | 每天定时发布时间 | `"09:00"` |
| `schedule_mode` | `"daily"` 每天一篇 / `"interval"` 每隔 N 分钟 | `"daily"` |
| `interval_minutes` | interval 模式的间隔分钟数 | `60` |
| `default_status` | 发布状态：`"publish"` / `"draft"` | `"publish"` |
| `random_pick` | 是否随机选取待发布文章 | `False` |
| `auto_more_paragraphs` | 第 N 段后插入"继续阅读"标记（0=关闭） | `1` |
| `excerpt_mode` | 摘要模式：`"llm"` / `"infer"` | `"llm"` |
| `timeout` | API 请求超时秒数 | `30` |

### 3. 准备 Markdown 文件

把 `.md` 文件放入 `posts/` 目录（**支持子目录**），下面是完整示例：

```markdown
---
title: "文章标题"
slug: "custom-url"               # 可选，自定义 URL 路径
status: "publish"                # 可选，覆盖全局 default_status
date: "2025-06-01T09:00:00"     # 可选，留空=立即发布
categories:
  - 技术
tags:
  - Python
excerpt: "自定义摘要"             # 可选，留空自动生成
featured_image: posts/cover.jpg  # 可选，本地图片路径
---

# 正文从这里开始
```

**自动推断规则**（缺失字段的默认行为）：

| Front Matter 字段 | 文件里没写时的自动推断 |
|---|---|
| `title` | 文件名（去序号、替换 `-` `_`） |
| `slug` | 日期 + 序号（如 `20260517-003`） |
| `excerpt` | `excerpt_mode=llm`：LLM 生成 → `excerpt_mode=infer`：第一段提取 |
| `categories` | 子目录名（如 `posts/AI/xxx.md` → `AI`）。根目录下为空 |
| `featured_image` | 同目录同名图片文件 |
| `status` | `CONFIG["default_status"]` |
| `tags` | 空 |

**子目录支持**：`posts/` 下可以按主题、类别建立子目录：

```
posts/
├── AI/
│   ├── ai-agent.md
│   └── ai-agent.jpg            # 同名图片自动作为封面
├── 技术/
│   └── python-async.md          # 分类自动推断为 "技术"
└── baby.jpg
```

### 4. LLM 摘要生成

设置 `excerpt_mode: "llm"` 后，发布前由大模型自动生成摘要（与文章首段不同，更具吸引力）。

**三种切换方式**（`.env` 中的 `LLM_PROFILE`）：

| Profile | 说明 | 适用场景 |
|---------|------|---------|
| `auto` | 自动发现 `claude_code` 的 DeepSeek 配置（DPAPI 解密 API Key） | 已通过 `set.ps1` 配置过 Claude Code + DeepSeek |
| `local` | 本地 LM Studio / Ollama（无需 API KEY） | 有本地 GPU，离线使用 |
| `ds` | 手动填入 DeepSeek API Key | 纯 API 调用 |

**Profile=auto 无需任何额外配置**，自动读取 `%USERPROFILE%\.claude_code\config.json` 和 DPAPI 加密的 API Key。仅需确保 `excerpt_mode: "llm"`。

**Profile=local** 配置示例（`.env`）：

```ini
LLM_PROFILE=local
LLM_LOCAL_API_BASE=http://localhost:1234/v1
LLM_LOCAL_MODEL=qwen/qwen3.5-9b
LLM_LOCAL_MAX_TOKENS=3000
LLM_LOCAL_TIMEOUT=300
```

**Profile=ds** 手动配置示例：

```ini
LLM_PROFILE=ds
LLM_DS_API_BASE=https://api.deepseek.com/v1
LLM_DS_API_KEY=sk-your-deepseek-key
LLM_DS_MODEL=deepseek-chat
LLM_DS_MAX_TOKENS=500
LLM_DS_TIMEOUT=60
```

### 5. 运行

```bash
# 测试连接
python wp_auto_publisher.py test

# 查看待发布列表
python wp_auto_publisher.py list

# 预览转换结果（不实际发布，推荐先预览）
python wp_auto_publisher.py preview
python wp_auto_publisher.py preview "my-post.md"

# 立即发布一篇
python wp_auto_publisher.py publish
python wp_auto_publisher.py publish "my-post.md"

# 启动定时调度器（每天 09:00 发布一篇）
python wp_auto_publisher.py
```

### 6. 后台运行（Linux 服务器）

```bash
nohup python wp_auto_publisher.py > /dev/null 2>&1 &
```

---

## 目录结构

```
./
├── wp_auto_publisher.py    # 主脚本
├── .env                    # 敏感配置（不入库）
├── .gitignore
├── README.md
├── published.json          # 自动生成，已发布记录（不入库）
├── wp_publisher.log        # 自动生成，运行日志（不入库）
└── posts/                  # 放置 Markdown 文章（支持子目录）
    ├── AI/
    │   ├── ai-agent.md
    │   └── ai-agent.jpg    # 同名封面图
    ├── 技术/
    │   └── docker-guide.md
    └── baby.jpg
```

---

## 命令速查

| 命令 | 功能 |
|------|------|
| `python wp_auto_publisher.py` | 启动定时调度器 |
| `python wp_auto_publisher.py test` | 测试 WordPress 连接 |
| `python wp_auto_publisher.py list` | 列出待发布文章 |
| `python wp_auto_publisher.py preview` | 预览下一篇的转换结果 |
| `python wp_auto_publisher.py preview <文件>` | 预览指定文件 |
| `python wp_auto_publisher.py publish` | 发布下一篇 |
| `python wp_auto_publisher.py publish <文件>` | 发布指定文件 |
