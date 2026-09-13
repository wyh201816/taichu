"""五层上下文投影；容量处理统一交给主 Agent 五级流水线。"""

from __future__ import annotations
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Literal
from uuid import uuid4
from taichu.application.agent_memory.models import (
    AgentMemoryDependencyRelation,
    AgentMemoryEntry,
    AgentMemoryKind,
    AgentMemoryValidity,
    memory_state_sha256,
)
from taichu.application.general_agent.models import (
    GeneralAgentContextEnvelope,
    GeneralAgentContextMemory,
    GeneralAgentContextMemoryRef,
    GeneralAgentContextSnapshot,
    GeneralAgentCurrentRequest,
    GeneralAgentHistoryMemory,
    GeneralAgentMessageType,
    GeneralAgentRun,
    GeneralAgentWorkingMemory,
    GeneralAgentAssemblyTrace,
    GeneralAgentContextLayerTrace,
    context_snapshot_sha256,
)
from taichu.application.contracts.long_term_memory import LongTermMemoryRetriever
from taichu.application.invocations.models import now_iso
from taichu.application.services.agent_memory_service import AgentMemoryService
from taichu.application.general_agent.pipeline import project_envelope, memory_sources

ContextPhase = Literal["plan", "replan", "verify"]


class ContextAssemblyError(ValueError):
    """必要上下文无法安全组装。"""

    reason_code = "unsafe_context"
    input_tokens = None
    context_window_tokens = None
    output_tokens = 0
    current_request_sha256 = None
    stable_memory_sha256 = None


@dataclass(frozen=True, slots=True)
class ContextAssemblyResult:
    snapshot: GeneralAgentContextSnapshot
    reused_snapshot: bool = False
    resume_differences: tuple[str, ...] = ()


class ContextAssembler:
    def __init__(
        self,
        *,
        memory_service: AgentMemoryService,
        long_term_memory_retriever: LongTermMemoryRetriever | None = None,
    ) -> None:
        self._memory_service = memory_service
        self._long_term_memory_retriever = long_term_memory_retriever

    async def assemble(
        self, run: GeneralAgentRun, *, phase: ContextPhase, replan_guidance: str = ""
    ) -> ContextAssemblyResult:
        await self._memory_service.refresh_evidence_validity(run.conversation_id)
        entries = await self._memory_service.list_active(
            run.conversation_id,
            current_request_index=run.request_index,
            as_of=now_iso(),
            refresh_evidence=False,
        )
        refs = set(run.context_pipeline.results)
        validities = await self._memory_service.producer_validities(
            run.conversation_id, refs
        )
        excluded = [
            key
            for key, validity in validities.items()
            if validity not in {None, AgentMemoryValidity.ACTIVE}
        ]
        invalidated = await self._memory_service.list_invalidated(
            run.conversation_id,
            current_request_index=run.request_index,
            refresh_evidence=False,
        )
        for source_id in memory_sources(run.context_pipeline.working_memory):
            if source_id.startswith("memory_"):
                entry = await self._memory_service.get(source_id)
                if entry is None or not entry.is_active(
                    as_of=now_iso(), request_index=run.request_index
                ):
                    excluded.append(source_id)
        long_term = (
            await self._long_term_memory_retriever.retrieve(run.user_goal)
            if self._long_term_memory_retriever
            else []
        )
        # 执行事实来自 Runtime；模型五字段记忆不能覆盖节点或依赖状态。
        nodes = {
            node.node_id: node
            for node in run.node_runs
            if node.plan_revision == run.plan_revision
        }
        plan = None
        if run.plan:
            plan = {
                "目标": run.user_goal,
                "节点": [
                    {
                        "node_id": node.node_id,
                        "能力": node.capability_name,
                        "目标": node.objective,
                        "依赖": node.dependencies,
                        "状态": nodes[node.node_id].status.value
                        if node.node_id in nodes
                        else "pending",
                    }
                    for node in run.plan.nodes
                    if node.node_id not in nodes
                    or nodes[node.node_id].status.value not in {"success", "skipped"}
                ],
            }
        envelope = GeneralAgentContextEnvelope(
            phase=phase,
            stable_memory=list(_STABLE_MEMORY),
            long_term_memory=long_term,
            history_memory=GeneralAgentHistoryMemory(),
            working_memory=GeneralAgentWorkingMemory(
                plan_summary=plan,
                excluded_result_sources=excluded,
                invalidated_memories=[
                    _context_memory(
                        entry,
                        projection_role=AgentMemoryDependencyRelation.REPAIR_SOURCE,
                        repair_only=True,
                    )
                    for entry in invalidated
                ],
                unresolved_issues=run.verification_issues,
                replan_guidance=replan_guidance,
            ),
            current_request=_current_request(run),
        )
        envelope = project_envelope(envelope, run, run.context_pipeline)
        references = [
            GeneralAgentContextMemoryRef(
                memory_id=e.memory_id,
                content_sha256=e.content_sha256,
                state_sha256=memory_state_sha256(e),
            )
            for e in entries
        ]
        differences = []
        if run.context_snapshot:
            if run.context_snapshot.envelope.long_term_memory != long_term:
                differences.append("按当前请求召回的长期记忆已经变化。")
            for ref in run.context_snapshot.memory_refs:
                current = await self._memory_service.get(ref.memory_id)
                if (
                    current is None
                    or current.deleted_at
                    or memory_state_sha256(current) != ref.state_sha256
                ):
                    differences.append(f"运行记忆 {ref.memory_id} 的使用状态已经变化。")
        return ContextAssemblyResult(
            snapshot=create_snapshot(run, envelope, references),
            resume_differences=tuple(differences),
        )


