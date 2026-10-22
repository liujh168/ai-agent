# Changelog

本项目所有重要变更均记录于此。格式参考 [Keep a Changelog](https://keepachangelog.com/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added（新增）
- **文档体系重组** `docs/`：按用途分三层目录
  - `docs/project/`：项目自身文档（设计决策清单、Skill 原理、API 接口、Config 参考、Quickstart、架构总览、Contributing、FAQ）
  - `docs/knowledge/`：外部知识源（RAG 扫描源，默认递归扫描此目录下 .md/.txt/.pdf）
  - `docs/blog/`：开发博客（WordPress 系列 5 篇：Function Calling 极简实现 → 单一真相源 → Skill 做成 Markdown → 安全三层防御 → Gold Case 评测框架）
- **设计决策清单** `docs/project/设计决策清单.md`：34 条设计决策（架构/安全/RAG/Skill/协议/体验/评测 7 类），每条含代码片段 + 权衡分析 + 演进方向
- **Config.rag_directories** `ai_agent/config.py`：新增 RAG 知识扫描目录配置项，默认 `("docs/knowledge",)`，支持 `RAG_DIRECTORIES` 环境变量分号分隔
- **WordPress 博客发布工具 `wp_publish`**（1478acf / ad15239 / 73fc31d）：薄包装 `skills/wordpress-publish/wp_auto_publisher.py`，支持 md_file 自动解析 Front Matter 与 title+content_html 直传两种模式；凭证经 Config 注入（wp_user 缺省时从应用密码解析）；自动加载 skill 目录 `.env` 且不覆盖已有环境变量；修复非 posts 目录下 md 文件解析失败
- **network 风险级** `ai_agent/tooling.py`：ToolPolicy 风险分类从四级扩为五级（read/write/execute/mcp/network），wp_publish / web_search / demo_vision 等外发工具归入 network；CLI 默认执行前人工确认，Web 公网分享模式整体禁用，eval 默认放行
- **CI 工作流** `.github/workflows/ci.yml`：push/PR 触发，matrix Python 3.10–3.13（Windows runner，锁文件含 pywin32），步骤为 pip 安装 → ruff → pytest
- **离线测试扩充至 101 条**：新增 Skill 解析/匹配、demos（ReAct 解析、structured_extract 重试）、wp_publish 全 mock 流程、MCP `_send` 错误分支与连接失败占位、eval HTML 报告模板渲染、路径穿越防护等测试文件
- **RAG FAISS 可选加速** `ai_agent/rag.py`：`faiss-cpu` 作为可选依赖（`pyproject.toml` 新增 `faiss` extra），chunk 数超过 `faiss_threshold`（构造参数，默认 2000）且 faiss 可用时用 L2 归一化向量构建 `IndexFlatIP` 内积索引，检索自动切 FAISS；否则静默回退纯 Python 余弦扫描，两条路径返回结构一致；FAISS 索引由向量缓存确定性重建，不进 `_rag_cache.json`；新增离线测试 `ai_agent/tests/test_rag_faiss.py`（embedding 全 mock）
- **Config.rag_faiss_threshold** `ai_agent/config.py`：FAISS 阈值接入配置（env `RAG_FAISS_THRESHOLD`，默认 2000），`validate()` 校验必须为正整数；`bootstrap.py` / `eval.py` 两处 `RAGKnowledgeBase` 实例化透传该值；eval 入口补 `cfg.validate()`，与 CLI/Web 一样启动即校验配置
- **MCP STDIO Transport** `ai_agent/mcp_client.py`：新增 `MCPClientSTDIO`（子进程 + 换行分隔 JSON-RPC，initialize / tools/list / tools/call 与 HTTP 客户端同款接口，server 往 stdout 打的非 JSON 噪声行自动跳过，stderr 透传，`close()` / `__del__` 兜底终止子进程）；`MCPManager` 按 `stdio:` 前缀分派 Transport（如 `MCP_SERVERS=stdio:python -m my_mcp_server`），可接入 Desktop Commander / Filesystem 等本地 server，工具注册沿用 `mcp_` 前缀约定；新增 `ai_agent/tests/test_mcp_stdio.py`（内嵌假 server 子进程，离线 7 条）
- **交互课件中心** `web_courses/` + `ai_agent/static/courses/` + `ai_agent/web_ui.py`：Vue3 单页应用源码收进仓库（Vite + TypeScript，hash 路由），`deploy-to-static.ps1` / `.sh` 一键构建并同步产物到 `ai_agent/static/courses/`；经顶层「🎓 交互课件中心」页签 iframe 嵌入，launch 后挂载 `StaticFiles` 到 `/courses/`（须在 launch 之后挂载——launch 内部重建 FastAPI app）。四门课程：MCP 交互演示（11 步完整报文回放）、Function Calling 循环与安全门（含人工确认三分支决策）、上下文压缩×RAG 检索模拟器（可调参）、Skill 路由 / ReAct 双栏对照；每门课含机制要点与「对照真实源码」折叠区（课件步骤 → 真实函数对照表 + 项目源码原文，经 `?raw` 打包，`src/real/` 为项目源码快照，源码演进后需重新同步）
- **零依赖教学 demo 脚本** `ai_agent/demos/`：新增 `mcp_stdio_demo/`（stdio 版 MCP server+client，纯标准库实现 JSON-RPC 全会话）、`fc_safety_demo.py`（脚本桩 LLM + 五级风险人工确认，输入 y/n 体验确认/拒绝分支）、`context_rag_demo.py`（压缩触发演示 + 主题示意向量余弦检索）、`react_vs_fc_demo.py`（Skill 关键词路由 + ReAct/FC 双协议对照），全部不依赖 LM Studio 可直接运行
- **文档 ↔ 课件 ↔ 源码学习路径贯通**：`docs/blog/` 5 篇相关文章与 `docs/project/MCP客户端协议拆解.md` 顶部新增 🎓 课件指引（精确到课程 hash 路由与对照符号）；README 新增「学习路径指引」一节（四步闭环 + 课程总览表）并明确 CLI/Web UI 分工

### Changed（变更）
- **多 Agent Executor 接入工具调用** `ai_agent/multi_agent.py` / `app_core.py` / `web_ui.py`：`run_multi_agent` 新增可选 `tools`/`tool_map`/`tool_executor` 参数，Executor 执行与修正子任务统一走 `_executor_chat`——传入工具集时委托 `chat_with_tools` 复用 `AgentRuntime` 的 FC 循环（零复制），工具调用过程经 `on_event` 转成 trace 行，工具结果随最终回复纳入子任务产出；不传时保持纯 `client.chat` 旧行为。两处装配点（FC 工具闭包、Web 模式菜单）均传入经 `clean_react_tool_map` 清洗并剔除 `run_multi_agent` 自身的工具集（防递归），Web 侧复用同一 `ToolExecutor` 安全策略；新增 `ai_agent/tests/test_executor_tools.py`（mock client + 真实 FC 循环，3 条）
- **RAG 初始化改为目录扫描** `main.py` / `ai_agent/web_ui.py` / `ai_agent/eval.py`：从硬编码单文件路径改为遍历 `cfg.rag_directories` 下全部 `.md/.txt/.pdf` 文件，新增知识源放入 `docs/knowledge/` 即自动纳入索引
- **根目录 README.md 更新**：补全新增模块（config.py / skill.py / mcp_client.py）、配置表格补 rag_directories、项目结构树更新为 docs 三层结构
- **修复文件名尾部空格**：`docs/大语言模型能力说明 .md` → `docs/knowledge/大语言模型能力说明.md`
- **client.py 拆分**：演示级 Agent 算法（ReAct / 思维链 / 原生推理 / 反思 / 规划 / 结构化提取）迁至 `ai_agent/demos/` 包，`LMStudioClient` 保留同名薄委托方法，对外 API 不变
- **build_tool_map 工厂化** `ai_agent/app_core.py`：158 行嵌套闭包拆为 `_build_workspace_tools` / `_build_demo_tools` / `_build_wp_tools` 三个小工厂，build_tool_map 只做组装，行为逐行等价
- **评测 HTML 模板外置** `ai_agent/eval_report_template.html`：`_generate_html_report` 的内嵌模板字符串改为 string.Template 占位符文件，pyproject 增加 package-data 打包配置
- **CLI 会话命令表分发** `main.py`：`_try_session_command` 的 128 行 if/elif 改为命令表 + 每命令独立 handler 方法
- **配置收口** `ai_agent/config.py`：新增幂等 `load_env()`，main.py / web_ui.py / eval.py / wordpress.py 四处 .env 加载统一调用；`Config.history_max_chars`（env `HISTORY_MAX_CHARS`）收口 CLI/Web 两处压缩阈值；数值型环境变量转换失败时给出带变量名与原始值的友好报错；`LMStudioClient` 不再直读 `os.environ`，统一走构造参数
- **依赖补齐与锁文件重生**：markdown / python-frontmatter / schedule 进入 `[project] dependencies`（修复全新安装后 wp_publish 必 ImportError）；`requirements.lock` / `requirements-dev.lock` 改用 pip-compile 重新生成（去除本机绝对路径与私有服务器地址）；requires-python 放宽至 `>=3.10,<3.14`
- **重复代码消除**：RAG 目录扫描抽为 `rag.collect_rag_files()`；测试图生成抽为 `app_core.ensure_test_image()`；压缩+持久化抽为 `app_core.compress_and_persist_history()`；Skill 注册统一走 `skill.create_skill_registry()`；删除 `tools/__init__.py` 中与 TOOL_SCHEMAS 重复的 9 个过期 schema 常量

### Fixed（修复）
- **外发工具路径校验**：`analyze_image` 与 `wp_publish` 的 md_file 模式均限制在工作区内（复用 `resolve_workspace_path`），阻断 LLM 传任意路径读本地文件
- **评测路径工具注册对齐**：eval.py 构建 tool_map 时传入 history 访问器，`summarize_history` 不再出现"schema 存在但工具缺失"
- **删除死代码 `chat_with_skill`** `ai_agent/client.py`：无调用方（Skill 路由实际在 `runtime.py` 的 AgentRuntime 内），同步修正三处文档/注释的过期描述
- **`MCPError` 改为 Exception 子类** `ai_agent/mcp_client.py`：修复普通 dataclass 被 raise 时实际抛 TypeError 的潜伏 bug；新增 `MCPManager.close_all()` 并在 Web 入口退出路径调用；`_server_index` / `_tools` 私有属性收口为公开成员
- **异常收窄与日志**：persistence.close / rag PDF 抽页 / mcp ping / sql 查询 / RAG 初始化 / 测试图生成等 6 处裸 `except Exception` 收窄类型并补日志
- **assert 控制流修正**：fs.py / sql.py / wordpress.py 的 `assert ... is not None` 改显式错误返回（`python -O` 下 assert 失效）
- **AgentRuntime 私有穿透收口**：`client.client.chat.completions` 两层私有访问改为 `LMStudioClient.create_chat_completion()` 公开委托
- **SYSTEM_PROMPT 措辞修正**：run_terminal 的关键词拦截降级为"辅助提示"，明确真正的闸门是默认禁用 + 执行前人工确认
- **仓库卫生**：example.db / greet.txt / eval_report_run.html / .workbuddy 移出版本控制，.gitignore 补 `*.db` / `eval_report*.html` / `.workbuddy/`；.env.example 与 config.py 注释中的个人域名改为 example.com

---

## [0.1.0] - 2026-09-17

首个里程碑版本。基于上游大型重构（client/main/web_ui 多文件改造）拉取同步后，完成依赖固化、全量测试、自动化评测、两处代码缺陷修复，并补齐新手学习资料与安全/工程化约定。

### Added（新增）
- **新手可交互学习路线图** `docs/ai-agent-learning-roadmap.html`：7 阶段由浅入深（预备知识 → 提示词 → 工具调用 → 推理范式 → RAG → 多智能体 → 工程化评测 → 安全上线），每步绑定本项目真实代码作为活教材。
  - 进度条 + 勾选完成，localStorage 自动持久化
  - 阶段手风琴展开/收起、顶部 stepper 跳转
  - 关键词搜索 + 标签筛选
  - 起步自测：按基础打分推荐起点
- **项目记忆体系** `.workbuddy/memory/`：记录依赖安装坑（requires-python 约束）、系统代理劫持 localhost 的环境坑、评测结论与修复记录，供后续会话复用。
- **里程碑标签** `v0.1.0`，已同时推送至 `www`（VPS）与 `github`（私人仓库 `liujh168/ai-agent`）。

### Changed（变更）
- **依赖固化**：引入 `requirements.lock` / `requirements-dev.lock` 锁文件，明确全部运行时与开发依赖版本；测试运行环境统一到 managed python venv（Python 3.13.x）。
- **`web_search` 失败降级增强** `ai_agent/tools/search.py`：博查（Bocha）API 调用失败时自动回退 DuckDuckGo 并返回真实结果，不再直接放弃导致搜索步骤空手。
- **提交方式回归常规** `git commit`：移除了基于「Defender 拦截 git.exe 写对象」这一错误前提的自定义提交脚本（实测 `git.exe` 可正常写对象，无需绕过）。

### Fixed（修复）
- **`get_weather` 参数名兼容** `ai_agent/tools/demo.py`：原签名仅收 `city`，模型在 ReAct 中常猜 `location` 触发 `TypeError`。改为兼容 `location` 别名（`target = city or location`），空时返回提示而非崩溃。
- **移除废弃 API 用法** `ai_agent/tools/demo.py`：删除 Python 3.8+ 已废弃的 `ast.Num` 分支（由 `ast.Constant` 覆盖），消除 pytest 告警。
- **评测环境代理坑**：本机系统代理 `HTTP_PROXY=http://127.0.0.1:5849` 会劫持 localhost 请求（直连 LM Studio 返回 502 假响应）。评测/直连须清空大小写全部代理变量并以 `NO_PROXY=localhost,127.0.0.1` 绕开；破坏性用例须以 `stdin` 接 `/dev/null` 避免交互确认卡住。

### Removed（移除）
- **`scripts/git_commit.py`**：冗余的自定义提交脚本（详见 Changed 说明），常规 `git commit` 已可正常工作。
- **旧评测报告产物** `eval_report*.html`（多个历史版本）：清理工作区，仅保留本轮 `eval_report_run.html` 作为 18/18 PASS 的留档。

### 验证结果
- **全量单元测试**：`pytest` **58 passed, 7 warnings**，约 7s。
- **自动化评测** `ai_agent.eval`：模型 `qwen/qwen3.6-35b-a3b`（本地 LM Studio，CPU 推理），**18/18 PASS，通过率 100%**。
  - 覆盖维度：tool_routing(5) / chat(2) / react(2) / slow_integration(2) / calculator_security / few_shot / reasoning / reflexion / sql_security / structured / vision 各 1，全部满分。
  - 安全行为校验正确：`DROP TABLE` 触发人工确认并被拦截；`run_terminal` 默认关闭。

[Unreleased]: https://github.com/liujh168/ai-agent/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/liujh168/ai-agent/releases/tag/v0.1.0
