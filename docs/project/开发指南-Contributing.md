# 开发指南 — Contributing

> 如何扩展这个 Agent 项目：新增工具、写 Skill、加评测用例、运行测试。

---

## 目录

- [环境准备](#环境准备)
- [代码规范](#代码规范)
- [新增一个工具](#新增一个工具)
- [写一个 Skill](#写一个-skill)
- [新增一条评测用例](#新增一条评测用例)
- [运行测试](#运行测试)

---

## 环境准备

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
```

依赖分层：

| 层级 | 包 | requirements 文件 |
|------|----|-----------------|
| 运行时 | openai / requests / gradio / pypdf / pillow | requirements.txt |
| 开发 | pytest / ruff / 其他 dev 工具 | requirements-dev.txt |
| 锁定 | 完整依赖图 + 精确版本号 | requirements.lock / requirements-dev.lock |

> 开发中如果只加了运行时依赖，同步更新 `requirements.lock`。如果加了 pytest 插件等开发工具，更新 `requirements-dev.lock`。

---

## 代码规范

### 类型标注

所有公开函数和 dataclass 都需要完整类型标注。用 `from __future__ import annotations` 推迟求值（避免循环引用）。

```python
# ✅ 好的：完整类型
def _parse_frontmatter(fm_text: str) -> dict: ...

# ✅ 好的：复杂嵌套类型用 alias
from typing import Literal
RiskKind = Literal["read", "write", "execute", "mcp"]

# ❌ 不好的：缺少类型
def _parse_frontmatter(fm_text): ...
```

### dataclass 必须 frozen

项目所有数据结构用 `@dataclass(frozen=True)`，保证不可变性：

```python
@dataclass(frozen=True)
class ToolPolicy:
    allow_execute: bool = False
    confirm_execute: bool = True
    mcp_allowlist: frozenset[str] | None = None  # 容器也用不可变类型
```

### `@wraps(func)` 包装函数

任何包装原始函数的 wrapper（如 ToolCallTracker）必须用 `@wraps(func)` 保留 `__name__` / `__doc__`：

```python
from functools import wraps

def _wrap(self, name: str, func):
    @wraps(func)           # ← 关键！
    def wrapper(*args, **kwargs):
        self.calls.append(name)
        return func(*args, **kwargs)
    return wrapper
```

Function Calling 层会读取 `func.__name__`。如果 wrapper 不 `@wraps`，LLM 会收到叫 `wrapper` 的工具名——完全混乱。

### 错误处理策略

- 工具实现返回 **str**（异常也返回 `f"执行失败: {exc}"`，不 raise）
- ToolExecutor 捕获异常 → 包装为 ToolExecution(ok=False, content=..., error=...)
- 评测代码断言 `execution.ok` / 检查 `execution.error`
- 不要让异常泄漏到 runtime 循环外层（会中断 AgentRuntime.run()）

### ruff 检查

```powershell
ruff check main.py ai_agent --ignore E501
# 关注 F 错误（F401 未使用 import / F841 未使用变量）
```

---

## 新增一个工具

### Step 1：写 Python 函数

在 `ai_agent/tools/` 下选合适的子模块（demo.py / fs.py / system.py ...）：

```python
# tools/demo.py
def tell_joke(style: str = "random") -> str:
    """讲一个笑话（开发示例工具）"""
    jokes = {"random": "为什么程序员分不清 Halloween 和 Christmas？因为 Oct 31 == Dec 25"}
    return jokes.get(style, jokes["random"])
```

约束：
- **返回 str**（必须，Function Calling 结果回灌的都是字符串）
- **参数必须有默认值或全 required**（JSON schema 里 required 是数组，不支持可选标记）
- **不要 raise**（返回错误描述字符串）

### Step 2：写 Tool Schema

在 `ai_agent/app_core.py` 的 `TOOL_SCHEMAS` 列表里加一项：

```python
# app_core.py TOOL_SCHEMAS 列表
{
    "type": "function",
    "function": {
        "name": "tell_joke",
        "description": "讲一个程序员笑话",
        "parameters": {
            "type": "object",
            "properties": {
                "style": {
                    "type": "string",
                    "description": "笑话风格，可选 random / dad / dark",
                    "enum": ["random", "dad", "dark"],
                }
            },
        },
    },
},
```

### Step 3：build_tool_map 注册

在 `ai_agent/app_core.py` 的 `build_tool_map()` 里绑定到 tool_map：

```python
# build_tool_map() 内部（纯函数直接引用）
tool_map["tell_joke"] = tell_joke
```

如果需要 workspace_root / client 等依赖，写成闭包：

```python
def my_tool(file_path: str) -> str:
    abs_path = os.path.join(workspace_root, file_path)
    return func(abs_path)
my_tool.__name__ = "my_tool"
my_tool.__doc__ = func.__doc__
tool_map["my_tool"] = my_tool
```

### Step 4：安全分类

在 `tooling.py` 的 EXECUTE_TOOLS / WRITE_TOOLS 集合里加（如果属于这些风险）：

```python
EXECUTE_TOOLS = frozenset({"run_code", "run_terminal", "demo_code_run", "my_exec_tool"})
WRITE_TOOLS = frozenset({"file_write", "my_write_tool"})
```

如果是 read 风险（默认），不需要改。

### Step 5：跑起来

```powershell
python main.py
# > 讲一个笑话
#   [工具调用] tell_joke(style="random") → "为什么程序员分不清 Halloween 和 Christmas？..."
```

### Step 6（可选）：加评测用例

```python
# ai_agent/eval.py EVAL_CASES
EvalCase(
    name="route_tell_joke",
    capability="tool_routing",
    prompt="讲个程序员笑话",
    expected_tool="tell_joke",
    description="模型应路由到 tell_joke 工具",
),
```

---

## 写一个 Skill

### Skill 文件结构

```yaml
---
name: skill_name                   # Skill 唯一 ID
description: 一句话说明这个 Skill 做什么
trigger_keywords:                  # 关键词路由匹配（子串匹配，不区分大小写）
  - 关键词1
  - 关键词2
required_tools:                    # 激活时只暴露这些工具名
  - tool_a
  - tool_b
max_rounds: 5                      # 迭代上限（可选，默认 5）
entry_mode: auto                   # auto / manual（可选）
---

# 正文 = 激活时的 System Prompt
## 角色
你是一个...（人格 / 职责说明）

## 工作流
1. ...
2. ...

## 注意事项
- ...
```

### 最短路径

1. 复制 `skills/example_plan.md` → `skills/my_skill.md`
2. 改 Frontmatter（name / trigger_keywords / required_tools）
3. 改正文（角色 + 工作流）
4. **重启** CLI / Web UI（SkillRegistry 启动时扫描 skills/ 目录，不做热加载）

### 路由验证

```bash
python -c "from ai_agent.skill import SkillRegistry; sr = SkillRegistry('skills'); m = sr.match('帮我规划一下写大纲'); print(m.name if m else 'No match')"
```

> 详见 [API-接口文档](./API-接口文档.md) 和 [Skill实现本质原理](../project/Skill实现本质原理.md)

---

## 新增一条评测用例

`ai_agent/eval.py` 的 `EVAL_CASES` 列表里加：

```python
EvalCase(
    name="my_new_case",                    # 唯一 ID（snake_case）
    capability="tool_routing",              # 能力标签（评测 --tag 过滤用）
    prompt="告诉用户天气怎么样",             # 用户输入
    expected_tool="get_weather",            # 期望路由到的工具
    expected_tools_called=["get_weather"],  # 完整调用序列（顺序敏感，前缀匹配）
    expected_contains=["天气"],             # 最终输出必须包含
    expected_not_contains=["错误"],         # 最终输出不能包含
    expected_tool_output_contains=["晴"],    # 工具返回值应包含
    expected_min_len=10,                   # 最少字符数
    description="验证 weather 工具路由",
),
```

### 6 维断言优先级

1. **expected_tool**（必需工具路由）→ 路由准确性
2. **expected_tools_called**（调用序列）→ ReAct 等多步场景
3. **expected_contains**（输出包含）→ 基本质量
4. **expected_not_contains**（输出排除）→ 安全边界
5. **expected_tool_output_contains**（工具返回值包含）→ I/O 正确性
6. **expected_min_len**（最少字符）→ 防空回复

### 跑评测

```powershell
python -m ai_agent.eval --list                  # 先确认你加的 case 在不在列表里
python -m ai_agent.eval --tag tool_routing      # 只跑 tool_routing 标签
python -m ai_agent.eval --no-rag                # 跳过 RAG（快）
python -m ai_agent.eval --html-report my.html  # 生成 HTML 报告
```

---

## 运行测试

```powershell
# 全量
python -m pytest -q

# 指定文件
python -m pytest -q ai_agent/tests/test_core.py

# 带详细输出
python -m pytest -v ai_agent/tests/test_tools.py -k "calculator"

# ruff 检查
ruff check main.py ai_agent --ignore E501

# ruff 自动修复
ruff check main.py ai_agent --fix --ignore E501
```

测试覆盖：

| 文件 | 覆盖内容 |
|------|---------|
| tests/test_core.py | AgentRuntime 主循环 / ToolCallTracker / RAG 缓存 |
| tests/test_tools.py | 工具注册 / schema 完整性 / AST 白名单 |
| tests/test_runtime_and_sessions.py | Event 迭代器 / 会话级联删除 |
| tests/test_mcp_client.py | MCP Schema 解析 / 工具名冲突解决 |
