"""通用写作助手 Runtime 独立的业务运行模型。"""

from __future__ import annotations

from enum import StrEnum
from hashlib import sha256
import json
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


from taichu.application.general_agent.pipeline_models import (
    ContextPipelineState,
    SessionWorkingMemory,
    ContextPipelineEvent,
)


class GeneralAgentModel(BaseModel):
    """拒绝额外字段的不可变 Runtime 模型。"""

    model_config = ConfigDict(frozen=True, extra="forbid")


class GeneralAgentRunStatus(StrEnum):
    """通用 Agent 长流程生命周期。"""

    INIT = "init"
    CLARIFYING = "clarifying"
    PLANNING = "planning"
    EXECUTING = "executing"
    WAITING_HUMAN = "waiting_human"
    VERIFYING = "verifying"
    REPLANNING = "replanning"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


class GeneralAgentNodeKind(StrEnum):
    TOOL = "tool"
    SUBAGENT = "subagent"


class GeneralAgentNodeStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    WAITING_HUMAN = "waiting_human"


class GeneralAgentMessageType(StrEnum):
    """持久标识一条用户可见消息在所属请求轮次中的职责。"""

    USER_REQUEST = "user_request"
    HUMAN_PROMPT = "human_prompt"
    HUMAN_RESPONSE = "human_response"
    ASSISTANT_FINAL = "assistant_final"


class RecoveryAction(StrEnum):
    """Runtime 基于持久证据采取的恢复动作。"""

    REUSE = "reuse"
    RETRY = "retry"
    RECONCILE = "reconcile"
    RESUME = "resume"
    REQUIRES_HUMAN = "requires_human"
    STOP = "stop"


class RecoveryDecision(GeneralAgentModel):
    """一次启动恢复根据 Checkpoint、Result 与 Effect 作出的审计决定。"""

    decision_id: str = Field(pattern=r"^recovery_decision_[a-f0-9]{32}$")
    run_id: str = Field(min_length=1, max_length=128)
    ordinal: int = Field(ge=1)
    action: RecoveryAction
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,127}$")
    reason: str = Field(min_length=1, max_length=2_000)
    checkpoint_revision: int | None = Field(default=None, ge=1)
    effect_id: str | None = Field(default=None, max_length=80)
    evidence: dict[str, Any] = Field(default_factory=dict)
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_evidence_hash(self) -> Self:
        if self.evidence_sha256 != recovery_evidence_sha256(self.evidence):
            raise ValueError("恢复决定证据哈希不匹配。")
        return self


class GeneralAgentMessage(GeneralAgentModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=100_000)
    created_at: str = Field(min_length=1)
    message_id: str | None = Field(
        default=None,
        pattern=r"^message_[a-f0-9]{32}$",
    )
    turn_id: str | None = Field(default=None, min_length=1, max_length=128)
    request_index: int | None = Field(default=None, ge=1)
    message_type: GeneralAgentMessageType | None = None
    human_request_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        identity = (
            self.message_id,
            self.turn_id,
            self.request_index,
            self.message_type,
        )
        if all(value is None for value in identity):
            # 旧 JSON 与独立历史消息对象由 GeneralAgentRun 的只读迁移器补齐。
            if self.human_request_id is not None:
                raise ValueError("旧消息不能只携带人工请求标识。")
            return self
        if any(value is None for value in identity):
            raise ValueError("消息身份必须同时包含消息、轮次、请求序号与职责。")
        if self.message_type in {
            GeneralAgentMessageType.USER_REQUEST,
            GeneralAgentMessageType.HUMAN_RESPONSE,
        }:
            if self.role != "user":
                raise ValueError("用户请求与人工回答必须使用 user 角色。")
        elif self.role != "assistant":
            raise ValueError("人工提示与最终回答必须使用 assistant 角色。")
        if self.message_type in {
            GeneralAgentMessageType.HUMAN_PROMPT,
            GeneralAgentMessageType.HUMAN_RESPONSE,
        }:
            if self.human_request_id is None:
                raise ValueError("人工提示与回答必须关联同一个人工请求标识。")
        elif self.human_request_id is not None:
            raise ValueError("非人工消息不能携带人工请求标识。")
        return self


class GeneralAgentScope(GeneralAgentModel):
    """由页面显式传入的当前写作范围，不通过 Tool 反查页面状态。"""

    scope_type: Literal["none", "selection", "chapter", "range", "novel"] = "none"
    current_chapter_id: str | None = Field(default=None, max_length=128)
    chapter_ids: list[str] = Field(default_factory=list, max_length=100)
    selection_text: str = Field(default="", max_length=100_000)
    direct_context: str = Field(default="", max_length=100_000)


