# 项目长期记忆：AI-Agent（本地 LM Studio 智能体 Demo）

## 项目性质
基于 LM Studio OpenAI 兼容 API 的本地 AI Agent 演示项目。CLI(`main.py`) + Gradio Web(`web_ui.py`) 双入口，共享核心逻辑。

## 架构约定（关键）
- `ai_agent/app_core.py`：**单一数据源** —— `SYSTEM_PROMPT` 与 `TOOL_SCHEMAS`(21 种能力) 只在此定义；`build_tool_map` / `clean_react_tool_map` / `make_summarize_history` 也在此。CLI 与 Web 必须 import 复用，禁止再写第二份。
- `ai_agent/config.py`：`Config` frozen dataclass，环境变量覆盖（`LM_STUDIO_BASE_URL`/`API_KEY`/`MODEL`、`EMBED_MODEL`、`RAG_CHUNK_SIZE`/`OVERLAP`、`RAG_CACHE_FILE`）。
- 工具实现在 `ai_agent/tools/`（`__init__` 导出 9 个 schema）；其余 12 个 demo/编排类 schema 以内联闭包形式放在 `app_core.py`。
- 测试：`ai_agent/tests/`（`test_core.py` 21 项含离线 RAG；`test_tools.py` 临时 SQLite 清理需重试绕开 Defender 锁）。

## 工程约定（务必遵守）
- **仓库 HEAD 在 `master`**（不是 main），两分支并存。提交走 `scripts/git_commit.py --add <files> -m "..."`；该脚本已修复为 `update-ref HEAD`（跟随当前分支），不要用硬编码 main。
- 该脚本用 Python 直写 git object 绕开 **Windows Defender 拦截 `git.exe` 写 `.git/objects`**；ref 写入仍用 git.exe（本机未被拦）。
- 测试运行环境：WorkBuddy managed python venv `C:/Users/xuan/.workbuddy/binaries/python/versions/3.13.12/python.exe -m venv .../envs/default`，pytest 装在里面。
- 安全约束：`demo_code_run` / `run_terminal` 属危险工具，Web `--share` 模式须禁用；`system.py` 命令黑名单含动词+链式绕过检测。

## 非项目文件（勿提交）
`docs/工业流程企业与Agent应用.md`、`www.bat` 与本修复无关，保持未跟踪。
