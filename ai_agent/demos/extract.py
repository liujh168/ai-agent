"""结构化输出演示：按字段提取 JSON，带围栏清洗 + 失败重试"""

import json
import re


def run_structured_extract(
    client, text: str, fields: str, temperature: float = 0.0, max_tokens: int = 1024
) -> str:
    """结构化输出：按字段提取 JSON，带围栏清洗 + 失败重试（强化 prompt）"""
    client.ensure_model_loaded()
    prompt = (
        "你是一个数据提取助手。请从用户提供的文本中提取以下字段："
        f"{fields}。\n只输出一个合法的 JSON 对象，不要输出任何解释文字、不要使用 markdown 围栏。\n\n"
        f"文本：{text}"
    )

    def _generate(extra_hint: str = "") -> str:
        return (
            client.create_chat_completion(
                label="结构化提取" + ("（重试）" if extra_hint else ""),
                model=client.model,
                messages=[{"role": "user", "content": prompt + extra_hint}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            .choices[0]
            .message.content
        )

    def _parse(content: str) -> dict:
        content = content.strip()
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.MULTILINE)
        return json.loads(content)

    raw = _generate()
    try:
        data = _parse(raw)
    except json.JSONDecodeError:
        raw = _generate(
            '\n\n上一次输出不是合法 JSON，这次请**严格只输出** {"字段1": 值1, "字段2": 值2} 格式的 JSON。'
        )
        data = _parse(raw)

    return json.dumps(data, ensure_ascii=False, indent=2)