def create_snapshot(
    run: GeneralAgentRun,
    envelope: GeneralAgentContextEnvelope,
    memory_refs: list[GeneralAgentContextMemoryRef] | None = None,
) -> GeneralAgentContextSnapshot:
    created = now_iso()
    encoded = json.dumps(envelope.model_dump(mode="json"), ensure_ascii=False)
    envelope = envelope.model_copy(update={"total_char_count": len(encoded)})
    layers = []
    # 保留原审计协议的固定列序；实际模型拼接始终稳定、长期、历史、工作、当前。
    for name in (
        "stable_memory",
        "working_memory",
        "long_term_memory",
        "history_memory",
        "current_request",
    ):
        value = getattr(envelope, name)
        payload = (
            value.model_dump(mode="json")
            if hasattr(value, "model_dump")
            else [
                v.model_dump(mode="json") if hasattr(v, "model_dump") else v
                for v in value
            ]
        )
        length = len(json.dumps(payload, ensure_ascii=False))
        count = len(value) if isinstance(value, list) else 1
        layers.append(
            GeneralAgentContextLayerTrace(
                layer=name,
                pre_count=count,
                pre_char_count=length,
                pre_token_estimate=0,
                post_count=count,
                post_char_count=length,
                post_token_estimate=0,
                omitted_count=0,
            )
        )
    trace = GeneralAgentAssemblyTrace.create(
        layers=tuple(layers),
        omitted_item_refs=(),
        omitted_source_refs=(),
        protected_refs=("stable_memory", "current_request"),
        digest_used=bool(run.context_pipeline.folds),
        fallback_used=False,
        digest_source_ids=tuple(
            mid for fold in run.context_pipeline.folds for mid in fold.message_ids
        ),
        current_request_sha256=sha256(
            envelope.current_request.content.encode("utf-8")
        ).hexdigest(),
        stable_memory_sha256=context_snapshot_sha256(envelope.stable_memory),
        projections=(),
    )
    payload = dict(
        snapshot_id=f"context_{created[:10].replace('-', '')}_{created[11:19].replace(':', '')}_{uuid4().hex[:8]}",
        phase=envelope.phase,
        conversation_id=run.conversation_id,
        run_id=run.run_id,
        created_at=created,
        policy_snapshot={"policy": "main_agent_context_pipeline"},
        memory_refs=[r.model_dump(mode="json") for r in (memory_refs or [])],
        envelope=envelope.model_dump(mode="json"),
        assembly_trace=trace.model_dump(mode="json"),
    )
    return GeneralAgentContextSnapshot.model_validate(
        {**payload, "content_sha256": context_snapshot_sha256(payload)}
    )


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


