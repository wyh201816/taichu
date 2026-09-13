"""五级上下文流水线的检查点状态；不属于小说事实。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PipelineModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WorkingMemoryItem(PipelineModel):
    """模型提出的运行信息必须关联真实输入来源。"""

    content: str = Field(min_length=1)
    source_ids: list[str] = Field(min_length=1)


class SessionWorkingMemory(PipelineModel):
    task_goal: dict[str, WorkingMemoryItem] = Field(default_factory=dict)
    file_list: dict[str, WorkingMemoryItem] = Field(default_factory=dict)
    workflow_state: dict[str, WorkingMemoryItem] = Field(default_factory=dict)
    error_knowledge: dict[str, WorkingMemoryItem] = Field(default_factory=dict)
    constraints: dict[str, WorkingMemoryItem] = Field(default_factory=dict)


class WorkingMemoryPatch(PipelineModel):
    """缺省字段不变，稳定条目标识对应 null 表示撤销，空对象表示无变化。"""

    task_goal: dict[str, WorkingMemoryItem | None] = Field(default_factory=dict)
    file_list: dict[str, WorkingMemoryItem | None] = Field(default_factory=dict)
    workflow_state: dict[str, WorkingMemoryItem | None] = Field(default_factory=dict)
    error_knowledge: dict[str, WorkingMemoryItem | None] = Field(default_factory=dict)
    constraints: dict[str, WorkingMemoryItem | None] = Field(default_factory=dict)


class FullContextSummary(PipelineModel):
    history_summary: str = Field(min_length=1)
    working_memory: SessionWorkingMemory


class HistoryFold(PipelineModel):
    fold_id: str
    message_ids: list[str]
    content: str


class ResultProjection(PipelineModel):
    result_ref: str
    source_id: str
    run_id: str
    request_index: int
    node_id: str
    call_id: str | None = None
    capability_name: str
    kind: Literal["tool", "subagent"]
    status: str
    objective: str
    content_sha256: str
    preview: Any
    original_tokens: int
    preview_tokens: int
    truncated: bool
    source_refs: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    cleared: bool = False
    full_compacted: bool = False


class ContextPipelineEvent(PipelineModel):
    event_id: str
    stage: Literal["truncate", "clear", "extract", "fold", "full"]
    reason: str
    status: Literal["completed", "failed", "skipped"]
    created_at: str
    before_tokens: int = 0
    after_tokens: int = 0
    source_ids: list[str] = Field(default_factory=list)
    model_id: str | None = None
    detail: str = ""
    patch: dict[str, Any] | None = None


class ContextPipelineState(PipelineModel):
    working_memory: SessionWorkingMemory = Field(default_factory=SessionWorkingMemory)
    results: dict[str, ResultProjection] = Field(default_factory=dict)
    folds: list[HistoryFold] = Field(default_factory=list)
    processed_sources: dict[str, str] = Field(default_factory=dict)
    pending_message_ids: list[str] = Field(default_factory=list)
    invalid_source_ids: list[str] = Field(default_factory=list)
    last_activity_at: str | None = None
    cleaned_activity_at: str | None = None
    last_fold_request_index: int = 0
    events: list[ContextPipelineEvent] = Field(default_factory=list)
    completed_manual_requests: list[str] = Field(default_factory=list)
    migrated_legacy_memory: bool = False


class ManualCompactionRequest(PipelineModel):
    request_id: str
    conversation_id: str
    status: Literal["queued", "running", "completed", "failed"] = "queued"
    created_at: str
    message: str = "已排队，将在下一处安全执行边界压缩上下文。"


class ContextPipelinePolicy(PipelineModel):
    result_preview_tokens: int = Field(default=4_000, ge=256)
    idle_seconds: int = Field(default=3_600, ge=1)
    recent_result_rounds: int = Field(default=2, ge=0)
    recent_history_messages: int = Field(default=5, ge=0)
    fold_after_rounds: int = Field(default=20, ge=1)
    fold_fraction: float = 0.8
    full_fraction: float = 0.9
    target_fraction: float = 0.7
    extractor_model_id: str = "deepseek-v4-flash"
