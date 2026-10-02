"""轨迹观测记录；不参与图状态、记忆或恢复。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def observation_time() -> str:
    return datetime.now(UTC).isoformat()


class TrajectoryEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # 仅用于落盘协议兼容，不表示开发阶段。
    schema_version: int = 1
    event_id: str = Field(default_factory=lambda: f"event_{uuid4().hex}")
    sequence: int = 0
    conversation_id: str
    run_id: str
    request_index: int
    kind: str
    recorded_at: str = Field(default_factory=observation_time)
    call_id: str | None = None
    parent_call_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class TrajectoryRead(BaseModel):
    events: list[TrajectoryEvent] = Field(default_factory=list)
    complete: bool = True
    warnings: list[str] = Field(default_factory=list)
