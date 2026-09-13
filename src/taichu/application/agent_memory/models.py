"""通用写作助手自动运行记忆的应用层模型。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
import json
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgentMemoryModel(BaseModel):
    """拒绝额外字段的不可变运行记忆模型。"""

    model_config = ConfigDict(frozen=True, extra="forbid")


class AgentMemoryKind(StrEnum):
    """当前任务工作记忆的内容类型，不承担用户偏好长期记忆职责。"""

    USER_INSTRUCTION = "user_instruction"
    TASK_SUMMARY = "task_summary"
    RESOURCE_SUMMARY = "resource_summary"
    WORK_NOTE = "work_note"
    UNRESOLVED_ISSUE = "unresolved_issue"
    FACT_REFERENCE = "fact_reference"


class AgentMemorySensitivity(StrEnum):
    NORMAL = "normal"
    PRIVATE = "private"
    RESTRICTED = "restricted"


class AgentMemoryValidity(StrEnum):
    """运行记忆是否仍可作为当前任务依据。"""

    ACTIVE = "active"
    STALE = "stale"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class AgentMemoryDependencyRelation(StrEnum):
    """下游记录使用上游记录的方式，决定失效是否传播。"""

    BASIS = "basis"
    REVIEW_TARGET = "review_target"
    REPAIR_SOURCE = "repair_source"


class AgentMemoryEvidenceAnchor(AgentMemoryModel):
    """一处外部事实源在记忆生成时的内容指纹。"""

    reference: str = Field(min_length=1, max_length=512)
    content_sha256: str = Field(min_length=64, max_length=64)


class AgentMemoryDependency(AgentMemoryModel):
    """一条可传播或仅供修复参考的工作记忆依赖。"""

    memory_id: str = Field(min_length=1, max_length=128)
    relation: AgentMemoryDependencyRelation = AgentMemoryDependencyRelation.BASIS


class ProducerMemoryValidityProof(AgentMemoryModel):
    """对一个精确节点 producer 当前状态的可复核证明。"""

    conversation_id: str = Field(min_length=1, max_length=128)
    producer_ref: str = Field(
        pattern=r"^node:[^:]+:\d+:[^:]+$",
        max_length=256,
    )
    source_node_id: str = Field(min_length=1, max_length=128)
    memory_id: str = Field(pattern=r"^memory_\d{8}_\d{6}_[a-z0-9]{8}$")
    validity: AgentMemoryValidity
    state_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    dependency_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    supersedes_memory_id: str | None = Field(default=None, max_length=128)
    observed_at: str = Field(min_length=1, max_length=64)


_LEGACY_KIND_MAP = {
    "author_constraint": AgentMemoryKind.USER_INSTRUCTION.value,
    "human_correction": AgentMemoryKind.USER_INSTRUCTION.value,
    "task_conclusion": AgentMemoryKind.TASK_SUMMARY.value,
    "artifact_reference": AgentMemoryKind.RESOURCE_SUMMARY.value,
    "execution_summary": AgentMemoryKind.WORK_NOTE.value,
    "decision": AgentMemoryKind.WORK_NOTE.value,
}


class AgentMemoryEntry(AgentMemoryModel):
    """一条由 Runtime 自动写入、自动过期且可追溯的工作记忆。"""

    memory_id: str = Field(pattern=r"^memory_\d{8}_\d{6}_[a-z0-9]{8}$")
    kind: AgentMemoryKind
    content: str = Field(min_length=1, max_length=20_000)
    source_refs: list[str] = Field(default_factory=list, max_length=100)
    artifact_refs: list[str] = Field(default_factory=list, max_length=100)
    run_ids: list[str] = Field(default_factory=list, max_length=100)
    conversation_id: str = Field(min_length=1, max_length=128)
    created_request_index: int = Field(default=1, ge=1)
    expires_after_request_index: int | None = Field(default=None, ge=1)
    retention_priority: int = Field(default=50, ge=0, le=100)
    created_at: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)
    expires_at: str | None = Field(default=None, max_length=64)
    supersedes_memory_id: str | None = Field(default=None, max_length=128)
    content_sha256: str = Field(min_length=64, max_length=64)
    basis_sha256: str = Field(min_length=64, max_length=64)
    producer_ref: str | None = Field(default=None, max_length=256)
    result_type: str | None = Field(default=None, max_length=128)
    evidence_anchors: list[AgentMemoryEvidenceAnchor] = Field(
        default_factory=list,
        max_length=200,
    )
    dependencies: list[AgentMemoryDependency] = Field(
        default_factory=list,
        max_length=100,
    )
    validity: AgentMemoryValidity = AgentMemoryValidity.ACTIVE
    previous_validity: AgentMemoryValidity | None = None
    invalidated_at: str | None = Field(default=None, max_length=64)
    invalidation_reason: str = Field(default="", max_length=2_000)
    invalidated_by_memory_id: str | None = Field(default=None, max_length=128)
    sensitivity: AgentMemorySensitivity = AgentMemorySensitivity.NORMAL
    deleted_at: str | None = Field(default=None, max_length=64)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_record(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        payload.pop("lifecycle", None)
        payload.pop("scope", None)
        kind = payload.get("kind")
        if isinstance(kind, str):
            payload["kind"] = _LEGACY_KIND_MAP.get(kind, kind)
        payload.setdefault("created_request_index", 1)
        payload.setdefault("expires_after_request_index", None)
        payload.setdefault("producer_ref", None)
        payload.setdefault("result_type", None)
        payload.setdefault("evidence_anchors", [])
        payload.setdefault("dependencies", [])
        payload.setdefault("validity", AgentMemoryValidity.ACTIVE.value)
        payload.setdefault("previous_validity", None)
        payload.setdefault("invalidated_at", None)
        payload.setdefault("invalidation_reason", "")
        payload.setdefault("invalidated_by_memory_id", None)
        payload.setdefault(
            "basis_sha256",
            memory_basis_sha256(
                content_sha256=str(payload.get("content_sha256", "")),
                source_refs=list(payload.get("source_refs") or []),
                artifact_refs=list(payload.get("artifact_refs") or []),
                evidence_anchors=[],
                dependencies=[],
            ),
        )
        return payload

    @model_validator(mode="after")
    def validate_integrity(self) -> Self:
        if self.content_sha256 != memory_content_sha256(self.content):
            raise ValueError("运行记忆内容校验和不匹配。")
        if self.kind is AgentMemoryKind.FACT_REFERENCE and not self.source_refs:
            raise ValueError("事实引用记忆必须包含稳定来源引用。")
        if (
            self.expires_after_request_index is not None
            and self.expires_after_request_index < self.created_request_index
        ):
            raise ValueError("运行记忆的过期请求序号不能早于创建序号。")
        if self.validity is AgentMemoryValidity.ACTIVE:
            if (
                self.previous_validity is not None
                or self.invalidated_at is not None
                or self.invalidation_reason
                or self.invalidated_by_memory_id is not None
            ):
                raise ValueError("有效运行记忆不能携带失效信息。")
        elif self.invalidated_at is None or not self.invalidation_reason.strip():
            raise ValueError("失效运行记忆必须记录失效时间和原因。")
        return self

    def is_retained(self, *, as_of: str, request_index: int) -> bool:
        if self.deleted_at is not None:
            return False
        if self.expires_at is not None and self.expires_at <= as_of:
            return False
        return not (
            self.expires_after_request_index is not None
            and request_index > self.expires_after_request_index
        )

    def is_active(self, *, as_of: str, request_index: int) -> bool:
        return self.validity is AgentMemoryValidity.ACTIVE and self.is_retained(
            as_of=as_of, request_index=request_index
        )


class MemoryWriteCandidate(AgentMemoryModel):
    """由 Runtime 在明确节点提交的自动记忆写入。"""

    kind: AgentMemoryKind
    content: str = Field(min_length=1, max_length=20_000)
    source_refs: list[str] = Field(default_factory=list, max_length=100)
    artifact_refs: list[str] = Field(default_factory=list, max_length=100)
    run_ids: list[str] = Field(default_factory=list, max_length=100)
    conversation_id: str = Field(min_length=1, max_length=128)
    created_request_index: int = Field(ge=1)
    expires_after_request_index: int | None = Field(default=None, ge=1)
    retention_priority: int = Field(default=50, ge=0, le=100)
    expires_at: str | None = Field(default=None, max_length=64)
    supersedes_memory_id: str | None = Field(default=None, max_length=128)
    producer_ref: str | None = Field(default=None, max_length=256)
    result_type: str | None = Field(default=None, max_length=128)
    evidence_anchors: list[AgentMemoryEvidenceAnchor] = Field(
        default_factory=list,
        max_length=200,
    )
    dependencies: list[AgentMemoryDependency] = Field(
        default_factory=list,
        max_length=100,
    )
    validity: AgentMemoryValidity = AgentMemoryValidity.ACTIVE
    invalidation_reason: str = Field(default="", max_length=2_000)
    invalidated_by_memory_id: str | None = Field(default=None, max_length=128)
    sensitivity: AgentMemorySensitivity = AgentMemorySensitivity.NORMAL


class AgentMemoryQuery(AgentMemoryModel):
    conversation_id: str = Field(min_length=1, max_length=128)
    current_request_index: int = Field(ge=1)
    query_text: str = Field(default="", max_length=40_000)
    kinds: list[AgentMemoryKind] = Field(default_factory=list, max_length=20)
    run_id: str | None = Field(default=None, max_length=128)
    as_of: str | None = Field(default=None, max_length=64)


class AgentMemorySelection(AgentMemoryModel):
    entries: list[AgentMemoryEntry] = Field(default_factory=list)
    selected_memory_ids: list[str] = Field(default_factory=list)
    candidate_count: int = Field(default=0, ge=0)
    selected_char_count: int = Field(default=0, ge=0)
    dropped_duplicate_count: int = Field(default=0, ge=0)
    dropped_budget_count: int = Field(default=0, ge=0)
    policy_snapshot: dict[str, int | float | str] = Field(default_factory=dict)


def memory_content_sha256(content: str) -> str:
    return sha256(content.strip().encode("utf-8")).hexdigest()


def memory_basis_sha256(
    *,
    content_sha256: str,
    source_refs: list[str],
    artifact_refs: list[str],
    evidence_anchors: list[AgentMemoryEvidenceAnchor],
    dependencies: list[AgentMemoryDependency],
) -> str:
    payload = {
        "content_sha256": content_sha256,
        "source_refs": sorted(set(source_refs)),
        "artifact_refs": sorted(set(artifact_refs)),
        "evidence_anchors": sorted(
            (
                anchor.reference,
                anchor.content_sha256,
            )
            for anchor in evidence_anchors
        ),
        "dependencies": sorted(
            (
                dependency.memory_id,
                dependency.relation.value,
            )
            for dependency in dependencies
        ),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def memory_state_sha256(entry: AgentMemoryEntry) -> str:
    payload = {
        "content_sha256": entry.content_sha256,
        "basis_sha256": entry.basis_sha256,
        "validity": entry.validity.value,
        "previous_validity": (
            entry.previous_validity.value
            if entry.previous_validity is not None
            else None
        ),
        "invalidated_at": entry.invalidated_at,
        "invalidation_reason": entry.invalidation_reason,
        "invalidated_by_memory_id": entry.invalidated_by_memory_id,
        "supersedes_memory_id": entry.supersedes_memory_id,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def memory_source_fingerprint(entry: AgentMemoryEntry) -> str:
    payload = {
        "producer_ref": entry.producer_ref,
        "content_sha256": entry.content_sha256,
        "source_refs": sorted(set(entry.source_refs)),
        "artifact_refs": sorted(set(entry.artifact_refs)),
        "evidence_anchors": sorted(
            (anchor.reference, anchor.content_sha256)
            for anchor in entry.evidence_anchors
        ),
    }
    return _canonical_sha256(payload)


def memory_dependency_fingerprint(
    *,
    dependencies: list[tuple[str, AgentMemoryDependencyRelation, str]],
    supersession: tuple[str, str] | None,
) -> str:
    payload = {
        "dependencies": sorted(
            (memory_id, relation.value, state_hash)
            for memory_id, relation, state_hash in dependencies
        ),
        "supersession": supersession,
    }
    return _canonical_sha256(payload)


def producer_validity_proof_sha256(
    proof: ProducerMemoryValidityProof,
) -> str:
    return _canonical_sha256(proof.model_dump(mode="json", exclude={"observed_at"}))


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def memory_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
