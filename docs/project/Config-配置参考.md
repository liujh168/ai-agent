# Config 配置参考

> 所有运行时配置集中在 `ai_agent/config.py` 的 frozen dataclass `Config` 中。
> 默认值可被环境变量覆盖（`Config.from_env()` 自动读取）。

---

## Config 字段一览

| 字段 | 环境变量 | 默认值 | 类型 | 说明 |
|------|---------|--------|------|------|
| `base_url` | `LM_STUDIO_BASE_URL` | `http://localhost:1234/v1` | str | LM Studio OpenAI 兼容 API 地址 |
| `api_key` | `LM_STUDIO_API_KEY` | `lm-studio` | str | API key（本地服务可任意填） |
| `model` | `LM_STUDIO_MODEL` | `auto` | str | 模型 ID；`auto` 自动选择第一个聊天模型 |
| `embed_model` | `EMBED_MODEL` | `text-embedding-nomic-embed-text-v1.5` | str | RAG embedding 模型 ID |
| `chunk_size` | `RAG_CHUNK_SIZE` | `500` | int | RAG 分块字符数 |
| `chunk_overlap` | `RAG_CHUNK_OVERLAP` | `50` | int | RAG 分块重叠字符数 |
| `rag_cache_file` | `RAG_CACHE_FILE` | `_rag_cache.json` | str | RAG 缓存文件路径（相对 project_root） |
| `project_root` | `PROJECT_ROOT` | 自动检测项目根 | str | 项目根目录（默认自动从文件位置推导） |
| `workspace_root` | `AGENT_WORKSPACE_ROOT` | 同 project_root | str | 文件工具允许访问的根目录 |
| `request_timeout` | `LM_STUDIO_TIMEOUT` | `120.0` | float | 模型请求超时秒数 |
| `enable_dangerous_tools` | `ENABLE_DANGEROUS_TOOLS` | `false` | bool | 环境变量方式启用危险工具 |
| `mcp_servers` | `MCP_SERVERS` | `()` | tuple[str,...] | MCP 服务器 URL 列表；环境变量中分号分隔 |
| `mcp_allowed_tools` | `MCP_ALLOWED_TOOLS` | `()` | tuple[str,...] | Web UI 允许的 MCP 工具；分号分隔 |
| `skill_dir` | `SKILL_DIR` | `skills` | str | Skill Markdown 定义目录（为空或不存在则跳过） |
| `rag_directories` | `RAG_DIRECTORIES` | `("docs/knowledge",)` | tuple[str,...] | RAG 知识扫描目录；分号分隔；递归扫描 .md/.txt/.pdf |
| `wp_url` | `WP_URL` | `""` | str | WordPress 站点根地址（如 `https://blog.liujh18.com`），供 wordpress-publish Skill 使用 |
| `wp_user` | `WP_USER` | `""` | str | WordPress 用户名（可选）；为空时自动从 WP_APP_PASSWORD 格式 `username xxxx` 空格拆分取第一段 |
| `wp_app_password` | `WP_APP_PASSWORD` | `""` | str | WordPress Application Password（后台用户页生成），Basic Auth 用 |

---

## 按功能分组

### 模型服务连接

| 字段 | 环境变量 | 默认值 |
|------|---------|--------|
| `base_url` | `LM_STUDIO_BASE_URL` | `http://localhost:1234/v1` |
| `api_key` | `LM_STUDIO_API_KEY` | `lm-studio` |
| `model` | `LM_STUDIO_MODEL` | `auto` |
| `request_timeout` | `LM_STUDIO_TIMEOUT` | `120.0` |

**典型 .env 配置**：

```bash
# 模型服务
LM_STUDIO_BASE_URL=http://localhost:1234/v1
LM_STUDIO_MODEL=qwen/qwen3.5-9b
LM_STUDIO_TIMEOUT=120
```

### RAG 知识库

| 字段 | 环境变量 | 默认值 |
|------|---------|--------|
| `embed_model` | `EMBED_MODEL` | `text-embedding-nomic-embed-text-v1.5` |
| `chunk_size` | `RAG_CHUNK_SIZE` | `500` |
| `chunk_overlap` | `RAG_CHUNK_OVERLAP` | `50` |
| `rag_cache_file` | `RAG_CACHE_FILE` | `_rag_cache.json` |
| `rag_directories` | `RAG_DIRECTORIES` | `docs/knowledge` |

