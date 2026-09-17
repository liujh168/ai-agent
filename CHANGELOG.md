# Changelog

本项目所有重要变更均记录于此。格式参考 [Keep a Changelog](https://keepachangelog.com/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

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

[0.1.0]: https://github.com/liujh168/ai-agent/releases/tag/v0.1.0