class GeneralAgentRunLimits(GeneralAgentModel):
    max_plan_nodes: int = Field(default=24, ge=1, le=40)
    max_replans: int = Field(default=1, ge=0, le=3)
    max_concurrency: int = Field(default=3, ge=1, le=8)
    max_total_tool_calls: int = Field(default=80, ge=1, le=100)
    max_runtime_seconds: int = Field(default=3_600, ge=30, le=7_200)


class GeneralAgentInputBinding(GeneralAgentModel):
    """把一个上游节点输出字段绑定到当前节点输入字段。"""

    source_node_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    source_path: str = Field(min_length=1, max_length=256)
    target_path: str = Field(min_length=1, max_length=256)

    @field_validator("source_path", "target_path", mode="before")
    @classmethod
    def normalize_path(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        normalized = re.sub(r"\[(\d+)\]", r".\1", value.removeprefix("output."))
        normalized = normalized.strip(".")
        if not normalized or not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*(?:\.(?:[A-Za-z_][A-Za-z0-9_]*|\d+))*",
            normalized,
        ):
            raise ValueError(
                "输入绑定路径必须使用字段名和点号数组下标，例如 chunks.0.content。"
            )
        return normalized


class GeneralAgentPlanNode(GeneralAgentModel):
    """高层编排 Agent 针对单次请求生成的一个能力调用节点。"""

    node_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    kind: GeneralAgentNodeKind
    capability_name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    objective: str = Field(min_length=1, max_length=10_000)
    input_data: dict[str, Any] = Field(default_factory=dict)
    dependencies: list[str] = Field(default_factory=list, max_length=20)
    input_bindings: list[GeneralAgentInputBinding] = Field(
        default_factory=list,
        max_length=30,
    )
    reuse_from_node_id: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_]{0,63}$",
    )
    continue_on_failure: bool = False


