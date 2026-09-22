# FAQ · 故障排查

> 按故障类型组织，每条含"现象 → 原因 → 解决"三栏。
> 覆盖频率：⭐ = 偶发，⭐⭐ = 常见，⭐⭐⭐ = 高频

---

## 一、启动失败

### LM Studio 连不上

| 项 | 内容 |
|----|------|
| 现象 | CLI 启动后打印 "无法连接到 LM Studio 服务" / Web UI 一直转圈 |
| 原因 | ① LM Studio 没启动；② base_url 不对；③ 系统代理劫持 localhost 请求 |
| 解决 | ① 浏览器打开 `http://localhost:1234/v1/models` 确认有返回；② `.env` 检查 `LM_STUDIO_BASE_URL`；③ 如果开了系统代理（HTTP_PROXY=...:5849 之类），评测/直连前清空并设 `NO_PROXY=localhost,127.0.0.1` |
| 频率 | ⭐⭐⭐ |

### 只有 embedding 模型

| 项 | 内容 |
|----|------|
| 现象 | LM Studio 服务能连通，但 Agent 提示"未发现可用的聊天模型" |
| 原因 | LM Studio 只加载了 embedding 模型（如 nomic-embed-text），聊天模型处于 "Downloaded" 但没 "Loaded" |
| 解决 | LM Studio 主界面 → Models → 手动把聊天模型（qwen3.5-9b 等）切到 Loaded 状态；或改配置 `LM_STUDIO_MODEL=qwen3.5-9b-instruct` |
| 频率 | ⭐⭐ |

### `pip install` 报 requires-python

| 项 | 内容 |
|----|------|
| 现象 | `ERROR: Package 'xxx' requires Python '>=3.10' but you have Python '3.9'` |
| 原因 | Python 版本低于项目依赖的最低要求 |
| 解决 | 装 Python 3.10+（推荐 3.12 LTS），用 pyenv / uv 管理版本；Windows 直接 python.org 下载安装 |
| 频率 | ⭐ |

### Gradio 版本冲突

| 项 | 内容 |
|----|------|
| 现象 | `ImportError: cannot import name 'XXX' from 'gradio'` |
| 原因 | Gradio 5.x vs 4.x API 差异大，项目锁定 5.50.0 |
| 解决 | `pip install -r requirements.lock` 严格版本；不要随便 `pip install gradio --upgrade` |
| 频率 | ⭐⭐ |

---

## 二、Agent 不调工具

### 模型没有 tool_calls finish_reason

| 项 | 内容 |
|----|------|
| 现象 | CLI 输出里看不到 `[工具调用]` 行，Agent 直接用自然语言回答 |
| 原因 | ① System Prompt 路由指令没读到（system prompt 被意外覆盖）；② 模型不支持 Function Calling |
| 解决 | ① 检查 `build_system_prompt()` 是否被正确调用；② 换 qwen3 / qwen3.5 / qwen3.6 系列（明确支持 function calling）；③ 换更高 temperature（0.7 → 0.3 可能更"愿意"调工具） |
| 频率 | ⭐⭐ |

### 工具名被模型猜歪了

| 项 | 内容 |
|----|------|
| 现象 | Agent 想查天气但工具调用是 `{"name": "get_current_weather", ...}`（不是我们注册的 `get_weather`） → ToolExecutor 报 "未知工具" |
| 原因 | 模型的 tool_call 名称参数不是精确匹配 TOOL_SCHEMAS 里的 `"name"` 字段 |
| 解决 | 换更稳定的模型（qwen3.5-9b / qwen3.6-35b 准确率 95%+）；Schema 里的 `name` 保持简短英文动词（4-8 字母） |
| 频率 | ⭐（qwen 系列上罕见，其他模型偶尔） |

### Summarize History 不触发

