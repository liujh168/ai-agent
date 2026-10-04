# MCP 客户端协议拆解

> 以本项目（AI Agent 交互式演示，基于 LM Studio 本地大模型）为实例，拆解 MCP（Model Context Protocol）客户端的实现：它如何用 443 行纯 `requests` 代码把远端工具接入本地 Function Calling 循环。
> 所有代码引用均可按 `文件:行号` 定位核实。设计权衡部分与《设计决策清单》P1–P3 互补：那边讲"为什么这样设计"，这篇讲"机制怎么运转"。

---

## 一、先搞清楚：MCP 到底是什么

**MCP 是一个"远端工具协议"**——本地 Agent 的工具不止可以是 Python 函数，也可以是跑在另一台服务器上的能力。MCP 定义了"列出工具 / 调用工具"这两个操作在 HTTP 上的标准格式，客户端（本项目）和任何实现了 MCP 的服务端（文件系统、数据库、第三方 API 网关……）都能直接对话。

放到本项目已有的概念体系里对比：

| 维度 | 本地原子 Tool | MCP 远端工具 | Skill |
|------|--------------|-------------|-------|
| 实现位置 | 本项目 Python 函数 | 远端 MCP Server | 本项目 Markdown 文件 |
| 定义方式 | `TOOL_SCHEMAS` 手写 JSON Schema | 服务端返回 `inputSchema`，客户端透传 | Frontmatter 声明 |
| 调用链路 | `tool_map[name](**kwargs)` | 闭包 → HTTP POST JSON-RPC → 远端执行 | 换 prompt + 过滤工具 |
| 注册时机 | 启动时 `build_tool_map()` 注册 | 启动时 `connect_all()` 拉取后并入 | 启动时扫描 `skills/*.md` |
| 风险分类 | read / write / execute / network | 独立的一类：`mcp` | 不引入新工具，复用现有分类 |

一句话概括 MCP 客户端的实现本质：**把"远端工具的发现与调用"翻译成"本地 tool_map 里的普通 Python 函数"**——翻译完成后，Agent 的 Function Calling 循环（`ai_agent/runtime.py:90-183`）根本不知道自己调的是本地函数还是远端 HTTP 服务。

---

