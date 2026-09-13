"""按模型计量完整原生请求，未知窗口不能用旧字符配额代替。"""

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace

from taichu.infrastructure.llm.context_tokens import ModelContextTokens


def test_window_and_full_native_request_count():
    counter = ModelContextTokens(windows={"deployment": 50_000})
    assert counter.window("deployment") == 50_000
    with pytest.raises(ValueError, match="未配置上下文窗口"):
        counter.window("unknown")
    messages = [SystemMessage(content="稳定规则"), HumanMessage(content="当前请求")]
    base = counter.count_request(messages, [], "deployment")
    tools = [
        {
            "type": "function",
            "function": {
                "name": "read",
                "description": "原生工具定义" * 100,
                "parameters": {"type": "object"},
            },
        }
    ]
    assert counter.count_request(messages, tools, "deployment") > base + 1000
    assert counter.count_text("中文") == 6


def test_configured_model_uses_its_tokenizer(tmp_path):
    tokenizer = Tokenizer(
        WordLevel({"[UNK]": 0, "hello": 1, "world": 2}, unk_token="[UNK]")
    )
    tokenizer.pre_tokenizer = Whitespace()
    path = tmp_path / "tokenizer.json"
    tokenizer.save(str(path))
    counter = ModelContextTokens(
        windows={"deployment": 50_000}, tokenizer_paths={"deployment": str(path)}
    )
    assert counter.count_text("hello world", "deployment") == 2
    assert counter.count_text("hello world", "another-deployment") == 11
