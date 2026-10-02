"""LangChain ``BaseChatModel`` 与太初供应商网关之间的边界适配器。"""

from __future__ import annotations

from collections.abc import AsyncIterator
import json
from typing import Any, Callable, Sequence, cast

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    ChatMessage,
    HumanMessage,
    SystemMessage,
    ToolCallChunk,
    ToolMessage,
    UsageMetadata,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import PrivateAttr

from taichu.application.invocations.config import (
    TAICHU_MODEL_REQUEST_METADATA_KEY,
)
from taichu.infrastructure.trajectory.model_observation import observed_completion, observed_stream
from taichu.infrastructure.llm.contracts import (
    LLMGatewayContract,
    LLMRequest,
    LLMResponse,
    LLMRole,
    LLMToolCall,
    LLMToolDefinition,
    LLMUsage,
)


class GatewayChatModel(BaseChatModel):
    """把现有传输、用量与回放设施挂到 LangChain 官方模型协议。"""

    model_id: str
    temperature: float | None = None
    max_output_tokens: int | None = None
    task_type: str = "langchain_chat_model"
    task_name: str = "LangChain 模型调用"
    taichu_run_id: str | None = None
    context_snapshot_id: str | None = None
    chapter_ids: tuple[str, ...] = ()
    feature: str = ""
    _gateway: LLMGatewayContract = PrivateAttr()
    _structured_output_strict: bool | None = PrivateAttr(default=None)

    def __init__(
        self,
        gateway: LLMGatewayContract,
        *,
        model_id: str,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(  # type: ignore[call-arg]  # Pydantic fields are dynamic.
            model_id=model_id,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            **kwargs,
        )
        self._gateway = gateway

    @property
    def _llm_type(self) -> str:
        return "taichu_gateway_chat_model"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model_id": self.model_id}

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        strict: bool | None = None,
        **kwargs: Any,
    ) -> Runnable:
        """只通过原生 ``tools`` 参数传递 Schema，不注入消息正文。"""
        if strict is None and "ls_structured_output_format" in kwargs:
            strict = self._structured_output_strict
        # 保留工具原有可选字段和动态字典；只有调用方明确要求时才转换为严格契约。
        formatted = [convert_to_openai_tool(tool, strict=strict) for tool in tools]
        normalized_choice = _normalize_tool_choice(tool_choice)
        return self.bind(
            tools=formatted,
            tool_choice=normalized_choice,
            **kwargs,
        )

    def with_structured_output(
        self,
        schema: dict[str, Any] | type,
        *,
        include_raw: bool = False,
        strict: bool | None = None,
        **kwargs: Any,
    ) -> Runnable:
        """保留显式严格选项，解析和错误封装仍由官方 BaseChatModel 实现。"""
        # 官方基础实现忽略 strict；用独立模型副本向它内部的 bind_tools
        # 传递此选项，不改变原模型及普通 Tool 调用的默认行为。
        structured_model = self.model_copy()
        structured_model._structured_output_strict = strict
        return super(GatewayChatModel, structured_model).with_structured_output(
            schema, include_raw=include_raw, **kwargs,
        )

    def for_request(self, **settings: Any) -> GatewayChatModel:
        """生成可直接交给 ``create_agent`` 的请求级模型配置。"""

        unknown = set(settings) - {
            "model_id",
            "temperature",
            "max_output_tokens",
            "task_type",
            "task_name",
            "taichu_run_id",
            "context_snapshot_id",
            "chapter_ids",
            "feature",
        }
        if unknown:
            raise ValueError("未知模型请求配置：" + "、".join(sorted(unknown)))
        return self.model_copy(update=settings)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise NotImplementedError("太初模型网关只支持异步 LangChain 调用。")

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        if stop:
            raise ValueError("当前模型网关不支持 stop 参数。")
        request = _gateway_request(self, messages, run_manager, kwargs)
        response, trajectory_call_id = await observed_completion(self._gateway, request)
        message = AIMessage(
            content=_response_content(response, response.text),
            id=response.call_id or response.provider_request_id,
            tool_calls=[
                {
                    "id": call.call_id,
                    "name": call.name,
                    "args": _arguments(call.arguments_json),
                    "type": "tool_call",
                }
                for call in response.tool_calls
            ],
            usage_metadata=_usage_metadata(response.usage),
            response_metadata={**_response_metadata(response), **(
                {"trajectory_call_id": trajectory_call_id} if trajectory_call_id else {}
            )},
        )
        return ChatResult(
            generations=[ChatGeneration(message=message)],
            llm_output={"model_id": response.model_id},
        )

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        if stop:
            raise ValueError("当前模型网关不支持 stop 参数。")
        request = _gateway_request(self, messages, run_manager, kwargs)
        saw_text = False
        usage_emitted = False
        completed = False
        streamed_tool_metadata: dict[int, tuple[str | None, str | None]] = {}
        async for event, trajectory_call_id in observed_stream(self._gateway, request):
            if event.event_type == "text_delta" and event.delta:
                saw_text = True
                yield ChatGenerationChunk(
                    message=AIMessageChunk(
                        content=event.delta,
                        id=event.call_id,
                    )
                )
            elif (
                event.event_type == "tool_call_delta"
                and event.tool_call_chunk is not None
            ):
                tool_chunk = event.tool_call_chunk
                prior_id, prior_name = streamed_tool_metadata.get(
                    tool_chunk.index, (None, None)
                )
                streamed_tool_metadata[tool_chunk.index] = (
                    prior_id or tool_chunk.call_id,
                    prior_name or tool_chunk.name,
                )
                yield ChatGenerationChunk(
                    message=AIMessageChunk(
                        content="",
                        id=event.call_id,
                        tool_call_chunks=[
                            {
                                "id": tool_chunk.call_id,
                                "name": tool_chunk.name,
                                "args": tool_chunk.arguments_delta,
                                "index": tool_chunk.index,
                                "type": "tool_call_chunk",
                            }
                        ],
                    )
                )
            elif event.event_type == "usage" and event.usage is not None:
                usage_emitted = True
                yield ChatGenerationChunk(
                    message=AIMessageChunk(
                        content="",
                        id=event.call_id,
                        usage_metadata=_usage_metadata(event.usage),
                    )
                )
            elif event.event_type == "failed":
                raise RuntimeError(event.error or "模型流式调用失败。")
            elif event.event_type == "completed" and event.response is not None:
                response = event.response
                completed = True
                completion_tool_chunks: list[ToolCallChunk] = []
                for index, call in enumerate(response.tool_calls):
                    streamed_id, streamed_name = streamed_tool_metadata.get(
                        index, (None, None)
                    )
                    if index not in streamed_tool_metadata:
                        completion_tool_chunks.append(
                            {
                                "id": call.call_id,
                                "name": call.name,
                                "args": call.arguments_json,
                                "index": index,
                                "type": "tool_call_chunk",
                            }
                        )
                    elif streamed_id is None or streamed_name is None:
                        completion_tool_chunks.append(
                            {
                                "id": call.call_id if streamed_id is None else None,
                                "name": call.name if streamed_name is None else None,
                                "args": "",
                                "index": index,
                                "type": "tool_call_chunk",
                            }
                        )
                yield ChatGenerationChunk(
                    message=AIMessageChunk(
                        content=_response_content(response, "" if saw_text else response.text),
                        id=response.call_id or response.provider_request_id,
                        tool_call_chunks=completion_tool_chunks,
                        usage_metadata=(
                            None if usage_emitted else _usage_metadata(response.usage)
                        ),
                        response_metadata={**_response_metadata(response), **(
                            {"trajectory_call_id": trajectory_call_id} if trajectory_call_id else {}
                        )},
                    )
                )
        if not completed:
            raise RuntimeError("模型流式调用未返回完成事件。")


