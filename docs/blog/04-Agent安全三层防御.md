# 让 Agent 安全执行代码：从 AST 白名单到 Human-in-the-loop 的三层防御

> 作者：Agent Dev 实践笔记 | 发布日期：2026-09-21 | 标签：Agent, 安全, AST, Human-in-the-loop, Tool Policy

> 🎓 **配套交互课件**：Web UI 的「🎓 交互课件中心」页签 → 《Function Calling 循环与安全门》（`http://127.0.0.1:7860/courses/#/fc`）——安全门决策点支持"确认 / 拒绝 / 查看风险表"三分支回放，「真实代码」页签可对照 `tooling.py` 的 `ToolPolicy.classify()` 与 `ToolExecutor.execute()`。

---

## 引子

我见过有人跑 Agent 时说："没事，大不了把 `run_terminal` 工具从 tool_map 里删掉就安全了。"

听起来很合理对吧？工具没注册，LLM 想调也调不到。

但你有没有想过，LLM 到底会生成什么？

有一天我让 qwen3.5-9b 跑了一个 demo，用户输入是"帮我清理一下项目根目录的临时文件"。模型第一次调 `file_read` 读了根目录列表，第二次调 `run_terminal` 时生成的命令是：

```powershell
Remove-Item -Path "_test_chart.png","*.tmp","*.log" -Force -ErrorAction SilentlyContinue
```

看起来很合理对不对？但如果用户输入的是"帮我清理一下电脑上不需要的文件"，模型**可能**会生成：

```powershell
Remove-Item -Path "C:\Users\*\Documents\*" -Recurse -Force
```

LLM 不是恶意的。但它也不是安全专家。它不知道 `Remove-Item -Recurse -Force` 会删什么，它也不知道 C:\Users\*\Documents\* 里面有什么。它只是在概率上生成了"清理文件"这句话最可能对应的命令。

所以**安全不能靠"LLM 会自觉"**。我们需要**纵深防御**——从硬到软、从代码到用户，层层兜住。

---

## 一、三层防御架构总览

我们的安全体系分三层，从外到内、从"代码执行不到"到"让 LLM 自己判断"：

```
┌─────────────────────────────────────────────────────────────────────┐
│ 第三层：让 LLM 自己判断                                              │
│ human_confirm 工具（注册为 Function Calling 工具）                   │
│ → "敏感操作前请调这个工具" → Agent 可以主动暂停等用户确认              │
│                                                                     │
│ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─  │
│ 第二层：软拦截                                                       │
│ ToolPolicy 四级风险分类 + ToolExecutor                              │
│ classify → 判定 → allow/confirm → 执行/拦截                           │
│                                                                     │
│ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─  │
│ 第一层：硬拦截                                                       │
│ AST 白名单 + SQL mode=ro + PowerShell 关键词黑名单                    │
│ → 在函数内部，危险输入被拦截，执行不到 OS 层                          │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 二、第一层：硬拦截 — 根本执行不到危险路径

硬拦截是"把危险挡在函数门口"——不管谁调这个函数（LLM 调的、测试代码调的、未来某个新 Agent 调的），只要输入是危险的，函数直接返回错误，永远执行不到真正的危险代码。

### 2.1 Calculator：AST 白名单

`calculator` 工具的问题是"让 LLM 写一个数学表达式然后 eval 它"。但如果表达式是 `__import__('os').listdir()` 呢？`eval` 会真的执行。

我们的解法是**根本不让 `eval` 见到任何非数学节点**：

```python
# tools/demo.py — 简化版（真实代码在项目里）
import ast

_ALLOWED_OPS = {ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.USub}

def calculator(expression: str) -> str:
    try:
        tree = ast.parse(expression, mode="eval")
        # 递归校验每一个节点
        _safe_eval(tree.body)   # ← 在这里会拒绝 Call/Name/Attribute 等
        return "OK"
    except Exception as exc:
        return f"计算失败: {exc}"

def _safe_eval(node):
    if isinstance(node, ast.BinOp):
        if type(node.op) not in _ALLOWED_OPS:
            raise ValueError(f"不支持的运算符: {type(node.op).__name__}")
        return _safe_eval(node.left), _safe_eval(node.right)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    # ↑ 只允许 BinOp（四则运算）和 Constant（数字常量）
    # Call / Name / Attribute / Subscript / Lambda — 一律拒绝
    raise ValueError(f"不支持的表达式元素: {type(node).__name__}")