class GeneralAgentExecutionPlan(GeneralAgentModel):
    """一次计划修订对应的动态 DAG 定义。"""

    rationale: str = Field(min_length=1, max_length=20_000)
    requires_clarification: bool = False
    clarification_question: str = Field(default="", max_length=20_000)
    direct_response: str = Field(default="", max_length=100_000)
    nodes: list[GeneralAgentPlanNode] = Field(default_factory=list, max_length=40)
    final_response_guidance: str = Field(default="", max_length=20_000)

    @model_validator(mode="after")
    def validate_plan(self) -> Self:
        if self.requires_clarification:
            if not self.clarification_question.strip():
                raise ValueError("需要澄清的计划必须提供澄清问题。")
            if self.nodes:
                raise ValueError("等待澄清时不得提前安排执行节点。")
            return self
        if not self.nodes and not self.direct_response.strip():
            raise ValueError("计划必须包含能力节点或可直接回答的内容。")
        node_ids = [node.node_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("计划节点 ID 必须唯一。")
        known = set(node_ids)
        for node in self.nodes:
            if node.node_id in node.dependencies:
                raise ValueError(f"节点“{node.node_id}”不能依赖自身。")
            unknown = set(node.dependencies) - known
            if unknown:
                raise ValueError(
                    f"节点“{node.node_id}”依赖未知节点：{', '.join(sorted(unknown))}"
                )
            for binding in node.input_bindings:
                if binding.source_node_id not in node.dependencies:
                    raise ValueError(
                        f"节点“{node.node_id}”的输入绑定必须来自直接依赖节点。"
                    )
        _ensure_acyclic(self.nodes)
        return self


class GeneralAgentNodeRun(GeneralAgentModel):
    node_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    plan_revision: int = Field(ge=0)
    kind: GeneralAgentNodeKind
    capability_name: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    dependencies: list[str] = Field(default_factory=list)
    attempt_id: str | None = Field(
        default=None,
        pattern=r"^attempt_[a-f0-9]{32}$",
    )
    status: GeneralAgentNodeStatus = GeneralAgentNodeStatus.PENDING
    resolved_input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    trace_id: str | None = None
    authorization_grant_id: str | None = None
    authorization_approved: bool = False
    authorization_second_confirmation: bool = False
    authorization_resource_scopes: list[str] = Field(default_factory=list)
    effect_id: str | None = Field(
        default=None,
        pattern=r"^effect_[a-f0-9]{32}$",
    )
    effect_status: str | None = Field(default=None, max_length=64)
    reconciliation_reason: str = Field(default="", max_length=2_000)
    duplicate_execution_protected: bool = False
    reused_from_producer_ref: str | None = Field(default=None, max_length=256)
    producer_validity_proof_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    reused_source_plan_revision: int | None = Field(default=None, ge=0)
    reused_source_fingerprint: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    reused_dependency_fingerprint: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: int = Field(default=0, ge=0)
    error_type: str | None = None
    error_message: str | None = None


class GeneralAgentHumanRequest(GeneralAgentModel):
    request_id: str = Field(min_length=1, max_length=128)
    kind: Literal[
        "clarification",
        "write_authorization",
        "effect_reconciliation",
    ]
    prompt: str = Field(min_length=1, max_length=20_000)
    node_id: str | None = None
    tool_name: str | None = None
    effect_id: str | None = Field(default=None, max_length=80)
    input_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    input_summary: dict[str, Any] = Field(default_factory=dict)
    resource_scopes: list[str] = Field(default_factory=list)
    second_confirmation_required: bool = False
    created_at: str = Field(min_length=1)


class GeneralAgentLifecycleEvent(GeneralAgentModel):
    status: GeneralAgentRunStatus
    reason: str = ""
    created_at: str = Field(min_length=1)


class GeneralAgentContextCategoryStat(GeneralAgentModel):
    category: str = Field(min_length=1, max_length=64)
    selected_count: int = Field(default=0, ge=0)
    selected_char_count: int = Field(default=0, ge=0)
    omitted_count: int = Field(default=0, ge=0)
    compressed: bool = False
    reason: str = Field(default="", max_length=500)


class GeneralAgentContextMemory(GeneralAgentModel):
    memory_id: str = Field(min_length=1, max_length=128)
    kind: str = Field(min_length=1, max_length=64)
    content: str = Field(min_length=1, max_length=20_000)
    source_refs: list[str] = Field(default_factory=list, max_length=100)
    artifact_refs: list[str] = Field(default_factory=list, max_length=100)
    content_sha256: str = Field(min_length=64, max_length=64)
    basis_sha256: str = Field(min_length=64, max_length=64)
    state_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    validity: Literal["active", "stale", "rejected", "superseded"] = "active"
    previous_validity: Literal["active", "stale", "rejected", "superseded"] | None = (
        None
    )
    invalidation_reason: str = Field(default="", max_length=2_000)
    invalidated_by_memory_id: str | None = Field(default=None, max_length=128)
    supersedes_memory_id: str | None = Field(default=None, max_length=128)
    result_type: str | None = Field(default=None, max_length=128)
    producer_ref: str | None = Field(default=None, max_length=256)
    projection_role: Literal["basis", "review_target", "repair_source"] | None = None
    repair_only: bool = False

    @model_validator(mode="before")
    @classmethod
    def migrate_validity_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        payload.setdefault("basis_sha256", payload.get("content_sha256"))
        payload.setdefault("state_sha256", None)
        payload.setdefault("validity", "active")
        payload.setdefault("previous_validity", None)
        payload.setdefault("invalidation_reason", "")
        payload.setdefault("invalidated_by_memory_id", None)
        payload.setdefault("supersedes_memory_id", None)
        payload.setdefault("result_type", None)
        payload.setdefault("producer_ref", None)
        payload.setdefault("projection_role", None)
        payload.setdefault("repair_only", False)
        return payload


class GeneralAgentCurrentRequest(GeneralAgentModel):
    """完整保留且最后才允许触及的当前请求层。"""

    content: str = Field(min_length=1, max_length=100_000)
    human_responses: list[str] = Field(default_factory=list, max_length=20)
    user_constraints: list[str] = Field(default_factory=list, max_length=100)
    scope: dict[str, Any] = Field(default_factory=dict)


class GeneralAgentWorkingMemory(GeneralAgentModel):
    """当前工作面：召回资料、运行状态、工具与子 Agent 结果。"""

    memories: list[GeneralAgentContextMemory] = Field(default_factory=list)
    invalidated_memories: list[GeneralAgentContextMemory] = Field(default_factory=list)
    plan_summary: dict[str, Any] | None = None
    node_summaries: list[dict[str, Any]] = Field(default_factory=list)
    unresolved_issues: list[str] = Field(default_factory=list, max_length=100)
    replan_guidance: str = Field(default="", max_length=20_000)
    session_memory: SessionWorkingMemory = Field(default_factory=SessionWorkingMemory)
    excluded_result_sources: list[str] = Field(default_factory=list)


class GeneralAgentHistoryMemory(GeneralAgentModel):
    """完整对话历史的受预算投影，不包含任何内部运行记录。"""

    summary: str = ""
    messages: list[GeneralAgentMessage] = Field(default_factory=list)
    total_message_count: int = Field(default=0, ge=0)
    omitted_message_count: int = Field(default=0, ge=0)


class GeneralAgentContextEnvelope(GeneralAgentModel):
    """按稳定、长期、历史、工作和当前请求顺序组装的五层上下文。"""

    phase: Literal["plan", "replan", "verify"]
    stable_memory: list[str] = Field(default_factory=list, max_length=100)
    long_term_memory: list[GeneralAgentContextMemory] = Field(default_factory=list)
    history_memory: GeneralAgentHistoryMemory = Field(
        default_factory=GeneralAgentHistoryMemory
    )
    working_memory: GeneralAgentWorkingMemory = Field(
        default_factory=GeneralAgentWorkingMemory
    )
    current_request: GeneralAgentCurrentRequest
    category_stats: list[GeneralAgentContextCategoryStat] = Field(default_factory=list)
    total_char_count: int = Field(default=0, ge=0)
    estimated_token_count: int = Field(default=0, ge=0)
    compressed: bool = False
    fallback_used: bool = False
    context_window_tokens: int = 0
    token_count_method: str = "未计量"
    pipeline_events: list[ContextPipelineEvent] = Field(default_factory=list)

    @property
    def current_goal(self) -> str:
        return self.current_request.content

    @property
    def author_constraints(self) -> list[str]:
        return self.current_request.user_constraints

    @property
    def scope(self) -> dict[str, Any]:
        return self.current_request.scope

    @property
    def recent_messages(self) -> list[GeneralAgentMessage]:
        return self.history_memory.messages

    @property
    def runtime_memories(self) -> list[GeneralAgentContextMemory]:
        return self.working_memory.memories

    @property
    def plan_summary(self) -> dict[str, Any] | None:
        return self.working_memory.plan_summary

    @property
    def node_summaries(self) -> list[dict[str, Any]]:
        return self.working_memory.node_summaries

    @property
    def unresolved_issues(self) -> list[str]:
        return self.working_memory.unresolved_issues


class GeneralAgentContextMemoryRef(GeneralAgentModel):
    memory_id: str = Field(min_length=1, max_length=128)
    content_sha256: str = Field(min_length=64, max_length=64)
    state_sha256: str = Field(min_length=64, max_length=64)

    @model_validator(mode="before")
    @classmethod
    def migrate_state_hash(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        payload.setdefault("state_sha256", payload.get("content_sha256"))
        return payload


class GeneralAgentContextLayerTrace(GeneralAgentModel):
    """单一记忆层在上下文组装前后的确定性计量。"""

    layer: Literal[
        "stable_memory",
        "working_memory",
        "long_term_memory",
        "history_memory",
        "current_request",
    ]
    pre_count: int = Field(ge=0)
    pre_char_count: int = Field(ge=0)
    pre_token_estimate: int = Field(ge=0)
    post_count: int = Field(ge=0)
    post_char_count: int = Field(ge=0)
    post_token_estimate: int = Field(ge=0)
    omitted_count: int = Field(ge=0)
    omitted_item_refs: tuple[str, ...] = ()
    omitted_source_refs: tuple[str, ...] = ()
    protected_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_pre_post_counts(self) -> Self:
        if self.post_count > self.pre_count:
            raise ValueError("上下文层组装后的条目数不得大于组装前。")
        if self.omitted_count < self.pre_count - self.post_count:
            raise ValueError("上下文层遗漏数不能小于前后条目差。")
        return self


class GeneralAgentContextProjectionTrace(GeneralAgentModel):
    """大节点结果进入模型上下文时的契约化投影证据。"""

    node_id: str = Field(min_length=1, max_length=128)
    original_content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    projected_content_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    original_item_count: int = Field(ge=0)
    projected_item_count: int = Field(ge=0)
    omitted_item_count: int = Field(ge=0)
    required_output_paths: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_projection_counts(self) -> Self:
        if self.projected_item_count > self.original_item_count:
            raise ValueError("节点结果投影条目数不得超过原始结果。")
        if (
            self.omitted_item_count
            != self.original_item_count - self.projected_item_count
        ):
            raise ValueError("节点结果遗漏数必须由原始与投影条目数唯一派生。")
        return self


class GeneralAgentAssemblyTrace(GeneralAgentModel):
    """新上下文快照的五层输入、收缩、保护与投影证据。"""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        populate_by_name=True,
    )

    schema_: Literal["taichu.general_agent.context_assembly_trace@1"] = Field(
        alias="schema",
        default="taichu.general_agent.context_assembly_trace@1",
    )
    layers: tuple[GeneralAgentContextLayerTrace, ...] = Field(
        min_length=5,
        max_length=5,
    )
    omitted_item_refs: tuple[str, ...] = ()
    omitted_source_refs: tuple[str, ...] = ()
    protected_refs: tuple[str, ...] = Field(min_length=2)
    digest_used: bool
    fallback_used: bool
    digest_source_ids: tuple[str, ...] = ()
    current_request_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    stable_memory_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    projections: tuple[GeneralAgentContextProjectionTrace, ...] = ()
    trace_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_trace_identity(self) -> Self:
        expected_layers = (
            "stable_memory",
            "working_memory",
            "long_term_memory",
            "history_memory",
            "current_request",
        )
        if tuple(item.layer for item in self.layers) != expected_layers:
            raise ValueError("AssemblyTrace 必须按固定五层顺序记录。")
        if self.trace_sha256 != context_snapshot_sha256(
            self.model_dump(
                mode="json",
                by_alias=True,
                exclude={"trace_sha256"},
            )
        ):
            raise ValueError("AssemblyTrace 内容哈希不匹配。")
        return self

    @classmethod
    def create(
        cls,
        *,
        layers: tuple[GeneralAgentContextLayerTrace, ...],
        omitted_item_refs: tuple[str, ...],
        omitted_source_refs: tuple[str, ...],
        protected_refs: tuple[str, ...],
        digest_used: bool,
        fallback_used: bool,
        digest_source_ids: tuple[str, ...],
        current_request_sha256: str,
        stable_memory_sha256: str,
        projections: tuple[GeneralAgentContextProjectionTrace, ...],
    ) -> GeneralAgentAssemblyTrace:
        payload = {
            "schema": "taichu.general_agent.context_assembly_trace@1",
            "layers": layers,
            "omitted_item_refs": omitted_item_refs,
            "omitted_source_refs": omitted_source_refs,
            "protected_refs": protected_refs,
            "digest_used": digest_used,
            "fallback_used": fallback_used,
            "digest_source_ids": digest_source_ids,
            "current_request_sha256": current_request_sha256,
            "stable_memory_sha256": stable_memory_sha256,
            "projections": projections,
        }
        return cls.model_validate(
            {
                **payload,
                "trace_sha256": context_snapshot_sha256(
                    {
                        key: (
                            [item.model_dump(mode="json") for item in value]
                            if isinstance(value, tuple)
                            and value
                            and isinstance(value[0], GeneralAgentModel)
                            else value
                        )
                        for key, value in payload.items()
                    }
                ),
            }
        )


class GeneralAgentContextSnapshot(GeneralAgentModel):
    snapshot_id: str = Field(pattern=r"^context_\d{8}_\d{6}_[a-z0-9]{8}$")
    phase: Literal["plan", "replan", "verify"]
    conversation_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    created_at: str = Field(min_length=1)
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    memory_refs: list[GeneralAgentContextMemoryRef] = Field(default_factory=list)
    envelope: GeneralAgentContextEnvelope
    assembly_trace: GeneralAgentAssemblyTrace | None = None
    content_sha256: str = Field(min_length=64, max_length=64)

    original_content_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="before")
    @classmethod
    def migrate_snapshot_projection(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        raw_envelope = payload.get("envelope")
        if not isinstance(raw_envelope, dict):
            return value
        original_hash = payload.get("content_sha256")
        original_payload = {
            key: item for key, item in payload.items() if key != "content_sha256"
        }
        if original_hash != context_snapshot_sha256(original_payload):
            return value  # 后续校验拒绝被改写的审计。
        envelope = dict(raw_envelope)
        working = dict(envelope.get("working_memory", {}))
        legacy = "digest" in working or "context_window_tokens" not in envelope
        working.pop("digest", None)
        envelope["working_memory"] = working
        payload["envelope"] = GeneralAgentContextEnvelope.model_validate(
            envelope
        ).model_dump(mode="json")
        payload.setdefault("assembly_trace", None)
        payload.setdefault("memory_refs", [])
        payload.setdefault("policy_snapshot", {})
        payload.setdefault("original_content_sha256", original_hash if legacy else None)
        payload["content_sha256"] = context_snapshot_sha256(
            {key: item for key, item in payload.items() if key != "content_sha256"}
        )
        return payload

    @model_validator(mode="after")
    def validate_snapshot_hash(self) -> Self:
        current_payload = self.model_dump(mode="json", exclude={"content_sha256"})
        if self.content_sha256 == context_snapshot_sha256(current_payload):
            return self
        pre_trace_payload = dict(current_payload)
        if pre_trace_payload.pop("assembly_trace", None) is None and (
            self.content_sha256 == context_snapshot_sha256(pre_trace_payload)
        ):
            return self
        if self.content_sha256 != context_snapshot_sha256(
            _legacy_context_snapshot_payload(pre_trace_payload)
        ):
            raise ValueError("上下文快照校验和不匹配。")
        return self


class GeneralAgentCompressionStats(GeneralAgentModel):
    compressed: bool = False
    fallback_used: bool = False
    input_char_count: int = Field(default=0, ge=0)
    output_char_count: int = Field(default=0, ge=0)
    estimated_token_count: int = Field(default=0, ge=0)
    omitted_message_count: int = Field(default=0, ge=0)
    omitted_node_count: int = Field(default=0, ge=0)
    selected_memory_count: int = Field(default=0, ge=0)


class GeneralAgentRun(GeneralAgentModel):
    """通用 Runtime 的业务审计与界面投影；图恢复由 Checkpointer 负责。"""

    run_id: str = Field(pattern=r"^general_run_\d{8}_\d{6}_[a-z0-9]{6}$")
    task_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(min_length=1, max_length=128)
    request_index: int = Field(ge=1)
    parent_run_id: str | None = Field(default=None, max_length=128)
    agent_name: Literal["general_writing_assistant"] = "general_writing_assistant"
    user_goal: str = Field(min_length=1, max_length=100_000)
    conversation_title: str | None = Field(default=None, min_length=1, max_length=200)
    model_id: str | None = Field(default=None, min_length=1, max_length=128)
    scope: GeneralAgentScope = Field(default_factory=GeneralAgentScope)
    author_constraints: list[str] = Field(default_factory=list, max_length=100)
    external_access_allowed: bool = False
    limits: GeneralAgentRunLimits = Field(default_factory=GeneralAgentRunLimits)
    status: GeneralAgentRunStatus = GeneralAgentRunStatus.INIT
    messages: list[GeneralAgentMessage] = Field(default_factory=list)
    context_pipeline: ContextPipelineState = Field(default_factory=ContextPipelineState)
    current_request_message_id: str | None = Field(
        default=None,
        pattern=r"^message_[a-f0-9]{32}$",
    )
    plan: GeneralAgentExecutionPlan | None = None
    plan_revision: int = 0
    replan_count: int = 0
    node_runs: list[GeneralAgentNodeRun] = Field(default_factory=list)
    pending_human_request: GeneralAgentHumanRequest | None = None
    final_answer: str = ""
    final_answer_basis_sha256: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
    )
    verification_attempt_count: int = Field(default=0, ge=0)
    verification_issues: list[str] = Field(default_factory=list)
    recovery_decisions: list[RecoveryDecision] = Field(
        default_factory=list,
        max_length=100,
    )
    memory_refs: list[str] = Field(default_factory=list, max_length=500)
    context_snapshot_id: str | None = Field(default=None, max_length=128)
    context_snapshot: GeneralAgentContextSnapshot | None = None
    compression_stats: GeneralAgentCompressionStats = Field(
        default_factory=GeneralAgentCompressionStats
    )
    context_resume_differences: list[str] = Field(default_factory=list, max_length=100)
    lifecycle_events: list[GeneralAgentLifecycleEvent] = Field(default_factory=list)
    checkpoint_revision: int = Field(
        default=0,
        ge=0,
        description=("历史字段名，仅表示业务投影保存序号；不得用于 LangGraph 恢复。"),
    )
    resumable: bool = True
    created_at: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)
    started_at: str = Field(min_length=1)
    finished_at: str | None = None
    errors: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_conversation_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        run_id = payload.get("run_id")
        task_id = payload.get("task_id")
        payload.setdefault("conversation_id", task_id or run_id)
        messages = payload.get("messages")
        legacy_request_count = (
            sum(
                1
                for message in messages
                if isinstance(message, dict) and message.get("role") == "user"
            )
            if isinstance(messages, list)
            else 1
        )
        payload.setdefault("request_index", max(1, legacy_request_count))
        payload.setdefault("parent_run_id", None)
        payload.setdefault("model_id", None)
        _migrate_legacy_message_identities(payload)
        payload.setdefault("memory_refs", [])
        payload.setdefault("final_answer_basis_sha256", None)
        payload.setdefault("verification_attempt_count", 0)
        payload.setdefault("recovery_decisions", [])
        payload.setdefault("context_snapshot_id", None)
        payload.setdefault("context_snapshot", None)
        payload.setdefault("compression_stats", {})
        payload.setdefault("context_resume_differences", [])
        return payload

    @model_validator(mode="after")
    def validate_current_request_message(self) -> Self:
        if self.current_request_message_id is None:
            return self
        matches = [
            message
            for message in self.messages
            if message.message_id == self.current_request_message_id
        ]
        if len(matches) != 1:
            raise ValueError("当前请求消息标识必须命中唯一消息。")
        current = matches[0]
        if (
            current.turn_id != self.run_id
            or current.request_index != self.request_index
            or current.message_type is not GeneralAgentMessageType.USER_REQUEST
            or current.content != self.user_goal
        ):
            raise ValueError("当前请求消息与运行轮次或用户原文不一致。")
        return self

    @model_validator(mode="after")
    def validate_context_snapshot_reference(self) -> Self:
        if self.context_snapshot is None:
            if self.context_snapshot_id is not None:
                raise ValueError("上下文快照标识缺少对应快照。")
            return self
        if self.context_snapshot_id != self.context_snapshot.snapshot_id:
            raise ValueError("运行引用的上下文快照标识不一致。")
        if self.context_snapshot.run_id != self.run_id:
            raise ValueError("上下文快照不能跨运行复用。")
        if self.context_snapshot.conversation_id != self.conversation_id:
            raise ValueError("上下文快照不能跨会话复用。")
        return self


