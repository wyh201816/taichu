"""支持原生 Tool 调用的顺序 ChatModel 测试替身。"""

from __future__ import annotations

import json
from typing import Any, Callable, Sequence
from uuid import uuid4

from langchain_core.callbacks.manager import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import PrivateAttr


class NativeToolCallSequenceChatModel(BaseChatModel):
    """把顺序 JSON 响应包装成真实 ``AIMessage.tool_calls``。"""

    responses: list[AIMessage]
    _bound_tool_name: str | None = PrivateAttr(default=None)
    _seen_messages: list[list[BaseMessage]] = PrivateAttr(default_factory=list)
    _bound_tool_definitions: list[list[dict[str, Any]]] = PrivateAttr(
        default_factory=list
    )
    _bound_tool_choices: list[str | None] = PrivateAttr(default_factory=list)

    @property
    def seen_messages(self) -> tuple[tuple[BaseMessage, ...], ...]:
        return tuple(tuple(messages) for messages in self._seen_messages)

    @property
    def bound_tool_definitions(self) -> tuple[tuple[dict[str, Any], ...], ...]:
        return tuple(tuple(tools) for tools in self._bound_tool_definitions)

    @property
    def bound_tool_choices(self) -> tuple[str | None, ...]:
        return tuple(self._bound_tool_choices)

    @property
    def _llm_type(self) -> str:
        return "taichu-native-tool-sequence"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        del kwargs
        formatted = [convert_to_openai_tool(tool) for tool in tools]
        self._bound_tool_definitions.append(formatted)
        self._bound_tool_choices.append(tool_choice)
        bound = self.model_copy()
        bound._bound_tool_name = _selected_tool_name(tools, tool_choice)
        return bound

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        self._seen_messages.append(list(messages))
        if not self.responses:
            raise RuntimeError("没有可用的模拟 LLM 响应。")
        message = self.responses.pop(0)
        if self._bound_tool_name is not None and not message.tool_calls:
            try:
                arguments = json.loads(str(message.content))
            except json.JSONDecodeError:
                pass
            else:
                message = AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": f"call_{uuid4().hex}",
                            "name": self._bound_tool_name,
                            "args": arguments,
                            "type": "tool_call",
                        }
                    ],
                )
        return ChatResult(generations=[ChatGeneration(message=message)])


def _selected_tool_name(
    tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
    tool_choice: str | None,
) -> str:
    if tool_choice and tool_choice not in {"auto", "any", "required", "none"}:
        return tool_choice
    if not tools:
        raise ValueError("原生 Tool 调用缺少 Tool 定义。")
    function = convert_to_openai_tool(tools[-1])["function"]
    return str(function["name"])
