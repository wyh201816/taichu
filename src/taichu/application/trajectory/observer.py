"""异步调用作用域与可降级观测；不修改执行语义。"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from collections.abc import Callable

from taichu.application.contracts.trajectory import TrajectoryRepository
from taichu.application.trajectory.models import TrajectoryEvent, TrajectoryRead

logger = logging.getLogger(__name__)


class TrajectoryRecorder:
    def __init__(self, repository: TrajectoryRepository) -> None:
        self.repository = repository
        self.incomplete: set[str] = set()
        self._pending_gaps: set[str] = set()
        self.sanitize: Callable[[Any], tuple[Any, int]] | None = None

    async def append(self, events: list[TrajectoryEvent]) -> None:
        if not events:
            return
        try:
            conversation_id = events[0].conversation_id
            gap = conversation_id in self._pending_gaps
            if gap:
                events = [events[0].model_copy(update={"event_id": f"gap_{events[0].event_id}", "kind": "recording_gap", "data": {"reason": "此前观测未能保存。"}}), *events]
            if self.sanitize is not None:
                safe_events = []
                for event in events:
                    data, count = self.sanitize(event.data)
                    if count:
                        data["redaction_count"] = int(data.get("redaction_count", 0)) + count
                    safe_events.append(event.model_copy(update={"data": data}))
                events = safe_events
            await self.repository.append(events)
            if gap:
                self._pending_gaps.discard(conversation_id)
        except Exception:
            self.incomplete.update(event.conversation_id for event in events)
            self._pending_gaps.update(event.conversation_id for event in events)
            logger.exception("轨迹写入失败；助手继续执行，本次轨迹可能不完整。")

    async def read(self, conversation_id: str) -> TrajectoryRead:
        try:
            result = await self.repository.read(conversation_id)
        except Exception:
            return TrajectoryRead(complete=False, warnings=["轨迹读取失败，请检查本机存储。"])
        if conversation_id in self.incomplete:
            return result.model_copy(update={
                "complete": False,
                "warnings": [*result.warnings, "轨迹写入曾失败，记录不完整。"],
            })
        return result

    def mark_incomplete(self, conversation_id: str) -> None:
        self.incomplete.add(conversation_id)
        self._pending_gaps.add(conversation_id)


@dataclass(frozen=True)
class ObservationScope:
    recorder: TrajectoryRecorder
    conversation_id: str
    run_id: str
    request_index: int
    plan_origins: dict[tuple[int, str], str] = field(default_factory=dict)
    native_tool_origins: dict[str, str] = field(default_factory=dict)

    def event(
        self,
        kind: str,
        data: dict[str, Any],
        *,
        call_id: str | None = None,
        parent_call_id: str | None = None,
    ) -> TrajectoryEvent:
        return TrajectoryEvent(
            conversation_id=self.conversation_id,
            run_id=self.run_id,
            request_index=self.request_index,
            kind=kind,
            data=data,
            call_id=call_id,
            parent_call_id=parent_call_id,
        )

    async def emit(
        self,
        kind: str,
        data: dict[str, Any],
        *,
        call_id: str | None = None,
        parent_call_id: str | None = None,
    ) -> None:
        try:
            await self.recorder.append([
                self.event(kind, data, call_id=call_id, parent_call_id=parent_call_id)
            ])
        except Exception:
            self.recorder.mark_incomplete(self.conversation_id)
            logger.exception("轨迹采集失败；执行继续。")

    async def emit_lazy(self, kind: str, build: Callable[[], dict[str, Any]], *,
                        call_id: str | None = None, parent_call_id: str | None = None) -> None:
        try:
            await self.emit(kind, build(), call_id=call_id, parent_call_id=parent_call_id)
        except Exception:
            self.recorder.mark_incomplete(self.conversation_id)
            logger.exception("轨迹载荷加工失败；执行继续。")

    def collect(self, pending: list[TrajectoryEvent], kind: str,
                build: Callable[[], dict[str, Any]], *, call_id: str | None = None) -> None:
        """批量收集也在观测边界内加工，异常不得改变上游流。"""
        try:
            pending.append(self.event(kind, build(), call_id=call_id))
        except Exception:
            self.recorder.mark_incomplete(self.conversation_id)
            logger.exception("轨迹流片段采集失败；执行继续。")


active_observation: ContextVar[ObservationScope | None] = ContextVar(
    "taichu_trajectory_observation", default=None
)


@dataclass
class ToolObservation:
    scope: ObservationScope
    call_id: str
    parent_call_id: str | None
    execution: str = "not_started"


active_tool_observation: ContextVar[ToolObservation | None] = ContextVar("taichu_tool_observation", default=None)
