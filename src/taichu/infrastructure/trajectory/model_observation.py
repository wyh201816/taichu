"""在现有 LangChain 模型适配边界保存实际业务输入输出。"""

from dataclasses import asdict
from contextvars import ContextVar
from typing import Any
from collections.abc import AsyncIterator
from time import perf_counter
from uuid import uuid4
import httpx

from taichu.application.trajectory.observer import active_observation
from taichu.application.trajectory.models import TrajectoryEvent
from taichu.infrastructure.llm.contracts import (
    LLMGatewayContract, LLMRequest, LLMResponse, LLMStreamEvent,
)

active_model_call: ContextVar[str | None] = ContextVar("taichu_trajectory_model_call", default=None)


async def observed_stream(
    gateway: LLMGatewayContract, request: LLMRequest,
) -> AsyncIterator[tuple[LLMStreamEvent, str | None]]:
    scope = active_observation.get()
    if scope is None:
        async for event in gateway.stream(request):
            yield event, None
        return
    sanitizer = getattr(gateway, "sanitize_observation", None)
    if callable(sanitizer):
        scope.recorder.sanitize = sanitizer
    call_id = f"model_{uuid4().hex}"
    timer = perf_counter()
    await scope.emit_lazy("model_started", lambda: {"request": asdict(request), "stream": True}, call_id=call_id)
    pending: list[TrajectoryEvent] = []
    last_flush = 0.0
    terminal = False
    upstream = gateway.stream(request)
    try:
        while True:
            token = active_model_call.set(call_id)
            try:
                event = await anext(upstream)
            except StopAsyncIteration:
                break
            finally:
                active_model_call.reset(token)
            scope.collect(pending, "stream_chunk", lambda: {"event": asdict(event)}, call_id=call_id)
            if len(pending) >= 32 or perf_counter() - last_flush >= 0.1 or event.event_type in {"completed", "failed"}:
                await scope.recorder.append(pending)
                pending = []
                last_flush = perf_counter()
            if event.event_type == "completed" and event.response is not None:
                terminal = True
                response = event.response
                await scope.emit_lazy("model_completed", lambda: {
                    "response": asdict(response),
                    "duration_ms": round((perf_counter() - timer) * 1000),
                }, call_id=call_id)
                for tool_call in event.response.tool_calls:
                    scope.native_tool_origins[tool_call.call_id] = call_id
            elif event.event_type == "failed":
                terminal = True
                await scope.emit("model_failed", {"error": event.error,
                    "duration_ms": round((perf_counter() - timer) * 1000)}, call_id=call_id)
            yield event, call_id
    except BaseException as error:
        if not terminal:
            terminal = True
            await scope.emit("model_stopped" if type(error).__name__ in {"CancelledError", "GeneratorExit"} else "model_failed", {"error_type": type(error).__name__,
                "error": str(error), "duration_ms": round((perf_counter() - timer) * 1000)}, call_id=call_id)
        raise
    finally:
        await scope.recorder.append(pending)
        if not terminal:
            await scope.emit("model_incomplete", {"error": "模型流未返回完成事件，无法确认最终状态。",
                "duration_ms": round((perf_counter() - timer) * 1000)}, call_id=call_id)
        close = getattr(upstream, "aclose", None)
        if close is not None:
            await close()


async def observed_completion(
    gateway: LLMGatewayContract, request: LLMRequest,
) -> tuple[LLMResponse, str | None]:
    scope = active_observation.get()
    if scope is None:
        return await gateway.complete(request), None
    sanitizer = getattr(gateway, "sanitize_observation", None)
    if callable(sanitizer):
        scope.recorder.sanitize = sanitizer
    call_id = f"model_{uuid4().hex}"
    started = perf_counter()
    await scope.emit_lazy("model_started", lambda: {"request": asdict(request)}, call_id=call_id)
    token = active_model_call.set(call_id)
    try:
        response = await gateway.complete(request)
    except BaseException as error:
        await scope.emit("model_stopped" if type(error).__name__ == "CancelledError" else "model_failed", {
            "error_type": type(error).__name__, "error": str(error),
            "duration_ms": round((perf_counter() - started) * 1000),
        }, call_id=call_id)
        raise
    finally:
        active_model_call.reset(token)
    await scope.emit_lazy("model_completed", lambda: {
        "response": asdict(response),
        "duration_ms": round((perf_counter() - started) * 1000),
    }, call_id=call_id)
    for tool_call in response.tool_calls:
        scope.native_tool_origins[tool_call.call_id] = call_id
    return response, call_id


async def observed_http_post(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    json: dict[str, Any],
    provider: str,
    wire_protocol: str,
) -> httpx.Response:
    scope = active_observation.get()
    call_id = active_model_call.get()
    if scope is None or call_id is None:
        return await client.post(url, headers=headers, json=json)
    attempt_id = f"attempt_{uuid4().hex}"
    timer = perf_counter()
    await scope.emit("attempt_started", {
        "attempt_id": attempt_id, "provider": provider,
        "wire_protocol": wire_protocol, "wire_request": json,
    }, call_id=call_id)
    try:
        response = await client.post(url, headers=headers, json=json)
    except BaseException as error:
        await scope.emit("attempt_failed", {
            "attempt_id": attempt_id, "error_type": type(error).__name__,
            "duration_ms": round((perf_counter() - timer) * 1000),
        }, call_id=call_id)
        raise
    response.extensions["taichu_trajectory_attempt_id"] = attempt_id
    try:
        body: Any = response.json()
    except ValueError:
        body = response.text
    await scope.emit("attempt_received", {
        "attempt_id": attempt_id, "status_code": response.status_code,
        "wire_response": body,
        "duration_ms": round((perf_counter() - timer) * 1000),
    }, call_id=call_id)
    return response


async def observe_attempt_outcome(response: httpx.Response, *, status: str,
                                  error_code: str | None = None,
                                  error: str | None = None) -> None:
    scope = active_observation.get()
    call_id = active_model_call.get()
    attempt_id = response.extensions.get("taichu_trajectory_attempt_id")
    if scope is not None and call_id is not None and attempt_id:
        await scope.emit("attempt_outcome", {"attempt_id": attempt_id,
            "status": status, "error_code": error_code, "error": error}, call_id=call_id)
