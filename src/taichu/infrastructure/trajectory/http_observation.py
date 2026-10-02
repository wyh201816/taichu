"""实际流式 HTTP 业务载荷观测，不收集传输鉴权头。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import perf_counter
from typing import Any
from uuid import uuid4

import httpx

from taichu.application.trajectory.observer import ObservationScope, active_observation
from taichu.application.trajectory.models import TrajectoryEvent
from taichu.infrastructure.trajectory.model_observation import active_model_call

ATTEMPT_EXTENSION = "taichu_trajectory_attempt"


class StreamAttemptObservation:
    def __init__(self, scope: ObservationScope, call_id: str, attempt_id: str) -> None:
        self.scope = scope
        self.call_id = call_id
        self.attempt_id = attempt_id
        self.pending: list[TrajectoryEvent] = []
        self.last_flush = 0.0

    async def received(self, event_name: str, payload: dict[str, Any]) -> None:
        self.scope.collect(self.pending, "wire_stream_chunk", lambda: {
            "attempt_id": self.attempt_id, "event_name": event_name,
            "payload": payload,
        }, call_id=self.call_id)
        if len(self.pending) >= 32 or perf_counter() - self.last_flush >= 0.1:
            await self.flush()

    async def flush(self) -> None:
        await self.scope.recorder.append(self.pending)
        self.pending = []
        self.last_flush = perf_counter()


@asynccontextmanager
async def observed_http_stream(
    client: httpx.AsyncClient, method: str, url: str, *,
    headers: dict[str, str], json: dict[str, Any], provider: str,
    wire_protocol: str,
) -> AsyncIterator[httpx.Response]:
    scope = active_observation.get()
    call_id = active_model_call.get()
    if scope is None or call_id is None:
        async with client.stream(method, url, headers=headers, json=json) as response:
            yield response
        return
    attempt_id = f"attempt_{uuid4().hex}"
    timer = perf_counter()
    await scope.emit("attempt_started", {"attempt_id": attempt_id,
        "provider": provider, "wire_protocol": wire_protocol,
        "wire_request": json}, call_id=call_id)
    observation = StreamAttemptObservation(scope, call_id, attempt_id)
    status_code = None
    try:
        async with client.stream(method, url, headers=headers, json=json) as response:
            status_code = response.status_code
            response.extensions[ATTEMPT_EXTENSION] = observation
            yield response
    except BaseException as error:
        await scope.emit("attempt_failed", {"attempt_id": attempt_id,
            "error_type": type(error).__name__, "status_code": status_code,
            "duration_ms": round((perf_counter() - timer) * 1000)}, call_id=call_id)
        raise
    finally:
        await observation.flush()
        await scope.emit("attempt_stream_ended", {"attempt_id": attempt_id,
            "status_code": status_code,
            "duration_ms": round((perf_counter() - timer) * 1000)}, call_id=call_id)