```

`eval("__import__('os').listdir()")` 的 AST 会有一个 `Call` 节点 → `_safe_eval` 遇到 `Call` → 直接 raise → 错误返回 → **LLM 拿不到执行机会**。

### 2.2 SQL：数据库层物理只读

```python
# tools/sql.py — SQLite URI 参数 + PRAGMA 双重只读
uri = abs_path.as_uri() + "?mode=ro"    # ← OS 文件级只读：无法写文件
conn = sqlite3.connect(uri, uri=True)
conn.execute("PRAGMA query_only=ON")   # ← SQLite 引擎级只读：无法执行写操作
```

两层保护：
- URI `mode=ro` 让 SQLite 根本打不开文件的写入句柄 → OS 层拒绝
- `PRAGMA query_only=ON` 让 SQLite 引擎拒绝所有写操作 → 引擎层拒绝

`DROP TABLE users` 会在 SQL 执行时被引擎拒绝，根本不会真正删数据。

### 2.3 PowerShell 关键词黑名单

```python
# tools/system.py — 简化版
_DANGEROUS_CMD_PATTERNS = [
    "rm -rf /", "Remove-Item -Recurse -Force",
    "Stop-Computer", "Restart-Computer", "Format-Volume",
    "Set-Content", "Invoke-WebRequest -OutFile",
    # ... 约 40 条
]

def run_terminal(command: str) -> str:
    # 硬拦截第一步：关键词匹配
    for pattern in _DANGEROUS_CMD_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return f"安全拦截：命令包含危险关键词 '{pattern}'"
    # 通过后才真的 subprocess.run(command)
    ...
```

关键词黑名单不是完美的（可以被绕过），但它是**第一道关**——绝大多数常见危险命令会在这里被拦下。

---

## 三、第二层：软拦截 — 谁可以调什么

硬拦截是函数内部的防线。但在 Agent 体系里，我们还需要**函数外面**的防线——决定"当前这个 Agent 实例能不能用 run_terminal"。

### 3.1 ToolPolicy：四级风险分类

```python
# tooling.py — 只靠工具名判断风险等级
EXECUTE_TOOLS = frozenset({"run_code", "run_terminal", "demo_code_run"})
WRITE_TOOLS = frozenset({"file_write"})

class ToolPolicy:
    def classify(self, name: str) -> RiskKind:
        if name in EXECUTE_TOOLS:
            return "execute"       # 执行类（subprocess / eval）
        if name in WRITE_TOOLS:
            return "write"         # 写入类（文件写入）
        if name.startswith("mcp_"):
            return "mcp"           # MCP 远端工具
        return "read"              # 默认：只读

    allow_write: bool = True
    allow_execute: bool = False    # ← 默认禁止执行类工具
    allow_mcp: bool = True
    confirm_write: bool = False
    confirm_execute: bool = True   # ← 如果允许，每次需要确认
    confirm_mcp: bool = True
```

四级分类 + 6 个布尔开关，一行配置就能切换整套安全策略。

### 3.2 不同 surface 可以有不同策略

```python
# CLI 端：允许 execute，但每次需要确认
cli_executor = ToolExecutor(
    tool_map,
    policy=ToolPolicy(
        allow_write=True,
        allow_execute=True,        # CLI 用户在自己电脑上
        confirm_execute=True,      # 但每次都弹确认
    ),
    confirm=lambda name, risk, args: input(f"⚠️ 即将执行 {name}，确认？[y/N] ").lower() == "y",
)

# Web UI 端：完全禁止 execute
web_executor = ToolExecutor(
    tool_map,
    policy=ToolPolicy(
        allow_write=True,
        allow_execute=False,       # Web 端禁止
    ),
)
```

注意：**同一个 tool_map**（21 个工具），两个 surface 各自构造不同的 ToolExecutor。CLI 上 `run_terminal` 可用但要确认，Web UI 上直接被拒。

### 3.3 自动注入 allow_unsafe

```python
# tooling.py ToolExecutor.execute() 内部
params = inspect.signature(func).parameters
if risk == "execute" and "allow_unsafe" in params:
    kwargs["allow_unsafe"] = True
```

我们的 `run_terminal` / `run_code` 有一个 `allow_unsafe=False` 参数，默认不允许。ToolExecutor 检测到这是 execute 风险且函数有 `allow_unsafe` 参数时，**自动注入 `allow_unsafe=True`**——但前提是这个 surface 允许 execute。这是个小技巧：让工具函数的默认行为是"不安全"，只有安全策略放行时才解锁。

---

## 四、第三层：让 LLM 自己判断 — human_confirm

前两层都是**我们的代码在拦截**。但还有一层是**让 LLM 主动配合**——注册一个"向用户请求确认"的工具。

### 4.1 human_confirm 是什么

`human_confirm` 是一个 **Function Calling 工具**——和 calculator、file_read 并列的工具。LLM 可以调它。

```python
# app_core.py — Tool Schema
{
    "type": "function",
    "function": {
        "name": "human_confirm",
        "description": "Human-in-the-loop 人工确认。执行敏感/破坏性操作前暂停等待用户确认...",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "描述即将执行的操作"
                }
            },
            "required": ["action"],
        },
    },
}
```

System Prompt 里有指引：
> 执行敏感/破坏性操作前（删除覆盖文件、写入重要数据、SQL DDL/DML、终端写入命令），**必须先调用** human_confirm 让用户确认。

### 4.2 触发时机

LLM 在"想调 run_terminal"之前，先想"我要不要先问一下用户"——然后调 `human_confirm(action="将执行 rm -rf _temp_dir/，确认清理？")`。

CLI 端同步阻塞等用户输入：
```python
def cli_human_confirm(action: str) -> str:
    resp = input(f"🤔 {action}\n请输入 y/yes 确认，其他任意键取消: ").strip().lower()
    return "用户已批准" if resp in ("y", "yes") else "用户取消"
