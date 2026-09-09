"""评测可观测平台的只读应用层投影。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import Field

from taichu.application.evaluations.general_agent_benchmark.models import (
    BenchmarkModel,
)
from taichu.application.evaluations.general_agent_benchmark.portfolio import (
    BenchmarkEntryId,
)


class BenchmarkObservabilityStatus(StrEnum):
    """可观测结果当前是否可安全展示。"""

    AVAILABLE = "available"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"


class BenchmarkObservabilityScore(BenchmarkModel):
    name: str = Field(min_length=1, max_length=100)
    value: float = Field(ge=0, le=1)


class BenchmarkObservabilityEntry(BenchmarkModel):
    """一个评测入口对应的 Dataset 与最新合格 Experiment。"""

    entry_id: BenchmarkEntryId
    dataset_name: str = Field(min_length=1, max_length=200)
    dataset_id: str = Field(min_length=1)
    dataset_url: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    dataset_item_count: int = Field(ge=0)
    experiment_id: str = Field(min_length=1)
    experiment_name: str = Field(min_length=1)
    experiment_url: str = Field(min_length=1)
    traces_url: str = Field(min_length=1)
    experiment_status: str = Field(min_length=1)
    created_at: datetime
    case_count: int = Field(gt=0)
    passed_count: int = Field(ge=0)
    trace_count: int = Field(ge=0)
    duration_p50_ms: float | None = Field(default=None, ge=0)
    duration_p90_ms: float | None = Field(default=None, ge=0)
    total_estimated_cost: float | None = Field(default=None, ge=0)
    scores: tuple[BenchmarkObservabilityScore, ...] = Field(min_length=1)


class BenchmarkObservabilitySnapshot(BenchmarkModel):
    """不含供应商凭据的前端展示快照。"""

    provider: Literal["opik"] = "opik"
    status: BenchmarkObservabilityStatus
    project_name: str = Field(min_length=1)
    project_url: str | None = None
    suite_content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    refreshed_at: datetime
    message: str = Field(min_length=1, max_length=500)
    entries: tuple[BenchmarkObservabilityEntry, ...] = ()


class BenchmarkObservabilityQuery(Protocol):
    async def get_snapshot(self) -> BenchmarkObservabilitySnapshot: ...


class UnavailableBenchmarkObservabilityQuery:
    """在未启用或未注入供应商时返回稳定、可解释的空投影。"""

    def __init__(
        self,
        *,
        project_name: str,
        suite_content_hash: str,
        enabled: bool = False,
        message: str | None = None,
    ) -> None:
        self._project_name = project_name
        self._suite_content_hash = suite_content_hash
        self._enabled = enabled
        self._message = message

    async def get_snapshot(self) -> BenchmarkObservabilitySnapshot:
        status = (
            BenchmarkObservabilityStatus.UNAVAILABLE
            if self._enabled
            else BenchmarkObservabilityStatus.DISABLED
        )
        return BenchmarkObservabilitySnapshot(
            status=status,
            project_name=self._project_name,
            suite_content_hash=self._suite_content_hash,
            refreshed_at=datetime.now(UTC),
            message=self._message
            or (
                "Opik 已启用，但当前无法读取云端评测结果。"
                if self._enabled
                else "Opik 云端评测尚未启用。"
            ),
        )


__all__ = [
    "BenchmarkObservabilityEntry",
    "BenchmarkObservabilityQuery",
    "BenchmarkObservabilityScore",
    "BenchmarkObservabilitySnapshot",
    "BenchmarkObservabilityStatus",
    "UnavailableBenchmarkObservabilityQuery",
]