def _gateway_message(message: BaseMessage) -> Any:
    from taichu.infrastructure.llm.contracts import LLMMessage

    if isinstance(message, ToolMessage):
        return LLMMessage(
            role="tool",
            content=_stringify_content(message.content),
            tool_call_id=message.tool_call_id,
            tool_name=message.name,
            is_error=message.status == "error",
        )
    if isinstance(message, AIMessage):
        return LLMMessage(
            role="assistant",
            content=_stringify_content(message.content),
            thinking_blocks=tuple(
                dict(block) for block in message.content
                if isinstance(block, dict)
                and block.get("type") in {"thinking", "redacted_thinking"}
            ) if isinstance(message.content, list) else (),
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
    role: LLMRole
    if isinstance(message, ChatMessage) and message.role == "developer":
        role = "developer"
    elif isinstance(message, SystemMessage):
        role = "system"
    elif isinstance(message, HumanMessage):
        role = "user"
    else:
        raise TypeError(f"不支持的 LangChain 消息类型：{type(message).__name__}")
    return LLMMessage(role=role, content=_stringify_content(message.content))


def _response_content(response: LLMResponse, text: str) -> Any:
    """保留供应商原生思考块及签名，供官方消息链回传，不混入回答文本。"""
    if not response.thinking_blocks:
        return text
    return [*response.thinking_blocks, *([{"type": "text", "text": text}] if text else [])]


def _gateway_tool_definition(value: dict[str, Any]) -> Any:
    function = value.get("function", value)
    if not isinstance(function, dict):
        raise ValueError("原生 Tool 定义缺少 function 对象。")
    parameters = function.get("parameters")
    if not isinstance(parameters, dict):
        raise ValueError("原生 Tool 定义缺少 parameters Schema。")
    return LLMToolDefinition(
        name=str(function.get("name") or ""),
        description=str(function.get("description") or ""),
        parameters=parameters,
        strict=bool(function.get("strict", False)),
    )


def _normalize_tool_choice(value: str | None) -> str:
    """项目采用自动工具选择，包括框架为结构化输出生成的 any/命名选择。"""
    if value is None:
        return "auto"
    normalized = value.strip()
    if not normalized:
        raise ValueError("工具选择策略不能为空。")
    return "none" if normalized == "none" else "auto"


def _model_request_metadata(
    run_manager: AsyncCallbackManagerForLLMRun | None,
) -> dict[str, Any]:
    metadata = getattr(run_manager, "metadata", None)
    if not isinstance(metadata, dict):
        return {}
    request_metadata = metadata.get(TAICHU_MODEL_REQUEST_METADATA_KEY)
    return request_metadata if isinstance(request_metadata, dict) else {}


def _gateway_request(
    model: GatewayChatModel,
    messages: list[BaseMessage],
    run_manager: AsyncCallbackManagerForLLMRun | None,
    kwargs: dict[str, Any],
) -> LLMRequest:
    request_metadata = _model_request_metadata(run_manager)
    tools = tuple(_gateway_tool_definition(item) for item in kwargs.pop("tools", ()))
    tool_choice = str(kwargs.pop("tool_choice", "auto"))
    kwargs.pop("ls_structured_output_format", None)
    request = LLMRequest(
        model_id=str(
            kwargs.pop("model_id", request_metadata.get("model_id", model.model_id))
        ),
        messages=tuple(_gateway_message(item) for item in messages),
        task_type=str(
            kwargs.pop("task_type", request_metadata.get("task_type", model.task_type))
        ),
        task_name=str(
            kwargs.pop("task_name", request_metadata.get("task_name", model.task_name))
        ),
        run_id=_optional_string(
            kwargs.pop(
                "taichu_run_id",
                request_metadata.get("run_id", model.taichu_run_id),
            )
        ),
        context_snapshot_id=_optional_string(
            kwargs.pop(
                "context_snapshot_id",
                request_metadata.get("context_snapshot_id", model.context_snapshot_id),
            )
        ),
        chapter_ids=tuple(
            str(item)
            for item in kwargs.pop(
                "chapter_ids",
                request_metadata.get("chapter_ids", model.chapter_ids),
            )
        ),
        temperature=kwargs.pop(
            "temperature", request_metadata.get("temperature", model.temperature)
        ),
        max_output_tokens=kwargs.pop(
            "max_output_tokens",
            request_metadata.get("max_output_tokens", model.max_output_tokens),
        ),
        feature=str(
            kwargs.pop("feature", request_metadata.get("feature", model.feature))
        ),
        tools=tools,
        tool_choice=tool_choice,
    )
    if kwargs:
        unknown = "、".join(sorted(str(key) for key in kwargs))
        raise ValueError(f"模型适配器收到不支持的参数：{unknown}")
    return request


def _response_metadata(response: LLMResponse) -> dict[str, Any]:
    return {
        "model_id": response.model_id,
        "upstream_model": response.upstream_model,
        "finish_reason": response.finish_reason,
        "provider_request_id": response.provider_request_id,
        "cost_amount": (
            str(response.cost.amount) if response.cost.amount is not None else None
        ),
        "cost_currency": response.cost.currency,
        "cost_kind": response.cost.kind,
    }


def _usage_metadata(usage: LLMUsage) -> UsageMetadata | None:
    values = (
        usage.input_tokens,
        usage.cached_input_tokens,
        usage.output_tokens,
        usage.reasoning_tokens,
        usage.total_tokens,
    )
    if all(value is None for value in values):
        return None
    input_tokens = usage.input_tokens or 0
    output_tokens = usage.output_tokens or 0
    metadata: dict[str, Any] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": (
            usage.total_tokens
            if usage.total_tokens is not None
            else input_tokens + output_tokens
        ),
    }
    if usage.cached_input_tokens is not None:
        metadata["input_token_details"] = {"cache_read": usage.cached_input_tokens}
    if usage.reasoning_tokens is not None:
        metadata["output_token_details"] = {"reasoning": usage.reasoning_tokens}
    return cast(UsageMetadata, metadata)


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