## 二、架构全景：MCP 在整个系统里的位置

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        启动期（一次性）                                    │
│                                                                         │
│  Config.from_env()（config.py:100-105）                                 │
│    读 MCP_SERVERS="https://a.com/mcp;http://b.com/mcp"（分号分隔）         │
│    读 MCP_ALLOWED_TOOLS="mcp_0_xxx;mcp_1_yyy"（白名单）                    │
│                    │                                                    │
│                    ▼                                                    │
│  create_mcp_manager(urls)（mcp_client.py:437-443）                       │
│    对每个 URL: MCPClient(url) → initialize() → list_tools()              │
│    工具改名: "git-log" → 清洗 "git_log" → 加前缀 "mcp_0_git_log"          │
│    产出两样东西：                                                        │
│      tool_schemas  —— OpenAI function schema 格式（给 LLM 看）            │
│      make_callables —— {name: 闭包函数}（给 ToolExecutor 执行）            │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  并入本地工具体系（app_core.py）                                          │
│                                                                         │
│  build_tool_map(mcp_manager=mgr)      （app_core.py:521-523）            │
│    tool_map.update(mgr.make_callables())  ← 远端工具变成普通函数           │
│                                                                         │
│  get_tool_schemas(mcp_manager=mgr)    （app_core.py:458-463）            │
│    schemas.extend(mgr.tool_schemas)     ← 远端 schema 追加在本地之后       │
│                                                                         │
│  build_tool_executor(..., mcp_allowlist=...) （app_core.py:669-696）      │
│    ToolPolicy.classify(): name.startswith("mcp_") → 风险等级 "mcp"        │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        运行期（每次对话）                                  │
│                                                                         │
│  AgentRuntime.run()（runtime.py:90-183）统一的 FC 循环                    │
│    LLM 返回 tool_call(name="mcp_0_git_log", arguments={...})             │
│         │                                                               │
│         ▼                                                               │
│    ToolExecutor.execute()（tooling.py:67-93）                            │
│      classify → "mcp" → allowlist 检查 → 确认回调 → func(**kwargs)        │
│         │                                                               │
│         ▼                                                               │
│    闭包 fn()（mcp_client.py:404-415 生成）                               │
│      → MCPClient.call_tool("git_log", kwargs)                            │
│      → POST {"jsonrpc":"2.0","method":"tools/call",...}                  │
│      → 远端执行 → 文本结果                                               │
│         │                                                               │
│         ▼                                                               │
│    结果以 role="tool" 消息回灌 history（runtime.py:145-151）→ 下一轮      │
└─────────────────────────────────────────────────────────────────────────┘
```

**关键洞察：MCP 没有改动 Agent 循环的任何一行代码。** 它只在启动期做了两次"并入"（tool_map 并函数、schemas 并 schema），运行期的一切（循环、安全策略、结果回灌）全部复用现有机制。远端工具与本地工具的唯一区别是名字以 `mcp_` 开头——这个前缀同时承担了两个职责：路由回正确的远端 server，和被安全策略识别为 `mcp` 风险等级。

---

## 三、核心模块拆解（一）：MCPClient —— 协议层

**位置**：`ai_agent/mcp_client.py:72-314`

`MCPClient` 是对一个 MCP Server 的连接。模块 docstring（mcp_client.py:6-7）说得很明白：只支持 **Streamable HTTP Transport**（POST JSON-RPC 请求），不支持 STDIO / SSE（见 P1 的权衡分析）。底层协议是 **JSON-RPC 2.0**：每个请求是 `{jsonrpc, id, method, params}`，响应要么有 `result` 要么有 `error`。

### 3.1 _send()：一切 API 的底层收发

```python
# mcp_client.py:108-126
def _send(self, method: str, params: dict | None = None) -> Any:
    """发送一个 JSON-RPC 请求，返回 result 或抛 MCPError / requests 异常"""
    with self._lock:
        self._request_id += 1
        req_id = self._request_id

    payload = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": method,
        "params": params or {},
    }

    # Streamable HTTP: 如果已有 session_id，放进 headers
    hdrs = dict(self.headers)
    if self._session_id:
        hdrs["Mcp-Session-Id"] = self._session_id
    if self.protocol_version:
        hdrs["Mcp-Protocol-Version"] = self.protocol_version
```

三个细节值得注意：

1. **请求 ID 用锁保护自增**（mcp_client.py:110-112）：JSON-RPC 靠 `id` 对账请求与响应，多线程下必须保证唯一；
2. **`Mcp-Session-Id` 头**：Streamable HTTP 的会话机制——服务端在首次响应里下发 session id（mcp_client.py:139-141 提取保存），之后每个请求带上它；
3. **`Mcp-Protocol-Version` 头**：协议版本协商，initialize 时如果服务端返回了不同的版本号，客户端会采纳（mcp_client.py:231-233）。

响应处理部分（mcp_client.py:143-163）做三件事：非 200/202/204 状态码抛 `MCPError`；解析响应体；如果 JSON-RPC 层有 `error` 字段也抛 `MCPError`（带上协议层的 code/message/data）。**HTTP 错误和 JSON-RPC 错误被统一折叠成同一种异常**，上层不需要区分。

### 3.2 _parse_response()：兼容 SSE 文本格式

Streamable HTTP 允许服务端返回两种 body：纯 JSON 或 SSE（`data: {...}` 行）。解析器（mcp_client.py:195-215）按 Content-Type 或首字符前缀分流，SSE 格式取第一个能解析成 JSON 的 `data:` 行：

```python
# mcp_client.py:200-211
content_type = resp.headers.get("Content-Type", "")
if "text/event-stream" in content_type or text.startswith(("event:", "data:")):
    for line in text.splitlines():
        if line.startswith("data:"):
            payload = line[5:].strip()
            if not payload:
                continue
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                continue
    raise MCPError(code=-1, message=f"无法解析 SSE 响应: {text[:200]}")
