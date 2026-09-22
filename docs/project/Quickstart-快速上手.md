# Quickstart · 10 分钟跑通

> 完全新手友好：从零启动到看到 Agent 调工具，不假设你知道 LM Studio / Function Calling。

---

## 预备清单

开始之前，确认你有：

- [ ] Windows / macOS / Linux 电脑
- [ ] Python **3.10** 或以上（项目测试在 3.13 上通过，3.10 到 3.12 都可以）
- [ ] 至少 **8GB 显存** 或 **16GB 内存**（CPU 也能跑 qwen3.5-9b，只是慢）
- [ ] 本项目代码已 clone 到本地

---

## Step 1：安装 LM Studio 并加载模型（5 分钟）

### 1.1 下载 LM Studio

打开 <https://lmstudio.ai/> 下载对应系统版本（Windows 是 `.exe`，macOS 是 `.dmg`）。

### 1.2 下载一个支持 Function Calling 的模型

推荐 **qwen3.5-9b**：
- 中文好
- 支持 Function Calling（工具调用）
- 对显存要求友好（约 5.4GB）
- CPU 也能跑（约 16GB 内存）

打开 LM Studio → Explore → 搜索 `qwen3.5-9b` → Download。

> 如果 9B 太大，试试 qwen3-4b 或 qwen3-turbo。只要模型页面显示支持 **function calling** 就能用。

### 1.3 启动本地服务

下载完模型后，点左侧菜单 **Local Server** → **Start Server**。

默认监听 `http://localhost:1234`。你应该能看到绿色的 "Server is running" 状态。

### 1.4 验证模型

打开浏览器访问 `http://localhost:1234/v1/models`，应该能看到一个或多个已加载的模型 ID，比如：

```json
{"data": [{"id": "qwen3.5-9b-instruct"}]}
```

如果只看到 embedding 模型（名字里有 "embed"），回到 LM Studio 主界面，手动把聊天模型切换为 Loaded 状态。

---

## Step 2：安装项目依赖（2 分钟）

打开 PowerShell / Terminal，进入项目根目录：

```powershell
cd e:\ai-agent

# 创建虚拟环境（Windows）
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 创建虚拟环境（macOS/Linux）
# python3 -m venv .venv
# source .venv/bin/activate

# 升级 pip
python -m pip install --upgrade pip

# 安装全部依赖（含开发工具）
python -m pip install -r requirements-dev.txt
```

安装完成后验证：

```powershell
python -c "import openai; import gradio; print('依赖 OK')"
# 预期输出：依赖 OK
```

---

## Step 3：启动 CLI 并对话（2 分钟）

```powershell
python main.py
```

预期输出（大致）：

```
==================================================
检测 LM Studio 服务状态...
  服务运行: True
  模型已加载: True
  模型名称: qwen3.5-9b-instruct
==================================================
✅ LM Studio 已就绪

✅ 已加载 21 个工具

==================================================
本地 AI Agent 交互式演示
输入消息开始对话；输入 /help 查看命令；exit 退出
==================================================

>
```

### 试试第一个问题

在 `>` 后输入：

```
帮我算一下 2 的 10 次方
```

预期输出（大致）：

```
💭 [思考] 用户想计算 2 的 10 次方...

  [工具调用] calculator(expression="2 ** 10")
  [工具结果] OK 1024

💡 答案是 1024。
```

> 如果模型说"2 的 10 次方等于 1024"但**没调工具**，那也是对的（模型自己会算 2^10）。试试更难的表达式 `帮我算一下 123 * 456`，几乎一定会触发 calculator。

### 试试其他问题

```
北京今天天气怎么样？
把 "AI Agent 的核心是 Function Calling" 保存到 greet.txt
读取一下 greet.txt 的内容
```

每个问题应该看到不同的工具被调用（get_weather / file_write / file_read）。

---

## Step 4：启动 Web UI（1 分钟）

打开第二个终端窗口（保持 CLI 运行），执行：

```powershell
python -m ai_agent.web_ui
```

预期输出：

```
Running on local URL:   http://127.0.0.1:7860
To create a public link, set `share=True`.
```

浏览器打开 `http://127.0.0.1:7860`，你会看到：

- 左侧：对话输入框
- 右侧：实时工具调用轨迹（run_started → route_selected → tool_started → tool_finished → final）

Web UI 默认**禁用 run_terminal / run_code**。CLI 可以用 `--enable-dangerous-tools` 启用。

---

## Step 5：跑一次评测（可选，3 分钟）

```powershell
python -m ai_agent.eval --no-rag --html-report eval_report.html
```

> 第一次跑需要模型能响应，所以比 CLI 直接对话慢一些。`--no-rag` 跳过 RAG 用例（第一次不需要加载知识库）。

预期输出：

```
=== 评测开始 ===
模型: qwen/qwen3.5-9b (auto)
用例数: 15
进度: 15/15

=== 总结 ===
通过: 14
失败: 1
通过率: 93%

HTML 报告: eval_report.html
```

打开 `eval_report.html`，你会看到彩色柱状图和每个用例的详情。

---

## 常见卡点

| 现象 | 原因 | 解决 |
|------|------|------|
| LM Studio 连不上 | 服务没启动 / base_url 错了 | 浏览器打开 `http://localhost:1234/v1/models`，确认有返回 |
| 只有 embedding 模型 | LM Studio 只加载了 embed 模型 | 手动加载 qwen3.5-9b 等聊天模型 |
| CLI 启动后一直不回复 | 模型推理慢 / CPU 跑 9B 模型需要几十秒 | 耐心等；或换更小的模型 |
| Agent 不调工具 | System Prompt 没读到 / 模型不支持 Function Calling | 换 qwen3.5 或 qwen3 系列；确认 LM Studio 里模型标记支持 function calling |
| Web UI 空白 | Gradio 版本不兼容 | 运行 `pip install -r requirements.lock` 严格版本 |

---

## 下一步

- 看 [API 接口文档](./API-接口文档.md) 了解每个工具的完整参数
- 看 [Config 配置参考](./Config-配置参考.md) 了解环境变量
- 看 [架构总览](./架构总览.md) 了解模块关系
- 看 [开发指南-Contributing](./开发指南-Contributing.md) 尝试自己加一个工具
- 看 [FAQ-故障排查](./FAQ-故障排查.md) 解决其他问题
