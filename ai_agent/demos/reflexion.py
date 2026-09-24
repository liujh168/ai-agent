"""反思与自我修正演示"""


def run_reflexion(
    client, messages: list[dict], rounds: int = 3, temperature: float = 0.7, max_tokens: int = 2048
):
    """反思与自我修正：迭代多轮 作答 → 自我批评 → 修正"""
    client.ensure_model_loaded()
    history = [{"role": "system", "content": "你是一个严谨、追求答案正确的助手。"}] + list(messages)

    def _ask() -> str:
        return (
            client.create_chat_completion(
                model=client.model,
                messages=history,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            .choices[0]
            .message.content
        )

    draft = _ask()
    print(f"\n[反思·第1轮 初稿] {draft}")

    for i in range(1, rounds):
        history.append({"role": "assistant", "content": draft})
        history.append(
            {
                "role": "user",
                "content": (
                    f"请以严格批评者的身份审查上面第 {i} 轮的回答，"
                    "列出其中的错误、漏洞、缺失或可改进之处。不要重写答案，只输出批评意见。"
                ),
            }
        )
        critique = _ask()
        print(f"\n[反思·第{i + 1}轮 批评] {critique}")
        history.append({"role": "assistant", "content": critique})
        history.append(
            {
                "role": "user",
                "content": (
                    "请结合上面的批评意见重新认真作答最初的问题，输出修正后的答案。"
                    + ("这是最后一轮修正，请给出最终完整答案。" if i == rounds - 1 else "")
                ),
            }
        )
        draft = _ask()
        print(f"\n[反思·第{i + 1}轮 修正] {draft}")
    return draft
