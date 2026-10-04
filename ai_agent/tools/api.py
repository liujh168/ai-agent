"""
LLM API 调用入门演示
====================
本文件是给 LLM 初学者的**最简教学脚本**。它不依赖项目中复杂的 LMStudioClient，
只用 openai 库直接调用本地 LM Studio 服务，把每个环节打印出来让你看得清清楚楚。

前置条件：
  1. 安装 LM Studio（https://lmstudio.ai）
  2. 在 LM Studio 里加载一个聊天模型（比如 Qwen、Gemma 等）
  3. 点击 "Start Server"，让它监听 http://localhost:1234
  4. 本项目已安装 openai 和 requests 依赖

运行方式：
  python -m ai_agent.tools.api
"""

from __future__ import annotations

import json

# ============================================================
# 第一步：导入库并配置连接参数
# ============================================================
# openai 库不仅能调用官方 API，还能调用任何"OpenAI 兼容"的接口——
# LM Studio、Ollama、vLLM 等本地推理服务都兼容这套协议，这是 LLM 生态的事实标准。
from openai import OpenAI

# LM Studio 默认监听地址和"假的"API Key（它不做鉴权，但 openai 库要求必须传一个）
BASE_URL = "http://localhost:12345/v1"
API_KEY = "lm-studio"  # 随便填都行，LM Studio 不校验

print("=" * 60)
print("  🚀 LLM API 调用入门演示")
print("=" * 60)

# ----------------------------------------------------------
# 先做一件事：确认 LM Studio 服务真的在跑
# ----------------------------------------------------------
print("\n【0. 检查 LM Studio 服务状态】")
try:
    import requests

    resp = requests.get(f"{BASE_URL}/models", timeout=3)
    models = [m["id"] for m in resp.json().get("data", [])]
    print(f"  ✅ 服务正常，已加载 {len(models)} 个模型: {models}")
except Exception as e:
    print(f"  ❌ 连不上 LM Studio: {e}")
    print("     请先打开 LM Studio → 加载一个聊天模型 → 点 Start Server")
    raise SystemExit(1)


# ============================================================
# 第二步：创建客户端
# ============================================================
print("\n【1. 创建 OpenAI 兼容客户端】")
print(f"  base_url = {BASE_URL}")
print(f"  api_key  = {API_KEY!r}")

client = OpenAI(
    base_url=BASE_URL,      # 指向本地 LM Studio，而非官方 api.openai.com
    api_key=API_KEY,
)
print("  ✅ 客户端已创建")


# ============================================================
# 第三步：准备请求消息
# ============================================================
# Chat Completions 接口要求 messages 是一个列表，每条消息有 role 和 content。
# role 只能是 "system" / "user" / "assistant" 三者之一。
# - system  : 给模型定"人设"，它会尽量遵守
# - user    : 用户问的话
# - assistant: 模型之前的回复（多轮对话时才需要）
print("\n【2. 构造 messages 请求体】")
messages = [
    {"role": "system", "content": "你是一个耐心的编程老师，用通俗的中文回答。"},
    {"role": "user", "content": "请用一句话解释什么是 Token。"},
]
print(f"  messages = \n{json.dumps(messages, ensure_ascii=False, indent=4)}")


# ============================================================
# 第四步：发起非流式请求（等全部生成完一次性返回）
# ============================================================
print("\n【3. 发起非流式请求 —— 等待模型完整生成后返回】")

response = client.chat.completions.create(
    model="auto",          # "auto" 让 LM Studio 用当前加载的模型
    messages=messages,
    temperature=0.7,       # 0=确定  2=发散，入门用 0.5~0.7 就行
    max_tokens=200,        # 最多生成多少 token，防止跑太久
    stream=False,          # False = 一次性返回完整结果
)

# 把响应完整打印出来——这就是 LLM API 返回的原始结构
print("\n  📦 原始响应对象的关键字段:")
print(f"  model       = {response.model}")
print(f"  created     = {response.created}")
print(f"  finish_reason = {response.choices[0].finish_reason}")
print(f"  usage       = {response.usage}")

# 真正的答案藏在 response.choices[0].message.content 里
answer = response.choices[0].message.content
print(f"\n  🤖 模型回复:\n  {answer}")


# ============================================================
# 第五步：演示流式请求（像打字机一样逐字生成）
# ============================================================
print("\n" + "=" * 60)
print("  🔥 进阶演示：流式请求（stream=True）")
print("=" * 60)
print("\n【4. 发起流式请求 —— 模型边生成边返回，体验打字机效果】")

stream = client.chat.completions.create(
    model="auto",
    messages=messages,
    temperature=0.7,
    max_tokens=200,
    stream=True,  # 👈 关键区别：开了流式
)

print("\n  🤖 模型回复（逐 token 打印）:\n  ", end="")
for chunk in stream:
    # 每个 chunk 只包含一点点增量内容
    delta = chunk.choices[0].delta
    if delta.content:
        print(delta.content, end="", flush=True)

print("\n")


# ============================================================
# 第六步：总结一下你刚看到的
# ============================================================
print("\n" + "=" * 60)
print("  📚 总结：LLM API 调用的 5 个核心环节")
print("=" * 60)
print("""
  ① 选择接口        —— Chat Completions（最常用）
  ② 构造 messages   —— system / user / assistant 三种 role
  ③ 选模型          —— model 参数，LM Studio 用 "auto" 就行
  ④ 选模式          —— stream=False 一次性 / stream=True 流式
  ⑤ 提取答案        —— response.choices[0].message.content

你可以动手试试：
  · 改 messages 里的 user 内容，问不同问题
  · 加更多 messages 做多轮对话（把上一次 assistant 的回复也放进去）
  · 改 temperature 和 max_tokens 看效果变化
""")