```

注意这不是完整的 SSE 流处理——不挂连接、不逐事件消费，只是"从 SSE 格式里抠出第一条 JSON-RPC 响应"。这是极简实现的典型取舍：够用、可读、几十行覆盖主流通路。

### 3.3 initialize / list_tools / call_tool：三个高层 API

**initialize（mcp_client.py:219-237）——握手**：

```python
result = self._send(
    "initialize",
    {
        "protocolVersion": self.protocol_version,
        "capabilities": {},
        "clientInfo": {"name": "ai-agent", "version": "1.0"},
    },
)
...
self._notify("notifications/initialized", {})
```

报上协议版本和客户端身份，采纳服务端协商出的版本，然后发一个 `notifications/initialized` **通知**（notification 没有 `id`，不需要响应，走单独的 `_notify()`，mcp_client.py:165-193）。MCP 规范要求 initialize 之后必须发这个通知才算握手完成。

**list_tools（mcp_client.py:239-258）——发现工具**：发 `tools/list`，把返回的每个工具包装成 `MCPTool` dataclass（mcp_client.py:44-52，含 name / description / input_schema / server_url）。注意 `input_schema` 是**原样透传**的——客户端不理解也不校验 schema 内容。

**call_tool（mcp_client.py:260-285）——调用工具**：发 `tools/call`，把结果折叠成一个字符串：

```python
# mcp_client.py:272-285
is_error = bool((result or {}).get("isError"))

# MCP 返回的 content 是 list，每个元素有 type + text
contents = (result or {}).get("content", [])
parts = []
for c in contents:
    if isinstance(c, dict) and c.get("type") == "text":
        parts.append(c.get("text", ""))
    elif isinstance(c, dict):
        # image / blob 等类型，先输出类型占位
        parts.append(f"[{c.get('type', 'unknown')}: {str(c)[:200]}]")

content = "\n".join(parts) if parts else "(无输出)"
return f"[MCP 工具错误] {content}" if is_error else content
```

返回值**永远是字符串**——因为调用链的终点是把它塞进 `role="tool"` 消息的 `content` 字段（runtime.py:145-151），那本来就是个字符串位置。`isError` 不抛异常，而是加 `[MCP 工具错误]` 前缀返回：让 LLM 看到错误信息并自行决定重试或换方案，这和本地工具"报错喂回给模型"的策略一致。

---

## 四、核心模块拆解（二）：MCPManager —— 整合层

**位置**：`ai_agent/mcp_client.py:322-443`

单个 `MCPClient` 只管一个 server；`MCPManager` 负责聚合多个 server，并完成"翻译成本地工具"的全部工作。

### 4.1 connect_all()：连接、改名、失败占位

```python
# mcp_client.py:332-356
def connect_all(self) -> None:
    """连接所有 server 并 list_tools（同步阻塞）"""
    for idx, url in enumerate(self.server_urls):
        try:
            client = MCPClient(url)
            client.initialize()
            tools = client.list_tools()
            client.server_index = idx

            for t in tools:
                safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", t.name)
                final_name = f"mcp_{idx}_{safe_name}"
                suffix = 2
                while final_name in self._tool_names:
                    final_name = f"mcp_{idx}_{safe_name}_{suffix}"
                    suffix += 1
                self._tool_names[final_name] = (idx, t.name)
                t.server_index = idx
                t.name = final_name

            self._clients.append(client)
            self._errors.append(f"✅ {url}: {len(tools)} 个工具")
        except Exception as e:
            self._clients.append(None)  # 占位，保持索引对齐
            self._errors.append(f"❌ {url}: {e}")
