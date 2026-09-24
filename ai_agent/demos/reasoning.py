"""思维链 / 原生推理演示"""


def run_reasoning(client, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096):
    """思维链（Chain-of-Thought）：提示词引导模型逐步展示推理过程"""
    client.ensure_model_loaded()
    chain_prompt = (
        "请严格按以下格式回答：\n"
        "1. 【推理过程】逐步展示你的思考逻辑，每一步单独说明；\n"
        "2. 【结论】最后单独给出最终答案。\n"
    )
    guided = [{"role": "system", "content": "你是一个擅长逐步推理的助手。"}] + list(messages)
    last = guided[-1]
    guided[-1] = {**last, "content": f"{last['content']}\n\n{chain_prompt}"}
    response = client.create_chat_completion(
        model=client.model,
        messages=guided,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content


def run_native_reasoning(
    client,
    messages: list[dict],
    reasoning_effort: str = "high",
    temperature: float = 0.7,
    max_tokens: int = 8192,
):
    """原生 reasoning_effort 模式（qwen3 等推理模型）"""
    client.ensure_model_loaded()
    try:
        response = client.create_chat_completion(
            model=client.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
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