```

Web UI 端更复杂——需要用 Gradio queue + 事件回调实现暂停等待：
```python
# Web 端：把 human_confirm 的执行权交给 Gradio 事件循环
def web_human_confirm(action: str) -> str:
    # 触发前端弹确认框，暂停 Agent 执行流
    # 用户点击后结果通过 thread-local 传回
    ...
```

### 4.3 三层防御如何串联

```
用户说"清理一下临时文件"
        ↓
LLM 想调 run_terminal → human_confirm(action="执行 Remove-Item -Path _temp -Force")
        ↓
第三层：human_confirm 弹确认 → 用户点"拒绝"
        ↓
LLM 收到结果 "用户取消" → 放弃执行 → 回复"好的，我没执行任何操作"
        ↓
（如果用户点"批准"→ LLM 继续调 run_terminal）
        ↓
第二层：ToolExecutor.classify("run_terminal") → "execute"
         confirm_execute=True → 再弹一次确认
        ↓
第一层：run_terminal 内部 → 关键词黑名单检查 → 通过 → subprocess.run()
```

三层串联的好处是**即使某一层漏了也有兜底**：
- LLM 忘了调 human_confirm → ToolExecutor 的 confirm_execute 兜底（CLI 每次 execute 都弹）
- ToolExecutor 因为配置错漏了 → 关键词黑名单兜底（run_terminal 内部硬拦截）

---

## 五、安全策略的判定表

看完三层防御，我给一个实用的判定表——你的 Agent 至少需要哪几层：

| 你的 Agent 场景 | 第一层（硬拦截） | 第二层（ToolPolicy） | 第三层（human_confirm） |
|----------------|----------------|---------------------|-----------------------|
| 本地 demo，工具只读（RAG、知识问答） | 可选（没有 execute 工具） | 不需要 | 不需要 |
| 本地 demo，有 execute 工具（run_terminal） | **必须**（AST / 黑名单） | **必须**（allow_execute + confirm） | 可选 |
| Web UI（给别人用） | **必须** | **必须**（Web 端 allow_execute=False） | **必须** |
| 生产环境（多人共享） | **必须** | **必须**（细粒度 allowlist） | **必须** |
| 有 MCP 远端工具 | **必须** | **必须**（mcp_allowlist） | **必须**（远端工具权限不可控） |

---

## 六、演进方向

| 局限 | 现状 | 演进方向 |
|------|------|---------|
| 关键词黑名单可绕过 | PowerShell 换个写法就绕 | PowerShell AST 解析（`[Parser]::ParseInput`）真正理解命令意图 |
| ToolExecutor.classify 靠工具名 | 自写的本地工具如果叫 `mcp_backup` 会被误判为 MCP | 让 callable 加 `__risk__` 元属性，classify 从 callable 读取 |
| human_confirm 只有 approve/deny | 用户不能"修改命令后再批准" | 返回结构化结果（approve / deny / modify / delegate） |
| ToolPolicy 全靠布尔开关 | 不够灵活（比如"file_write 只能写 workspace 目录"） | 每类风险的 allowlist 可以是一个 predicate 函数 |

---

## 七、动手练习：把演进方向做出来

表里前两行（PowerShell AST、human_confirm 结构化返回）是最适合亲手实现的两个——它们各自只动第一层或第三层的一个点，却能让"纵深防御"从概念变成你写过一遍的代码。每个练习给出源码锚点和可检查的验收标准。

### 练习 1：用 PowerShell AST 解析替代关键词黑名单

- **背景**：第一层防御的 PowerShell 部分是两份字符串清单——`_DANGEROUS_CMD_PATTERNS`（`ai_agent/tools/system.py:50-106`，约 40 条子串匹配）加 `_is_dangerous_chain()` 的链式绕过检测（`:132-138`）。它能拦住 `"Remove-Item -Recurse -Force"`，但拦不住 `&('Remove'+'-Item') x -Recurse`——字符串拼接在黑名单眼里只是一段无害文本，在 PowerShell 里却会被求值成真实命令。黑名单在和"语言的表达能力"对抗，这是结构性劣势。
- **任务**：让 PowerShell 自己解析自己——调用 PowerShell 的 `[System.Management.Automation.Language.Parser]::ParseInput()` 把待执行命令解析成 AST，按节点语义判定危险：遍历 `CommandAst` 节点取 `GetCommandName()` 的真实命令名（变量引用、拼接表达式会被求值出真面目），再对照危险命令集合判定。判定通过才放行到 `subprocess.run`。
- **提示**：这里有个"鸡生蛋"问题要先想清楚——为了校验一条 PowerShell 命令，你得先起一次 PowerShell 进程做解析。要点是：① 校验用的解析命令模板是**固定的、不含用户输入拼接**的，待校验命令文本通过 `-EncodedCommand`（base64）或 stdin 传入，避免校验通道本身成为注入面；② 解析结果按命令字符串做缓存，同一条命令不重复起进程；③ 非 Windows 平台没有 PowerShell AST 可用，静默回退到现有黑名单——防御层降级但不消失。AST 遍历的思路和 calculator 的 `_safe_eval`（`tools/demo.py` 的 Python AST 白名单）完全同构，那是现成的参照物。
- **验收标准**：扩展 `ai_agent/tests/test_core.py:330` 的 `TestTerminalSecurity`——现有黑名单用例必须全部保持绿色（这是回归底线），新增绕过用例断言 AST 版能拦截：`&('Remove'+'-Item') target -Recurse`、`$c='Remove-Item'; & $c target`、`iex ('Remove'+'-Item x')` 等字符串拼接/变量间接调用。注意解析型测试打 Windows-only 标记（如 `pytest.mark.skipif`），保证 CI 在非 Windows 上不炸。

### 练习 2：human_confirm 结构化返回——approve / deny / modify / delegate

- **背景**：`human_confirm`（`tools/system.py:198-214`）的返回值是一个自然语言字符串，用户实际只有两档选择：输入 y/yes 批准，输入其他任何内容取消。但真实场景里用户最常说的是第三句话："命令不对，改成这样再执行"——现在这句话只会被当成"取消"，LLM 收到 `[已取消]` 后要么放弃、要么瞎猜用户想要的修改。
- **任务**：把返回值升级为结构化 JSON：`{"decision": "approve" | "deny" | "modify" | "delegate", "detail": "..."}`，其中 `modify` 携带用户改过的命令文本（`detail` 字段），`delegate` 表示"这事你自己看着办但责任你担"。CLI 侧接受 `y / n / m / d` 四种输入；`m` 时再提示一行让用户输入修改后的命令。
- **提示**：好消息是 LLM 侧零改动也能工作——JSON 字符串作为 `role="tool"` 消息回灌后，模型自然读得懂 `{"decision": "modify", "detail": "Remove-Item _temp -Force"}` 并据此调整下一步动作。这个设计在项目里有先例：Critic 的评审结论就是 JSON 协议（`multi_agent.py` 的 `_parse_critic_decision` 三级解析正是为这类"模型读/写 JSON"场景的兜底）；`ToolExecution` dataclass（`tooling.py:44-51`）也证明"结构化结果"是项目的既有风格。Web 端的确认框（`web_ui.py` 的 human_confirm 分支）可以顺手加"修改后批准"输入框，但先做 CLI 版就够验收。
- **验收标准**：`monkeypatch` 掉 `builtins.input`，分别模拟 `y / n / m（附修改文本）/ d` 四种输入，断言返回字符串是合法 JSON 且 `decision` / `detail` 字段正确；中断（EOFError/KeyboardInterrupt）路径断言返回 `deny` 而非抛异常。测试写法参照 `test_core.py` 的 `TestTerminalSecurity`。

---

## 延伸阅读

- [本项目设计决策清单 - S1~S5](../project/设计决策清单.md)
- [OWASP Top 10 for LLM Applications](https://owasp.org/www-project-top-10-for-large-language-model-applications/)
- [LangChain Security Best Practices](https://python.langchain.com/docs/concepts/security/)

---

## 本系列其他文章

- ✅ 第 1 篇：[LLM Agent 极简实现：200 行代码跑通 Function Calling 全流程](./01-Function-Calling-极简实现.md)
- ✅ 第 2 篇：[Agent 项目的单一真相源 — 当 CLI 和 Web UI 的 Prompt 开始漂移时](./02-单一真相源与Prompt漂移.md)
- ✅ 第 3 篇：[把 Skill 做成 Markdown 文件 — Agent 框架的"低代码"设计决策](./03-Skill做成Markdown.md)
- ✅ 第 4 篇：你正在读的这篇
- 📝 [第 5 篇：Agent 不是 Demo — 如何用 20 条 Gold Case 搭建可自动运行的评测框架](./05-Gold-Case评测框架.md)