```

**命名规则分两步**（对应设计决策 P3）：

1. **清洗**：`re.sub(r"[^a-zA-Z0-9_]", "_", ...)` 把 `git-log` 变成 `git_log`——因为工具名要作为 Python 函数名和 JSON 标识符流通；
2. **加前缀**：`mcp_{server索引}_{清洗后名字}`，例如 `mcp_0_git_log`。server 索引让不同 server 的同名工具天然区分；同一 server 内重名则用 `_2`、`_3` 后缀递增。

`_tool_names` 字典（`final_name → (server_index, 原始名字)`）是运行期路由的依据：LLM 看到的是 `mcp_0_git_log`，真正发给远端的必须是原始的 `git-log`。

**失败降级**（mcp_client.py:354-356）：某个 server 连不上，只往 `_clients` 里塞一个 `None` 占位（保持索引对齐），记录错误信息，继续连下一个。整个 `connect_all()` 不会抛异常——配合 `create_mcp_manager()`（mcp_client.py:437-443），"连接全失败"的结果是得到一个 0 工具的可用实例，而不是启动崩溃。

### 4.2 _build_schemas()：转成 OpenAI function schema

```python
# mcp_client.py:361-378（节选）
schemas.append(
    {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or f"MCP 工具: {tool.name}",
            "parameters": tool.input_schema or {"type": "object", "properties": {}},
        },
    }
)
```

MCP 的 `inputSchema` 和 OpenAI function calling 的 `parameters` 都是 JSON Schema，所以转换是零翻译成本的直接映射。产出与本地 `TOOL_SCHEMAS`（app_core.py:103）完全同构的列表，由 `get_tool_schemas()` 追加在本地 schema 之后（app_core.py:458-463）：

```python
def get_tool_schemas(mcp_manager=None) -> list[dict]:
    """运行时合成最终的 TOOL_SCHEMAS（本地 + 可选 MCP 远端）"""
    schemas = list(TOOL_SCHEMAS)
    if mcp_manager is not None and mcp_manager.total_tools > 0:
        schemas.extend(mcp_manager.tool_schemas)
    return schemas
```

### 4.3 make_callables()：把远端调用伪装成本地函数

```python
# mcp_client.py:404-415
def _make_fn(c: MCPClient, orig: str) -> Callable:
    def fn(**kwargs) -> str:
        try:
            return c.call_tool(orig, kwargs)
        except MCPError as e:
            return f"[MCP 错误] {e}"
        except Exception as e:
            return f"[MCP 调用异常] {type(e).__name__}: {e}"

    fn.__name__ = final_name
    fn.__doc__ = f"MCP tool from {c.url}: {orig}"
    return fn
```

这是整个整合层最精妙的一笔：**每个远端工具被包装成一个普通闭包**，签名是 `(**kwargs) -> str`——与本地工具在 `ToolExecutor` 眼里的形态完全一致。LLM 传来的 `arguments` dict 被直接展开成 kwargs，闭包再用**原始名字**（`orig`）发 `tools/call`。所有异常（协议错误、网络超时）都在闭包内折叠成错误字符串返回，绝不向上抛——`AgentRuntime` 循环因此对远端故障免疫。

这个 dict 由 `build_tool_map()` 一行并入（app_core.py:521-523）：

```python
# MCP 远端工具（可选，追加在本地工具之后）
if mcp_manager is not None:
    tool_map.update(mcp_manager.make_callables())
```

### 4.4 ToolPolicy：MCP 工具的安全分类

远端工具天然比本地工具危险（代码和数据都不可控），所以项目给它单独立了一类风险等级（对应设计决策 P2）。分类靠的就是 `mcp_` 前缀（tooling.py:32-41）：

```python
def classify(self, name: str) -> RiskKind:
    if name in EXECUTE_TOOLS:
        return "execute"
    if name in WRITE_TOOLS:
        return "write"
    if name in NETWORK_TOOLS:
        return "network"
    if name.startswith("mcp_"):
        return "mcp"
    return "read"
```

执行前的拦截逻辑（tooling.py:102-107）：

```python
if risk == "mcp":
    if not self.policy.allow_mcp:
        return f"安全限制：当前模式禁止 MCP 工具 {name}"
    allowlist = self.policy.mcp_allowlist
    if allowlist is not None and name not in allowlist:
        return f"安全限制：MCP 工具 {name} 不在允许列表中"