**多目录示例**：

```bash
RAG_DIRECTORIES=docs/knowledge;data/papers;notes/reading
```

### 文件系统

| 字段 | 环境变量 | 默认值 |
|------|---------|--------|
| `project_root` | `PROJECT_ROOT` | 自动 |
| `workspace_root` | `AGENT_WORKSPACE_ROOT` | 同 project_root |

**限制文件操作范围**：

```bash
AGENT_WORKSPACE_ROOT=C:/Users/me/Documents/agent_workspace
```

### MCP 扩展（可选）

| 字段 | 环境变量 | 默认值 |
|------|---------|--------|
| `mcp_servers` | `MCP_SERVERS` | 空 |
| `mcp_allowed_tools` | `MCP_ALLOWED_TOOLS` | 空 |

**启用 MCP**：

```bash
MCP_SERVERS=https://mcp.example.com/tools;http://localhost:9000/sse
MCP_ALLOWED_TOOLS=mcp_0_query;mcp_0_list
```

### WordPress 博客发布（可选）

| 字段 | 环境变量 | 默认值 |
|------|---------|--------|
| `wp_url` | `WP_URL` | `""` |
| `wp_app_password` | `WP_APP_PASSWORD` | `""` |

**使用步骤**：

1. 登录 WordPress 后台 → 用户 → 个人资料 → 滚动到 **Application Passwords**
2. 输入应用名（如 `ai-agent`）→ 添加新应用密码 → 复制生成的密码（格式：`username xxxx xxxx xxxx xxxx`）
3. `.env` 里填：

```bash
WP_URL=https://blog.liujh18.com
WP_APP_PASSWORD=admin abc1 def2 ghi3 jkl4
```

4. 启动 Agent，触发词"投稿"/"发博客" → `wordpress-publish` Skill 自动激活

### Skill 与安全

| 字段 | 环境变量 | 默认值 |
|------|---------|--------|
| `skill_dir` | `SKILL_DIR` | `skills` |
| `enable_dangerous_tools` | `ENABLE_DANGEROUS_TOOLS` | `false` |

---

## CLI 独有参数

`python main.py` 的 argparse 参数（不通过 Config dataclass，直接 argparse）：

| 参数 | 说明 | 默认 |
|------|------|------|
| `--enable-dangerous-tools` | 显式启用 `run_terminal` / `run_code` | 不传则用 `ENABLE_DANGEROUS_TOOLS` 配置 |

> 危险工具开关统一由 `ai_agent/bootstrap.py` 的 `resolve_dangerous_tools()` 裁决，优先级：
> 强制禁用（如 Web `--share`）> CLI flag 显式传入 > `Config.enable_dangerous_tools`。

---

## Web UI 独有参数

`python -m ai_agent.web_ui` 的 argparse 参数：

| 参数 | 说明 | 默认 |
|------|------|------|
| `--host` | 绑定地址 | `127.0.0.1` |
| `--port` | 端口 | `7860` |
| `--share` | 生成公网分享链接（启用时安全策略最严：禁 write / execute / MCP） | `false` |

---

## 评测独有参数

`python -m ai_agent.eval` 的 argparse 参数：

| 参数 | 说明 | 默认 |
|------|------|------|
| `--list` | 列出所有评测用例 | — |
| `--tag TAG` | 只跑指定 capability 标签的用例 | — |
| `--no-rag` | 跳过 RAG 用例（不加载知识库） | `false` |
| `--enable-dangerous-tools` | 允许 execute 风险用例 | `false` |
| `--html-report PATH` | 生成 HTML 评测报告 | — |

---

## 代码访问

```python
from ai_agent.config import Config

cfg = Config.from_env()
print(cfg.base_url)           # "http://localhost:1234/v1"
print(cfg.rag_directories)    # ('docs/knowledge',)
print(cfg.mcp_servers)        # () — 空元组

# 完全手动构造（不读环境变量）
cfg = Config(base_url="http://localhost:11434/v1", model="llama3")
```

Config 是 frozen dataclass（不可变），修改字段需构造新实例：

```python
new_cfg = Config.from_env().__class__(**{**Config.from_env().__dict__, "model": "qwen3.6-35b"})
```