| 项 | 内容 |
|----|------|
| 现象 | 长对话后 history 越来越长，没有自动摘要 |
| 原因 | `summarize_history` 工具只有在 `build_tool_map()` 传了 `get_history` / `set_history` 才注册；Web UI 的 history 由 Gradio State 管理，没传 |
| 解决 | CLI 端传了，正常触发（阈值 5000 字符）；Web UI 可忽略（Gradio State 本身有长度限制） |
| 频率 | ⭐ |

---

## 三、RAG 问题

### 知识库扫不到文件

| 项 | 内容 |
|----|------|
| 现象 | Agent 回答 "知识库没有相关内容" / 空结果 |
| 原因 | ① 文件没放在 `docs/knowledge/`；② 文件格式不支持（只扫 .md/.txt/.pdf）；③ 路径里有中文/空格导致 os.walk 漏扫 |
| 解决 | ① 把 .md/.txt/.pdf 放到 `docs/knowledge/`；② `RAG_DIRECTORIES=docs/knowledge;自定义路径`；③ Agent 启动时会打印 `RAG 已加载 N 个文件，共 M 个 chunks` |
| 频率 | ⭐⭐ |

### embedding 模型找不到

| 项 | 内容 |
|----|------|
| 现象 | RAG 初始化报错 `404 No such model ... embedding` |
| 原因 | LM Studio 没加载 embedding 模型（只有聊天模型），或 embed_model 名字写错 |
| 解决 | LM Studio Loaded Models 里手动加载 `nomic-embed-text-v1.5`；或改 `EMBED_MODEL=text-embedding-nomic-embed-text-v1.5` |
| 频率 | ⭐⭐ |

### 缓存版本过期

| 项 | 内容 |
|----|------|
| 现象 | 明明加了新文件，检索结果里看不到 |
| 原因 | `_rag_cache.json` 里 CACHE_VERSION 与当前代码不匹配（升级后自动失效），但如果是手动改文件没等 mtime 刷新 |
| 解决 | 删除 `_rag_cache.json` 让它重建；或等几秒让文件系统 mtime 刷新 |
| 频率 | ⭐ |

### "文件损坏"的 JSON 缓存

| 项 | 内容 |
|----|------|
| 现象 | RAG 启动时报 `JSONDecodeError` |
| 原因 | 上次向量化过程中 Ctrl+C 留下了半写的 `_rag_cache.json`（概率极低但不是零） |
| 解决 | 删 `_rag_cache.json` 重启（项目已用 `tmp + os.replace` 原子写入，正常不会发生） |
| 频率 | ⭐ |

---

## 四、安全拦截相关

### Calculator 拒绝合法表达式

| 项 | 内容 |
|----|------|
| 现象 | Agent 想算 `a * b`，返回 "不支持的表达式元素: Name" |
| 原因 | Calculator 的 AST 白名单只允许 `Constant`（数字常量）和 `BinOp`（四则运算），不支持变量 |
| 解决 | 这是**设计好的安全边界**，拒绝任何非纯数学表达式。让 LLM 把变量代入常量后再算（比如 `(1+2) * (3+4)` 而不是 `(a+b) * (c+d)`） |
| 频率 | ⭐ |

### run_terminal 默认关闭

| 项 | 内容 |
|----|------|
| 现象 | Agent 想调终端执行，返回 "安全限制：当前模式禁止执行工具 run_terminal" |
| 原因 | ToolPolicy 默认 `allow_execute=False` |
| 解决 | CLI：`python main.py --enable-dangerous-tools`；Web UI：`--share` 模式下完全禁用，本地启动默认也禁用；环境变量 `ENABLE_DANGEROUS_TOOLS=true` |
| 频率 | ⭐⭐⭐（新手第一次跑都会遇到） |

### SQL DDL 被拦截

| 项 | 内容 |
|----|------|
| 现象 | `DROP TABLE users` 返回 "执行失败" |
| 原因 | SQLite URI `mode=ro` + `PRAGMA query_only=ON` 双重只读保护 |
| 解决 | 这是**设计好的安全边界**，SQL 工具永远只读。真的需要 DDL/DML？写单独的工具函数（不经过 ToolPolicy 的 sql_query） |
| 频率 | ⭐（故意测试安全边界时） |

