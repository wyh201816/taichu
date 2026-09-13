"""工作记忆在摘要、快照、复用与并行分支中的抗污染门禁。"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
import json
from pathlib import Path
from typing import Any, Literal, Protocol, Self, TypeAlias

from pydantic import Field, model_validator

from taichu.application.agent_memory.models import (
    AgentMemoryDependencyRelation,
    AgentMemoryEntry,
    AgentMemoryKind,
    AgentMemoryValidity,
)
from taichu.application.evaluations.general_agent_benchmark.canonical import (
    canonical_sha256,
)
from taichu.application.evaluations.general_agent_benchmark.models import (
    BenchmarkModel,
    Sha256,
    StableId,
)
from taichu.application.general_agent.models import (
    GeneralAgentContextSnapshot,
    GeneralAgentNodeRun,
)
from taichu.application.invocations.models import InvocationEnvelope


class ModelToolCallSnapshot(Protocol):
    call_id: str
    name: str
    arguments_json: str


class ModelMessageSnapshot(Protocol):
    role: str
    content: str
    tool_calls: tuple[ModelToolCallSnapshot, ...]
    tool_call_id: str | None
    tool_name: str | None
    is_error: bool


class ModelRequestSnapshot(Protocol):
    """评测只读投影所需的最小供应商请求快照。"""

    messages: tuple[ModelMessageSnapshot, ...]


class MemoryCarrierKind(StrEnum):
    NODE_SUMMARY = "node_summary"
    NORMAL_DIGEST = "normal_digest"
    FALLBACK_DIGEST = "fallback_digest"
    SNAPSHOT_CURRENT = "snapshot_current"
    REUSE = "reuse"
    PARALLEL_BRANCH = "parallel_branch"
    REPAIR_PROJECTION = "repair_projection"


_CURRENT_CARRIERS = frozenset(
    {
        MemoryCarrierKind.NODE_SUMMARY,
        MemoryCarrierKind.NORMAL_DIGEST,
        MemoryCarrierKind.FALLBACK_DIGEST,
        MemoryCarrierKind.SNAPSHOT_CURRENT,
        MemoryCarrierKind.REUSE,
        MemoryCarrierKind.PARALLEL_BRANCH,
    }
)


class MemoryCarrierObservation(BenchmarkModel):
    carrier: MemoryCarrierKind
    memory_id: str = Field(min_length=1, max_length=128)
    producer_ref: str = Field(
        pattern=r"^node:[^:]+:\d+:[^:]+$",
        max_length=256,
    )
    validity: AgentMemoryValidity
    role: AgentMemoryDependencyRelation
    repair_only: bool
    source_fingerprint: Sha256
    dependency_fingerprint: Sha256
    state_hash: Sha256
    proof_valid: bool
    branch_id: StableId
    evidence_ref: StableId


class MemoryCarrierGateResult(BenchmarkModel):
    carrier: MemoryCarrierKind
    passed: bool
    observation_count: int = Field(ge=0)
    violation_count: int = Field(ge=0)
    evidence_refs: tuple[StableId, ...]


class MemoryPollutionGateReport(BenchmarkModel):
    carrier_results: tuple[MemoryCarrierGateResult, ...]
    violations: tuple[str, ...]
    evidence_refs: tuple[StableId, ...]
    complete: bool


def audit_memory_carriers(
    observations: tuple[MemoryCarrierObservation, ...],
) -> MemoryPollutionGateReport:
    violations: list[str] = []
    violations_by_carrier: dict[MemoryCarrierKind, int] = {}
    evidence_by_carrier: dict[MemoryCarrierKind, list[StableId]] = {}
    counts: dict[MemoryCarrierKind, int] = {}
    for observation in observations:
        counts[observation.carrier] = counts.get(observation.carrier, 0) + 1
        evidence_by_carrier.setdefault(observation.carrier, []).append(
            observation.evidence_ref
        )
        problems = _observation_problems(observation)
        if not problems:
            continue
        violations_by_carrier[observation.carrier] = (
            violations_by_carrier.get(observation.carrier, 0) + 1
        )
        violations.append(
            f"{observation.carrier.value}:{observation.memory_id}："
            + "；".join(problems)
        )
    carrier_results = tuple(
        MemoryCarrierGateResult(
            carrier=carrier,
            passed=violations_by_carrier.get(carrier, 0) == 0,
            observation_count=counts[carrier],
            violation_count=violations_by_carrier.get(carrier, 0),
            evidence_refs=tuple(dict.fromkeys(evidence_by_carrier[carrier])),
        )
        for carrier in MemoryCarrierKind
        if carrier in counts
    )
    evidence_refs = tuple(
        dict.fromkeys(observation.evidence_ref for observation in observations)
    )
    return MemoryPollutionGateReport(
        carrier_results=carrier_results,
        violations=tuple(violations),
        evidence_refs=evidence_refs,
        complete=not violations,
    )


def _observation_problems(
    observation: MemoryCarrierObservation,
) -> tuple[str, ...]:
    problems: list[str] = []
    if observation.carrier in _CURRENT_CARRIERS:
        if observation.validity is not AgentMemoryValidity.ACTIVE:
            problems.append(f"当前载体包含 {observation.validity.value} 运行记忆")
        if observation.role not in {
            AgentMemoryDependencyRelation.BASIS,
            AgentMemoryDependencyRelation.REVIEW_TARGET,
        }:
            problems.append("当前载体使用了仅供修复的角色")
        if observation.repair_only:
            problems.append("仅供修复内容进入了当前载体")
    else:
        if observation.validity is AgentMemoryValidity.ACTIVE:
            problems.append("当前有效内容不应伪装成修复来源")
        if observation.role is not AgentMemoryDependencyRelation.REPAIR_SOURCE:
            problems.append("修复投影必须使用 REPAIR_SOURCE 角色")
        if not observation.repair_only:
            problems.append("修复投影缺少 repair_only 隔离标记")
    if not observation.proof_valid:
        problems.append("producer 有效性证明校验失败")
    return tuple(problems)


MemoryBehaviorCaseId: TypeAlias = Literal[
    "memory_active_projection",
    "memory_stale_dependency",
    "memory_rejected_parallel_isolation",
    "memory_superseded_repair",
]
MemoryBehaviorVariant: TypeAlias = Literal[
    "baseline",
    "candidate",
    "observed",
]


class MemoryBehaviorCarrier(StrEnum):
    """案例 18—21 中需要扫描的模型可见运行工作记忆载体。"""

    BASIS = "basis"
    REPAIR_HISTORY = "repair_history"
    HISTORY_MEMORY = "history_memory"
    PARENT_WORKING_MEMORY = "parent_working_memory"
    ORCHESTRATOR_INPUT = "orchestrator_input"
    NORMAL_DIGEST = "normal_digest"
    FALLBACK_DIGEST = "fallback_digest"
    BRANCH_INPUT = "branch_input"
    BRANCH_OUTPUT = "branch_output"
    SUBAGENT_REQUEST = "subagent_request"
    SUBAGENT_ENVELOPE = "subagent_envelope"
    SUBAGENT_RESULT = "subagent_result"
    AGGREGATE = "aggregate"
    FINAL_ANSWER = "final_answer"


class MemoryAnswerContract(BenchmarkModel):
    """由密封案例声明、从真实最终文本机械判定的答案合同。"""

    required_fragments: tuple[str, ...] = Field(min_length=1)
    ordered_fragments: tuple[str, ...] = ()
    forbidden_fragments: tuple[str, ...] = ()


class MemorySeedDependency(BenchmarkModel):
    memory_ref: StableId
    relation: AgentMemoryDependencyRelation = AgentMemoryDependencyRelation.BASIS


class MemorySeedEntry(BenchmarkModel):
    """密封夹具中的逻辑记忆；真实 memory_id 必须由 MemoryService 生成。"""

    memory_ref: StableId
    kind: AgentMemoryKind
    content: str = Field(min_length=1, max_length=20_000)
    target_validity: AgentMemoryValidity
    producer_ref: str = Field(
        pattern=r"^node:[^:]+:\d+:[^:]+$",
        max_length=256,
    )
    dependencies: tuple[MemorySeedDependency, ...] = ()
    supersedes_memory_ref: StableId | None = None
    invalidation_reason: str = Field(default="", max_length=2_000)
    sentinel_ref: StableId


class MemoryBehaviorSeedFixture(BenchmarkModel):
    """案例 18—21 的密封运行工作记忆与答案合同。"""

    schema_: Literal["taichu.general_agent_benchmark.memory_seed@1"] = Field(
        alias="schema"
    )
    memory_seed_ref: StableId
    entries: tuple[MemorySeedEntry, ...] = Field(min_length=1)
    answer_contracts: dict[MemoryBehaviorCaseId, MemoryAnswerContract]
    active_baseline_answer: str = Field(min_length=1)
    content_hash: Sha256

    @model_validator(mode="after")
    def validate_fixture(self) -> Self:
        payload = self.model_dump(
            mode="json",
            by_alias=True,
            exclude={"content_hash"},
        )
        if self.content_hash != canonical_sha256(payload):
            raise ValueError("运行工作记忆密封夹具校验和不匹配。")
        refs = tuple(item.memory_ref for item in self.entries)
        if len(refs) != len(set(refs)):
            raise ValueError("运行工作记忆夹具 memory_ref 不得重复。")
        seen: set[str] = set()
        for entry in self.entries:
            dependencies = {item.memory_ref for item in entry.dependencies}
            if not dependencies <= seen:
                raise ValueError("运行工作记忆夹具依赖必须引用前序条目。")
            if (
                entry.supersedes_memory_ref is not None
                and entry.supersedes_memory_ref not in seen
            ):
                raise ValueError("运行工作记忆夹具只能替代前序条目。")
            seen.add(entry.memory_ref)
        if set(self.answer_contracts) != {
            "memory_active_projection",
            "memory_stale_dependency",
            "memory_rejected_parallel_isolation",
            "memory_superseded_repair",
        }:
            raise ValueError("运行工作记忆夹具必须覆盖案例 18—21 的答案合同。")
        states = {item.target_validity for item in self.entries}
        if states != set(AgentMemoryValidity):
            raise ValueError(
                "运行工作记忆夹具必须覆盖 active/stale/rejected/superseded。"
            )
        return self


def load_memory_behavior_seed(path: Path) -> MemoryBehaviorSeedFixture:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("运行工作记忆密封夹具根节点必须是对象。")
    return MemoryBehaviorSeedFixture.model_validate(payload)


class MemoryInvalidOccurrence(BenchmarkModel):
    memory_id: str = Field(min_length=1, max_length=128)
    state: Literal["stale", "rejected", "superseded"]
    sentinel_ref: StableId
    occurrence_count: int = Field(ge=0)


class MemoryBehaviorCarrierEvidence(BenchmarkModel):
    carrier: MemoryBehaviorCarrier
    key: str = Field(
        pattern=r"^[a-z][a-z0-9_]*(?::[a-z][a-z0-9_]*)?$",
        max_length=160,
    )
    variant: MemoryBehaviorVariant = "observed"
    branch_id: StableId | None = None
    payload_sha256: Sha256
    target_occurrence_count: int = Field(default=0, ge=0)
    invalid_occurrences: tuple[MemoryInvalidOccurrence, ...] = ()
    evidence_ref: StableId


class MemoryInvalidEvidence(BenchmarkModel):
    memory_id: str = Field(min_length=1, max_length=128)
    state: Literal["stale", "rejected", "superseded"]
    dependency_memory_ids: tuple[str, ...] = ()
    invalidated_by_memory_id: str | None = Field(default=None, max_length=128)
    repair_present: bool


class MemoryBranchExchange(BenchmarkModel):
    """一次真实 Subagent 分支的输入、模型请求、信封与结果快照。"""

    branch_id: StableId
    node_id: StableId
    dependencies: tuple[StableId, ...] = ()
    resolved_input: dict[str, Any]
    output: dict[str, Any]
    request_payload: tuple[dict[str, Any], ...]
    envelope_payload: dict[str, Any]
    result_payload: dict[str, Any]
    evidence_ref: StableId

    @classmethod
    def from_runtime(
        cls,
        *,
        branch_id: str,
        node: GeneralAgentNodeRun,
        request: ModelRequestSnapshot,
        envelope: InvocationEnvelope[Any],
        evidence_ref: str,
    ) -> MemoryBranchExchange:
        """只读取生产 NodeRun、模型请求快照与 InvocationEnvelope。"""

        output = envelope.output.model_dump(mode="json")
        return cls(
            branch_id=branch_id,
            node_id=node.node_id,
            dependencies=tuple(node.dependencies),
            resolved_input=node.resolved_input,
            output=node.output,
            request_payload=_request_messages(request),
            envelope_payload={
                "invocation_id": envelope.invocation_id,
                "capability_type": envelope.capability_type,
                "capability_name": envelope.capability_name,
                "status": envelope.status.value,
                "output": output,
                "source_refs": envelope.source_refs,
                "artifact_refs": envelope.artifact_refs,
                "trace_id": envelope.trace_id,
            },
            result_payload=output,
            evidence_ref=evidence_ref,
        )


class MemoryBranchTopologyEvidence(BenchmarkModel):
    branch_id: StableId
    node_id: StableId
    dependencies: tuple[StableId, ...] = ()


class MemoryBehaviorArtifact(BenchmarkModel):
    """案例 18—21 的真实载体、答案与因果关系投影。"""

    case_id: MemoryBehaviorCaseId
    memory_seed_ref: StableId
    current_request_sha256: Sha256
    baseline_current_request_sha256: Sha256 | None = None
    target_memory_id: str | None = Field(default=None, max_length=128)
    latest_supersedes_memory_id: str | None = Field(default=None, max_length=128)
    baseline_answer: str | None = None
    final_answer: str
    answer_contract: MemoryAnswerContract
    carriers: tuple[MemoryBehaviorCarrierEvidence, ...]
    invalid_memories: tuple[MemoryInvalidEvidence, ...] = ()
    branch_topology: tuple[MemoryBranchTopologyEvidence, ...] = ()
    current_memory_ids: tuple[str, ...] = ()
    repair_memory_ids: tuple[str, ...] = ()
    required_carrier_keys: tuple[str, ...]
    evidence_ref: StableId
    content_sha256: Sha256

    @model_validator(mode="after")
    def validate_content_hash(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"content_sha256"})
        if self.content_sha256 != canonical_sha256(payload):
            raise ValueError("运行工作记忆行为工件校验和不匹配。")
        return self

    @classmethod
    def seal(cls, **payload: Any) -> MemoryBehaviorArtifact:
        return cls(
            **payload,
            content_sha256=canonical_sha256(payload),
        )


class MemoryBehaviorGateReport(BenchmarkModel):
    complete: bool
    violations: tuple[str, ...]
    answer_changed: bool
    target_memory_consumed: bool
    invalid_dependency_isolated: bool
    supersession_relation_valid: bool
    branch_count: int = Field(ge=0)
    evidence_refs: tuple[StableId, ...]


def _session_source_ids(working: Any) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                source
                for field in type(working.session_memory).model_fields
                for item in getattr(working.session_memory, field).values()
                for source in item.source_ids
            }
        )
    )


class MemoryBehaviorProjector:
    """把真实上下文、模型请求、分支信封和最终答案投影为行为证据。"""

    def project_active_pair(
        self,
        *,
        memory_seed_ref: str,
        target_memory: AgentMemoryEntry,
        baseline_snapshot: GeneralAgentContextSnapshot,
        candidate_snapshot: GeneralAgentContextSnapshot,
        baseline_request: ModelRequestSnapshot,
        candidate_request: ModelRequestSnapshot,
        baseline_answer: str,
        candidate_answer: str,
        answer_contract: MemoryAnswerContract,
        evidence_ref: str,
    ) -> MemoryBehaviorArtifact:
        if target_memory.validity is not AgentMemoryValidity.ACTIVE:
            raise ValueError("active 成对行为投影只接受当前有效运行工作记忆。")
        baseline_request_text = baseline_snapshot.envelope.current_request.content
        candidate_request_text = candidate_snapshot.envelope.current_request.content
        carriers = (
            _carrier_evidence(
                carrier=MemoryBehaviorCarrier.BASIS,
                key="baseline_basis",
                variant="baseline",
                payload=_current_memory_payload(baseline_snapshot),
                target_content=target_memory.content,
                evidence_ref=evidence_ref,
            ),
            _carrier_evidence(
                carrier=MemoryBehaviorCarrier.ORCHESTRATOR_INPUT,
                key="baseline_orchestrator_input",
                variant="baseline",
                payload=_developer_messages(baseline_request),
                target_content=target_memory.content,
                evidence_ref=evidence_ref,
            ),
            _carrier_evidence(
                carrier=MemoryBehaviorCarrier.FINAL_ANSWER,
                key="baseline_final_answer",
                variant="baseline",
                payload=baseline_answer,
                target_content=target_memory.content,
                evidence_ref=evidence_ref,
            ),
            _carrier_evidence(
                carrier=MemoryBehaviorCarrier.BASIS,
                key="candidate_basis",
                variant="candidate",
                payload=_current_memory_payload(candidate_snapshot),
                target_content=target_memory.content,
                evidence_ref=evidence_ref,
            ),
            _carrier_evidence(
                carrier=MemoryBehaviorCarrier.ORCHESTRATOR_INPUT,
                key="candidate_orchestrator_input",
                variant="candidate",
                payload=_developer_messages(candidate_request),
                target_content=target_memory.content,
                evidence_ref=evidence_ref,
            ),
            _carrier_evidence(
                carrier=MemoryBehaviorCarrier.FINAL_ANSWER,
                key="candidate_final_answer",
                variant="candidate",
                payload=candidate_answer,
                target_content=target_memory.content,
                evidence_ref=evidence_ref,
            ),
        )
        payload = {
            "case_id": "memory_active_projection",
            "memory_seed_ref": memory_seed_ref,
            "current_request_sha256": canonical_sha256(candidate_request_text),
            "baseline_current_request_sha256": canonical_sha256(baseline_request_text),
            "target_memory_id": target_memory.memory_id,
            "latest_supersedes_memory_id": None,
            "baseline_answer": baseline_answer,
            "final_answer": candidate_answer,
            "answer_contract": answer_contract,
            "carriers": carriers,
            "invalid_memories": (),
            "branch_topology": (),
            "current_memory_ids": _session_source_ids(
                candidate_snapshot.envelope.working_memory
            ),
            "repair_memory_ids": tuple(
                item.memory_id
                for item in candidate_snapshot.envelope.working_memory.invalidated_memories
            ),
            "required_carrier_keys": tuple(item.key for item in carriers),
            "evidence_ref": evidence_ref,
        }
        return MemoryBehaviorArtifact.seal(**payload)

    def project_invalid_case(
        self,
        *,
        case_id: MemoryBehaviorCaseId,
        memory_seed_ref: str,
        invalid_memories: tuple[AgentMemoryEntry, ...],
        sentinel_refs: Mapping[str, str],
        snapshot: GeneralAgentContextSnapshot,
        orchestrator_request: ModelRequestSnapshot,
        final_answer: str,
        answer_contract: MemoryAnswerContract,
        evidence_ref: str,
        branches: tuple[MemoryBranchExchange, ...] = (),
        fallback_snapshot: GeneralAgentContextSnapshot | None = None,
        latest_active_memory: AgentMemoryEntry | None = None,
        carrier_overrides: Mapping[str, Any] | None = None,
    ) -> MemoryBehaviorArtifact:
        if case_id == "memory_active_projection":
            raise ValueError("active 案例必须使用成对行为投影。")
        if not invalid_memories:
            raise ValueError("失效运行工作记忆案例至少需要一条真实失效记录。")
        invalid_by_id = {item.memory_id: item for item in invalid_memories}
        if set(sentinel_refs) != set(invalid_by_id):
            raise ValueError("失效运行记忆与哨兵引用必须一一对应。")
        if any(
            item.validity is AgentMemoryValidity.ACTIVE for item in invalid_memories
        ):
            raise ValueError("失效行为投影不能接收 active 记录。")
        if latest_active_memory is not None and (
            latest_active_memory.validity is not AgentMemoryValidity.ACTIVE
        ):
            raise ValueError("最新替代记忆必须处于 active 状态。")

        working = snapshot.envelope.working_memory
        fallback_working = (
            fallback_snapshot.envelope.working_memory
            if fallback_snapshot is not None
            else None
        )
        payloads: dict[str, tuple[MemoryBehaviorCarrier, str | None, Any]] = {
            "basis": (
                MemoryBehaviorCarrier.BASIS,
                None,
                working.session_memory.model_dump(mode="json"),
            ),
            "repair_history": (
                MemoryBehaviorCarrier.REPAIR_HISTORY,
                None,
                [item.model_dump(mode="json") for item in working.invalidated_memories],
            ),
            "history_memory": (
                MemoryBehaviorCarrier.HISTORY_MEMORY,
                None,
                snapshot.envelope.history_memory.model_dump(mode="json"),
            ),
            "parent_working_memory": (
                MemoryBehaviorCarrier.PARENT_WORKING_MEMORY,
                None,
                working.model_dump(mode="json"),
            ),
            "orchestrator_input": (
                MemoryBehaviorCarrier.ORCHESTRATOR_INPUT,
                None,
                _developer_messages(orchestrator_request),
            ),
            "normal_digest": (
                MemoryBehaviorCarrier.NORMAL_DIGEST,
                None,
                (
                    working.session_memory.model_dump(mode="json")
                    if working.session_memory is not None
                    else None
                ),
            ),
            "fallback_digest": (
                MemoryBehaviorCarrier.FALLBACK_DIGEST,
                None,
                {
                    "fallback_used": (
                        fallback_snapshot.envelope.fallback_used
                        if fallback_snapshot is not None
                        else False
                    ),
                    "digest": (
                        fallback_working.session_memory.model_dump(mode="json")
                        if fallback_working is not None
                        and fallback_working.session_memory is not None
                        else None
                    ),
                },
            ),
            "aggregate": (
                MemoryBehaviorCarrier.AGGREGATE,
                None,
                _developer_messages(orchestrator_request),
            ),
            "final_answer": (
                MemoryBehaviorCarrier.FINAL_ANSWER,
                None,
                final_answer,
            ),
        }
        for branch in branches:
            payloads.update(
                {
                    f"branch_input:{branch.branch_id}": (
                        MemoryBehaviorCarrier.BRANCH_INPUT,
                        branch.branch_id,
                        branch.resolved_input,
                    ),
                    f"branch_output:{branch.branch_id}": (
                        MemoryBehaviorCarrier.BRANCH_OUTPUT,
                        branch.branch_id,
                        branch.output,
                    ),
                    f"subagent_request:{branch.branch_id}": (
                        MemoryBehaviorCarrier.SUBAGENT_REQUEST,
                        branch.branch_id,
                        branch.request_payload,
                    ),
                    f"subagent_envelope:{branch.branch_id}": (
                        MemoryBehaviorCarrier.SUBAGENT_ENVELOPE,
                        branch.branch_id,
                        branch.envelope_payload,
                    ),
                    f"subagent_result:{branch.branch_id}": (
                        MemoryBehaviorCarrier.SUBAGENT_RESULT,
                        branch.branch_id,
                        branch.result_payload,
                    ),
                }
            )
        overrides = dict(carrier_overrides or {})
        unknown = set(overrides) - set(payloads)
        if unknown:
            raise ValueError(
                "运行工作记忆扫描包含未知载体：" + "、".join(sorted(unknown))
            )
        for key, value in overrides.items():
            carrier, branch_id, _ = payloads[key]
            payloads[key] = (carrier, branch_id, value)

        target_content = (
            latest_active_memory.content if latest_active_memory is not None else None
        )
        carriers = tuple(
            _carrier_evidence(
                carrier=carrier,
                key=key,
                variant="observed",
                branch_id=branch_id,
                payload=payload,
                target_content=target_content,
                invalid_memories=invalid_memories,
                sentinel_refs=sentinel_refs,
                evidence_ref=(
                    next(
                        (
                            branch.evidence_ref
                            for branch in branches
                            if branch.branch_id == branch_id
                        ),
                        evidence_ref,
                    )
                    if branch_id is not None
                    else evidence_ref
                ),
            )
            for key, (carrier, branch_id, payload) in payloads.items()
        )
        current_memory_ids = _session_source_ids(working)
        repair_memory_ids = tuple(
            item.memory_id for item in working.invalidated_memories
        )
        invalid_evidence = tuple(
            MemoryInvalidEvidence(
                memory_id=memory.memory_id,
                state=_invalid_state(memory.validity),
                dependency_memory_ids=tuple(
                    item.memory_id for item in memory.dependencies
                ),
                invalidated_by_memory_id=memory.invalidated_by_memory_id,
                repair_present=memory.memory_id in repair_memory_ids,
            )
            for memory in invalid_memories
        )
        payload = {
            "case_id": case_id,
            "memory_seed_ref": memory_seed_ref,
            "current_request_sha256": canonical_sha256(
                snapshot.envelope.current_request.content
            ),
            "baseline_current_request_sha256": None,
            "target_memory_id": (
                latest_active_memory.memory_id
                if latest_active_memory is not None
                else None
            ),
            "latest_supersedes_memory_id": (
                latest_active_memory.supersedes_memory_id
                if latest_active_memory is not None
                else None
            ),
            "baseline_answer": None,
            "final_answer": final_answer,
            "answer_contract": answer_contract,
            "carriers": carriers,
            "invalid_memories": invalid_evidence,
            "branch_topology": tuple(
                MemoryBranchTopologyEvidence(
                    branch_id=branch.branch_id,
                    node_id=branch.node_id,
                    dependencies=branch.dependencies,
                )
                for branch in branches
            ),
            "current_memory_ids": current_memory_ids,
            "repair_memory_ids": repair_memory_ids,
            "required_carrier_keys": tuple(payloads),
            "evidence_ref": evidence_ref,
        }
        return MemoryBehaviorArtifact.seal(**payload)

    def to_oracle_observations(
        self,
        artifact: MemoryBehaviorArtifact,
    ) -> tuple[Any, ...]:
        """投影到中央 Typed Oracle 已有的无效记忆载体观察。"""

        from taichu.application.evaluations.general_agent_benchmark.oracles import (
            MemoryCarrierObservation as OracleMemoryCarrierObservation,
        )

        observations: list[OracleMemoryCarrierObservation] = []
        for carrier in artifact.carriers:
            oracle_carrier = _oracle_carrier(carrier.carrier)
            for occurrence in carrier.invalid_occurrences:
                observations.append(
                    OracleMemoryCarrierObservation(
                        memory_seed_ref=artifact.memory_seed_ref,
                        state=occurrence.state,
                        carrier=oracle_carrier,
                        sentinel_ref=occurrence.sentinel_ref,
                        occurrence_count=occurrence.occurrence_count,
                    )
                )
        return tuple(observations)


def audit_memory_behavior(
    artifact: MemoryBehaviorArtifact,
) -> MemoryBehaviorGateReport:
    """验证最终行为；仅有 active/repair 对象集合永远不足以通过。"""

    violations: list[str] = []
    carrier_keys = {item.key for item in artifact.carriers}
    required_keys = set(artifact.required_carrier_keys)
    if not required_keys or carrier_keys != required_keys:
        violations.append("模型可见载体证据不完整，不能只凭记忆对象状态集合通过。")
    if not _answer_satisfies(artifact.final_answer, artifact.answer_contract):
        violations.append("最终答案没有满足当前运行工作记忆行为合同。")

    answer_changed = False
    target_memory_consumed = False
    invalid_dependency_isolated = False
    supersession_relation_valid = False
    branch_ids = {
        item.branch_id for item in artifact.carriers if item.branch_id is not None
    }

    if artifact.case_id == "memory_active_projection":
        answer_changed = (
            artifact.baseline_answer is not None
            and artifact.baseline_answer != artifact.final_answer
        )
        if not answer_changed:
            violations.append("active 运行工作记忆没有可观察地改变最终答案。")
        if artifact.baseline_answer is None or _answer_satisfies(
            artifact.baseline_answer,
            artifact.answer_contract,
        ):
            violations.append(
                "基线答案已满足目标约束，无法证明 active 记忆的因果影响。"
            )
        if artifact.baseline_current_request_sha256 != artifact.current_request_sha256:
            violations.append("active 成对运行的当前请求不一致。")
        baseline_occurrences = sum(
            item.target_occurrence_count
            for item in artifact.carriers
            if item.variant == "baseline"
        )
        candidate_basis = sum(
            item.target_occurrence_count
            for item in artifact.carriers
            if item.variant == "candidate"
            and item.carrier is MemoryBehaviorCarrier.BASIS
        )
        candidate_model_input = sum(
            item.target_occurrence_count
            for item in artifact.carriers
            if item.variant == "candidate"
            and item.carrier is MemoryBehaviorCarrier.ORCHESTRATOR_INPUT
        )
        target_memory_consumed = (
            baseline_occurrences == 0
            and candidate_basis > 0
            and candidate_model_input > 0
            and artifact.target_memory_id in artifact.current_memory_ids
        )
        if not target_memory_consumed:
            violations.append(
                "active 运行工作记忆未形成 memory→envelope→answer 的真实消费链。"
            )
    else:
        invalid_ids = {item.memory_id for item in artifact.invalid_memories}
        for carrier in artifact.carriers:
            covered_ids = {item.memory_id for item in carrier.invalid_occurrences}
            if covered_ids != invalid_ids:
                violations.append(f"{carrier.key} 没有扫描全部无效运行工作记忆哨兵。")
        revived = tuple(
            (
                carrier.key,
                occurrence.sentinel_ref,
                occurrence.occurrence_count,
            )
            for carrier in artifact.carriers
            for occurrence in carrier.invalid_occurrences
            if occurrence.occurrence_count
        )
        if revived:
            violations.extend(
                f"{key} 复活了无效运行工作记忆哨兵 {sentinel_ref}（{count} 次）。"
                for key, sentinel_ref, count in revived
            )
        if not artifact.invalid_memories:
            violations.append("失效运行工作记忆行为证据为空。")
        if any(not item.repair_present for item in artifact.invalid_memories):
            violations.append("失效记录没有保留在隔离的修复历史中。")

        if artifact.case_id == "memory_stale_dependency":
            invalid_dependency_isolated = (
                all(
                    item.state == AgentMemoryValidity.STALE.value
                    for item in artifact.invalid_memories
                )
                and any(
                    set(item.dependency_memory_ids) & invalid_ids
                    for item in artifact.invalid_memories
                )
                and not revived
            )
            if not invalid_dependency_isolated:
                violations.append("stale 记录及其失效依赖没有被完整隔离。")
        elif artifact.case_id == "memory_rejected_parallel_isolation":
            if any(
                item.state != AgentMemoryValidity.REJECTED.value
                for item in artifact.invalid_memories
            ):
                violations.append("rejected 分支案例包含非 rejected 目标记录。")
            if len(branch_ids) != 2:
                violations.append("rejected 分支案例必须观察两个独立分支。")
            branch_node_ids = {item.node_id for item in artifact.branch_topology}
            if {item.branch_id for item in artifact.branch_topology} != branch_ids:
                violations.append("rejected 分支拓扑与载体分支身份不一致。")
            if any(
                set(item.dependencies) & branch_node_ids
                for item in artifact.branch_topology
            ):
                violations.append("rejected 两个分析分支存在隐藏串行依赖。")
            for branch_id in branch_ids:
                required_branch_carriers = {
                    MemoryBehaviorCarrier.BRANCH_INPUT,
                    MemoryBehaviorCarrier.BRANCH_OUTPUT,
                    MemoryBehaviorCarrier.SUBAGENT_REQUEST,
                    MemoryBehaviorCarrier.SUBAGENT_ENVELOPE,
                    MemoryBehaviorCarrier.SUBAGENT_RESULT,
                }
                actual_branch_carriers = {
                    item.carrier
                    for item in artifact.carriers
                    if item.branch_id == branch_id
                }
                if actual_branch_carriers != required_branch_carriers:
                    violations.append(
                        f"分支 {branch_id} 的输入、输出或 Subagent 载体不完整。"
                    )
        elif artifact.case_id == "memory_superseded_repair":
            superseded_ids = {
                item.memory_id
                for item in artifact.invalid_memories
                if item.state == AgentMemoryValidity.SUPERSEDED.value
            }
            supersession_relation_valid = (
                len(superseded_ids) == len(artifact.invalid_memories)
                and artifact.target_memory_id in artifact.current_memory_ids
                and artifact.latest_supersedes_memory_id in superseded_ids
                and all(
                    item.invalidated_by_memory_id == artifact.target_memory_id
                    for item in artifact.invalid_memories
                )
            )
            target_memory_consumed = (
                sum(
                    item.target_occurrence_count
                    for item in artifact.carriers
                    if item.carrier is MemoryBehaviorCarrier.BASIS
                )
                > 0
                and sum(
                    item.target_occurrence_count
                    for item in artifact.carriers
                    if item.carrier is MemoryBehaviorCarrier.ORCHESTRATOR_INPUT
                )
                > 0
            )
            if not supersession_relation_valid:
                violations.append("最新 active 与 superseded 旧记录的替代关系不完整。")
            if not target_memory_consumed:
                violations.append("最终回答没有消费最新 active 运行工作记忆。")

    return MemoryBehaviorGateReport(
        complete=not violations,
        violations=tuple(violations),
        answer_changed=answer_changed,
        target_memory_consumed=target_memory_consumed,
        invalid_dependency_isolated=invalid_dependency_isolated,
        supersession_relation_valid=supersession_relation_valid,
        branch_count=len(branch_ids),
        evidence_refs=tuple(
            dict.fromkeys(
                [
                    artifact.evidence_ref,
                    *(item.evidence_ref for item in artifact.carriers),
                ]
            )
        ),
    )


def _carrier_evidence(
    *,
    carrier: MemoryBehaviorCarrier,
    key: str,
    variant: MemoryBehaviorVariant,
    payload: Any,
    evidence_ref: str,
    target_content: str | None = None,
    invalid_memories: tuple[AgentMemoryEntry, ...] = (),
    sentinel_refs: Mapping[str, str] | None = None,
    branch_id: str | None = None,
) -> MemoryBehaviorCarrierEvidence:
    serialized = _payload_text(payload)
    refs = sentinel_refs or {}
    return MemoryBehaviorCarrierEvidence(
        carrier=carrier,
        key=key,
        variant=variant,
        branch_id=branch_id,
        payload_sha256=canonical_sha256(payload),
        target_occurrence_count=(
            serialized.count(target_content) if target_content else 0
        ),
        invalid_occurrences=tuple(
            MemoryInvalidOccurrence(
                memory_id=memory.memory_id,
                state=_invalid_state(memory.validity),
                sentinel_ref=refs[memory.memory_id],
                occurrence_count=serialized.count(memory.content),
            )
            for memory in invalid_memories
        ),
        evidence_ref=evidence_ref,
    )


def _answer_satisfies(
    answer: str,
    contract: MemoryAnswerContract,
) -> bool:
    if any(fragment not in answer for fragment in contract.required_fragments):
        return False
    if any(fragment in answer for fragment in contract.forbidden_fragments):
        return False
    cursor = 0
    for fragment in contract.ordered_fragments:
        position = answer.find(fragment, cursor)
        if position < 0:
            return False
        cursor = position + len(fragment)
    return True


def _payload_text(payload: Any) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def _request_messages(
    request: ModelRequestSnapshot,
) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "role": message.role,
            "content": message.content,
            "tool_calls": [
                {
                    "call_id": item.call_id,
                    "name": item.name,
                    "arguments_json": item.arguments_json,
                }
                for item in message.tool_calls
            ],
            "tool_call_id": message.tool_call_id,
            "tool_name": message.tool_name,
            "is_error": message.is_error,
        }
        for message in request.messages
    )


def _developer_messages(
    request: ModelRequestSnapshot,
) -> tuple[dict[str, Any], ...]:
    return tuple(
        item for item in _request_messages(request) if item["role"] == "developer"
    )


def _current_memory_payload(
    snapshot: GeneralAgentContextSnapshot,
) -> dict[str, Any]:
    return snapshot.envelope.working_memory.session_memory.model_dump(mode="json")


def _invalid_state(
    validity: AgentMemoryValidity,
) -> Literal["stale", "rejected", "superseded"]:
    if validity is AgentMemoryValidity.STALE:
        return "stale"
    if validity is AgentMemoryValidity.REJECTED:
        return "rejected"
    if validity is AgentMemoryValidity.SUPERSEDED:
        return "superseded"
    raise ValueError("active 运行工作记忆不能作为失效哨兵。")


def _oracle_carrier(
    carrier: MemoryBehaviorCarrier,
) -> Literal[
    "basis",
    "repair",
    "digest",
    "fallback",
    "history",
    "working_memory",
    "node",
    "subagent",
    "final",
]:
    if carrier is MemoryBehaviorCarrier.BASIS:
        return "basis"
    if carrier is MemoryBehaviorCarrier.REPAIR_HISTORY:
        return "repair"
    if carrier is MemoryBehaviorCarrier.NORMAL_DIGEST:
        return "digest"
    if carrier is MemoryBehaviorCarrier.FALLBACK_DIGEST:
        return "fallback"
    if carrier is MemoryBehaviorCarrier.HISTORY_MEMORY:
        return "history"
    if carrier in {
        MemoryBehaviorCarrier.PARENT_WORKING_MEMORY,
        MemoryBehaviorCarrier.ORCHESTRATOR_INPUT,
    }:
        return "working_memory"
    if carrier in {
        MemoryBehaviorCarrier.BRANCH_INPUT,
        MemoryBehaviorCarrier.BRANCH_OUTPUT,
        MemoryBehaviorCarrier.AGGREGATE,
    }:
        return "node"
    if carrier in {
        MemoryBehaviorCarrier.SUBAGENT_REQUEST,
        MemoryBehaviorCarrier.SUBAGENT_ENVELOPE,
        MemoryBehaviorCarrier.SUBAGENT_RESULT,
    }:
        return "subagent"
    return "final"
