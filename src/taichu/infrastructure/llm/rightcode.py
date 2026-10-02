"""RightCode 主路由与 DeepSeek 官方可用性降级网关。"""

from __future__ import annotations

from taichu.infrastructure.trajectory.model_observation import observed_http_post, observe_attempt_outcome
from taichu.infrastructure.trajectory.http_observation import (
    ATTEMPT_EXTENSION, StreamAttemptObservation, observed_http_stream,
)

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from time import perf_counter
from typing import Any, Literal
from uuid import uuid4

import httpx

from taichu.application.contracts.llm import (
    LLMModelAvailability,
    LLMModelManagementError,
    LLMModelProfile,
)
from taichu.infrastructure.llm.contracts import (
    LLMCost,
    LLMGatewayContract,
    LLMRequest,
    LLMResponse,
    LLMStreamEvent,
    LLMToolCall,
    LLMToolCallChunk,
    LLMTransportProfile,
    LLMUsage,
)
from taichu.application.contracts.llm_usage import LLMUsageRepository
from taichu.application.contracts.llm_replay import LLMCallReplayRepository
from taichu.application.models.llm_replay import (
    LLMCallReplayRecord,
    LLMReplayMessage,
    LLMReplayToolCall,
    LLMReplayToolDefinition,
)
from taichu.application.models.llm_usage import LLMCallRecord
from taichu.config import Settings
from taichu.infrastructure.llm.catalog import LLMModelCatalog
from taichu.infrastructure.llm.costs import calculate_cost