### PowerShell "危险命令关键词"误伤

| 项 | 内容 |
|----|------|
| 现象 | Agent 想写一个无害的 `Set-Content` 脚本但被 run_terminal 拦截 |
| 原因 | `_DANGEROUS_CMD_PATTERNS` 包含约 40 条黑名单，有些比较激进（如 `Set-Content`） |
| 解决 | 临时改黑名单（编辑 `ai_agent/tools/system.py` 的 `_DANGEROUS_CMD_PATTERNS`）；或 CLI 上手动 y 确认（ToolExecutor.confirm_execute=True 生效在 run_terminal 内部关键词拦截之后） |
| 频率 | ⭐⭐ |

---

## 五、评测相关

### 通过率忽高忽低

| 项 | 内容 |
|----|------|
| 现象 | 同一个 case 第一次过、第二次挂、第三次又过 |
| 原因 | 评测用 temperature > 0，模型输出有随机性 |
| 解决 | 跑评测时 AgentRuntime 默认 temperature=0.7，关掉随机？不——真实运行就是 0.7。建议同一个模型跑 2-3 次取稳定通过率 |
| 频率 | ⭐⭐ |

### 评测卡在确认输入

| 项 | 内容 |
|----|------|
| 现象 | 评测进程挂住，10 分钟不继续 |
| 原因 | 某个工具执行需要 human_confirm / run_terminal 确认，但评测环境没有 stdin 输入 |
| 解决 | 评测用例默认跳过 execute 风险。`--enable-dangerous-tools` 时确保 stdin 接 `/dev/null`（或 ToolExecutor.confirm 返回 False） |
| 频率 | ⭐（enable-dangerous-tools 且包含 run_terminal 用例时） |

### 评测找不到 RAG 文件

| 项 | 内容 |
|----|------|
| 现象 | EvalResult 里 search_knowledge 用例全部 fail |
| 原因 | 评测环境的 TempDir 是空的，没 seed 知识库文件 |
| 解决 | 评测代码会自动从 `project_root/docs/knowledge/通用Agent时代价值迁移.md` copy 到 TempDir。如果这个源文件不存在或改路径了就会失败 |
| 频率 | ⭐⭐（docs 迁移后刚发生过一次） |

---

## 六、Web UI 特有

### 多标签页 history 串了

| 项 | 内容 |
|----|------|
| 现象 | Tab A 聊的内容出现在 Tab B 里 |
| 原因 | threading.local 在某些 Gradio 线程池复用时残留脏数据 |
| 解决 | 当前设计 per-tab 隔离靠 Gradio State 组件（每个请求传入/传出），不是 thread-local。thread-local 只用于 `summarize_history` 等工具闭包访问当前 history |
| 频率 | ⭐（Gradiο 4.x 偶发，5.x 修复了） |

### --share 后 Agent 什么都不能做

| 项 | 内容 |
|----|------|
| 现象 | Agent 只读、不能写、不能执行终端 |
| 原因 | `--share` 生成公网链接，Gradio 自动启用最严安全策略：禁 write / execute / MCP |
| 解决 | 设计如此——公网分享默认最保守。本地开发不用 `--share` |
| 频率 | ⭐⭐ |

---

## 快速诊断速查

```bash
# 1. 检查 LM Studio 服务
curl http://localhost:1234/v1/models

# 2. 检查项目依赖完整性
python -c "import openai, gradio, requests; print('OK')"

# 3. 检查配置加载是否正确
python -c "from ai_agent.config import Config; print(Config.from_env())"

# 4. 跑全量测试
python -m pytest -q

# 5. 跑一次评测（看报告）
python -m ai_agent.eval --no-rag --html-report report.html
```
