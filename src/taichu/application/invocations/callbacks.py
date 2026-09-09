"""非 Agent Runnable 的 LangChain 模型回调。"""

from __future__ import annotations

from typing import Any

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult


class ModelResponseCapture(AsyncCallbackHandler):
    """捕获官方 ChatModel 调用产生的原始 AIMessage，供业务审计使用。"""

    def __init__(self) -> None:
        super().__init__()
        self.response: AIMessage | None = None

    async def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        del kwargs
        if not response.generations or not response.generations[0]:
            return
        generation = response.generations[0][0]
        if isinstance(generation, ChatGeneration) and isinstance(
            generation.message, AIMessage
        ):
            self.response = generation.message
