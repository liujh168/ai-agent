"""demos 包纯逻辑测试：ReAct 输出解析 + structured_extract 重试（不依赖 LM Studio）"""

import json
from types import SimpleNamespace

from ai_agent.demos import parse_react_output, run_structured_extract


class TestParseReactOutput:
    def test_full_step(self):
        raw = 'Thought: 先查天气\nAction: get_weather\nAction Input: {"city": "北京"}'
        parsed = parse_react_output(raw)
        assert parsed["thought"] == "先查天气"
        assert parsed["action"] == "get_weather"
        assert parsed["action_input"] == '{"city": "北京"}'
        assert parsed["final"] is None

    def test_final_answer(self):
        parsed = parse_react_output("Thought: 信息够了\nFinal Answer: 北京今天晴")
        assert parsed["final"] == "北京今天晴"

    def test_chinese_colon(self):
        raw = 'Thought：想一下\nAction：calculator\nAction Input：{"expression": "1+1"}'
        parsed = parse_react_output(raw)
        assert parsed["thought"] == "想一下"
        assert parsed["action"] == "calculator"
        assert parsed["action_input"] == '{"expression": "1+1"}'

    def test_unformatted_output(self):
        parsed = parse_react_output("随便聊聊，没有格式")
        assert parsed == {"final": None, "thought": None, "action": None, "action_input": None}


class _FakeLLMClient:
    """按队列返回预设输出的假 client"""

    def __init__(self, outputs):
        self.model = "fake"
        self.outputs = list(outputs)
        self.prompts = []

    def ensure_model_loaded(self):
        return self.model

    def create_chat_completion(self, **kwargs):
        self.prompts.append(kwargs["messages"][-1]["content"])
        content = self.outputs.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class TestStructuredExtract:
    def test_fences_stripped_without_retry(self):
        client = _FakeLLMClient(['```json\n{"name": "小明", "age": 25}\n```'])
        out = run_structured_extract(client, "小明25岁", "姓名,年龄")
        assert json.loads(out) == {"name": "小明", "age": 25}
        assert len(client.prompts) == 1  # 围栏清洗后直接成功，无需重试

    def test_retry_with_stronger_prompt_on_invalid_json(self):
        client = _FakeLLMClient(["这不是 JSON", '{"name": "小明"}'])
        out = run_structured_extract(client, "小明", "姓名")
        assert json.loads(out) == {"name": "小明"}
        assert len(client.prompts) == 2
        assert "严格只输出" in client.prompts[1]  # 第二次带强化提示
