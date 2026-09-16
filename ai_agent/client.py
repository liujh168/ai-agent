"""
LM Studio 本地 LLM API 客户端
===============================
负责所有与 LM Studio OpenAI 兼容 API 直接交互的逻辑。
RAGKnowledgeBase 已拆分到 ai_agent/rag.py。
"""

import base64
import json
import logging
import os
import re
import time

from openai import OpenAI
import requests

log = logging.getLogger("Agent")


# ============================================================
# LM Studio 客户端
# ============================================================

class LMStudioClient:
    """LM Studio API 客户端，自动检测模型是否已加载"""

    def __init__(self, base_url: str = "http://localhost:1234/v1", api_key: str = "lm-studio", model: str = "auto"):
        self.base_url = os.environ.get("LM_STUDIO_BASE_URL", base_url)
        self.api_key = api_key
        self.model = os.environ.get("LM_STUDIO_MODEL", model)
        self.client = OpenAI(base_url=self.base_url, api_key=api_key)
        self._model_checked = False      # 只在首次调用时打印状态
        self._model_cached_name = None   # 首次 ensure_model_loaded 成功后缓存，跳过后续 HTTP 检查

    def check_server_status(self) -> dict:
        """检测 LM Studio 服务是否运行，以及模型是否已加载"""
        result = {
            "server_running": False,
            "model_loaded": False,
            "model_name": None,
            "error": None,
        }
        try:
            resp = requests.get(f"{self.base_url}/models", timeout=5)
            if resp.status_code == 200:
                result["server_running"] = True
                data = resp.json()
                models = data.get("data", [])
                if models:
                    result["model_loaded"] = True
                    result["model_name"] = models[0].get("id", "unknown")
            else:
                result["error"] = f"服务器返回状态码 {resp.status_code}"
        except requests.ConnectionError:
            result["error"] = "无法连接到 LM Studio 服务，请确认 LM Studio 已启动并开启本地服务"
        except requests.Timeout:
            result["error"] = "连接 LM Studio 超时"
        except Exception as e:
            result["error"] = str(e)
        return result

    def ensure_model_loaded(self) -> str:
        """确保模型已加载，未加载则抛出异常。成功后缓存结果，后续调用直接返回省掉 HTTP 健康检查。"""
        if self._model_cached_name is not None:
            return self._model_cached_name
        status = self.check_server_status()
        if not status["server_running"]:
            raise RuntimeError(status["error"])
        if not status["model_loaded"]:
            raise RuntimeError(
                "LM Studio 服务已运行，但未加载模型。请在 LM Studio 中加载一个模型后重试。"
            )
        if not self._model_checked:
            target = f" → {self.model}" if self.model != "auto" else " (auto)"
            log.info("服务运行中，已加载模型: %s%s", status["model_name"], target)
            self._model_checked = True
        self._model_cached_name = status["model_name"]
        return status["model_name"]

    def summarize_messages(self, messages: list[dict], keep_recent: int = 4) -> list[dict]:
        """长对话摘要压缩：保留最近 N 条完整消息，较早历史交给 LLM 做摘要"""
        if len(messages) <= keep_recent + 2:
            return messages

        system_msg = messages[0] if messages and messages[0]["role"] == "system" else None
        recent = messages[-keep_recent:]
        to_summarize = messages[1:-keep_recent] if system_msg else messages[:-keep_recent]

        # 清洗掉 tool_calls / tool 类型的噪声消息（纯对话摘要）
        clean = [m for m in to_summarize if m["role"] in ("user", "assistant")]
        history_text = "\n".join(
            f"[{m['role'].upper()}]: {m['content']}" for m in clean
        )

        summary = self.chat(
            [{"role": "user", "content": (
                "请对以下对话历史做简洁摘要（200-300 字），保留关键事实、决定、已完成任务和"
                "待续事项，用第三人称叙述，不要省略时间、数字、名字等硬信息。\n\n"
                f"=== 对话历史 ===\n{history_text}\n\n=== 摘要 ==="
            )}],
            temperature=0.1, max_tokens=512,
        )

        new_history = []
        if system_msg:
            new_history.append(system_msg)
        new_history.append({"role": "assistant", "content": f"【历史摘要】{summary}"})
        new_history.extend(recent)
        return new_history

    def chat(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 2048, stream: bool = False):
        """发送聊天请求，自动检测模型加载状态"""
        self.ensure_model_loaded()
        response = self.client.chat.completions.create(
            model=self.model, messages=messages,
            temperature=temperature, max_tokens=max_tokens, stream=stream,
        )
        if stream:
            return response
        return response.choices[0].message.content

    def chat_stream(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 2048):
        """流式聊天，逐 token 输出"""
        self.ensure_model_loaded()
        stream = self.client.chat.completions.create(
            model=self.model, messages=messages,
            temperature=temperature, max_tokens=max_tokens, stream=True,
        )
        def _generator():
            for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    yield delta.content
        return _generator()

    def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_map: dict,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        max_iterations: int = 5,
        skill_registry=None,
        mcp_manager=None,
    ):
        """Tool Calling 循环：自主选择工具 → 执行 → 把结果喂回模型，直到模型返回最终文本"""
        self.ensure_model_loaded()

        # ---------- Skill 路由：关键词匹配命中 Skill → 跳到 Skill 执行 ----------
        if skill_registry is not None:
            last_user = next(
                (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
            )
            if last_user:
                matched = skill_registry.match(last_user)
                if matched is not None:
                    log.info("[Skill 激活] %s（来自 chat_with_tools 路由）", matched.name)
                    return self.chat_with_skill(
                        messages, matched, tool_map,
                        temperature=temperature, max_tokens=max_tokens,
                        mcp_manager=mcp_manager,
                    )
        # ---------- 下面是原有逻辑，一行都不用改 ----------
        history = list(messages)

        for _ in range(max_iterations):
            response = self.client.chat.completions.create(
                model=self.model, messages=history, tools=tools,
                temperature=temperature, max_tokens=max_tokens,
            )
            choice = response.choices[0]

            if choice.finish_reason == "stop":
                return choice.message.content

            if choice.finish_reason == "tool_calls":
                tool_calls = choice.message.tool_calls
                history.append(choice.message.model_dump())

                for tool_call in tool_calls:
                    func_name = tool_call.function.name
                    func_args = tool_call.function.arguments
                    print(f"[Tool Call] 调用工具: {func_name}({func_args})")
                    t0 = time.time()

                    if func_name in tool_map:
                        try:
                            args = json.loads(func_args)
                        except json.JSONDecodeError:
                            result = f"错误：无法解析参数 JSON，工具 '{func_name}' 需要的参数被模型输出异常。请列出需要的正确参数格式。"
                            history.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})
                            continue
                        result = str(tool_map[func_name](**args))
                    else:
                        result = f"错误: 未知工具 {func_name}"

                    dt = time.time() - t0
                    print(f"[Tool Result] {result[:200]}... ({dt*1000:.0f}ms)")
                    log.info("tool %s completed in %.0fms", func_name, dt * 1000)
                    history.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})

        return "已达到最大工具调用迭代次数。"

    # ============================================================
    # Skill 执行 — 场景化 System Prompt + 工具子集过滤 + 多轮循环
    # ============================================================

    def chat_with_skill(
        self,
        messages: list[dict],
        skill,           # SkillDef 实例（避免循环导入，不传具体类型）
        tool_map: dict,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        mcp_manager=None,
    ) -> str:
        """
        Skill 执行循环：
        1. 把 Skill 的 instructions 注入为场景化 system prompt（替换默认 system）
        2. 过滤 tool_map 只保留 Skill 声明的 required_tools
        3. 复用 chat_with_tools 的 Function Calling 循环逻辑，最多跑 skill.max_rounds 轮
        """
        from ai_agent.app_core import get_tool_schemas

        self.ensure_model_loaded()

        # ---------- 1. 构造 Skill 专属 history：用 Skill instructions 替换 system prompt ----------
        other_msgs = []
        has_system = False
        for m in messages:
            if m["role"] == "system" and not has_system:
                other_msgs.append({"role": "system", "content": skill.instructions})
                has_system = True
            else:
                other_msgs.append(m)
        if not has_system:
            other_msgs.insert(0, {"role": "system", "content": skill.instructions})

        history = list(other_msgs)

        # ---------- 2. 过滤 tool_map 和对应 schema ----------
        required = set(skill.required_tools)
        if required:
            filtered_map = {name: fn for name, fn in tool_map.items() if name in required}
        else:
            filtered_map = dict(tool_map)

        # 生成过滤后的 tool schemas（包含 MCP 远端工具如果有 mcp_manager）
        all_schemas = get_tool_schemas(mcp_manager)
        filtered_schemas = [
            s for s in all_schemas
            if s["function"]["name"] in filtered_map
        ]

        log.info("chat_with_skill: %s, 可用工具=%d/%d, max_rounds=%d",
                 skill.name, len(filtered_map), len(tool_map), skill.max_rounds)

        # ---------- 3. 多轮 Function Calling 循环（复用 chat_with_tools 的逻辑） ----------
        for _ in range(skill.max_rounds):
            response = self.client.chat.completions.create(
                model=self.model, messages=history, tools=filtered_schemas,
                temperature=temperature, max_tokens=max_tokens,
            )
            choice = response.choices[0]

            if choice.finish_reason == "stop":
                return choice.message.content

            if choice.finish_reason == "tool_calls":
                tool_calls = choice.message.tool_calls
                history.append(choice.message.model_dump())

                for tool_call in tool_calls:
                    func_name = tool_call.function.name
                    func_args = tool_call.function.arguments
                    print(f"[Skill:{skill.name}] 调用工具: {func_name}({func_args})")
                    t0 = time.time()

                    if func_name in filtered_map:
                        try:
                            args = json.loads(func_args)
                        except json.JSONDecodeError:
                            result = f"错误：无法解析参数 JSON，工具 '{func_name}' 需要的参数被模型输出异常。"
                            history.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})
                            continue
                        try:
                            result = str(filtered_map[func_name](**args))
                        except Exception as e:
                            result = f"工具执行异常: {type(e).__name__}: {e}"
                    else:
                        result = f"错误: 未知工具 {func_name}（Skill {skill.name} 只允许: {', '.join(filtered_map.keys())}）"

                    dt = time.time() - t0
                    preview = result[:200] + ("..." if len(result) > 200 else "")
                    print(f"[Skill:{skill.name}] 结果 ({dt*1000:.0f}ms): {preview}")
                    log.info("skill %s tool %s completed in %.0fms", skill.name, func_name, dt * 1000)
                    history.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})

        # max_rounds 到了，返回最后一条助手消息或最后一条内容
        for m in reversed(history):
            if m["role"] == "assistant" and m.get("content"):
                return m["content"]
        return f"已达 Skill {skill.name} 最大执行轮次 ({skill.max_rounds})，未得到最终答案。"

    # ============================================================
    # ReAct — Reasoning + Acting（文本式推理轨迹可见）
    # ============================================================

    # ReAct 输出解析正则
    _REACT_THOUGHT_RE = re.compile(r"Thought\s*[:：]\s*(.+?)(?=\n\s*(?:Action|Final)\s*[:：]|$)", re.DOTALL | re.IGNORECASE)
    _REACT_ACTION_RE = re.compile(r"Action\s*[:：]\s*(\w+)", re.IGNORECASE)
    _REACT_ACTION_INPUT_RE = re.compile(
        r"Action\s*Input\s*[:：]\s*(.+?)(?=\n\s*(?:Observation|Final|Thought|Action)\s*[:：]|$)",
        re.DOTALL | re.IGNORECASE,
    )
    _REACT_FINAL_RE = re.compile(r"Final\s*Answer\s*[:：]\s*(.+)$", re.DOTALL | re.IGNORECASE)

    def chat_with_react(
        self,
        messages: list[dict],
        tool_map: dict,
        max_iterations: int = 6,
        temperature: float = 0.5,
        max_tokens: int = 2048,
    ) -> str:
        """
        ReAct（Reasoning + Acting）模式：
        让模型交替输出 Thought（思考） → Action（工具名） → Action Input（参数），
        我们执行工具后把 Observation（结果）喂回，直到模型给出 Final Answer。

        与 chat_with_tools 的核心区别：
        chat_with_tools 用原生 Function Calling，思考过程是黑盒；
        chat_with_react 用文本式 Prompt 技巧，每一步 Thought 都打印出来，
        让学习者看清 Agent 是"边想边做"还是"先想好再做"。
        """
        self.ensure_model_loaded()

        # 构造工具描述文本（ReAct 用自然语言告诉模型有哪些工具）
        tool_descriptions = ""
        for name, func in tool_map.items():
            doc = (func.__doc__ or "无描述").strip().split("\n")[0]
            tool_descriptions += f"- {name}: {doc}\n"

        react_system = (
            "你是一个会边想边做的 Agent。面对用户问题，你会交替进行：\n"
            "  1. Thought（思考）：用一句话写下你现在在想什么、打算做什么\n"
            "  2. Action（行动）：选择要用的工具名\n"
            "  3. Action Input（行动输入）：给工具的参数，用合法 JSON 格式\n"
            "  4. 收到 Observation（观察结果）后，继续下一轮 Thought → Action\n"
            "  5. 当信息足够时，给出 Final Answer（最终回答）\n\n"
            "严格按以下格式输出（每次只输出一个完整 Thought + Action + Action Input，或一个 Final Answer）：\n"
            "  Thought: 你的思考\n"
            "  Action: 工具名\n"
            "  Action Input: {\"key\": \"value\"}\n"
            "  ... （我们会返回 Observation）\n"
            "  ... （继续循环）\n"
            "  Final Answer: 完整回答用户的问题\n\n"
            "可用工具：\n" + tool_descriptions
        )

        history = [{"role": "system", "content": react_system}] + list(messages)

        print("\n╭──────── ReAct 推理轨迹 ────────")
        for iteration in range(1, max_iterations + 1):
            print(f"│\n│  🔄 第 {iteration} 轮")
            response = self.client.chat.completions.create(
                model=self.model, messages=history,
                temperature=temperature, max_tokens=max_tokens,
            )
            raw = response.choices[0].message.content or ""
            history.append({"role": "assistant", "content": raw})

            # ---- 尝试解析 Final Answer ----
            m_final = self._REACT_FINAL_RE.search(raw)
            if m_final:
                final = m_final.group(1).strip()
                print(f"│  ✅ Final Answer: {final[:120]}{'…' if len(final) > 120 else ''}")
                print(f"╰──────── 共 {iteration} 轮 ────────\n")
                return final

            # ---- 解析 Thought / Action / Action Input ----
            thought_m = self._REACT_THOUGHT_RE.search(raw)
            action_m = self._REACT_ACTION_RE.search(raw)
            input_m = self._REACT_ACTION_INPUT_RE.search(raw)

            if thought_m:
                thought = thought_m.group(1).strip()
                print(f"│  💭 Thought: {thought}")

            if action_m:
                action_name = action_m.group(1).strip()
                args_str = input_m.group(1).strip() if input_m else "{}"

                if action_name not in tool_map:
                    obs = f"❌ 未知工具: {action_name}。可用工具: {', '.join(tool_map.keys())}"
                else:
                    try:
                        args = json.loads(args_str) if args_str else {}
                    except json.JSONDecodeError:
                        obs = f"❌ Action Input 不是合法 JSON，收到的内容：{args_str}"
                    else:
                        print(f"│  🔧 Action: {action_name}")
                        print(f"│  📥 Input:  {args_str}")
                        t0 = time.time()
                        try:
                            tool_result = str(tool_map[action_name](**args))
                        except Exception as e:
                            tool_result = f"工具执行异常: {type(e).__name__}: {e}"
                        dt = time.time() - t0
                        obs = tool_result
                        # 截断过长结果，避免上下文爆炸
                        if len(obs) > 800:
                            obs = obs[:800] + f"\n... [结果过长已截断，总长 {len(obs)} 字符]"

                print(f"│  👁️ Observation: {obs[:200]}{'…' if len(obs) > 200 else ''}")
                history.append({"role": "user", "content": f"Observation: {obs}"})
                continue

            # 如果既没 Final Answer 也没 Action，说明模型跑偏了
            print(f"│  ⚠️ 模型未按 ReAct 格式输出，原始内容: {raw[:200]}...")
            history.append({
                "role": "user",
                "content": (
                    "上一次输出未识别到 Thought/Action/Action Input 或 Final Answer。"
                    "请严格按格式重新输出，先写 Thought，再写 Action 和 Action Input，"
                    "或者如果已能回答则写 Final Answer。"
                ),
            })
            continue

        print(f"╰──────── 已达最大迭代 {max_iterations} 轮 ────────\n")
        # 返回最后一轮模型说的东西（即使不是 Final Answer）
        return history[-1]["content"] if history else "已达最大迭代次数"

    def chat_with_reasoning(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096):
        """思维链（Chain-of-Thought）：提示词引导模型逐步展示推理过程"""
        self.ensure_model_loaded()
        chain_prompt = (
            "请严格按以下格式回答：\n"
            "1. 【推理过程】逐步展示你的思考逻辑，每一步单独说明；\n"
            "2. 【结论】最后单独给出最终答案。\n"
        )
        guided = [{"role": "system", "content": "你是一个擅长逐步推理的助手。"}] + list(messages)
        last = guided[-1]
        guided[-1] = {**last, "content": f"{last['content']}\n\n{chain_prompt}"}
        response = self.client.chat.completions.create(
            model=self.model, messages=guided, temperature=temperature, max_tokens=max_tokens,
        )
        return response.choices[0].message.content

    def chat_with_native_reasoning(self, messages: list[dict], reasoning_effort: str = "high", temperature: float = 0.7, max_tokens: int = 8192):
        """原生 reasoning_effort 模式（qwen3 等推理模型）"""
        self.ensure_model_loaded()
        try:
            response = self.client.chat.completions.create(
                model=self.model, messages=messages,
                temperature=temperature, max_tokens=max_tokens, reasoning_effort=reasoning_effort,
            )
        except Exception as e:
            raise RuntimeError(
                f"当前模型/服务不支持 reasoning_effort 参数（{e}）。"
                "可改用 chat_with_reasoning 的提示词引导方式。"
            )
        message = response.choices[0].message
        reasoning = getattr(message, "reasoning_content", None)
        if reasoning:
            return f"[模型内部推理]\n{reasoning}\n\n[最终回答]\n{message.content}"
        return message.content

    def chat_with_reflexion(self, messages: list[dict], rounds: int = 3, temperature: float = 0.7, max_tokens: int = 2048):
        """反思与自我修正：迭代多轮 作答 → 自我批评 → 修正"""
        self.ensure_model_loaded()
        history = [{"role": "system", "content": "你是一个严谨、追求答案正确的助手。"}] + list(messages)

        def _ask() -> str:
            return self.client.chat.completions.create(
                model=self.model, messages=history,
                temperature=temperature, max_tokens=max_tokens,
            ).choices[0].message.content

        draft = _ask()
        print(f"\n[反思·第1轮 初稿] {draft}")

        for i in range(1, rounds):
            history.append({"role": "assistant", "content": draft})
            history.append({"role": "user", "content": (
                f"请以严格批评者的身份审查上面第 {i} 轮的回答，"
                "列出其中的错误、漏洞、缺失或可改进之处。不要重写答案，只输出批评意见。"
            )})
            critique = _ask()
            print(f"\n[反思·第{i + 1}轮 批评] {critique}")
            history.append({"role": "assistant", "content": critique})
            history.append({"role": "user", "content": (
                "请结合上面的批评意见重新认真作答最初的问题，输出修正后的答案。"
                + ("这是最后一轮修正，请给出最终完整答案。" if i == rounds - 1 else "")
            )})
            draft = _ask()
            print(f"\n[反思·第{i + 1}轮 修正] {draft}")
        return draft

    def analyze_image(self, image_path: str, question: str = "请描述这张图片的内容", max_tokens: int = 1024):
        """图像识别：本地图片 base64 传给多模态模型"""
        self.ensure_model_loaded()
        mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
        ext = image_path.lower()
        ext = next((k for k in mime_map if ext.endswith(k)), ".png")
        mime = mime_map[ext]
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ]}],
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content

    def plan_and_execute(self, task: str, max_subtasks: int = 3, temperature: float = 0.7, max_tokens: int = 2048) -> str:
        """任务规划分解（Planner-Executor）：先规划后执行，逐个子任务完成"""
        self.ensure_model_loaded()
        plan_prompt = (
            f"请将下面的任务拆解为最多 {max_subtasks} 个有序子任务，"
            "每行一个，格式为「1. 子任务描述」。只输出子任务清单，不要其他内容。\n\n"
            f"任务：{task}"
        )
        plan_text = self.client.chat.completions.create(
            model=self.model, messages=[{"role": "user", "content": plan_prompt}],
            temperature=temperature, max_tokens=max_tokens,
        ).choices[0].message.content

        subtasks = [m.group(1).strip() for m in re.finditer(r"^\s*\d+[.、)]\s*(.+)$", plan_text or "", re.MULTILINE)]
        if not subtasks:
            subtasks = [task]
        subtasks = subtasks[:max_subtasks]

        print(f"\n[任务规划] 将任务拆解为 {len(subtasks)} 个子任务：")
        for i, st in enumerate(subtasks, 1):
            print(f"  {i}. {st}")

        accumulated = ""
        parts = []
        for i, st in enumerate(subtasks, 1):
            prev = accumulated if accumulated else "（暂无）"
            if len(prev) > 600:
                prev = "…（因篇幅较长已截断，保留最近内容）…\n" + prev[-600:]
            exec_prompt = (
                f"整体任务：{task}\n\n当前要完成的子任务：{st}\n\n"
                f"已完成部分：\n{prev}\n\n"
                "请完成这个子任务，输出可以直接合并到最终成果的内容，不要复述整体任务。"
            )
            result = self.client.chat.completions.create(
                model=self.model, messages=[{"role": "user", "content": exec_prompt}],
                temperature=temperature, max_tokens=max_tokens,
            ).choices[0].message.content
            print(f"\n[子任务 {i}/{len(subtasks)}] {st}")
            print(result)
            accumulated += f"\n{result}"
            parts.append(f"【子任务 {i}：{st}】\n{result}")

        summary = "\n".join(
            f"【子任务 {i}：{st}】\n{res[:200]}{'…' if len(res) > 200 else ''}"
            for i, (st, res) in enumerate(zip(subtasks, parts), 1)
        )
        return f"已完成 {len(subtasks)} 个子任务（完整内容已在上方直接展示）。\n{summary}"

    def structured_extract(self, text: str, fields: str, temperature: float = 0.0, max_tokens: int = 1024) -> str:
        """结构化输出：按字段提取 JSON，带围栏清洗 + 失败重试（强化 prompt）"""
        self.ensure_model_loaded()
        prompt = (
            "你是一个数据提取助手。请从用户提供的文本中提取以下字段："
            f"{fields}。\n只输出一个合法的 JSON 对象，不要输出任何解释文字、不要使用 markdown 围栏。\n\n"
            f"文本：{text}"
        )

        def _generate(extra_hint: str = "") -> str:
            return self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt + extra_hint}],
                temperature=temperature, max_tokens=max_tokens,
            ).choices[0].message.content

        def _parse(content: str) -> dict:
            content = content.strip()
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.MULTILINE)
            return json.loads(content)

        raw = _generate()
        try:
            data = _parse(raw)
        except json.JSONDecodeError:
            raw = _generate("\n\n上一次输出不是合法 JSON，这次请**严格只输出** {\"字段1\": 值1, \"字段2\": 值2} 格式的 JSON。")
            data = _parse(raw)

        return json.dumps(data, ensure_ascii=False, indent=2)