class LLMGatewayError(RuntimeError):
    """不会包含鉴权信息或完整上游响应的稳定错误。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
        provider_request_id: str | None = None,
        error_summary: str | None = None,
        content_block_types: tuple[str, ...] = (),
        retry_count: int = 0,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.provider_request_id = provider_request_id
        self.error_summary = error_summary
        self.content_block_types = content_block_types
        self.retry_count = retry_count


@dataclass(frozen=True, slots=True)
class _GatewayDiagnostics:
    """仅保留可安全审计的上游调用元数据。"""

    status_code: int | None = None
    provider_request_id: str | None = None
    error_summary: str | None = None
    content_block_types: tuple[str, ...] = ()
    retry_count: int = 0


@dataclass(frozen=True, slots=True)
class _CompletionResult:
    response: LLMResponse
    diagnostics: _GatewayDiagnostics


@dataclass(slots=True)
class _StreamingToolCall:
    """一条供应商流式工具调用的可合并状态。"""

    index: int
    call_id: str | None = None
    name: str | None = None
    initial_arguments_json: str = ""
    argument_parts: list[str] = field(default_factory=list)

    def append(self, value: str) -> None:
        if value:
            self.argument_parts.append(value)

    @property
    def streamed_arguments(self) -> str:
        return self.initial_arguments_json + "".join(self.argument_parts)

    @property
    def arguments_json(self) -> str:
        return self.streamed_arguments or "{}"

    def reconcile_completed_arguments(self, arguments_json: str) -> str:
        """返回尚未发送的尾部，拒绝无法用 LangChain 增量表达的改写。"""

        current = self.streamed_arguments
        if not current:
            self.argument_parts = [arguments_json] if arguments_json else []
            return arguments_json
        if arguments_json == current:
            return ""
        if arguments_json.startswith(current):
            remainder = arguments_json[len(current) :]
            self.append(remainder)
            return remainder
        raise LLMGatewayError(
            "LLM_RESPONSE_INVALID",
            "模型流式工具参数前后不一致，请稍后重试。",
            error_summary="函数参数完成事件与已接收增量不一致",
        )


class RightCodeLLMGateway(LLMGatewayContract):
    """优先调用 RightCode，在可用性故障时降级到 DeepSeek 官方。"""

    def __init__(
        self,
        settings: Settings,
        catalog: LLMModelCatalog,
        usage_repository: LLMUsageRepository,
        *,
        client: httpx.AsyncClient | None = None,
        replay_repository: LLMCallReplayRepository | None = None,
        enforce_active_provider: bool = False,
    ) -> None:
        self._settings = settings
        self._catalog = catalog
        self._usage_repository = usage_repository
        self._replay_repository = replay_repository
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.rightcode_request_timeout_seconds)
        )
        self._availability: dict[str, LLMModelAvailability] = {}
        self._active_provider: Literal["rightcode", "deepseek_official"] = "rightcode"
        self._enforce_active_provider = enforce_active_provider

    async def aclose(self) -> None:
        """关闭由网关创建的 HTTP 客户端；外部注入客户端仍由调用方管理。"""

        if self._owns_client:
            await self._client.aclose()

    def sanitize_observation(self, value: Any) -> tuple[Any, int]:
        """只脱敏观测副本，实际消息和供应商请求保持不变。"""
        safe, count = _redact_payload_value(value)
        secrets = {self._settings.rightcode_api_key.get_secret_value(),
                   self._settings.deepseek_api_key.get_secret_value()} - {""}

        def redact(item: Any) -> Any:
            nonlocal count
            if isinstance(item, str):
                for secret in secrets:
                    occurrences = item.count(secret)
                    if occurrences:
                        item = item.replace(secret, "[已脱敏]")
                        count += occurrences
                return item
            if isinstance(item, dict):
                return {key: redact(content) for key, content in item.items()}
            if isinstance(item, (list, tuple)):
                return [redact(content) for content in item]
            return item

        return redact(safe), count

    @property
    def active_provider(self) -> Literal["rightcode", "deepseek_official"]:
        return self._active_provider

    def set_active_provider(
        self, provider: Literal["rightcode", "deepseek_official"]
    ) -> None:
        if provider == "rightcode" and not self._rightcode_configured:
            raise LLMModelManagementError(
                "LLM_PROVIDER_NOT_CONFIGURED", "RightCode 尚未配置密钥，无法切换。"
            )
        if provider == "deepseek_official" and not self._fallback_configured:
            raise LLMModelManagementError(
                "LLM_PROVIDER_NOT_CONFIGURED", "DeepSeek 官方尚未配置密钥，无法切换。"
            )
        self._active_provider = provider

    def provider_configured(
        self, provider: Literal["rightcode", "deepseek_official"]
    ) -> bool:
        return (
            self._rightcode_configured
            if provider == "rightcode"
            else self._fallback_configured
        )

    def provider_models(
        self, provider: Literal["rightcode", "deepseek_official"]
    ) -> list[LLMModelProfile]:
        return self._catalog.list_models(provider)

    @property
    def configured(self) -> bool:
        return self._rightcode_configured or self._fallback_configured

    @property
    def _rightcode_configured(self) -> bool:
        return bool(self._settings.rightcode_api_key.get_secret_value().strip())

    @property
    def _fallback_configured(self) -> bool:
        return bool(
            self._settings.deepseek_fallback_enabled
            and self._settings.deepseek_api_key.get_secret_value().strip()
        )

    @property
    def default_model_id(self) -> str:
        return self._catalog.default_model_id_for(self._active_provider)

    def list_models(self) -> list[LLMModelProfile]:
        return [
            replace(profile, enabled=False)
            if self.availability_for(profile.id, profile.provider).availability
            == "unavailable"
            else profile
            for profile in self._catalog.list_models(
                self._active_provider if self._enforce_active_provider else "rightcode"
            )
        ]

    def availability_for(
        self,
        model_id: str,
        provider: Literal["rightcode", "deepseek_official"] | None = None,
    ) -> LLMModelAvailability:
        actual_provider = provider or (
            self._active_provider if self._enforce_active_provider else "rightcode"
        )
        return self._availability.get(
            f"{actual_provider}:{model_id}", LLMModelAvailability()
        )

    def _resolve_transport_model(
        self, model_id: str | None
    ) -> LLMTransportProfile:
        profile = self._catalog.resolve(
            model_id,
            provider=(
                self._active_provider if self._enforce_active_provider else "rightcode"
            ),
        )
        status = self.availability_for(profile.id, profile.provider)
        if status.availability == "unavailable":
            raise LLMModelManagementError(
                "LLM_MODEL_UNAVAILABLE",
                f"模型“{profile.display_name}”当前不可用，请选择其他模型或重新检测。",
            )
        return profile

    async def probe_model(self, model_id: str) -> LLMModelAvailability:
        profile = self._resolve_transport_model(model_id)
        request = LLMRequest(
            model_id=profile.id,
            messages=(
                # 探测内容固定且不包含用户数据。
                _message("user", "请只回复：可用"),
            ),
            task_type="model_probe",
            task_name="模型检测",
            feature="模型监控",
            max_output_tokens=1024 if profile.id.startswith("deepseek-") else 8,
        )
        try:
            response = await self._complete(
                request,
                allow_known_unavailable=True,
                allow_fallback=False,
            )
        except (LLMGatewayError, LLMModelManagementError) as exc:
            state = LLMModelAvailability(
                availability="unavailable",
                last_probed_at=_now_iso(),
                error=str(exc),
                requested_provider=profile.provider,
                requested_model_id=profile.id,
                actual_provider=profile.provider,
                actual_model_id=profile.id,
                fallback_used=False,
                fallback_from_provider=None,
                wire_protocol=profile.wire_protocol,
                provider_request_id=None,
            )
        else:
            state = LLMModelAvailability(
                availability="available",
                last_probed_at=_now_iso(),
                requested_provider=profile.provider,
                requested_model_id=profile.id,
                actual_provider=profile.provider,
                actual_model_id=response.model_id,
                fallback_used=False,
                fallback_from_provider=None,
                wire_protocol=profile.wire_protocol,
                provider_request_id=response.provider_request_id,
            )
        self._availability[f"{profile.provider}:{profile.id}"] = state
        return state

    async def complete(self, request: LLMRequest) -> LLMResponse:
        return await self._complete(request, allow_known_unavailable=False)

    async def _complete(
        self,
        request: LLMRequest,
        *,
        allow_known_unavailable: bool,
        allow_fallback: bool = True,
    ) -> LLMResponse:
        profile = (
            self._catalog.resolve(
                request.model_id,
                provider=(
                    self._active_provider
                    if self._enforce_active_provider
                    else "rightcode"
                ),
            )
            if allow_known_unavailable
            else self._resolve_transport_model(request.model_id)
        )
        call_id = f"llm-call-{uuid4().hex}"
        started_at = _now_iso()
        timer = perf_counter()
        payload: dict[str, Any] | None = None
        effective_profile = profile
        fallback_from_provider: str | None = None
        try:
            if self._enforce_active_provider:
                self._ensure_provider_configured(profile.provider)
            else:
                self._ensure_configured()
            if (
                self._enforce_active_provider
                and profile.provider == "deepseek_official"
            ):
                payload = _request_payload(profile, request, stream=False)
                parsed = await self._complete_with_retries(
                    profile, request, payload, call_id
                )
            elif self._enforce_active_provider:
                payload = _request_payload(profile, request, stream=False)
                parsed = await self._complete_with_retries(
                    profile, request, payload, call_id
                )
            elif not self._rightcode_configured and not allow_fallback:
                raise LLMGatewayError(
                    "LLM_TOKEN_MISSING",
                    "尚未配置请求的 RightCode 提供商密钥。",
                )
            elif self._rightcode_configured:
                payload = _request_payload(profile, request, stream=False)
                try:
                    parsed = await self._complete_with_retries(
                        profile,
                        request,
                        payload,
                        call_id,
                    )
                except Exception as primary_error:
                    safe_primary_error = _normalize_error(primary_error)
                    if not allow_fallback or not self._can_fallback(safe_primary_error):
                        raise
                    effective_profile = self._fallback_profile(profile)
                    fallback_from_provider = profile.provider
                    payload = _request_payload(
                        effective_profile,
                        request,
                        stream=False,
                    )
                    parsed = await self._complete_with_retries(
                        effective_profile,
                        request,
                        payload,
                        call_id,
                    )
            else:
                effective_profile = self._fallback_profile(profile)
                fallback_from_provider = profile.provider
                payload = _request_payload(
                    effective_profile,
                    request,
                    stream=False,
                )
                parsed = await self._complete_with_retries(
                    effective_profile,
                    request,
                    payload,
                    call_id,
                )
        except Exception as exc:
            safe = _normalize_error(exc)
            await self._record_failure(
                call_id,
                request,
                effective_profile,
                started_at,
                timer,
                safe,
                wire_request_body=payload,
                fallback_from_provider=fallback_from_provider,
            )
            raise safe from None
        await self._record_success(
            call_id,
            request,
            effective_profile,
            started_at,
            timer,
            parsed.response,
            wire_request_body=payload,
            fallback_from_provider=fallback_from_provider,
            diagnostics=parsed.diagnostics,
        )
        return parsed.response

    async def _complete_with_retries(
        self,
        profile: LLMTransportProfile,
        request: LLMRequest,
        payload: dict[str, Any],
        call_id: str,
    ) -> _CompletionResult:
        url = self._endpoint(profile)
        retries = (
            self._settings.deepseek_fallback_max_retries
            if profile.provider == "deepseek_official"
            else self._settings.rightcode_max_retries
        )
        attempts = max(0, retries) + 1
        for attempt in range(attempts):
            try:
                response = await observed_http_post(
                    self._client,
                    url,
                    headers=self._headers(profile),
                    json=payload,
                    provider=profile.provider,
                    wire_protocol=profile.wire_protocol,
                )
                if response.status_code < 400:
                    response_body = response.json()
                    parsed = _parse_response(response_body, profile, call_id)
                    provider_request_id = (
                        parsed.provider_request_id
                        or _response_provider_request_id(response)
                    )
                    if provider_request_id != parsed.provider_request_id:
                        parsed = replace(
                            parsed, provider_request_id=provider_request_id
                        )
                    content_block_types = _content_block_types(response_body, parsed)
                    if parsed.text.strip() or parsed.tool_calls:
                        await observe_attempt_outcome(response, status="completed")
                        return _CompletionResult(
                            response=parsed,
                            diagnostics=_GatewayDiagnostics(
                                status_code=response.status_code,
                                provider_request_id=provider_request_id,
                                content_block_types=content_block_types,
                                retry_count=attempt,
                            ),
                        )
                    if _output_was_truncated(parsed.finish_reason):
                        error = LLMGatewayError(
                            "LLM_OUTPUT_TRUNCATED",
                            "模型输出达到上限，尚未生成最终内容。",
                            status_code=response.status_code,
                            provider_request_id=provider_request_id,
                            error_summary="输出在上限处结束",
                            content_block_types=content_block_types,
                            retry_count=attempt,
                        )
                        await observe_attempt_outcome(response, status="failed", error_code=error.code, error=error.message)
                        if attempt == attempts - 1:
                            raise error
                        continue
                    error = LLMGatewayError(
                        "LLM_EMPTY_RESPONSE",
                        "模型返回了空内容，请稍后重试。",
                        status_code=response.status_code,
                        provider_request_id=provider_request_id,
                        error_summary="响应内容为空",
                        content_block_types=content_block_types,
                        retry_count=attempt,
                    )
                    await observe_attempt_outcome(response, status="failed", error_code=error.code, error=error.message)
                    if attempt == attempts - 1:
                        raise error
                else:
                    error = _status_error(
                        response.status_code,
                        provider_request_id=_response_provider_request_id(response),
                        error_summary=_upstream_error_summary(response),
                        retry_count=attempt,
                    )
                    await observe_attempt_outcome(response, status="failed", error_code=error.code, error=error.message)
                    if response.status_code != 429 and response.status_code < 500:
                        raise error
                    if attempt == attempts - 1:
                        raise error
            except httpx.TimeoutException:
                if attempt == attempts - 1:
                    raise LLMGatewayError(
                        "LLM_TIMEOUT",
                        "模型调用超时，请稍后重试。",
                        error_summary="请求超时",
                        retry_count=attempt,
                    ) from None
            except httpx.NetworkError:
                if attempt == attempts - 1:
                    raise LLMGatewayError(
                        "LLM_NETWORK_ERROR",
                        "无法连接模型服务，请稍后重试。",
                        error_summary="网络连接失败",
                        retry_count=attempt,
                    ) from None
            except json.JSONDecodeError as exc:
                error = LLMGatewayError(
                    "LLM_RESPONSE_INVALID",
                    "模型服务返回了无法解析的响应，请稍后重试。",
                    status_code=response.status_code
                    if "response" in locals()
                    else None,
                    provider_request_id=(
                        _response_provider_request_id(response)
                        if "response" in locals()
                        else None
                    ),
                    error_summary="响应不是合法 JSON",
                    retry_count=attempt,
                )
                await observe_attempt_outcome(response, status="failed", error_code=error.code, error=error.message)
                if attempt == attempts - 1:
                    raise error from exc
            await asyncio.sleep(min(0.25 * (2**attempt), 1.0))
        raise LLMGatewayError(
            "LLM_UPSTREAM_ERROR",
            "模型服务暂时不可用。",
            error_summary="上游调用未返回可用结果",
            retry_count=attempts - 1,
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamEvent]:
        profile = self._resolve_transport_model(request.model_id)
        fallback_from_provider: str | None = None
        if (
            not self._enforce_active_provider
            and not self._rightcode_configured
            and self._fallback_configured
        ):
            fallback_from_provider = profile.provider
            profile = self._fallback_profile(profile)
        call_id = f"llm-call-{uuid4().hex}"
        started_at = _now_iso()
        timer = perf_counter()
        completed = False
        recorded = False
        last_usage = LLMUsage()
        text_parts: list[str] = []
        streamed_tool_calls: dict[int, _StreamingToolCall] = {}
        thinking_blocks: dict[int, dict[str, Any]] = {}
        provider_tool_indexes: dict[int, int] = {}
        payload: dict[str, Any] | None = None
        yield LLMStreamEvent(event_type="started", call_id=call_id)
        try:
            self._ensure_provider_configured(profile.provider)
            payload = _request_payload(profile, request, stream=True)
            url = self._endpoint(profile)
            async with observed_http_stream(
                self._client, "POST", url, headers=self._headers(profile), json=payload,
                provider=profile.provider, wire_protocol=profile.wire_protocol,
            ) as response:
                if response.status_code >= 400:
                    raise _status_error(response.status_code)
                provider_request_id: str | None = None
                finish_reason: str | None = None
                async for event_name, event_payload in _iter_sse(response):
                    event_type = str(event_payload.get("type") or event_name)
                    if event_type == "response.output_text.delta":
                        delta = str(event_payload.get("delta") or "")
                        if delta:
                            text_parts.append(delta)
                            yield LLMStreamEvent(
                                event_type="text_delta",
                                delta=delta,
                                call_id=call_id,
                            )
                    elif event_type == "response.output_item.added":
                        item = event_payload.get("item")
                        if isinstance(item, dict) and item.get("type") == "function_call":
                            index = _stream_tool_index(
                                event_payload.get("output_index"),
                                streamed_tool_calls,
                                provider_tool_indexes,
                                new_call=True,
                            )
                            state = _StreamingToolCall(
                                index=index,
                                call_id=_optional_text(
                                    item.get("call_id") or item.get("id")
                                ),
                                name=_optional_text(item.get("name")),
                                initial_arguments_json=_stream_arguments_json(
                                    item.get("arguments")
                                ),
                            )
                            streamed_tool_calls[index] = state
                            yield LLMStreamEvent(
                                event_type="tool_call_delta",
                                tool_call_chunk=LLMToolCallChunk(
                                    index=index,
                                    call_id=state.call_id,
                                    name=state.name,
                                    arguments_delta=state.initial_arguments_json,
                                ),
                                call_id=call_id,
                            )
                    elif event_type == "response.function_call_arguments.delta":
                        index = _stream_tool_index(
                            event_payload.get("output_index"),
                            streamed_tool_calls,
                            provider_tool_indexes,
                        )
                        state = streamed_tool_calls.setdefault(
                            index,
                            _StreamingToolCall(
                                index=index,
                                call_id=_optional_text(event_payload.get("call_id")),
                                name=_optional_text(event_payload.get("name")),
                            ),
                        )
                        delta = str(event_payload.get("delta") or "")
                        if delta:
                            state.append(delta)
                            yield LLMStreamEvent(
                                event_type="tool_call_delta",
                                tool_call_chunk=LLMToolCallChunk(
                                    index=index,
                                    arguments_delta=delta,
                                ),
                                call_id=call_id,
                            )
                    elif event_type == "response.function_call_arguments.done":
                        index = _stream_tool_index(
                            event_payload.get("output_index"),
                            streamed_tool_calls,
                            provider_tool_indexes,
                        )
                        state = streamed_tool_calls.setdefault(
                            index,
                            _StreamingToolCall(
                                index=index,
                                call_id=_optional_text(event_payload.get("call_id")),
                                name=_optional_text(event_payload.get("name")),
                            ),
                        )
                        completed_arguments = _stream_arguments_json(
                            event_payload.get("arguments")
                        )
                        remainder = state.reconcile_completed_arguments(
                            completed_arguments
                        )
                        if remainder:
                            yield LLMStreamEvent(
                                event_type="tool_call_delta",
                                tool_call_chunk=LLMToolCallChunk(
                                    index=index,
                                    arguments_delta=remainder,
                                ),
                                call_id=call_id,
                            )
                    elif event_type in {"response.usage", "response.usage.delta"}:
                        last_usage = _parse_usage(event_payload.get("usage", {}))
                        yield LLMStreamEvent(
                            event_type="usage", usage=last_usage, call_id=call_id
                        )
                    elif event_type == "response.completed":
                        body = event_payload.get("response")
                        if not isinstance(body, dict):
                            body = event_payload
                        parsed = _parse_responses_response(body, profile, call_id)
                        if streamed_tool_calls and not parsed.tool_calls:
                            parsed = replace(
                                parsed,
                                tool_calls=_completed_stream_tool_calls(
                                    streamed_tool_calls
                                ),
                            )
                        if not parsed.text and text_parts:
                            parsed = LLMResponse(
                                text="".join(text_parts),
                                model_id=parsed.model_id,
                                upstream_model=parsed.upstream_model,
                                usage=parsed.usage,
                                cost=parsed.cost,
                                finish_reason=parsed.finish_reason,
                                provider_request_id=parsed.provider_request_id,
                                call_id=call_id,
                                tool_calls=parsed.tool_calls,
                            )
                        if not parsed.text.strip() and not parsed.tool_calls:
                            raise LLMGatewayError(
                                "LLM_EMPTY_RESPONSE", "模型返回了空内容，请稍后重试。"
                            )
                        last_usage = parsed.usage
                        if any(
                            value is not None for value in _usage_values(last_usage)
                        ):
                            yield LLMStreamEvent(
                                event_type="usage",
                                usage=last_usage,
                                call_id=call_id,
                            )
                        await self._record_success(
                            call_id,
                            request,
                            profile,
                            started_at,
                            timer,
                            parsed,
                            wire_request_body=payload,
                            fallback_from_provider=fallback_from_provider,
                        )
                        recorded = True
                        completed = True
                        yield LLMStreamEvent(
                            event_type="completed",
                            response=parsed,
                            usage=last_usage,
                            call_id=call_id,
                        )
                    elif event_type == "message_start":
                        message = event_payload.get("message")
                        if isinstance(message, dict):
                            provider_request_id = _optional_text(message.get("id"))
                            last_usage = _merge_usage(
                                last_usage,
                                _parse_anthropic_usage(message.get("usage", {})),
                            )
                    elif event_type == "content_block_start":
                        block = event_payload.get("content_block")
                        if isinstance(block, dict) and block.get("type") in {"thinking", "redacted_thinking"}:
                            thinking_blocks[int(event_payload.get("index", 0))] = dict(block)
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            index = _stream_tool_index(
                                event_payload.get("index"),
                                streamed_tool_calls,
                                provider_tool_indexes,
                                new_call=True,
                            )
                            initial_arguments = _stream_arguments_json(
                                block.get("input")
                            )
                            if initial_arguments == "{}":
                                initial_arguments = ""
                            state = _StreamingToolCall(
                                index=index,
                                call_id=_optional_text(block.get("id")),
                                name=_optional_text(block.get("name")),
                                initial_arguments_json=initial_arguments,
                            )
                            streamed_tool_calls[index] = state
                            yield LLMStreamEvent(
                                event_type="tool_call_delta",
                                tool_call_chunk=LLMToolCallChunk(
                                    index=index,
                                    call_id=state.call_id,
                                    name=state.name,
                                    arguments_delta=initial_arguments,
                                ),
                                call_id=call_id,
                            )
                    elif event_type == "content_block_delta":
                        delta_payload = event_payload.get("delta")
                        if isinstance(delta_payload, dict):
                            if delta_payload.get("type") in {"thinking_delta", "signature_delta"}:
                                block = thinking_blocks.setdefault(
                                    int(event_payload.get("index", 0)), {"type": "thinking", "thinking": ""},
                                )
                                field = "thinking" if delta_payload["type"] == "thinking_delta" else "signature"
                                block[field] = str(block.get(field, "")) + str(delta_payload.get(field) or "")
                            elif delta_payload.get("type") == "input_json_delta":
                                index = _stream_tool_index(
                                    event_payload.get("index"),
                                    streamed_tool_calls,
                                    provider_tool_indexes,
                                )
                                state = streamed_tool_calls.setdefault(
                                    index, _StreamingToolCall(index=index)
                                )
                                delta = str(
                                    delta_payload.get("partial_json") or ""
                                )
                                if delta:
                                    state.append(delta)
                                    yield LLMStreamEvent(
                                        event_type="tool_call_delta",
                                        tool_call_chunk=LLMToolCallChunk(
                                            index=index,
                                            arguments_delta=delta,
                                        ),
                                        call_id=call_id,
                                    )
                            else:
                                delta = str(delta_payload.get("text") or "")
                                if delta:
                                    text_parts.append(delta)
                                    yield LLMStreamEvent(
                                        event_type="text_delta",
                                        delta=delta,
                                        call_id=call_id,
                                    )
                    elif event_type == "message_delta":
                        delta_payload = event_payload.get("delta")
                        if isinstance(delta_payload, dict):
                            finish_reason = _optional_text(
                                delta_payload.get("stop_reason")
                            )
                        last_usage = _merge_usage(
                            last_usage,
                            _parse_anthropic_usage(event_payload.get("usage", {})),
                        )
                        if any(
                            value is not None for value in _usage_values(last_usage)
                        ):
                            yield LLMStreamEvent(
                                event_type="usage",
                                usage=last_usage,
                                call_id=call_id,
                            )
                    elif event_type == "message_stop":
                        text = "".join(text_parts)
                        tool_calls = _completed_stream_tool_calls(streamed_tool_calls)
                        if not text.strip() and not tool_calls:
                            raise LLMGatewayError(
                                "LLM_EMPTY_RESPONSE", "模型返回了空内容，请稍后重试。"
                            )
                        parsed = LLMResponse(
                            text=text,
                            model_id=profile.id,
                            upstream_model=profile.upstream_model,
                            usage=last_usage,
                            cost=calculate_cost(profile, last_usage, None),
                            finish_reason=finish_reason,
                            provider_request_id=provider_request_id,
                            call_id=call_id,
                            tool_calls=tool_calls,
                            thinking_blocks=tuple(thinking_blocks[index] for index in sorted(thinking_blocks)),
                        )
                        await self._record_success(
                            call_id,
                            request,
                            profile,
                            started_at,
                            timer,
                            parsed,
                            wire_request_body=payload,
                            fallback_from_provider=fallback_from_provider,
                        )
                        recorded = True
                        completed = True
                        yield LLMStreamEvent(
                            event_type="completed",
                            response=parsed,
                            usage=last_usage,
                            call_id=call_id,
                        )
                    elif event_type in {
                        "response.failed",
                        "response.incomplete",
                        "error",
                    }:
                        raise LLMGatewayError(
                            "LLM_STREAM_INTERRUPTED",
                            "模型流式输出中断，请稍后重试。",
                        )
            if not completed:
                raise LLMGatewayError(
                    "LLM_STREAM_INTERRUPTED", "模型流式输出中断，请稍后重试。"
                )
        except asyncio.CancelledError:
            safe = LLMGatewayError(
                "LLM_CLIENT_DISCONNECTED", "客户端已断开，模型调用已清理。"
            )
            if not recorded:
                await asyncio.shield(
                    self._record_failure(
                        call_id,
                        request,
                        profile,
                        started_at,
                        timer,
                        safe,
                        last_usage,
                        wire_request_body=payload,
                        fallback_from_provider=fallback_from_provider,
                    )
                )
                recorded = True
            raise
        except Exception as exc:
            safe = _normalize_error(exc)
            if (
                profile.provider == "rightcode"
                and not self._enforce_active_provider
                and not text_parts
                and not streamed_tool_calls
                and self._can_fallback(safe)
            ):
                fallback_profile = self._fallback_profile(profile)
                fallback_from_provider = profile.provider
                fallback_payload = _request_payload(
                    fallback_profile,
                    request,
                    stream=False,
                )
                try:
                    completion = await self._complete_with_retries(
                        fallback_profile,
                        request,
                        fallback_payload,
                        call_id,
                    )
                except Exception as fallback_error:
                    safe = _normalize_error(fallback_error)
                    profile = fallback_profile
                    payload = fallback_payload
                else:
                    profile = fallback_profile
                    payload = fallback_payload
                    parsed = completion.response
                    if parsed.text:
                        yield LLMStreamEvent(
                            event_type="text_delta",
                            delta=parsed.text,
                            call_id=call_id,
                        )
                    if any(value is not None for value in _usage_values(parsed.usage)):
                        yield LLMStreamEvent(
                            event_type="usage",
                            usage=parsed.usage,
                            call_id=call_id,
                        )
                    await self._record_success(
                        call_id,
                        request,
                        profile,
                        started_at,
                        timer,
                        parsed,
                        wire_request_body=payload,
                        fallback_from_provider=fallback_from_provider,
                        diagnostics=completion.diagnostics,
                    )
                    recorded = True
                    completed = True
                    yield LLMStreamEvent(
                        event_type="completed",
                        response=parsed,
                        usage=parsed.usage,
                        call_id=call_id,
                    )
                    return
            if not recorded:
                await self._record_failure(
                    call_id,
                    request,
                    profile,
                    started_at,
                    timer,
                    safe,
                    last_usage,
                    wire_request_body=payload,
                    fallback_from_provider=fallback_from_provider,
                )
                recorded = True
            yield LLMStreamEvent(
                event_type="failed", error=safe.message, call_id=call_id
            )
        finally:
            if not recorded and not completed:
                safe = LLMGatewayError(
                    "LLM_CLIENT_DISCONNECTED", "客户端已断开，模型调用已清理。"
                )
                await self._record_failure(
                    call_id,
                    request,
                    profile,
                    started_at,
                    timer,
                    safe,
                    last_usage,
                    wire_request_body=payload,
                    fallback_from_provider=fallback_from_provider,
                )

    def _ensure_configured(self) -> None:
        if not self.configured:
            raise LLMGatewayError(
                "LLM_TOKEN_MISSING",
                "尚未配置模型服务密钥，请在本机环境中完成配置。",
            )

    def _ensure_provider_configured(
        self, provider: Literal["rightcode", "deepseek_official"]
    ) -> None:
        if provider == "rightcode" and not self._rightcode_configured:
            raise LLMGatewayError(
                "LLM_TOKEN_MISSING", "尚未配置 RightCode 供应商密钥。"
            )
        if provider == "deepseek_official" and not self._fallback_configured:
            raise LLMGatewayError(
                "LLM_TOKEN_MISSING", "尚未配置 DeepSeek 官方供应商密钥。"
            )

    def _can_fallback(self, error: LLMGatewayError) -> bool:
        return self._fallback_configured and error.code in {
            "LLM_NETWORK_ERROR",
            "LLM_TIMEOUT",
            "LLM_RATE_LIMITED",
            "LLM_UPSTREAM_ERROR",
            "LLM_STREAM_INTERRUPTED",
        }

    def _fallback_profile(
        self, requested: LLMTransportProfile
    ) -> LLMTransportProfile:
        return LLMTransportProfile(
            id=requested.id,
            display_name=requested.display_name,
            provider="deepseek_official",
            upstream_model=self._settings.deepseek_fallback_model_id,
            wire_protocol="anthropic_messages",
            base_url_key="DEEPSEEK_ANTHROPIC_BASE_URL",
            enabled=True,
            is_default=requested.is_default,
            supports_streaming=True,
            upstream_verified=True,
        )

    def _endpoint(self, profile: LLMTransportProfile) -> str:
        if profile.provider == "deepseek_official":
            return _join_url(
                self._settings.deepseek_anthropic_base_url,
                "v1/messages",
            )
        if profile.wire_protocol == "anthropic_messages":
            if profile.base_url_key == "RIGHTCODE_DEEPSEEK_ANTHROPIC_BASE_URL":
                return _join_url(
                    self._settings.rightcode_deepseek_anthropic_base_url,
                    "v1/messages",
                )
            return _join_url(
                self._settings.rightcode_claude_sale_base_url, "v1/messages"
            )
        return _join_url(self._settings.rightcode_responses_base_url, "responses")

    def _headers(self, profile: LLMModelProfile) -> dict[str, str]:
        token = (
            self._settings.deepseek_api_key.get_secret_value()
            if profile.provider == "deepseek_official"
            else self._settings.rightcode_api_key.get_secret_value()
        )
        if profile.wire_protocol == "anthropic_messages":
            return {
                "Content-Type": "application/json",
                "x-api-key": token,
                "anthropic-version": "2023-06-01",
            }
        return {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + token,
        }

    async def _record_success(
        self,
        call_id: str,
        request: LLMRequest,
        profile: LLMModelProfile,
        started_at: str,
        timer: float,
        response: LLMResponse,
        *,
        wire_request_body: dict[str, Any],
        fallback_from_provider: str | None = None,
        diagnostics: _GatewayDiagnostics | None = None,
    ) -> None:
        finished_at = _now_iso()
        duration_ms = max(0, round((perf_counter() - timer) * 1000))
        await self._usage_repository.append(
            _call_record(
                call_id=call_id,
                request=request,
                profile=profile,
                status="completed",
                started_at=started_at,
                timer=timer,
                usage=response.usage,
                cost=response.cost,
                provider_request_id=(
                    diagnostics.provider_request_id
                    if diagnostics is not None
                    else response.provider_request_id
                ),
                status_code=diagnostics.status_code
                if diagnostics is not None
                else None,
                error_summary=(
                    diagnostics.error_summary if diagnostics is not None else None
                ),
                content_block_types=(
                    diagnostics.content_block_types if diagnostics is not None else ()
                ),
                retry_count=diagnostics.retry_count if diagnostics is not None else 0,
                finished_at=finished_at,
                duration_ms=duration_ms,
                fallback_from_provider=fallback_from_provider,
            )
        )
        if self._replay_repository is not None and request.run_id:
            await self._replay_repository.save(
                _replay_record(
                    call_id=call_id,
                    request=request,
                    profile=profile,
                    status="completed",
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_ms=duration_ms,
                    usage=response.usage,
                    response=response,
                    diagnostics=diagnostics,
                    wire_request_body=wire_request_body,
                    fallback_from_provider=fallback_from_provider,
                )
            )

    async def _record_failure(
        self,
        call_id: str,
        request: LLMRequest,
        profile: LLMModelProfile,
        started_at: str,
        timer: float,
        error: LLMGatewayError,
        usage: LLMUsage | None = None,
        *,
        wire_request_body: dict[str, Any] | None = None,
        fallback_from_provider: str | None = None,
    ) -> None:
        resolved_usage = usage or LLMUsage()
        finished_at = _now_iso()
        duration_ms = max(0, round((perf_counter() - timer) * 1000))
        await self._usage_repository.append(
            _call_record(
                call_id=call_id,
                request=request,
                profile=profile,
                status="failed",
                started_at=started_at,
                timer=timer,
                usage=resolved_usage,
                cost=LLMCost(),
                error=error,
                finished_at=finished_at,
                duration_ms=duration_ms,
                fallback_from_provider=fallback_from_provider,
            )
        )
        if self._replay_repository is not None and request.run_id:
            await self._replay_repository.save(
                _replay_record(
                    call_id=call_id,
                    request=request,
                    profile=profile,
                    status="failed",
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_ms=duration_ms,
                    usage=resolved_usage,
                    error=error,
                    wire_request_body=wire_request_body,
                    fallback_from_provider=fallback_from_provider,
                )
            )


def _message(role: str, content: str):
    from taichu.infrastructure.llm.contracts import LLMMessage

    return LLMMessage(role=role, content=content)  # type: ignore[arg-type]


def _request_payload(
    profile: LLMModelProfile, request: LLMRequest, *, stream: bool
) -> dict[str, Any]:
    if profile.wire_protocol == "anthropic_messages":
        return _anthropic_payload(profile, request, stream=stream)
    return _responses_payload(profile, request, stream=stream)


def _responses_payload(
    profile: LLMModelProfile, request: LLMRequest, *, stream: bool
) -> dict[str, Any]:
    system_parts = [item.content for item in request.messages if item.role == "system"]
    input_items: list[dict[str, Any]] = []
    for item in request.messages:
        if item.role == "system":
            continue
        if item.role == "tool":
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": item.tool_call_id,
                    "output": item.content,
                }
            )
            continue
        if item.content:
            input_items.append(
                {
                    "type": "message",
                    "role": item.role,
                    "content": [{"type": "input_text", "text": item.content}],
                }
            )
        for tool_call in item.tool_calls:
            input_items.append(
                {
                    "type": "function_call",
                    "call_id": tool_call.call_id,
                    "name": tool_call.name,
                    "arguments": tool_call.arguments_json,
                }
            )
    payload: dict[str, Any] = {
        "model": profile.upstream_model,
        "input": input_items,
        "stream": stream,
    }
    if system_parts:
        payload["instructions"] = "\n\n".join(system_parts)
    if request.tools:
        payload["tools"] = [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
                "strict": tool.strict,
            }
            for tool in request.tools
        ]
        payload["tool_choice"] = _responses_tool_choice(request.tool_choice)
    if request.temperature is not None and _supports_temperature(profile):
        payload["temperature"] = request.temperature
    if request.max_output_tokens is not None:
        payload["max_output_tokens"] = request.max_output_tokens
    return payload


def _anthropic_payload(
    profile: LLMModelProfile, request: LLMRequest, *, stream: bool
) -> dict[str, Any]:
    system_parts = [
        item.content
        for item in request.messages
        if item.role in {"system", "developer"}
    ]
    messages: list[dict[str, Any]] = []
    pending_tool_results: list[dict[str, Any]] = []

    def flush_tool_results() -> None:
        if pending_tool_results:
            messages.append({"role": "user", "content": list(pending_tool_results)})
            pending_tool_results.clear()

    for item in request.messages:
        if item.role in {"system", "developer"}:
            continue
        if item.role == "tool":
            pending_tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": item.tool_call_id,
                    "content": item.content,
                    "is_error": item.is_error,
                }
            )
            continue
        flush_tool_results()
        if item.role == "assistant" and (item.tool_calls or item.thinking_blocks):
            content: list[dict[str, Any]] = [*item.thinking_blocks]
            if item.content:
                content.append({"type": "text", "text": item.content})
            for tool_call in item.tool_calls:
                try:
                    tool_input = json.loads(tool_call.arguments_json)
                except json.JSONDecodeError:
                    tool_input = {}
                content.append(
                    {
                        "type": "tool_use",
                        "id": tool_call.call_id,
                        "name": tool_call.name,
                        "input": tool_input,
                    }
                )
            messages.append({"role": "assistant", "content": content})
        else:
            messages.append({"role": item.role, "content": item.content})
    flush_tool_results()
    payload: dict[str, Any] = {
        "model": profile.upstream_model,
        "messages": messages,
        "max_tokens": request.max_output_tokens or 4096,
        "stream": stream,
    }
    if profile.id.startswith("deepseek-"):
        payload["thinking"] = {"type": "enabled"}
    if system_parts:
        payload["system"] = "\n\n".join(system_parts)
    if request.tools:
        payload["tools"] = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.parameters,
            }
            for tool in request.tools
        ]
        payload["tool_choice"] = _anthropic_tool_choice(request.tool_choice)
    if request.temperature is not None and _supports_temperature(profile):
        payload["temperature"] = request.temperature
    return payload


def _supports_temperature(profile: LLMModelProfile) -> bool:
    """GPT-5.6 推理模型的 Responses 接口拒绝 temperature 参数。"""
    return not (
        profile.wire_protocol == "openai_responses"
        and profile.id.startswith("gpt-5-6-")
    )


def _responses_tool_choice(value: str) -> str | dict[str, str]:
    """把 LangChain 的命名工具选择映射到 Responses 原生参数。"""
    normalized = value.strip()
    if normalized in {"auto", "none", "required"}:
        return normalized
    if normalized == "any":
        return "required"
    return {"type": "function", "name": normalized}


def _anthropic_tool_choice(value: str) -> dict[str, str]:
    """把 LangChain 的命名工具选择映射到 Anthropic 原生参数。"""
    normalized = value.strip()
    standard = {
        "auto": {"type": "auto"},
        "none": {"type": "none"},
        "required": {"type": "any"},
        "any": {"type": "any"},
    }
    return standard.get(normalized, {"type": "tool", "name": normalized})


def _output_was_truncated(finish_reason: str | None) -> bool:
    return (finish_reason or "").strip().casefold() in {
        "length",
        "max_output_tokens",
        "max_tokens",
        "incomplete",
    }


def _stream_tool_index(
    value: Any,
    states: dict[int, _StreamingToolCall],
    provider_indexes: dict[int, int],
    *,
    new_call: bool = False,
) -> int:
    provider_index = _optional_int(value)
    if provider_index is None or provider_index < 0:
        if not new_call and len(provider_indexes) == 1:
            return next(iter(provider_indexes.values()))
        provider_index = max(provider_indexes, default=-1) + 1
    existing = provider_indexes.get(provider_index)
    if existing is not None:
        return existing
    normalized_index = len(states)
    provider_indexes[provider_index] = normalized_index
    return normalized_index


def _stream_arguments_json(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _completed_stream_tool_calls(
    states: dict[int, _StreamingToolCall],
) -> tuple[LLMToolCall, ...]:
    calls: list[LLMToolCall] = []
    for state in sorted(states.values(), key=lambda item: item.index):
        if not state.call_id or not state.name:
            raise LLMGatewayError(
                "LLM_RESPONSE_INVALID",
                "模型流式工具调用缺少名称或关联标识，请稍后重试。",
                error_summary=f"流式函数调用元数据不完整：index={state.index}",
            )
        calls.append(
            LLMToolCall(
                call_id=state.call_id,
                name=state.name,
                arguments_json=state.arguments_json,
            )
        )
    return tuple(calls)


def _parse_response(
    payload: dict[str, Any], profile: LLMModelProfile, call_id: str
) -> LLMResponse:
    if profile.wire_protocol == "anthropic_messages":
        return _parse_anthropic_response(payload, profile, call_id)
    return _parse_responses_response(payload, profile, call_id)


def _parse_responses_response(
    payload: dict[str, Any], profile: LLMModelProfile, call_id: str
) -> LLMResponse:
    text = payload.get("output_text")
    if not isinstance(text, str):
        parts: list[str] = []
        output = payload.get("output", [])
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content", [])
                if not isinstance(content, list):
                    continue
                for block in content:
                    if isinstance(block, dict) and block.get("type") in {
                        "output_text",
                        "text",
                    }:
                        value = block.get("text")
                        if isinstance(value, str):
                            parts.append(value)
        text = "".join(parts)
    tool_calls: list[LLMToolCall] = []
    output = payload.get("output", [])
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "function_call":
                continue
            provider_call_id = _optional_text(item.get("call_id") or item.get("id"))
            name = _optional_text(item.get("name"))
            arguments = item.get("arguments", "{}")
            if isinstance(arguments, dict):
                arguments = json.dumps(
                    arguments,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            if provider_call_id and name and isinstance(arguments, str):
                tool_calls.append(
                    LLMToolCall(
                        call_id=provider_call_id,
                        name=name,
                        arguments_json=arguments,
                    )
                )
    usage = _parse_usage(payload.get("usage", {}))
    actual_amount, actual_currency = _extract_actual_cost(payload)
    return LLMResponse(
        text=text or "",
        model_id=profile.id,
        upstream_model=profile.upstream_model,
        usage=usage,
        cost=calculate_cost(profile, usage, actual_amount, actual_currency),
        finish_reason=_optional_text(
            payload.get("finish_reason") or payload.get("status")
        ),
        provider_request_id=_optional_text(payload.get("id")),
        call_id=call_id,
        tool_calls=tuple(tool_calls),
    )


def _parse_anthropic_response(
    payload: dict[str, Any], profile: LLMModelProfile, call_id: str
) -> LLMResponse:
    parts: list[str] = []
    content = payload.get("content", [])
    tool_calls: list[LLMToolCall] = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(block, dict) and block.get("type") == "tool_use":
                call_id_value = _optional_text(block.get("id"))
                name = _optional_text(block.get("name"))
                arguments = block.get("input", {})
                if call_id_value and name and isinstance(arguments, dict):
                    tool_calls.append(
                        LLMToolCall(
                            call_id=call_id_value,
                            name=name,
                            arguments_json=json.dumps(
                                arguments,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        )
                    )
    usage = _parse_anthropic_usage(payload.get("usage", {}))
    actual_amount, actual_currency = _extract_actual_cost(payload)
    return LLMResponse(
        text="".join(parts),
        model_id=profile.id,
        upstream_model=profile.upstream_model,
        usage=usage,
        cost=calculate_cost(profile, usage, actual_amount, actual_currency),
        finish_reason=_optional_text(payload.get("stop_reason")),
        provider_request_id=_optional_text(payload.get("id")),
        call_id=call_id,
        tool_calls=tuple(tool_calls),
        thinking_blocks=tuple(
            dict(block) for block in content
            if isinstance(block, dict) and block.get("type") in {"thinking", "redacted_thinking"}
        ) if isinstance(content, list) else (),
    )


def _parse_usage(payload: Any) -> LLMUsage:
    if not isinstance(payload, dict):
        return LLMUsage()
    input_details = payload.get("input_tokens_details")
    output_details = payload.get("output_tokens_details")
    if not isinstance(input_details, dict):
        input_details = {}
    if not isinstance(output_details, dict):
        output_details = {}
    return LLMUsage(
        input_tokens=_optional_int(payload.get("input_tokens")),
        cached_input_tokens=_optional_int(input_details.get("cached_tokens")),
        output_tokens=_optional_int(payload.get("output_tokens")),
        reasoning_tokens=_optional_int(output_details.get("reasoning_tokens")),
        total_tokens=_optional_int(payload.get("total_tokens")),
    )


def _parse_anthropic_usage(payload: Any) -> LLMUsage:
    if not isinstance(payload, dict):
        return LLMUsage()
    input_tokens = _optional_int(payload.get("input_tokens"))
    cached_tokens = _optional_int(
        payload.get("cache_read_input_tokens") or payload.get("cached_input_tokens")
    )
    output_tokens = _optional_int(payload.get("output_tokens"))
    total_tokens = (
        input_tokens + (cached_tokens or 0) + output_tokens
        if input_tokens is not None and output_tokens is not None
        else None
    )
    return LLMUsage(
        input_tokens=input_tokens,
        cached_input_tokens=cached_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


def _merge_usage(current: LLMUsage, incoming: LLMUsage) -> LLMUsage:
    input_tokens = (
        incoming.input_tokens
        if incoming.input_tokens is not None
        else current.input_tokens
    )
    output_tokens = (
        incoming.output_tokens
        if incoming.output_tokens is not None
        else current.output_tokens
    )
    cached_input_tokens = (
        incoming.cached_input_tokens
        if incoming.cached_input_tokens is not None
        else current.cached_input_tokens
    )
    total_tokens = (
        incoming.total_tokens
        if incoming.total_tokens is not None
        else (
            input_tokens + (cached_input_tokens or 0) + output_tokens
            if input_tokens is not None and output_tokens is not None
            else current.total_tokens
        )
    )
    return LLMUsage(
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=(
            incoming.reasoning_tokens
            if incoming.reasoning_tokens is not None
            else current.reasoning_tokens
        ),
        total_tokens=total_tokens,
    )


def _extract_actual_cost(payload: dict[str, Any]) -> tuple[Decimal | None, str | None]:
    candidates: list[tuple[Any, Any]] = [
        (payload.get("cost"), payload.get("currency")),
    ]
    for container_name in ("usage", "billing"):
        container = payload.get(container_name)
        if isinstance(container, dict):
            candidates.append(
                (
                    container.get("cost") or container.get("amount"),
                    container.get("currency"),
                )
            )
    for value, currency in candidates:
        if isinstance(value, dict):
            currency = value.get("currency") or currency
            value = value.get("amount") or value.get("total")
        if value is None:
            continue
        try:
            return Decimal(str(value)), _optional_text(currency)
        except InvalidOperation:
            continue
    return None, None


async def _iter_sse(
    response: httpx.Response,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    event_name = ""
    data_lines: list[str] = []
    async for line in response.aiter_lines():
        if not line:
            if data_lines:
                raw = "\n".join(data_lines)
                if raw != "[DONE]":
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError as exc:
                        raise LLMGatewayError(
                            "LLM_STREAM_INVALID", "模型流式响应格式不正确。"
                        ) from exc
                    if isinstance(payload, dict):
                        observation = response.extensions.get(ATTEMPT_EXTENSION)
                        if isinstance(observation, StreamAttemptObservation):
                            await observation.received(event_name, payload)
                        yield event_name, payload
            event_name = ""
            data_lines = []
            continue
        if line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].strip())
    if data_lines:
        raw = "\n".join(data_lines)
        if raw != "[DONE]":
            payload = json.loads(raw)
            if isinstance(payload, dict):
                observation = response.extensions.get(ATTEMPT_EXTENSION)
                if isinstance(observation, StreamAttemptObservation):
                    await observation.received(event_name, payload)
                yield event_name, payload


def _response_provider_request_id(response: httpx.Response) -> str | None:
    """从常见响应头中读取供应商请求 ID，不保存完整响应头。"""

    for header in (
        "x-request-id",
        "request-id",
        "x-amzn-requestid",
        "cf-ray",
    ):
        value = response.headers.get(header, "").strip()
        if value:
            return value[:256]
    return None


def _content_block_types(
    payload: dict[str, Any], response: LLMResponse
) -> tuple[str, ...]:
    """提取安全的响应结构类型，用于诊断空正文与截断。"""

    values: list[str] = []

    def add(value: Any) -> None:
        if not isinstance(value, str):
            return
        normalized = value.strip()[:64]
        if normalized and normalized not in values and len(values) < 32:
            values.append(normalized)

    for key in ("content", "output"):
        blocks = payload.get(key)
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if not isinstance(block, dict):
                continue
            add(block.get("type"))
            nested = block.get("content")
            if isinstance(nested, list):
                for item in nested:
                    if isinstance(item, dict):
                        add(item.get("type"))
    if not values and response.text.strip():
        add("text")
    if response.tool_calls:
        add("tool_call")
    return tuple(values)


def _upstream_error_summary(response: httpx.Response) -> str | None:
    """只保留上游错误的类型、代码和短消息，并执行密钥脱敏。"""

    try:
        payload = response.json()
    except (json.JSONDecodeError, httpx.DecodingError):
        return f"HTTP {response.status_code}，响应正文不可解析"
    if not isinstance(payload, dict):
        return f"HTTP {response.status_code}"
    error = payload.get("error")
    source = error if isinstance(error, dict) else payload
    parts: list[str] = []
    for key in ("type", "code", "message"):
        value = source.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text and text not in parts:
            parts.append(text)
    summary = " | ".join(parts) or f"HTTP {response.status_code}"
    redacted, _ = _redact_secret_values(summary)
    return redacted[:1_000]


def _status_error(
    status_code: int,
    *,
    provider_request_id: str | None = None,
    error_summary: str | None = None,
    retry_count: int = 0,
) -> LLMGatewayError:
    if status_code == 402:
        return LLMGatewayError(
            "LLM_INSUFFICIENT_BALANCE",
            "模型供应商账户余额不足，请充值后重试。",
            status_code=status_code,
            provider_request_id=provider_request_id,
            error_summary=error_summary,
            retry_count=retry_count,
        )
    if status_code in {401, 403}:
        return LLMGatewayError(
            "LLM_MODEL_FORBIDDEN",
            "当前密钥无权调用该模型，请检查本机密钥权限。",
            status_code=status_code,
            provider_request_id=provider_request_id,
            error_summary=error_summary,
            retry_count=retry_count,
        )
    if status_code == 429:
        return LLMGatewayError(
            "LLM_RATE_LIMITED",
            "模型服务请求过多或额度不足，请稍后重试并检查账户额度。",
            status_code=status_code,
            provider_request_id=provider_request_id,
            error_summary=error_summary,
            retry_count=retry_count,
        )
    if status_code >= 500:
        return LLMGatewayError(
            "LLM_UPSTREAM_ERROR",
            "模型服务暂时不可用，请稍后重试。",
            status_code=status_code,
            provider_request_id=provider_request_id,
            error_summary=error_summary,
            retry_count=retry_count,
        )
    return LLMGatewayError(
        "LLM_REQUEST_REJECTED",
        "模型服务拒绝了本次请求，请检查模型权限或请求设置。",
        status_code=status_code,
        provider_request_id=provider_request_id,
        error_summary=error_summary,
        retry_count=retry_count,
    )


def _normalize_error(exc: Exception) -> LLMGatewayError:
    if isinstance(exc, LLMGatewayError):
        return exc
    if isinstance(exc, LLMModelManagementError):
        return LLMGatewayError(exc.code, exc.message)
    if isinstance(exc, httpx.TimeoutException):
        return LLMGatewayError("LLM_TIMEOUT", "模型调用超时，请稍后重试。")
    if isinstance(exc, httpx.NetworkError):
        return LLMGatewayError(
            "LLM_NETWORK_ERROR", "无法连接模型服务，请稍后重试。"
        )
    if isinstance(exc, (json.JSONDecodeError, httpx.DecodingError)):
        return LLMGatewayError(
            "LLM_RESPONSE_INVALID", "模型服务返回了无法解析的响应。"
        )
    return LLMGatewayError("LLM_CALL_FAILED", "模型调用失败，请稍后重试。")


def _call_record(
    *,
    call_id: str,
    request: LLMRequest,
    profile: LLMModelProfile,
    status: str,
    started_at: str,
    timer: float,
    usage: LLMUsage,
    cost: LLMCost,
    provider_request_id: str | None = None,
    status_code: int | None = None,
    error_summary: str | None = None,
    content_block_types: tuple[str, ...] = (),
    retry_count: int = 0,
    error: LLMGatewayError | None = None,
    finished_at: str | None = None,
    duration_ms: int | None = None,
    fallback_from_provider: str | None = None,
) -> LLMCallRecord:
    return LLMCallRecord(
        call_id=call_id,
        run_id=request.run_id,
        task_type=request.task_type,
        task_name=request.task_name,
        feature=request.feature,
        chapter_ids=list(request.chapter_ids),
        model_id=profile.id,
        model_display_name=profile.display_name,
        provider=profile.provider,
        fallback_from_provider=fallback_from_provider,
        upstream_model=profile.upstream_model,
        wire_protocol=profile.wire_protocol,
        status=status,  # type: ignore[arg-type]
        started_at=started_at,
        finished_at=finished_at or _now_iso(),
        duration_ms=(
            duration_ms
            if duration_ms is not None
            else max(0, round((perf_counter() - timer) * 1000))
        ),
        input_tokens=usage.input_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        output_tokens=usage.output_tokens,
        reasoning_tokens=usage.reasoning_tokens,
        total_tokens=usage.total_tokens,
        cost_amount=cost.amount,
        cost_currency=cost.currency,
        cost_kind=cost.kind,
        provider_request_id=(
            error.provider_request_id if error is not None else provider_request_id
        ),
        status_code=error.status_code if error is not None else status_code,
        error_code=error.code if error else None,
        error_message=error.message if error else None,
        error_summary=(error.error_summary if error is not None else error_summary),
        content_block_types=list(
            error.content_block_types if error is not None else content_block_types
        ),
        retry_count=error.retry_count if error is not None else retry_count,
    )


_SECRET_PATTERNS = (
    re.compile(r"(?i)((?:api[_ -]?key|x-api-key)\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)\bsk-[a-z0-9_-]{8,}"),
)
_REDACTION = "[已脱敏]"


def _redact_secret_values(value: str) -> tuple[str, int]:
    redacted = value
    count = 0
    for index, pattern in enumerate(_SECRET_PATTERNS):
        if index == 0:
            redacted, replacements = pattern.subn(
                lambda match: f"{match.group(1)}{_REDACTION}",
                redacted,
            )
        elif index == 1:
            redacted, replacements = pattern.subn(f"Bearer {_REDACTION}", redacted)
        else:
            redacted, replacements = pattern.subn(_REDACTION, redacted)
        count += replacements
    return redacted, count


def _redact_payload_value(value: Any) -> tuple[Any, int]:
    if isinstance(value, str):
        return _redact_secret_values(value)
    if isinstance(value, list):
        redacted_items: list[Any] = []
        total = 0
        for item in value:
            redacted, replacements = _redact_payload_value(item)
            redacted_items.append(redacted)
            total += replacements
        return redacted_items, total
    if isinstance(value, dict):
        redacted_mapping: dict[str, Any] = {}
        total = 0
        for key, item in value.items():
            redacted, replacements = _redact_payload_value(item)
            redacted_mapping[key] = redacted
            total += replacements
        return redacted_mapping, total
    return value, 0


def _replay_record(
    *,
    call_id: str,
    request: LLMRequest,
    profile: LLMModelProfile,
    status: str,
    started_at: str,
    finished_at: str,
    duration_ms: int,
    usage: LLMUsage,
    response: LLMResponse | None = None,
    diagnostics: _GatewayDiagnostics | None = None,
    error: LLMGatewayError | None = None,
    wire_request_body: dict[str, Any] | None = None,
    fallback_from_provider: str | None = None,
) -> LLMCallReplayRecord:
    messages: list[LLMReplayMessage] = []
    redaction_count = 0
    for message in request.messages:
        content, replacements = _redact_secret_values(message.content)
        redaction_count += replacements
        replay_tool_calls: list[LLMReplayToolCall] = []
        for tool_call in message.tool_calls:
            arguments_json, replacements = _redact_secret_values(
                tool_call.arguments_json
            )
            redaction_count += replacements
            replay_tool_calls.append(
                LLMReplayToolCall(
                    call_id=tool_call.call_id,
                    name=tool_call.name,
                    arguments_json=arguments_json,
                )
            )
        messages.append(
            LLMReplayMessage(
                role=message.role,
                content=content,
                tool_calls=replay_tool_calls,
                tool_call_id=message.tool_call_id,
                tool_name=message.tool_name,
                is_error=message.is_error,
            )
        )

    response_text, response_replacements = _redact_secret_values(
        response.text if response is not None else ""
    )
    redaction_count += response_replacements
    response_tool_calls: list[LLMReplayToolCall] = []
    for tool_call in response.tool_calls if response is not None else ():
        arguments_json, replacements = _redact_secret_values(tool_call.arguments_json)
        redaction_count += replacements
        response_tool_calls.append(
            LLMReplayToolCall(
                call_id=tool_call.call_id,
                name=tool_call.name,
                arguments_json=arguments_json,
            )
        )
    error_message, error_replacements = _redact_secret_values(
        error.message if error is not None else ""
    )
    redaction_count += error_replacements
    redacted_wire_request_body: dict[str, Any] | None = None
    if wire_request_body is not None:
        redacted_payload, _ = _redact_payload_value(wire_request_body)
        if isinstance(redacted_payload, dict):
            redacted_wire_request_body = redacted_payload
    request_payload = {
        "model_id": profile.id,
        "temperature": request.temperature,
        "max_output_tokens": request.max_output_tokens,
        "messages": [message.model_dump(mode="json") for message in messages],
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
                "strict": tool.strict,
            }
            for tool in request.tools
        ],
        "tool_choice": request.tool_choice,
    }
    request_text = json.dumps(
        request_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return LLMCallReplayRecord(
        call_id=call_id,
        run_id=request.run_id or "",
        context_snapshot_id=request.context_snapshot_id,
        task_type=request.task_type,
        task_name=request.task_name,
        feature=request.feature,
        model_id=profile.id,
        provider=profile.provider,
        fallback_from_provider=fallback_from_provider,
        upstream_model=profile.upstream_model,
        wire_protocol=profile.wire_protocol,
        status=status,  # type: ignore[arg-type]
        temperature=request.temperature,
        max_output_tokens=request.max_output_tokens,
        wire_request_body=redacted_wire_request_body,
        messages=messages,
        tools=[
            LLMReplayToolDefinition(
                name=tool.name,
                description=tool.description,
                parameters=tool.parameters,
                strict=tool.strict,
            )
            for tool in request.tools
        ],
        tool_choice=request.tool_choice,
        response_tool_calls=response_tool_calls,
        response_text=response_text,
        request_sha256=hashlib.sha256(request_text.encode("utf-8")).hexdigest(),
        response_sha256=hashlib.sha256(response_text.encode("utf-8")).hexdigest(),
        redaction_count=redaction_count,
        input_tokens=usage.input_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        output_tokens=usage.output_tokens,
        reasoning_tokens=usage.reasoning_tokens,
        total_tokens=usage.total_tokens,
        finish_reason=response.finish_reason if response is not None else None,
        provider_request_id=(
            error.provider_request_id
            if error is not None
            else (
                diagnostics.provider_request_id
                if diagnostics is not None
                else (response.provider_request_id if response is not None else None)
            )
        ),
        status_code=(
            error.status_code
            if error is not None
            else (diagnostics.status_code if diagnostics is not None else None)
        ),
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=duration_ms,
        error_code=error.code if error is not None else None,
        error_message=error_message or None,
        error_summary=(
            error.error_summary
            if error is not None
            else (diagnostics.error_summary if diagnostics is not None else None)
        ),
        content_block_types=list(
            error.content_block_types
            if error is not None
            else (diagnostics.content_block_types if diagnostics is not None else ())
        ),
        retry_count=(
            error.retry_count
            if error is not None
            else (diagnostics.retry_count if diagnostics is not None else 0)
        ),
    )


def _usage_values(usage: LLMUsage) -> tuple[int | None, ...]:
    return (
        usage.input_tokens,
        usage.cached_input_tokens,
        usage.output_tokens,
        usage.reasoning_tokens,
        usage.total_tokens,
    )


def _optional_int(value: Any) -> int | None:
    return int(value) if isinstance(value, int | float) else None


def _optional_text(value: Any) -> str | None:
    return str(value) if value is not None else None


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
