"""仅供测试使用的 LangChain 模型与太初模型网关替身。"""

from __future__ import annotations

from collections.abc import AsyncIterator
import json
from typing import Any

from langchain_core.callbacks.manager import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    UsageMetadata,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import PrivateAttr

from taichu.application.contracts.llm import LLMModelIdentity, LLMModelProfile
from taichu.infrastructure.llm.contracts import (
    LLMCost,
    LLMRequest,
    LLMResponse,
    LLMStreamEvent,
    LLMToolCall,
    LLMToolCallChunk,
    LLMUsage,
)


class MVPNoRealLLMChatModel(BaseChatModel):
    """不会访问外部服务的确定性 ChatModel 测试替身。"""

    response_text: str = (
        '{"card_type":"suggestion","content":'
        '{"body":"这是本地模拟输出，仅用于检查链路。"}}'
    )
    _bound_tool_name: str | None = PrivateAttr(default=None)

    @property
    def _llm_type(self) -> str:
        return "taichu-test-mock"

    def bind_tools(
        self,
        tools: Any,
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> MVPNoRealLLMChatModel:
        """返回会把响应载荷作为原生 Tool 调用发出的绑定副本。"""
        del kwargs
        bound = self.model_copy(deep=True)
        if tools and tool_choice in {None, "auto", "any", "required"}:
            bound._bound_tool_name = str(
                convert_to_openai_tool(tools[0])["function"]["name"]
            )
        else:
            bound._bound_tool_name = tool_choice
        return bound

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del messages, stop, run_manager, kwargs
        if self._bound_tool_name:
            try:
                arguments = json.loads(self.response_text)
            except json.JSONDecodeError:
                arguments = {"raw_text": self.response_text}
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "id": "call_mvp_structured_output",
                                    "name": self._bound_tool_name,
                                    "args": arguments,
                                    "type": "tool_call",
                                }
                            ],
                        )
                    )
                ]
            )
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=self.response_text))]
        )


class LangChainTestGateway:
    """把测试 ChatModel 适配为组合根所需的供应商网关契约。"""

    def __init__(
        self,
        chat_model: BaseChatModel,
        model_identity: LLMModelIdentity,
        *,
        default_model_id: str = "deepseek-v4-pro",
    ) -> None:
        self._chat_model = chat_model
        self._model_identity = model_identity
        actual_id = model_identity.model_id or default_model_id
        self._profile = LLMModelProfile(
            id=actual_id,
            display_name=actual_id,
            provider="rightcode",
            upstream_model=actual_id,
            wire_protocol="openai_responses",
            enabled=True,
            is_default=True,
            supports_streaming=True,
            upstream_verified=model_identity.known,
        )

    @property
    def model_identity(self) -> LLMModelIdentity:
        return self._model_identity

    def list_models(self) -> list[LLMModelProfile]:
        return [self._profile]

    async def complete(self, request: LLMRequest) -> LLMResponse:
        model = self._model_for_request(request)
        message = await model.ainvoke(_messages(request))
        if not isinstance(message, AIMessage):
            raise TypeError("测试模型必须返回 AIMessage。")
        return _response(message, self._profile)

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamEvent]:
        yield LLMStreamEvent(event_type="started")
        model = self._model_for_request(request)
        final_message: AIMessage | AIMessageChunk | None = None
        async for message in model.astream(_messages(request)):
            if not isinstance(message, (AIMessage, AIMessageChunk)):
                raise TypeError("测试模型流式输出必须是 AI 消息。")
            if final_message is None:
                final_message = message
            elif isinstance(final_message, AIMessageChunk) and isinstance(
                message, AIMessageChunk
            ):
                final_message = final_message + message
            else:
                raise TypeError("测试模型流式输出混用了完整消息与增量消息。")
            text = _stringify_content(message.content)
            if text:
                yield LLMStreamEvent(event_type="text_delta", delta=text)
            for position, chunk in enumerate(message.tool_call_chunks):
                raw_index = chunk.get("index")
                index = raw_index if isinstance(raw_index, int) else position
                yield LLMStreamEvent(
                    event_type="tool_call_delta",
                    tool_call_chunk=LLMToolCallChunk(
                        index=index,
                        call_id=_optional_string(chunk.get("id")),
                        name=_optional_string(chunk.get("name")),
                        arguments_delta=str(chunk.get("args") or ""),
                    ),
                )
            if message.usage_metadata is not None:
                yield LLMStreamEvent(
                    event_type="usage",
                    usage=_usage(message.usage_metadata),
                )
        if final_message is None:
            raise RuntimeError("测试模型流式调用未返回消息。")
        yield LLMStreamEvent(
            event_type="completed",
            response=_response(final_message, self._profile),
        )

    def _model_for_request(self, request: LLMRequest) -> Any:
        model: Any = self._chat_model
        if request.tools:
            model = model.bind_tools(
                [
                    {
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": tool.parameters,
                        },
                    }
                    for tool in request.tools
                ],
                tool_choice=request.tool_choice,
            )
        return model