```

两层闸门：**总开关 `allow_mcp`** 加 **白名单 `mcp_allowlist`**。白名单来自配置 `MCP_ALLOWED_TOOLS`（config.py:102-105，分号分隔的环境变量），在 `build_tool_executor()`（app_core.py:684-694）里转成 `frozenset` 或 `None`（空 = 不限制）。

CLI 和 Web 的默认策略不同，这是同一个 `ToolPolicy` 机制在不同入口的参数化：

- **CLI**（main.py:167-178）：`allow_mcp=True` 但 `confirm_mcp=True`——每次调 MCP 工具都要人工 y/yes 确认；
- **Web UI**（web_ui.py:209）：`allow_mcp = bool(self.cfg.mcp_allowed_tools) and not self.disable_dangerous`——**默认禁用**，必须显式配置 `MCP_ALLOWED_TOOLS` 才放行。

`filter_tool_schemas()`（app_core.py:699-717）还会做"预防性隐藏"：被策略明确禁止的 MCP 工具连 schema 都不发给 LLM——看不见的工具不会被调用，比运行期拦截更早一道防线。

### 4.5 与 Skill 的正交关系

MCP 工具注册进的是**同一个 tool_map**，所以 Skill 的 `required_tools` 可以直接声明 `mcp_0_search_api` 这样的远端工具名。链条是：Web 端先 `_init_mcp()` 再 `build_tool_map()` 再 `_register_skills()`（web_ui.py:195-206），SkillRegistry 校验 `required_tools` 时拿到的 `available_tools` 已经包含全部 MCP 工具名。Skill 激活时过滤 schema 用的也是 `get_tool_schemas(mcp_manager)`（runtime.py:65-67）——本地与远端工具走同一个过滤通道。**MCP 管"工具从哪来"，Skill 管"这次让 LLM 看到哪些工具"，两个维度互不相干。**

---

## 五、完整流程走一遍

假设 `.env` 配置了：

```
MCP_SERVERS=https://mcp.example.com/tools
MCP_ALLOWED_TOOLS=mcp_0_calc
```

server 上有一个工具 `calc`，用户输入：**"帮我算一下 123 乘以 456"**

```
Step 1 — 启动期：发现并翻译远端工具
  web_ui.py:237-246  _init_mcp()
    → config.mcp_servers = ("https://mcp.example.com/tools",)
    → create_mcp_manager(urls)（mcp_client.py:437-443）
      → MCPClient.initialize(): POST {"jsonrpc":"2.0","id":1,"method":"initialize",
                                     "params":{"protocolVersion":"2025-06-18",...}}
        ← 服务端返回 result + 响应头 Mcp-Session-Id: abc123（保存）
      → _notify("notifications/initialized")（无 id，不需要响应）
      → list_tools(): POST {"method":"tools/list"} 
        ← 返回 {"tools":[{"name":"calc","description":"计算器","inputSchema":{...}}]}
      → 改名: "calc" → "mcp_0_calc"，记录 _tool_names["mcp_0_calc"] = (0, "calc")

Step 2 — 启动期：并入本地工具体系
  app_core.py:521-523  tool_map.update(mgr.make_callables())
    → tool_map["mcp_0_calc"] = 闭包 fn（内部记得 client 和原始名 "calc"）
  app_core.py:458-463  schemas.extend(mgr.tool_schemas)
    → LLM 可见的工具列表末尾多了一个 mcp_0_calc 的 schema

Step 3 — 运行期：LLM 选择 MCP 工具
  runtime.py:104-111  create_chat_completion(tools=active_tools)
    → LLM 从 schema 里看到 mcp_0_calc，返回:
      tool_calls=[{"function":{"name":"mcp_0_calc",
                               "arguments":"{\"expression\": \"123*456\"}"}}]

Step 4 — 安全闸门
  runtime.py:143  executor.execute("mcp_0_calc", {"expression": "123*456"})
    → tooling.py:32-41   classify: startswith("mcp_") → risk="mcp"
    → tooling.py:102-107 allow_mcp? ✅; "mcp_0_calc" 在 allowlist 里? ✅
    → (CLI 下还会弹人工确认; Web 下 confirm_mcp=False 直接放行)

