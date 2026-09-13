"""模型计量和不可变完整结果文件的跨层契约。"""

from typing import Any, Protocol, runtime_checkable

from langchain_core.messages import AnyMessage


class ContextTokenCounter(Protocol):
    def window(self, model_id: str) -> int: ...

    def count_text(self, text: str, model_id: str | None = None) -> int: ...

    def count_request(
        self, messages: list[AnyMessage], tools: list[dict[str, Any]], model_id: str
    ) -> int: ...

    @property
    def method(self) -> str: ...


@runtime_checkable
class ContextResultStore(Protocol):
    async def save(
        self, conversation_id: str, source_id: str, output: dict[str, Any]
    ) -> str: ...

    async def read(self, conversation_id: str, result_ref: str) -> dict[str, Any]: ...