def make_test_llm_gateway(
    chat_model: BaseChatModel,
    model_identity: LLMModelIdentity | None = None,
    *,
    default_model_id: str = "deepseek-v4-pro",
) -> LangChainTestGateway:
    """创建带稳定默认身份的测试网关。"""
    identity = model_identity or LLMModelIdentity.unknown(
        "注入模型未提供身份。",
        model_id=default_model_id,
    )
    return LangChainTestGateway(
        chat_model,
        identity,
        default_model_id=default_model_id,
    )


def _messages(request: LLMRequest) -> list[BaseMessage]:
    result: list[BaseMessage] = []
    for item in request.messages:
        if item.role in {"system", "developer"}:
            result.append(SystemMessage(content=item.content))
        elif item.role == "assistant":
            result.append(
                AIMessage(
                    content=item.content,
                    tool_calls=[
                        {
                            "id": call.call_id,
                            "name": call.name,
                            "args": _arguments(call.arguments_json),
                            "type": "tool_call",
                        }
                        for call in item.tool_calls
                    ],
                )
            )
        elif item.role == "tool":
            result.append(
                ToolMessage(
                    content=item.content,
                    tool_call_id=item.tool_call_id or "",
                    name=item.tool_name,
                    status="error" if item.is_error else "success",
                )
            )
        else:
            result.append(HumanMessage(content=item.content))
    return result


def _response(
    message: AIMessage | AIMessageChunk,
    profile: LLMModelProfile,
) -> LLMResponse:
    metadata = message.response_metadata
    return LLMResponse(
        text=_stringify_content(message.content),
        model_id=profile.id,
        upstream_model=profile.upstream_model,
        usage=(
            _usage(message.usage_metadata)
            if message.usage_metadata is not None
            else LLMUsage()
        ),
        cost=LLMCost(),
        finish_reason=_optional_string(metadata.get("finish_reason")),
        provider_request_id=_optional_string(metadata.get("provider_request_id")),
        call_id=message.id,
        tool_calls=tuple(
            LLMToolCall(
                call_id=str(item.get("id") or ""),
                name=str(item.get("name") or ""),
                arguments_json=_arguments_json(item.get("args")),
            )
            for item in message.tool_calls
            if item.get("id") and item.get("name")
        ),
    )


def _usage(metadata: UsageMetadata) -> LLMUsage:
    input_details = metadata.get("input_token_details") or {}
    output_details = metadata.get("output_token_details") or {}
    return LLMUsage(
        input_tokens=metadata.get("input_tokens"),
        cached_input_tokens=input_details.get("cache_read"),
        output_tokens=metadata.get("output_tokens"),
        reasoning_tokens=output_details.get("reasoning"),
        total_tokens=metadata.get("total_tokens"),
    )


def _optional_string(value: Any) -> str | None:
    return str(value) if value is not None else None


def _stringify_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                value = item.get("text") or item.get("content")
                if isinstance(value, str):
                    parts.append(value)
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content)


def _arguments(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _arguments_json(value: Any) -> str:
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, str):
        return value
    return "{}"
