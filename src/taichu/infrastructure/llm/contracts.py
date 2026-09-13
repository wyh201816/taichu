"""供应商传输、计费与回放使用的内部 DTO；不作为 Agent 模型协议。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal, Protocol, runtime_checkable

from taichu.application.contracts.llm import (
    LLMModelCatalogContract,
    LLMModelProfile,
)


LLMRole = Literal["system", "developer", "user", "assistant", "tool"]
LLMToolChoice = str
LLMCostKind = Literal["actual", "estimated", "unavailable"]
LLMStreamEventType = Literal[
    "started", "text_delta", "tool_call_delta", "usage", "completed", "failed"
]


@dataclass(frozen=True, slots=True)
class LLMTransportProfile(LLMModelProfile):
    """基础设施内部的模型传输配置，不得通过应用层目录返回。"""

    base_url_key: str = field(kw_only=True)

    def to_public_profile(self) -> LLMModelProfile:
        """丢弃传输配置，只保留应用层可审计的模型身份与能力。"""
        return LLMModelProfile(
            id=self.id,
            display_name=self.display_name,
            provider=self.provider,
            upstream_model=self.upstream_model,
            wire_protocol=self.wire_protocol,
            enabled=self.enabled,
            is_default=self.is_default,
            supports_streaming=self.supports_streaming,
            input_price_per_million=self.input_price_per_million,
            cached_input_price_per_million=self.cached_input_price_per_million,
            output_price_per_million=self.output_price_per_million,
            reasoning_output_price_per_million=(
                self.reasoning_output_price_per_million
            ),
            currency=self.currency,
            upstream_verified=self.upstream_verified,
            context_window_tokens=self.context_window_tokens,
            token_count_method=self.token_count_method,
        )


@dataclass(frozen=True, slots=True)
class LLMToolDefinition:
    """映射到供应商原生 tools 参数的函数定义。"""

    name: str
    description: str
    parameters: dict[str, Any]
    strict: bool = True


@dataclass(frozen=True, slots=True)
class LLMToolCall:
    """供应商响应中规范化的一次原生函数调用。"""

    call_id: str
    name: str
    arguments_json: str


@dataclass(frozen=True, slots=True)
class LLMToolCallChunk:
    """供应商流式函数调用规范化后的增量。"""

    index: int
    arguments_delta: str = ""
    call_id: str | None = None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class LLMMessage:
    """仅用于供应商载荷、脱敏回放与计费的消息快照。"""

    role: LLMRole
    content: str = ""
    tool_calls: tuple[LLMToolCall, ...] = ()
    tool_call_id: str | None = None
    tool_name: str | None = None
    is_error: bool = False

    def __post_init__(self) -> None:
        if self.role == "assistant" and self.tool_call_id is not None:
            raise ValueError("assistant 消息不能声明工具结果关联标识。")
        if self.role != "assistant" and self.tool_calls:
            raise ValueError("只有 assistant 消息可以包含工具调用请求。")
        if self.role == "tool":
            if not (self.tool_call_id or "").strip():
                raise ValueError("tool 消息必须声明工具调用关联标识。")
        elif (
            self.tool_call_id is not None or self.tool_name is not None or self.is_error
        ):
            raise ValueError("只有 tool 消息可以声明工具结果元数据。")


@dataclass(frozen=True, slots=True)
class LLMRequest:
    """LangChain 调用映射到一次供应商请求后的不可变快照。"""

    model_id: str
    messages: tuple[LLMMessage, ...]
    task_type: str
    task_name: str
    run_id: str | None = None
    context_snapshot_id: str | None = None
    chapter_ids: tuple[str, ...] = ()
    temperature: float | None = None
    max_output_tokens: int | None = None
    feature: str = ""
    tools: tuple[LLMToolDefinition, ...] = ()
    tool_choice: LLMToolChoice = "auto"

    def __post_init__(self) -> None:
        if not self.tool_choice.strip():
            raise ValueError("工具选择策略不能为空。")

    def __str__(self) -> str:
        parts: list[str] = []
        for message in self.messages:
            parts.append(message.content)
            parts.extend(call.arguments_json for call in message.tool_calls)
        return "\n\n".join(parts)

    def __contains__(self, value: str) -> bool:
        return value in str(self)


@dataclass(frozen=True, slots=True)
class LLMUsage:
    """供应商返回的可空 Token 明细。"""

    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class LLMCost:
    """实际、预估或不可计算的供应商费用。"""

    amount: Decimal | None = None
    currency: str = "CNY"
    kind: LLMCostKind = "unavailable"


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """供应商完整响应的规范化结果。"""

    text: str
    model_id: str
    upstream_model: str
    usage: LLMUsage
    cost: LLMCost
    finish_reason: str | None = None
    provider_request_id: str | None = None
    call_id: str | None = None
    tool_calls: tuple[LLMToolCall, ...] = ()


@dataclass(frozen=True, slots=True)
class LLMStreamEvent:
    """供应商 SSE 到 LangChain Chunk 之间的内部事件。"""

    event_type: LLMStreamEventType
    delta: str = ""
    usage: LLMUsage | None = None
    response: LLMResponse | None = None
    tool_call_chunk: LLMToolCallChunk | None = None
    error: str | None = None
    call_id: str | None = None


@runtime_checkable
class LLMGatewayContract(LLMModelCatalogContract, Protocol):
    """供应商传输、用量和回放边界；Agent 不直接依赖此协议。"""

    async def complete(self, request: LLMRequest) -> LLMResponse: ...

    def stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamEvent]: ...