Step 5 — 闭包路由回远端
  mcp_client.py:404-415 的 fn(**kwargs)
    → client.call_tool("calc", {"expression": "123*456"})   ← 用原始名字！
    → POST {"jsonrpc":"2.0","id":2,"method":"tools/call",
            "params":{"name":"calc","arguments":{"expression":"123*456"}}}
      headers: Mcp-Session-Id: abc123
    ← {"result":{"content":[{"type":"text","text":"56088"}]}}
    → 折叠成字符串 "56088" 返回

Step 6 — 结果回灌，循环继续
  runtime.py:145-151  history.append({"role":"tool","tool_call_id":...,"content":"56088"})
    → 下一轮 LLM 基于工具结果生成最终回答: "123 × 456 = 56088" ✅

Step 7 — 退出清理
  web_ui.py:1621-1622  app.mcp_manager.close_all()
    → 每个 client 发 DELETE 请求终止 session（mcp_client.py:300-314，best-effort）
```

---

## 六、边界与扩展

### 这个实现不做什么（保持极简）

| 特性 | 状态 | 原因 |
|------|------|------|
| STDIO Transport | ❌ 不支持 | 需要子进程 + stdin/stdout 读写，`requests` 做不到；权衡见设计决策 P1 |
| 完整 SSE 流 | ❌ 不支持 | `_parse_response()` 只从 SSE 格式里抠第一条响应，不挂长连接逐事件消费 |
| MCP Resources / Prompts | ❌ 不支持 | MCP 协议还有资源读取和 prompt 模板两个原语，本项目只实现了 tools |
| 异步 / 并发调用 | ❌ 不支持 | 同步阻塞 `requests`；Agent 循环本身是串行的，无并发需求 |
| 服务端能力探测 | ❌ 部分 | `capabilities` 握手时传了空 dict，没有根据服务端能力裁剪行为 |
| 重连 / 会话恢复 | ❌ 不支持 | session 断了就报错返回字符串，由 LLM 决定是否重试 |

### 如果想扩展

1. **加 STDIO Transport**：新增 `MCPClientSTDIO`（子进程 + Popen），让 `MCPManager` 按 URL scheme 选择 Transport（`https://` → HTTP，命令行 → STDIO），即可接入 Desktop Commander、Filesystem 等本地 MCP server；
2. **去掉前缀约定**：`startswith("mcp_")` 是约定式识别，本地工具若叫 `mcp_backup` 会被误判。可改为在 `make_callables()` 生成的闭包上挂 `fn.__risk__ = "mcp"` 元属性，让 `classify()` 读取属性而非猜前缀（P2 的演进方向）；
3. **支持 MCP Resources**：照 `list_tools/call_tool` 的模式加 `resources/list` 和 `resources/read`，把远端文件/数据接入 RAG 知识库；
4. **连接池与异步**：把 `requests` 换成 `httpx.AsyncClient`，`MCPManager.connect_all()` 从串行连接改成并发，启动更快。

---

## 七、总结

MCP 客户端的实现本质只有三句话：

1. **协议就是两次 POST**：`tools/list` 发现工具、`tools/call` 调用工具，加上一次 `initialize` 握手——MCP 在本项目里被压缩成 JSON-RPC 2.0 的三个 method，全部走一个 `_send()`；
2. **远端工具 = 一个闭包**：`make_callables()` 把每个远端工具包装成 `(**kwargs) -> str` 的本地函数，连同透传的 schema 一起并入 tool_map——翻译完成后，FC 循环、安全策略、结果回灌全部零改动复用；
3. **`mcp_` 前缀是整合的枢纽**：命名冲突靠它解决，安全分类靠它识别，白名单靠它过滤——一个字符串前缀同时承担了路由标识和风险标记两个职责。

理解了这三点，再看 `mcp_client.py` 的 443 行会发现它的结构极其平直：协议层（`MCPClient`）只做收发，整合层（`MCPManager`）只做翻译，安全层（`ToolPolicy`）只做分类。**MCP 没有给 Agent 增加任何新机制，它只是给"工具"这个词换了一个更远的来源。**