_STABLE_MEMORY = (
    "你是太初通用写作助手的高层编排 Agent，负责理解当前请求、发现真实能力、选择最小充分路径并收敛结果。",
    "System Prompt 中的 Static Capability Index 是能力发现边界；只能调用其中真实存在的 Tool 或子 Agent，不得临时创造能力。",
    "Markdown 正文是章节原文事实源；MongoDB 中已确认知识卡是结构事实源；所有索引均为可重建派生层。",
    "运行记忆只延续工作状态，不是小说事实；涉及事实时必须重新读取正文或通过统一召回取证。",
    "工作记忆中标为失效、被否决或被替代的记录只用于理解错误和修复，不得作为当前事实、正文或最终结论。",
    "写入正文或结构事实必须遵守授权、校验和作者确认门禁。",
)
_CURRENT_REQUEST_CONTENT_LIMIT = 100_000
_INVALID_MEMORY_REPAIR_CONTENT = (
    "该运行记忆已经失效；模型上下文只保留状态与内容哈希用于修复审计，"
    "必须重新取证或重新生成，不得把原内容作为当前事实使用。"
)
_INVALID_MEMORY_REPAIR_REASON = (
    "原始失效原因保存在运行审计中；模型上下文不投影可能复活旧结论的原文。"
)


def _context_memory(
    entry: AgentMemoryEntry,
    *,
    projection_role: AgentMemoryDependencyRelation,
    repair_only: bool = False,
) -> GeneralAgentContextMemory:
    if not repair_only and entry.validity is not AgentMemoryValidity.ACTIVE:
        raise ContextAssemblyError("失效运行记忆不得进入当前事实投影。")
    if repair_only and entry.validity is AgentMemoryValidity.ACTIVE:
        raise ContextAssemblyError("有效运行记忆不得伪装成修复隔离投影。")
    content = entry.content
    if entry.kind is AgentMemoryKind.FACT_REFERENCE:
        content = f"事实引用标签：{content}。使用前必须通过正文或统一召回重新取证。"
    if repair_only:
        content = _INVALID_MEMORY_REPAIR_CONTENT
    return GeneralAgentContextMemory(
        memory_id=entry.memory_id,
        kind=entry.kind.value,
        content=content,
        source_refs=[] if repair_only else entry.source_refs,
        artifact_refs=[] if repair_only else entry.artifact_refs,
        content_sha256=entry.content_sha256,
        basis_sha256=entry.basis_sha256,
        state_sha256=memory_state_sha256(entry),
        validity=entry.validity.value,
        previous_validity=(
            entry.previous_validity.value
            if entry.previous_validity is not None
            else None
        ),
        invalidation_reason=(
            _INVALID_MEMORY_REPAIR_REASON if repair_only else entry.invalidation_reason
        ),
        invalidated_by_memory_id=entry.invalidated_by_memory_id,
        supersedes_memory_id=entry.supersedes_memory_id,
        result_type=None if repair_only else entry.result_type,
        producer_ref=None if repair_only else entry.producer_ref,
        projection_role=projection_role.value,
        repair_only=repair_only,
    )


def _current_human_responses(run: GeneralAgentRun) -> list[str]:
    return [
        item.content
        for item in run.messages
        if item.turn_id == run.run_id
        and item.request_index == run.request_index
        and item.message_type is GeneralAgentMessageType.HUMAN_RESPONSE
    ]


def _current_request(run: GeneralAgentRun) -> GeneralAgentCurrentRequest:
    return GeneralAgentCurrentRequest(
        content=run.user_goal,
        human_responses=_current_human_responses(run),
        user_constraints=_deduplicate(run.author_constraints),
        scope=run.scope.model_dump(mode="json"),
    )


def _raw_history_messages(run: GeneralAgentRun):
    """只返回此前已结束轮次的用户与展示回答，HIL 留在当前请求。"""
    return [
        message
        for message in run.messages
        if not (
            message.turn_id == run.run_id and message.request_index == run.request_index
        )
    ]
