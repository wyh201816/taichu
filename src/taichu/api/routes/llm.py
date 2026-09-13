"""模型目录、供应商切换、显式检测和模型监控 API。"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from taichu.api.deps import (
    provide_llm_gateway,
    provide_llm_usage_repository,
    provide_settings_preference_service,
)
from taichu.api.schemas.llm import (
    LLMCallListResponse,
    LLMModelListResponse,
    LLMModelProbeResponse,
    LLMProviderItem,
    LLMProviderListResponse,
    LLMProviderSwitchRequest,
    LLMUsageSummaryResponse,
    LLMTokenTrendResponse,
    PublicLLMModel,
)
from taichu.application.contracts.llm import (
    LLMModelManagementError,
    LLMModelManagementPort,
    LLMModelProfile,
)
from taichu.application.contracts.llm_usage import LLMUsageRepository
from taichu.application.services.settings_service import SettingsPreferenceService
from taichu.domain.models import LLMProvider
from taichu.application.models.llm_usage import LLMCallRecord, LLMUsageQuery


router = APIRouter(prefix="/api/llm", tags=["模型服务"])


@router.get("/providers", response_model=LLMProviderListResponse)
async def api_list_llm_providers(
    gateway: LLMModelManagementPort = Depends(provide_llm_gateway),
) -> LLMProviderListResponse:
    return _provider_response(gateway)


@router.put("/providers/active", response_model=LLMProviderListResponse)
async def api_switch_llm_provider(
    payload: LLMProviderSwitchRequest,
    gateway: LLMModelManagementPort = Depends(provide_llm_gateway),
    preference_service: SettingsPreferenceService = Depends(
        provide_settings_preference_service
    ),
) -> LLMProviderListResponse:
    previous_provider = gateway.active_provider
    try:
        gateway.set_active_provider(payload.provider_id)
    except LLMModelManagementError as exc:
        raise _llm_error(409, exc.code, exc.message) from exc
    try:
        await preference_service.set_llm_provider(LLMProvider(payload.provider_id))
    except Exception:
        gateway.set_active_provider(previous_provider)
        raise
    return _provider_response(gateway)


@router.get("/models", response_model=LLMModelListResponse)
async def api_list_llm_models(
    gateway: LLMModelManagementPort = Depends(provide_llm_gateway),
) -> LLMModelListResponse:
    profiles = gateway.list_models()
    default = next((item for item in profiles if item.is_default), None)
    return LLMModelListResponse(
        default_model_id=default.id if default is not None else "",
        models=[_public_model(gateway, profile) for profile in profiles],
    )


@router.post("/models/{model_id}/probe", response_model=LLMModelProbeResponse)
async def api_probe_llm_model(
    model_id: str,
    gateway: LLMModelManagementPort = Depends(provide_llm_gateway),
) -> LLMModelProbeResponse:
    try:
        state = await gateway.probe_model(model_id)
    except LLMModelManagementError as exc:
        raise _llm_error(422, exc.code, exc.message) from exc
    provider_label = (
        "DeepSeek 官方供应商"
        if state.requested_provider == "deepseek_official"
        else "RightCode 提供商"
    )
    return LLMModelProbeResponse(
        model_id=model_id,
        availability=state.availability,
        last_probed_at=state.last_probed_at,
        requested_provider=state.requested_provider or "rightcode",
        requested_model_id=state.requested_model_id or model_id,
        actual_provider=state.actual_provider,
        actual_model_id=state.actual_model_id,
        fallback_used=state.fallback_used,
        fallback_from_provider=state.fallback_from_provider,
        wire_protocol=state.wire_protocol or "anthropic_messages",
        provider_request_id=state.provider_request_id,
        message=(
            f"模型检测成功：请求的 {provider_label}可用。"
            if state.availability == "available"
            else (
                f"模型检测失败：{state.error}"
                if state.error
                else f"模型检测失败：请求的 {provider_label}不可用。"
            )
        ),
    )


@router.get("/usage/calls", response_model=LLMCallListResponse)
async def api_list_llm_calls(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    started_from: str | None = None,
    started_to: str | None = None,
    model_id: str | None = None,
    task_type: str | None = None,
    status: Literal["running", "completed", "failed"] | None = None,
    repository: LLMUsageRepository = Depends(provide_llm_usage_repository),
) -> LLMCallListResponse:
    result = await repository.list_calls(
        _query(
            page=page,
            page_size=page_size,
            started_from=started_from,
            started_to=started_to,
            model_id=model_id,
            task_type=task_type,
            status=status,
        )
    )
    return LLMCallListResponse(**result.model_dump())


@router.get("/usage/calls/{call_id}", response_model=LLMCallRecord)
async def api_get_llm_call(
    call_id: str,
    repository: LLMUsageRepository = Depends(provide_llm_usage_repository),
) -> LLMCallRecord:
    record = await repository.get(call_id)
    if record is None:
        raise _llm_error(404, "LLM_CALL_NOT_FOUND", "模型调用记录不存在。")
    return record


@router.get("/usage/summary", response_model=LLMUsageSummaryResponse)
async def api_get_llm_usage_summary(
    started_from: str | None = None,
    started_to: str | None = None,
    model_id: str | None = None,
    task_type: str | None = None,
    status: Literal["running", "completed", "failed"] | None = None,
    repository: LLMUsageRepository = Depends(provide_llm_usage_repository),
) -> LLMUsageSummaryResponse:
    summary = await repository.summarize(
        _query(
            started_from=started_from,
            started_to=started_to,
            model_id=model_id,
            task_type=task_type,
            status=status,
        )
    )
    return LLMUsageSummaryResponse(**summary.model_dump())


@router.get("/usage/trend", response_model=LLMTokenTrendResponse)
async def api_get_llm_token_trend(
    bucket: Literal["hour", "day"] = "day",
    started_from: str | None = None,
    started_to: str | None = None,
    model_id: str | None = None,
    task_type: str | None = None,
    status: Literal["running", "completed", "failed"] | None = None,
    repository: LLMUsageRepository = Depends(provide_llm_usage_repository),
) -> LLMTokenTrendResponse:
    points = await repository.token_trend(
        _query(
            started_from=started_from,
            started_to=started_to,
            model_id=model_id,
            task_type=task_type,
            status=status,
        ),
        bucket,
    )
    return LLMTokenTrendResponse(bucket=bucket, points=points)


def _public_model(
    gateway: LLMModelManagementPort,
    profile: LLMModelProfile,
) -> PublicLLMModel:
    state = gateway.availability_for(profile.id, profile.provider)
    return PublicLLMModel(
        id=profile.id,
        display_name=profile.display_name,
        provider=profile.provider,
        enabled=profile.enabled,
        is_default=profile.is_default,
        supports_streaming=profile.supports_streaming,
        context_window_tokens=profile.context_window_tokens,
        token_count_method=profile.token_count_method,
        availability=state.availability,
        last_probed_at=state.last_probed_at,
        availability_error=state.error,
        upstream_verified=profile.upstream_verified,
    )


def _provider_response(
    gateway: LLMModelManagementPort,
) -> LLMProviderListResponse:
    metadata = {
        "rightcode": (
            "RightCode",
            "统一接入 GPT、DeepSeek 与 Claude 系列模型。",
        ),
        "deepseek_official": (
            "DeepSeek 官方",
            "直接使用 DeepSeek 官方接口，仅提供官方支持的模型。",
        ),
    }
    providers: list[LLMProviderItem] = []
    provider_ids: tuple[Literal["rightcode", "deepseek_official"], ...] = (
        "rightcode",
        "deepseek_official",
    )
    for provider_id in provider_ids:
        profiles = gateway.provider_models(provider_id)
        display_name, description = metadata[provider_id]
        providers.append(
            LLMProviderItem(
                id=provider_id,
                display_name=display_name,
                description=description,
                configured=gateway.provider_configured(provider_id),
                model_count=len(profiles),
                model_names=[profile.display_name for profile in profiles],
            )
        )
    return LLMProviderListResponse(
        active_provider_id=gateway.active_provider,
        providers=providers,
    )


def _query(**values) -> LLMUsageQuery:
    return LLMUsageQuery(
        **{key: value for key, value in values.items() if value is not None}
    )


def _llm_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"error": {"code": code, "message": message}},
    )
