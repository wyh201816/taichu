"""应用层可见的模型目录与可审计身份契约。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, model_validator


LLMWireProtocol = Literal["openai_responses", "anthropic_messages"]
LLMProviderId = Literal["rightcode", "deepseek_official"]
LLMModelAvailabilityStatus = Literal["unknown", "available", "unavailable"]


@dataclass(frozen=True, slots=True)
class LLMModelProfile:
    """后端模型目录中的稳定模型配置。"""

    id: str
    display_name: str
    provider: Literal["rightcode", "deepseek_official"]
    upstream_model: str
    wire_protocol: LLMWireProtocol
    enabled: bool
    is_default: bool
    supports_streaming: bool
    input_price_per_million: Decimal | None = None
    cached_input_price_per_million: Decimal | None = None
    output_price_per_million: Decimal | None = None
    reasoning_output_price_per_million: Decimal | None = None
    currency: str = "CNY"
    upstream_verified: bool = False
    context_window_tokens: int | None = None
    token_count_method: str = "保守估算，可配置官方分词器"


class LLMModelIdentity(BaseModel):
    """供既有运行评估记录使用的可审计模型身份。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = ""
    model_id: str = ""
    family: str = ""
    endpoint_kind: str = ""
    fingerprint: str | None = None
    known: bool = False
    unknown_reason: str | None = None

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.known:
            if not self.provider.strip() or not self.model_id.strip():
                raise ValueError("已知模型身份必须包含供应商和模型标识。")
            if self.unknown_reason is not None:
                raise ValueError("已知模型身份不能包含未知原因。")
        elif not (self.unknown_reason or "").strip():
            raise ValueError("未知模型身份必须说明原因。")
        return self

    @classmethod
    def unknown(
        cls,
        reason: str,
        *,
        provider: str = "",
        model_id: str = "",
        family: str = "",
        endpoint_kind: str = "",
    ) -> LLMModelIdentity:
        return cls(
            provider=provider,
            model_id=model_id,
            family=family,
            endpoint_kind=endpoint_kind,
            known=False,
            unknown_reason=reason,
        )


@runtime_checkable
class LLMModelCatalogContract(Protocol):
    """模型目录查询契约；不承担模型消息传输。"""

    def list_models(self) -> list[LLMModelProfile]: ...


@dataclass(frozen=True, slots=True)
class LLMModelAvailability:
    """供应商中立的模型显式检测结果。"""

    availability: LLMModelAvailabilityStatus = "unknown"
    last_probed_at: str | None = None
    error: str | None = None
    requested_provider: LLMProviderId | None = None
    requested_model_id: str | None = None
    actual_provider: LLMProviderId | None = None
    actual_model_id: str | None = None
    fallback_used: bool = False
    fallback_from_provider: LLMProviderId | None = None
    wire_protocol: LLMWireProtocol | None = None
    provider_request_id: str | None = None


class LLMModelManagementError(ValueError):
    """模型目录、供应商切换或显式检测的稳定业务错误。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@runtime_checkable
class LLMModelManagementPort(LLMModelCatalogContract, Protocol):
    """供 API 使用的供应商中立模型管理边界。"""

    @property
    def active_provider(self) -> LLMProviderId: ...

    def set_active_provider(self, provider: LLMProviderId) -> None: ...

    def provider_configured(self, provider: LLMProviderId) -> bool: ...

    def provider_models(self, provider: LLMProviderId) -> list[LLMModelProfile]: ...

    def availability_for(
        self,
        model_id: str,
        provider: LLMProviderId | None = None,
    ) -> LLMModelAvailability: ...

    async def probe_model(self, model_id: str) -> LLMModelAvailability: ...
