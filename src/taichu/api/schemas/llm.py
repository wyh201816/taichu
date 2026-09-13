"""模型目录、检测与调用遥测 API 模型。"""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from taichu.application.models.llm_usage import (
    LLMCallRecord,
    LLMUsageGroup,
    LLMTokenTrendPoint,
)


class PublicLLMModel(BaseModel):
    id: str
    display_name: str
    provider: str
    enabled: bool
    is_default: bool
    supports_streaming: bool
    context_window_tokens: int | None = None
    token_count_method: str = "未计量"
    availability: str = "unknown"
    last_probed_at: str | None = None
    availability_error: str | None = None
    upstream_verified: bool = False


class LLMModelListResponse(BaseModel):
    default_model_id: str
    models: list[PublicLLMModel] = Field(default_factory=list)


class LLMProviderItem(BaseModel):
    id: str
    display_name: str
    description: str
    configured: bool
    model_count: int
    model_names: list[str] = Field(default_factory=list)


class LLMProviderListResponse(BaseModel):
    active_provider_id: str
    providers: list[LLMProviderItem] = Field(default_factory=list)


class LLMProviderSwitchRequest(BaseModel):
    provider_id: Literal["rightcode", "deepseek_official"]


class LLMModelProbeResponse(BaseModel):
    model_id: str
    availability: str
    last_probed_at: str | None = None
    requested_provider: str
    requested_model_id: str
    actual_provider: str | None = None
    actual_model_id: str | None = None
    fallback_used: bool
    fallback_from_provider: str | None = None
    wire_protocol: str
    provider_request_id: str | None = None
    message: str


class LLMCallListResponse(BaseModel):
    items: list[LLMCallRecord] = Field(default_factory=list)
    page: int
    page_size: int
    total: int


class LLMUsageSummaryResponse(BaseModel):
    total_calls: int
    completed_calls: int
    failed_calls: int
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    total_tokens: int | None
    actual_cost: Decimal
    estimated_cost: Decimal
    unavailable_cost_calls: int
    average_duration_ms: int
    by_model: list[LLMUsageGroup]
    by_task_type: list[LLMUsageGroup]


class LLMTokenTrendResponse(BaseModel):
    bucket: str
    points: list[LLMTokenTrendPoint] = Field(default_factory=list)