class GeneralAgentConversation(GeneralAgentModel):
    """由多次独立运行组成的持久化对话摘要。"""

    conversation_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=100_000)
    status: GeneralAgentRunStatus
    request_count: int = Field(ge=1)
    latest_run_id: str = Field(min_length=1, max_length=128)
    created_at: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)


class GeneralAgentPlanDraft(GeneralAgentExecutionPlan):
    """编排模型输出 Schema；与持久化计划保持同一业务约束。"""


class GeneralAgentVerification(GeneralAgentModel):
    outcome: Literal["satisfied", "partial", "failed"]
    final_answer: str = Field(min_length=1, max_length=200_000)
    issues: list[str] = Field(default_factory=list, max_length=100)
    should_replan: bool = False
    replan_guidance: str = Field(default="", max_length=20_000)


def _ensure_acyclic(
    nodes: list[GeneralAgentPlanNode],
) -> None:
    dependencies = {node.node_id: set(node.dependencies) for node in nodes}
    remaining = set(dependencies)
    while remaining:
        ready = {
            node_id for node_id in remaining if not dependencies[node_id] & remaining
        }
        if not ready:
            raise ValueError("计划节点依赖形成了循环。")
        remaining -= ready


def _migrate_legacy_message_identities(payload: dict[str, Any]) -> None:
    """只在读取旧运行 JSON 时补齐消息身份；运行主链必须显式写入这些字段。"""

    raw_messages = payload.get("messages")
    if not isinstance(raw_messages, list):
        payload.setdefault("current_request_message_id", None)
        return
    messages = [
        item.model_dump(mode="json")
        if isinstance(item, GeneralAgentMessage)
        else dict(item)
        if isinstance(item, dict)
        else item
        for item in raw_messages
    ]
    run_id = str(payload.get("run_id") or "legacy_run")
    conversation_id = str(
        payload.get("conversation_id") or payload.get("task_id") or run_id
    )
    request_index = max(1, int(payload.get("request_index") or 1))
    user_goal = payload.get("user_goal")
    current_request_message_id = payload.get("current_request_message_id")

    explicit_current = next(
        (
            item
            for item in messages
            if isinstance(item, dict)
            and item.get("turn_id") == run_id
            and item.get("request_index") == request_index
            and item.get("message_type") == GeneralAgentMessageType.USER_REQUEST.value
        ),
        None,
    )
    legacy_current_start: int | None = None
    if explicit_current is None and isinstance(user_goal, str):
        # 旧格式没有可恢复的轮次身份，只能在读取时做一次兼容投影。
        # 新写入路径不会经过这条文本推断分支。
        legacy_current_start = next(
            (
                index
                for index in range(len(messages) - 1, -1, -1)
                if isinstance(messages[index], dict)
                and messages[index].get("role") == "user"
                and messages[index].get("content") == user_goal
            ),
            None,
        )

    migrated: list[Any] = []
    active_human_request_id: str | None = None
    inferred_current_request_id: str | None = None
    for index, item in enumerate(messages):
        if not isinstance(item, dict):
            migrated.append(item)
            continue
        message = dict(item)
        has_complete_identity = all(
            message.get(field) is not None
            for field in ("message_id", "turn_id", "request_index", "message_type")
        )
        if has_complete_identity:
            if (
                message.get("turn_id") == run_id
                and message.get("request_index") == request_index
                and message.get("message_type")
                == GeneralAgentMessageType.USER_REQUEST.value
            ):
                inferred_current_request_id = str(message["message_id"])
            if (
                message.get("message_type")
                == GeneralAgentMessageType.HUMAN_PROMPT.value
            ):
                active_human_request_id = (
                    str(message.get("human_request_id") or "") or None
                )
            migrated.append(message)
            continue

        message_id = _legacy_message_id(run_id, index, message)
        belongs_to_current = (
            legacy_current_start is not None and index >= legacy_current_start
        )
        message_type: GeneralAgentMessageType
        human_request_id: str | None = None
        if belongs_to_current and index == legacy_current_start:
            message_type = GeneralAgentMessageType.USER_REQUEST
            inferred_current_request_id = message_id
        elif belongs_to_current and message.get("role") == "assistant":
            if (
                isinstance(payload.get("final_answer"), str)
                and payload.get("final_answer")
                and message.get("content") == payload.get("final_answer")
            ):
                message_type = GeneralAgentMessageType.ASSISTANT_FINAL
                active_human_request_id = None
            else:
                message_type = GeneralAgentMessageType.HUMAN_PROMPT
                active_human_request_id = _legacy_human_request_id(run_id, index)
                human_request_id = active_human_request_id
        elif belongs_to_current:
            message_type = GeneralAgentMessageType.HUMAN_RESPONSE
            human_request_id = active_human_request_id or _legacy_human_request_id(
                run_id,
                index,
            )
        else:
            message_type = (
                GeneralAgentMessageType.USER_REQUEST
                if message.get("role") == "user"
                else GeneralAgentMessageType.ASSISTANT_FINAL
            )
        message.update(
            {
                "message_id": message_id,
                "turn_id": (
                    run_id
                    if belongs_to_current
                    else f"legacy_turn_{sha256(conversation_id.encode('utf-8')).hexdigest()[:24]}"
                ),
                "request_index": (
                    request_index if belongs_to_current else max(1, request_index - 1)
                ),
                "message_type": message_type.value,
                "human_request_id": human_request_id,
            }
        )
        migrated.append(message)

    payload["messages"] = migrated
    payload["current_request_message_id"] = (
        current_request_message_id or inferred_current_request_id
    )


