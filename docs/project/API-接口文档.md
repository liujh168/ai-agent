# API 接口文档 — Tool Schema 详解

> 本项目共 **21 个 Function Calling 工具**，定义在 `ai_agent/app_core.py` 的 `TOOL_SCHEMAS` 列表中。
> 本文档按功能分组，每个工具标注：参数、风险等级、源码位置、调用示例。

---

## 目录

- [演示类工具（10）](#演示类工具10)
- [文件操作（2）](#文件操作2)
- [搜索与知识（2）](#搜索与知识2)
- [SQL 查询（1）](#sql-查询1)
- [代码/终端执行（2）](#代码终端执行2)
- [元工具（4）](#元工具4)
- [MCP 远端工具（动态）](#mcp-远端工具动态)

---

## 演示类工具（10）

### get_weather

- **描述**：查询指定城市的天气（模拟数据，返回固定格式）
- **参数**：
  | 字段 | 类型 | 必填 | 说明 |
  |------|------|------|------|
  | `city` | string | ✅ | 城市名称，如"北京"、"上海" |
- **风险等级**：read
- **源码**：`tools/demo.py` + `app_core.py#L101-L110`
- **调用示例**：`{"name": "get_weather", "arguments": {"city": "北京"}}`

### calculator

- **描述**：执行数学表达式计算（四则运算 + 幂 + 取模），使用 AST 白名单校验
- **参数**：
  | 字段 | 类型 | 必填 | 说明 |
  |------|------|------|------|
  | `expression` | string | ✅ | 数学表达式，如 `3+5*2`、`2**10`、`(15+27)*3` |
- **风险等级**：read
- **安全机制**：`ast.parse()` + 白名单节点（`BinOp` / `Constant` / `UnaryOp`），拒绝 `Call` / `Name` / `Attribute`，指数绝对值 > 1000 额外防护
- **源码**：`tools/demo.py` + `app_core.py#L115-L125`
- **调用示例**：`{"name": "calculator", "arguments": {"expression": "(15+27)*3"}}`

### demo_stream_chat

- **描述**：流式对话演示，边生成边逐 token 输出
- **参数**：
  | 字段 | 类型 | 必填 | 说明 |
  |------|------|------|------|
  | `topic` | string | ✅ | 对话主题 |
- **风险等级**：read
- **源码**：`app_core.py` 闭包（L493-L498）
- **调用示例**：`{"name": "demo_stream_chat", "arguments": {"topic": "什么是 RAG"}}`

### demo_reasoning

- **描述**：Chain-of-Thought 推理演示（让模型"一步一步想"）
- **参数**：
  | 字段 | 类型 | 必填 | 说明 |
  |------|------|------|------|
  | `question` | string | ✅ | 需要推理的问题 |
- **风险等级**：read
- **源码**：`app_core.py` 闭包（L500-L501）

### demo_native_reasoning

- **描述**：原生推理演示（用模型内置 thinking token）
- **参数**：
  | 字段 | 类型 | 必填 | 说明 |
  |------|------|------|------|
  | `question` | string | ✅ | 需要推理的问题 |
- **风险等级**：read
- **源码**：`app_core.py` 闭包（L503-L504）

### demo_react

- **描述**：ReAct 推理范式演示（Thought → Action → Observation → Final Answer），调用时会用"清洗后的 tool_map"
- **参数**：
  | 字段 | 类型 | 必填 | 说明 |
  |------|------|------|------|
  | `question` | string | ✅ | 需要 ReAct 推理的问题 |
- **风险等级**：read
- **特殊**：内部执行时调用 `clean_react_tool_map()` 剔除内部演示工具（防止 ReAct 自递归）
- **源码**：`app_core.py` 闭包（L506-L512）+ `client.chat_with_react()`

### demo_code_run

- **描述**：代码执行演示（执行简短 Python 片段）
- **参数**：
  | 字段 | 类型 | 必填 | 说明 |
  |------|------|------|------|
  | `code` | string | ✅ | 要执行的 Python 代码 |
  | `timeout` | integer | ❌ | 超时秒数，默认 10 |
- **风险等级**：execute（ToolPolicy 拦截）
- **源码**：`tools/system.py` + `app_core.py#L314`

### demo_few_shot

- **描述**：Few-Shot 学习演示（分类/提取任务，提供示例）
- **参数**：
  | 字段 | 类型 | 必填 | 说明 |
  |------|------|------|------|
  | `question` | string | ✅ | 待处理文本 |
  | `task_type` | string | ❌ | 任务类型，默认 `sentiment`，可选 `sentiment` / `classification` / `extraction` |
- **风险等级**：read
- **源码**：`app_core.py` 闭包（L535-L555）

### reflexion_answer

- **描述**：Reflexion 自我反思演示（模型先回答再自我批判再修正）
- **参数**：
  | 字段 | 类型 | 必填 | 说明 |
  |------|------|------|------|
  | `question` | string | ✅ | 需要反思回答的问题 |
- **风险等级**：read
- **源码**：`app_core.py` 闭包（L523-L527）+ `client.chat_with_reflexion()`

### demo_vision

- **描述**：图像理解演示（描述一张图片的内容）
- **参数**：
  | 字段 | 类型 | 必填 | 说明 |
  |------|------|------|------|
  | `image_path` | string | ❌ | 图片路径，默认 `_test_chart.png` |
  | `question` | string | ❌ | 关于图片的问题 |
- **风险等级**：read
- **前置条件**：模型需支持 vision（多模态）；默认工作区有 Pillow 生成的测试图
- **源码**：`app_core.py` 闭包（L529-L534）+ `client.analyze_image()`

---

## 文件操作（2）

### file_read

- **描述**：读取指定文件内容（有 max_chars 限制 + workspace_root 安全边界）
- **参数**：
  | 字段 | 类型 | 必填 | 说明 |
  |------|------|------|------|
  | `path` | string | ✅ | 文件路径（相对 workspace_root） |
  | `max_chars` | integer | ❌ | 最多读取字符数，默认 3000 |
- **风险等级**：read
- **安全机制**：路径需在 `AGENT_WORKSPACE_ROOT` 内；超长内容会被截断
- **源码**：`tools/fs.py`（workspace_root 绑定在 `build_tool_map()` 闭包中）+ `app_core.py#L144-L152`

### file_write

- **描述**：写入文件（新建或覆盖）
- **参数**：
  | 字段 | 类型 | 必填 | 说明 |
  |------|------|------|------|
  | `path` | string | ✅ | 文件路径（相对 workspace_root） |
  | `content` | string | ✅ | 要写入的文本内容 |
  | `mode` | string | ❌ | 写入模式，默认 `write`，可选 `write` / `append` |
- **风险等级**：write
- **安全机制**：路径需在 `AGENT_WORKSPACE_ROOT` 内；ToolPolicy 可全局禁用 write
- **源码**：`tools/fs.py` + `app_core.py#L159-L172`

---

## 搜索与知识（2）

### web_search

- **描述**：互联网搜索（优先博查 AI 搜索 API，未配 BOCHA_API_KEY 时降级 DuckDuckGo）
- **参数**：
  | 字段 | 类型 | 必填 | 说明 |
  |------|------|------|------|
  | `query` | string | ✅ | 搜索关键词 |
- **风险等级**：read
- **降级**：博查不可用时自动 DuckDuckGo，返回真实搜索结果
- **源码**：`tools/search.py` + `app_core.py#L129-L143`

### search_knowledge

- **描述**：查询本地 RAG 知识库（docs/knowledge/ 下的 .md/.txt/.pdf）
- **参数**：
  | 字段 | 类型 | 必填 | 说明 |
  |------|------|------|------|
  | `query` | string | ✅ | 知识库查询（自然语言） |
- **风险等级**：read
- **前置条件**：RAG 已初始化（`--no-rag` 评测时不存在此工具）
- **源码**：`app_core.py` 闭包（L514-L519）+ `rag.py` RAGKnowledgeBase.search()

---

## SQL 查询（1）

### sql_query

- **描述**：SQLite 查询（只读，demo_seed=True 时自动建表演示数据）
- **参数**：
  | 字段 | 类型 | 必填 | 说明 |
  |------|------|------|------|
  | `db_path` | string | ✅ | SQLite 数据库路径 |
  | `sql` | string | ✅ | 要执行的 SQL |
  | `demo_seed` | boolean | ❌ | 是否首次调用时建表演示数据，默认 true |
- **风险等级**：write（但内部物理只读，实际 execute 类拦截）
- **安全机制**：SQLite URI `mode=ro` + `PRAGMA query_only=ON` 双重只读，DDL/DML 在引擎层被拒绝
- **源码**：`tools/sql.py` + `app_core.py#L179-L200`

---

## 代码/终端执行（2）

### run_terminal

- **描述**：执行一条系统终端命令（subprocess）
- **参数**：
  | 字段 | 类型 | 必填 | 说明 |
  |------|------|------|------|
  | `command` | string | ✅ | 要执行的命令 |
  | `timeout` | integer | ❌ | 超时秒数，默认 10 |
  | `allow_unsafe` | boolean | ❌ | 自动注入为 True（ToolExecutor 判定 execute 风险后） |
- **风险等级**：execute
- **安全机制**：关键词黑名单（~40 条危险模式如 `Remove-Item -Recurse -Force`）；CLI 默认禁用，需 `--enable-dangerous-tools`
- **源码**：`tools/system.py` + `app_core.py#L201-L218`

### run_code（demo_code_run）

- **描述**：执行简短 Python 代码
- **参数**：
  | 字段 | 类型 | 必填 | 说明 |
  |------|------|------|------|
  | `code` | string | ✅ | 要执行的 Python 代码 |
  | `timeout` | integer | ❌ | 超时秒数，默认 10 |
  | `allow_unsafe` | boolean | ❌ | 自动注入为 True（ToolExecutor 判定 execute 风险后） |
- **风险等级**：execute
- **源码**：`tools/system.py`（run_code）+ `app_core.py`（demo_code_run wrapper）

---

## 元工具（4）

### plan_task

- **描述**：把复杂任务拆解为有序子任务（LLM 内部先规划再执行）
- **参数**：
  | 字段 | 类型 | 必填 | 说明 |
  |------|------|------|------|
  | `task` | string | ✅ | 要拆解的复杂任务描述 |
- **风险等级**：read
- **源码**：`app_core.py` 闭包（L529-L530）+ `client.plan_and_execute()`

### extract_structured

- **描述**：从非结构化文本中提取指定字段（LLM 输出 JSON，失败自动重试）
- **参数**：
  | 字段 | 类型 | 必填 | 说明 |
  |------|------|------|------|
  | `text` | string | ✅ | 原始文本 |
  | `fields` | string | ✅ | 要提取的字段列表，JSON 字符串如 `{"name": "string", "age": "integer"}` |
- **风险等级**：read
- **源码**：`app_core.py` 闭包（L532-L533）+ `client.structured_extract()`

### run_multi_agent

- **描述**：启动多 Agent 协作（Planner 规划 → Executor 逐个执行 → Critic 评审）
- **参数**：
  | 字段 | 类型 | 必填 | 说明 |
  |------|------|------|------|
  | `task` | string | ✅ | 整体任务描述 |
- **风险等级**：read
- **源码**：`app_core.py` 闭包（L556-L570）+ `multi_agent.run_multi_agent()`

### human_confirm

- **描述**：Human-in-the-loop 人工确认。执行敏感/破坏性操作前暂停等待用户确认
- **参数**：
  | 字段 | 类型 | 必填 | 说明 |
  |------|------|------|------|
  | `action` | string | ✅ | 描述即将执行的操作（展示给用户） |
- **风险等级**：read
- **实现差异**：CLI 用 `input()` 同步阻塞；Web UI 用 Gradio queue + 事件回调暂停执行
- **源码**：`tools/system.py`（human_confirm CLI）+ `web_ui.py`（_web_human_confirm）+ `app_core.py#L219-L231`

---

## 系统级（条件注册）

### summarize_history

- **描述**：长对话自动摘要压缩（history 总字符超 5000 时触发，保留最近 4 条 + LLM 摘要）
- **参数**：无（执行时自动调用）
- **注册条件**：`build_tool_map()` 传入了 `get_history` / `set_history` 访问器时才注册
- **风险等级**：read
- **源码**：`app_core.py` `make_summarize_history()`

---

## MCP 远端工具（动态）

MCPManager 连接的远端服务器上的工具会自动追加到 tool_map：

- **工具名**：`mcp_{server_idx}_{safe_name}[_{suffix}]`（server 索引 + 原始工具名清洗 + 冲突时后缀递增）
- **风险等级**：`mcp`（ToolPolicy 独立分类，支持 `mcp_allowlist` 白名单）
- **源码**：`mcp_client.py` MCPManager + MCPClient

---

## Tool Schema 总数验证

```bash
python -c "from ai_agent.app_core import TOOL_SCHEMAS; print(len(TOOL_SCHEMAS))"
# 预期输出：21
```

注意：`summarize_history` 和 MCP 工具**不在** TOOL_SCHEMAS 里（条件注册 / 动态追加），实际可用工具数 = 21（基础）+ MCP（动态）+ summarize_history（条件）。
