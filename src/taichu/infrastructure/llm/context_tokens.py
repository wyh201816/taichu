"""模型窗口与请求级计量；不设置任何记忆层配额。"""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from langchain_core.messages import AnyMessage


# 官方模型窗口。网关可能限制窗口，部署配置可按模型显式覆盖。
# https://developers.openai.com/api/docs/models
# https://platform.claude.com/docs/en/build-with-claude/context-windows
# https://api-docs.deepseek.com/quick_start/pricing
MODEL_CONTEXT_WINDOWS = {
    **{f"gpt-5-6-{name}": 1_050_000 for name in ("luna", "terra", "sol")},
    **{f"deepseek-v4-{name}": 1_000_000 for name in ("flash", "pro")},
    **{
        f"claude-{name}": 1_000_000
        for name in ("opus-4-6", "opus-4-7", "opus-4-8", "sonnet-4-6", "sonnet-5")
    },
}


class ModelContextTokens:
    """可配置官方 tokenizer；缺失时采用明确标注的 UTF-8 保守估算。

    字节估算有意避免中文被字符/4低估，不宣称等于供应商实际计费。
    原生消息封装和工具定义全部计入；真实用量仍由响应 usage 记录。
    """

    def __init__(
        self,
        windows: Mapping[str, int] | None = None,
        tokenizer_paths: Mapping[str, str] | None = None,
    ) -> None:
        self._windows = {**MODEL_CONTEXT_WINDOWS, **(windows or {})}
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in self._windows.values()
        ):
            raise ValueError("模型上下文窗口必须为正整数 Token 数。")
        self._tokenizers: dict[str, Any] = {}
        for model_id, path in (tokenizer_paths or {}).items():
            from tokenizers import Tokenizer

            self._tokenizers[model_id] = Tokenizer.from_file(str(Path(path)))

    @property
    def method(self) -> str:
        return "配置的模型分词器；未配置模型使用 UTF-8 字节保守估算（含协议余量）"

    def window(self, model_id: str) -> int:
        if model_id not in self._windows:
            raise ValueError(
                f"模型“{model_id}”未配置上下文窗口，不能安全判断压缩阈值。"
            )
        return self._windows[model_id]

    def count_text(self, text: str, model_id: str | None = None) -> int:
        tokenizer = self._tokenizers.get(model_id or "")
        if tokenizer is not None:
            return len(tokenizer.encode(text).ids)
        return len(text.encode("utf-8"))

    def count_request(
        self, messages: list[AnyMessage], tools: list[dict[str, Any]], model_id: str
    ) -> int:
        payload = {
            "messages": [
                {
                    "role": getattr(message, "role", message.type),
                    "content": message.content,
                    **(
                        {"tool_calls": message.tool_calls}
                        if getattr(message, "tool_calls", None)
                        else {}
                    ),
                    **(
                        {"tool_call_id": message.tool_call_id}
                        if hasattr(message, "tool_call_id")
                        else {}
                    ),
                }
                for message in messages
            ],
            "tools": tools,
        }
        return (
            self.count_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")), model_id
            )
            + 32 * len(messages)
            + 256
        )