def _legacy_message_id(run_id: str, index: int, message: dict[str, Any]) -> str:
    encoded = json.dumps(
        {
            "run_id": run_id,
            "index": index,
            "role": message.get("role"),
            "content": message.get("content"),
            "created_at": message.get("created_at"),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"message_{sha256(encoded.encode('utf-8')).hexdigest()[:32]}"


def _legacy_human_request_id(run_id: str, index: int) -> str:
    encoded = f"{run_id}:{index}:human_request".encode()
    return f"human_{sha256(encoded).hexdigest()[:32]}"


def _legacy_context_snapshot_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """重建新增细粒度失效字段前的快照形状，仅用于读取旧检查点。"""

    legacy = json.loads(json.dumps(payload, ensure_ascii=False))
    for reference in legacy.get("memory_refs", []):
        if isinstance(reference, dict):
            reference.pop("state_sha256", None)
    envelope = legacy.get("envelope")
    if not isinstance(envelope, dict):
        return legacy
    history_memory = envelope.get("history_memory")
    if isinstance(history_memory, dict):
        history_messages = history_memory.get("messages", [])
        if isinstance(history_messages, list):
            for message in history_messages:
                _remove_message_identity_fields(message)
    working_memory = envelope.get("working_memory")
    if isinstance(working_memory, dict):
        working_memory.pop("invalidated_memories", None)
        memories = working_memory.get("memories", [])
        if isinstance(memories, list):
            for memory in memories:
                _remove_context_memory_validity_fields(memory)
    long_term_memory = envelope.get("long_term_memory", [])
    if isinstance(long_term_memory, list):
        for memory in long_term_memory:
            _remove_context_memory_validity_fields(memory)
    return legacy


def _remove_message_identity_fields(value: Any) -> None:
    if not isinstance(value, dict):
        return
    for field in (
        "message_id",
        "turn_id",
        "request_index",
        "message_type",
        "human_request_id",
    ):
        value.pop(field, None)


def _remove_context_memory_validity_fields(value: Any) -> None:
    if not isinstance(value, dict):
        return
    for field in (
        "basis_sha256",
        "state_sha256",
        "validity",
        "previous_validity",
        "invalidation_reason",
        "invalidated_by_memory_id",
        "supersedes_memory_id",
        "result_type",
        "producer_ref",
        "projection_role",
        "repair_only",
    ):
        value.pop(field, None)


def context_snapshot_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def recovery_evidence_sha256(payload: dict[str, Any]) -> str:
    """稳定标识恢复决定所依据的最小审计证据。"""

    return context_snapshot_sha256(payload)


def result_basis_sha256(run: GeneralAgentRun) -> str:
    """标识当前计划修订及其真实节点证据，防止旧结论跨修订复用。"""
    current_nodes = [
        node.model_dump(mode="json")
        for node in run.node_runs
        if node.plan_revision == run.plan_revision
    ]
    payload = {
        "run_id": run.run_id,
        "request_index": run.request_index,
        "plan_revision": run.plan_revision,
        "plan": run.plan.model_dump(mode="json") if run.plan is not None else None,
        "node_runs": current_nodes,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(encoded.encode("utf-8")).hexdigest()
