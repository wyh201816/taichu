"""通用 Runtime 自动工作记忆的写入、过期与检索服务。"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING
from uuid import uuid4

from taichu.application.agent_memory.models import (
    AgentMemoryDependency,
    AgentMemoryDependencyRelation,
    AgentMemoryEntry,
    AgentMemoryEvidenceAnchor,
    AgentMemoryKind,
    AgentMemoryQuery,
    AgentMemorySelection,
    AgentMemorySensitivity,
    AgentMemoryValidity,
    MemoryWriteCandidate,
    ProducerMemoryValidityProof,
    memory_basis_sha256,
    memory_content_sha256,
    memory_dependency_fingerprint,
    memory_now_iso,
    memory_source_fingerprint,
    memory_state_sha256,
    producer_validity_proof_sha256,
)
from taichu.application.contracts.agent_memory import (
    AgentMemoryEvidenceResolver,
    AgentMemoryRepository,
)
from taichu.application.general_agent.memory_policy import AgentMemoryPolicy

from taichu.application.services.invocation_policy_service import canonical_input_hash

if TYPE_CHECKING:
    from taichu.application.general_agent.models import (
        GeneralAgentExecutionPlan,
        GeneralAgentNodeRun,
        GeneralAgentRun,
        GeneralAgentVerification,
    )

_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"(?i)\b(api[_ -]?key|authorization|bearer)\s*[:=]\s*\S+"),
)
_WORD_PATTERN = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")
_REVIEW_RESULT_TYPES = frozenset(
    {"consistency_review", "narrative_review", "style_review"}
)
_CANDIDATE_RESULT_TYPES = frozenset({"manuscript_candidate", "revision_candidate"})
_REVIEW_CAPABILITIES = frozenset(
    {"consistency_reviewer", "narrative_reviewer", "style_reviewer"}
)
_REVISION_CAPABILITIES = frozenset({"revision"})
_PROPAGATING_RELATIONS = frozenset(
    {
        AgentMemoryDependencyRelation.BASIS,
        AgentMemoryDependencyRelation.REVIEW_TARGET,
    }
)
_MAX_MEMORY_SOURCE_REFS = 100
_MAX_MEMORY_ARTIFACT_REFS = 100
_MAX_MEMORY_EVIDENCE_ANCHORS = 200
_MAX_MEMORY_DEPENDENCIES = 100
_MAX_MEMORY_RUN_IDS = 100


class AgentMemoryService:
    """由 Runtime 自动维护，并向内部编排工具提供受控写入原语。"""

    def __init__(
        self,
        *,
        repository: AgentMemoryRepository,
        policy: AgentMemoryPolicy | None = None,
        evidence_resolver: AgentMemoryEvidenceResolver | None = None,
    ) -> None:
        self._repository = repository
        self._policy = policy or AgentMemoryPolicy()
        self._evidence_resolver = evidence_resolver

    async def write(self, candidate: MemoryWriteCandidate) -> AgentMemoryEntry:
        normalized = candidate.content.strip()
        self._validate_candidate(candidate, normalized)
        await self._validate_supersession(candidate)
        dependencies = _deduplicate_dependencies(candidate.dependencies)
        await self._validate_dependencies(candidate, dependencies)
        existing = await self._repository.query(
            conversation_id=candidate.conversation_id,
            kinds=(candidate.kind,),
            include_deleted=False,
        )
        content_hash = memory_content_sha256(normalized)
        duplicate = next(
            (
                entry
                for entry in existing
                if entry.content_sha256 == content_hash
                and (
                    (
                        candidate.producer_ref is not None
                        and entry.producer_ref == candidate.producer_ref
                    )
                    or (
                        candidate.producer_ref is None
                        and entry.producer_ref is None
                        and candidate.supersedes_memory_id is None
                        and entry.validity is candidate.validity
                    )
                )
            ),
            None,
        )
        now = memory_now_iso()
        if duplicate is not None:
            source_refs = _deduplicate(
                [*duplicate.source_refs, *candidate.source_refs]
            )[:_MAX_MEMORY_SOURCE_REFS]
            artifact_refs = _deduplicate(
                [*duplicate.artifact_refs, *candidate.artifact_refs]
            )[:_MAX_MEMORY_ARTIFACT_REFS]
            evidence_anchors = _deduplicate_evidence_anchors(
                [*duplicate.evidence_anchors, *candidate.evidence_anchors]
            )[:_MAX_MEMORY_EVIDENCE_ANCHORS]
            merged_dependencies = _deduplicate_dependencies(
                [*duplicate.dependencies, *dependencies]
            )[:_MAX_MEMORY_DEPENDENCIES]
            updated = duplicate.model_copy(
                update={
                    "source_refs": source_refs,
                    "artifact_refs": artifact_refs,
                    "run_ids": _deduplicate([*duplicate.run_ids, *candidate.run_ids])[
                        :_MAX_MEMORY_RUN_IDS
                    ],
                    "created_request_index": min(
                        duplicate.created_request_index,
                        candidate.created_request_index,
                    ),
                    "expires_after_request_index": _later_request_expiry(
                        duplicate.expires_after_request_index,
                        candidate.expires_after_request_index,
                    ),
                    "retention_priority": max(
                        duplicate.retention_priority,
                        candidate.retention_priority,
                    ),
                    "supersedes_memory_id": (
                        candidate.supersedes_memory_id or duplicate.supersedes_memory_id
                    ),
                    "result_type": candidate.result_type or duplicate.result_type,
                    "evidence_anchors": evidence_anchors,
                    "dependencies": merged_dependencies,
                    "basis_sha256": memory_basis_sha256(
                        content_sha256=content_hash,
                        source_refs=source_refs,
                        artifact_refs=artifact_refs,
                        evidence_anchors=evidence_anchors,
                        dependencies=merged_dependencies,
                    ),
                    "updated_at": now,
                }
            )
            return await self._repository.save(updated)

        source_refs = _deduplicate(candidate.source_refs)
        artifact_refs = _deduplicate(candidate.artifact_refs)
        evidence_anchors = _deduplicate_evidence_anchors(candidate.evidence_anchors)
        invalidated_at = (
            now if candidate.validity is not AgentMemoryValidity.ACTIVE else None
        )
        memory_id = (
            f"memory_{now[0:10].replace('-', '')}_"
            f"{now[11:19].replace(':', '')}_{uuid4().hex[:8]}"
        )
        entry = AgentMemoryEntry(
            memory_id=memory_id,
            kind=candidate.kind,
            content=normalized,
            source_refs=source_refs,
            artifact_refs=artifact_refs,
            run_ids=_deduplicate(candidate.run_ids),
            conversation_id=candidate.conversation_id,
            created_request_index=candidate.created_request_index,
            expires_after_request_index=candidate.expires_after_request_index,
            retention_priority=candidate.retention_priority,
            created_at=now,
            updated_at=now,
            expires_at=candidate.expires_at,
            supersedes_memory_id=candidate.supersedes_memory_id,
            content_sha256=content_hash,
            basis_sha256=memory_basis_sha256(
                content_sha256=content_hash,
                source_refs=source_refs,
                artifact_refs=artifact_refs,
                evidence_anchors=evidence_anchors,
                dependencies=dependencies,
            ),
            producer_ref=candidate.producer_ref,
            result_type=candidate.result_type,
            evidence_anchors=evidence_anchors,
            dependencies=dependencies,
            validity=candidate.validity,
            invalidated_at=invalidated_at,
            invalidation_reason=candidate.invalidation_reason.strip(),
            invalidated_by_memory_id=candidate.invalidated_by_memory_id,
            sensitivity=candidate.sensitivity,
        )
        saved = await self._repository.save(entry)
        if saved.supersedes_memory_id is not None:
            await self._transition_validity(
                saved.supersedes_memory_id,
                validity=AgentMemoryValidity.SUPERSEDED,
                reason=f"已由运行记忆 {saved.memory_id} 的新产物替代。",
                invalidated_by_memory_id=saved.memory_id,
                exclude_memory_ids={saved.memory_id},
            )
        if saved.validity is AgentMemoryValidity.ACTIVE:
            saved = await self._invalidate_if_dependency_not_current(saved)
        return saved

    async def retrieve(
        self,
        query: AgentMemoryQuery,
        *,
        refresh_evidence: bool = True,
    ) -> AgentMemorySelection:
        as_of = query.as_of or memory_now_iso()
        await self._repository.purge_expired(as_of=as_of)
        entries = await self.list_active(
            query.conversation_id,
            current_request_index=query.current_request_index,
            as_of=as_of,
            kinds=tuple(query.kinds),
            run_id=query.run_id,
            refresh_evidence=refresh_evidence,
        )
        scores = _lexical_scores(entries, query_text=query.query_text)
        return self._policy.select(
            entries,
            lexical_scores=scores,
            as_of=as_of,
        )

    async def list_active(
        self,
        conversation_id: str,
        *,
        current_request_index: int,
        as_of: str | None = None,
        kinds: tuple[AgentMemoryKind, ...] = (),
        run_id: str | None = None,
        refresh_evidence: bool = True,
    ) -> list[AgentMemoryEntry]:
        current_time = as_of or memory_now_iso()
        if refresh_evidence:
            await self.refresh_evidence_validity(conversation_id)
        entries = await self._repository.query(
            conversation_id=conversation_id,
            kinds=kinds,
            run_id=run_id,
            include_deleted=False,
        )
        return [
            entry
            for entry in entries
            if entry.is_active(
                as_of=current_time,
                request_index=current_request_index,
            )
        ]

    async def list_invalidated(
        self,
        conversation_id: str,
        *,
        current_request_index: int,
        as_of: str | None = None,
        validities: tuple[AgentMemoryValidity, ...] = (
            AgentMemoryValidity.REJECTED,
            AgentMemoryValidity.STALE,
            AgentMemoryValidity.SUPERSEDED,
        ),
        refresh_evidence: bool = True,
    ) -> list[AgentMemoryEntry]:
        current_time = as_of or memory_now_iso()
        if refresh_evidence:
            await self.refresh_evidence_validity(conversation_id)
        expected = set(validities)
        entries = await self._repository.query(
            conversation_id=conversation_id,
            include_deleted=False,
        )
        selected: list[AgentMemoryEntry] = []
        for entry in entries:
            if entry.validity not in expected or not entry.is_retained(
                as_of=current_time,
                request_index=current_request_index,
            ):
                continue
            selected.append(entry)
        return selected

    async def refresh_evidence_validity(self, conversation_id: str) -> list[str]:
        if self._evidence_resolver is None:
            return []
        entries = await self._repository.query(
            conversation_id=conversation_id,
            include_deleted=False,
        )
        active_entries = [
            entry
            for entry in entries
            if entry.validity is AgentMemoryValidity.ACTIVE and entry.evidence_anchors
        ]
        references = _deduplicate(
            [
                anchor.reference
                for entry in active_entries
                for anchor in entry.evidence_anchors
            ]
        )
        fingerprints = await asyncio.gather(
            *[
                self._evidence_resolver.fingerprint(reference)
                for reference in references
            ]
        )
        current_by_reference = dict(zip(references, fingerprints, strict=True))
        invalidated: list[str] = []
        for entry in active_entries:
            changed = [
                anchor.reference
                for anchor in entry.evidence_anchors
                if (
                    current_by_reference.get(anchor.reference) is not None
                    and current_by_reference[anchor.reference] != anchor.content_sha256
                )
            ]
            if not changed:
                continue
            updated = await self._transition_validity(
                entry.memory_id,
                validity=AgentMemoryValidity.STALE,
                reason=(
                    "依赖的事实源内容已经变化，需要重新读取后再使用："
                    + "、".join(changed)
                ),
            )
            if updated is not None:
                invalidated.append(updated.memory_id)
        return invalidated

    async def invalidate(
        self,
        memory_id: str,
        *,
        validity: AgentMemoryValidity,
        reason: str,
        invalidated_by_memory_id: str | None = None,
        exclude_memory_ids: set[str] | None = None,
    ) -> AgentMemoryEntry | None:
        if validity is AgentMemoryValidity.ACTIVE:
            raise AgentMemoryServiceError("失效操作不能把运行记忆恢复为有效状态。")
        return await self._transition_validity(
            memory_id,
            validity=validity,
            reason=reason,
            invalidated_by_memory_id=invalidated_by_memory_id,
            exclude_memory_ids=exclude_memory_ids or set(),
        )

    async def list_for_conversation(
        self,
        conversation_id: str,
        *,
        include_deleted: bool = False,
    ) -> list[AgentMemoryEntry]:
        await self._repository.purge_expired(as_of=memory_now_iso())
        return await self._repository.query(
            conversation_id=conversation_id,
            include_deleted=include_deleted,
        )

    async def get(self, memory_id: str) -> AgentMemoryEntry | None:
        return await self._repository.get(memory_id)

    async def producer_validities(
        self,
        conversation_id: str,
        producer_refs: set[str],
    ) -> dict[str, AgentMemoryValidity]:
        if not producer_refs:
            return {}
        entries = await self._repository.query(
            conversation_id=conversation_id,
            include_deleted=False,
        )
        result: dict[str, AgentMemoryValidity] = {}
        for entry in entries:
            if entry.producer_ref in producer_refs and entry.producer_ref is not None:
                result.setdefault(entry.producer_ref, entry.validity)
        return result

    async def producer_validity_proof(
        self,
        conversation_id: str,
        producer_ref: str,
        *,
        current_request_index: int | None = None,
    ) -> ProducerMemoryValidityProof:
        if not re.fullmatch(r"node:[^:]+:\d+:[^:]+", producer_ref):
            raise AgentMemoryServiceError("producer 引用不是精确节点引用。")
        await self._repository.purge_expired(as_of=memory_now_iso())
        entries = await self._repository.query(
            conversation_id=conversation_id,
            include_deleted=False,
        )
        matches = [entry for entry in entries if entry.producer_ref == producer_ref]
        if len(matches) != 1:
            raise AgentMemoryServiceError("producer 引用必须命中唯一一条当前运行记忆。")
        entry = matches[0]
        observed_at = memory_now_iso()
        if current_request_index is not None and not entry.is_retained(
            as_of=observed_at,
            request_index=current_request_index,
        ):
            raise AgentMemoryServiceError("producer 运行记忆已经删除或过期。")
        expected_basis = memory_basis_sha256(
            content_sha256=entry.content_sha256,
            source_refs=entry.source_refs,
            artifact_refs=entry.artifact_refs,
            evidence_anchors=entry.evidence_anchors,
            dependencies=entry.dependencies,
        )
        if entry.basis_sha256 != expected_basis:
            raise AgentMemoryServiceError("producer 运行记忆的依据摘要不匹配。")
        dependency_states: list[tuple[str, AgentMemoryDependencyRelation, str]] = []
        for dependency in entry.dependencies:
            dependency_entry = await self._repository.get(dependency.memory_id)
            if dependency_entry is None or dependency_entry.deleted_at is not None:
                raise AgentMemoryServiceError(
                    "producer 运行记忆的依赖不存在或已经删除。"
                )
            dependency_states.append(
                (
                    dependency.memory_id,
                    dependency.relation,
                    memory_state_sha256(dependency_entry),
                )
            )
        supersession: tuple[str, str] | None = None
        if entry.supersedes_memory_id is not None:
            superseded = await self._repository.get(entry.supersedes_memory_id)
            if superseded is None or superseded.deleted_at is not None:
                raise AgentMemoryServiceError(
                    "producer 运行记忆的替代来源不存在或已经删除。"
                )
            supersession = (
                superseded.memory_id,
                memory_state_sha256(superseded),
            )
        return ProducerMemoryValidityProof(
            conversation_id=conversation_id,
            producer_ref=producer_ref,
            source_node_id=producer_ref.rsplit(":", maxsplit=1)[-1],
            memory_id=entry.memory_id,
            validity=entry.validity,
            state_hash=memory_state_sha256(entry),
            source_fingerprint=memory_source_fingerprint(entry),
            dependency_fingerprint=memory_dependency_fingerprint(
                dependencies=dependency_states,
                supersession=supersession,
            ),
            supersedes_memory_id=entry.supersedes_memory_id,
            observed_at=observed_at,
        )

    async def require_active_producer(
        self,
        conversation_id: str,
        producer_ref: str,
        *,
        expected_source_fingerprint: str,
        expected_dependency_fingerprint: str,
        current_request_index: int | None = None,
    ) -> ProducerMemoryValidityProof:
        proof = await self.producer_validity_proof(
            conversation_id,
            producer_ref,
            current_request_index=current_request_index,
        )
        if proof.validity is not AgentMemoryValidity.ACTIVE:
            raise AgentMemoryServiceError("producer 运行记忆不是当前有效状态。")
        if proof.source_fingerprint != expected_source_fingerprint:
            raise AgentMemoryServiceError("producer 运行记忆的来源指纹不匹配。")
        if proof.dependency_fingerprint != expected_dependency_fingerprint:
            raise AgentMemoryServiceError("producer 运行记忆的依赖指纹不匹配。")
        return proof

    async def delete_run_memories(
        self,
        conversation_id: str,
        run_id: str,
    ) -> int:
        """解除目标运行的记忆所有权，并软删除不再属于其他运行的记忆。"""

        scoped_entries = await self._repository.query(
            conversation_id=conversation_id,
            run_id=run_id,
            include_deleted=True,
        )
        deleted_memory_ids = {
            entry.memory_id for entry in scoped_entries if entry.deleted_at is not None
        }
        changed_count = 0
        changed_at = memory_now_iso()
        for entry in scoped_entries:
            if entry.deleted_at is not None:
                continue
            remaining_run_ids = _deduplicate(
                [candidate for candidate in entry.run_ids if candidate != run_id]
            )
            if remaining_run_ids:
                await self._repository.save(
                    entry.model_copy(
                        update={
                            "run_ids": remaining_run_ids,
                            "updated_at": changed_at,
                        }
                    )
                )
            else:
                deleted = await self._repository.delete(
                    entry.memory_id,
                    deleted_at=changed_at,
                )
                if deleted is None:
                    continue
                deleted_memory_ids.add(entry.memory_id)
            changed_count += 1

        if deleted_memory_ids:
            retained_entries = await self._repository.query(
                conversation_id=conversation_id,
                include_deleted=False,
            )
            for entry in retained_entries:
                if entry.validity is not AgentMemoryValidity.ACTIVE:
                    continue
                deleted_dependency_id = next(
                    (
                        dependency.memory_id
                        for dependency in entry.dependencies
                        if dependency.memory_id in deleted_memory_ids
                        and dependency.relation in _PROPAGATING_RELATIONS
                    ),
                    None,
                )
                invalidated_by_memory_id = deleted_dependency_id or (
                    entry.supersedes_memory_id
                    if entry.supersedes_memory_id in deleted_memory_ids
                    else None
                )
                if invalidated_by_memory_id is None:
                    continue
                current = await self._repository.get(entry.memory_id)
                if (
                    current is None
                    or current.deleted_at is not None
                    or current.validity is not AgentMemoryValidity.ACTIVE
                ):
                    continue
                await self._transition_validity(
                    entry.memory_id,
                    validity=AgentMemoryValidity.STALE,
                    reason=(
                        "依赖的上游运行记忆 "
                        f"{invalidated_by_memory_id} 已随所属运行删除，"
                        "该结论需要根据当前有效产物重新生成。"
                    ),
                    invalidated_by_memory_id=invalidated_by_memory_id,
                )
        return changed_count

    async def delete_conversation_memories(self, conversation_id: str) -> int:
        entries = await self._repository.query(
            conversation_id=conversation_id,
            include_deleted=False,
        )
        for entry in entries:
            await self._repository.delete(entry.memory_id, deleted_at=memory_now_iso())
        return len(entries)

    async def record_clarification_request(
        self,
        run: GeneralAgentRun,
        *,
        request_id: str,
        plan: GeneralAgentExecutionPlan,
    ) -> str:
        if not plan.requires_clarification or not plan.clarification_question.strip():
            raise AgentMemoryServiceError("只有真实澄清计划才能记录待解决问题。")
        request_ref = _clarification_request_ref(run.run_id, request_id)
        entry = await self.write(
            MemoryWriteCandidate(
                kind=AgentMemoryKind.UNRESOLVED_ISSUE,
                content=f"澄清执行记录：等待回答；问题指纹={canonical_input_hash({'question': plan.clarification_question})}",
                source_refs=[request_ref],
                run_ids=[run.run_id],
                conversation_id=run.conversation_id,
                created_request_index=run.request_index,
                expires_after_request_index=run.request_index,
                retention_priority=90,
                producer_ref=request_ref,
                result_type="clarification_request",
            )
        )
        return entry.memory_id

    async def resolve_clarification(
        self,
        run: GeneralAgentRun,
        *,
        request_id: str,
        content: str,
    ) -> str:
        if not content.strip():
            raise AgentMemoryServiceError("澄清回答不能为空。")
        request_ref = _clarification_request_ref(run.run_id, request_id)
        unresolved = [
            entry
            for entry in await self._repository.query(
                conversation_id=run.conversation_id,
                kinds=(AgentMemoryKind.UNRESOLVED_ISSUE,),
                run_id=run.run_id,
                include_deleted=False,
            )
            if entry.producer_ref == request_ref
            and entry.validity is AgentMemoryValidity.ACTIVE
        ]
        if len(unresolved) != 1:
            raise AgentMemoryServiceError("澄清回答必须关联唯一且尚未解决的人工请求。")
        response_ref = f"run:{run.run_id}:clarification_response:{request_id}"
        entry = await self.write(
            MemoryWriteCandidate(
                kind=AgentMemoryKind.WORK_NOTE,
                content=f"澄清执行记录：已回答；回答指纹={canonical_input_hash({'answer': content})}",
                source_refs=[response_ref],
                run_ids=[run.run_id],
                conversation_id=run.conversation_id,
                created_request_index=run.request_index,
                expires_after_request_index=run.request_index,
                retention_priority=90,
                supersedes_memory_id=unresolved[0].memory_id,
                producer_ref=response_ref,
                result_type="clarification_response",
            )
        )
        return entry.memory_id

    async def record_node_results(
        self,
        run: GeneralAgentRun,
        nodes: list[GeneralAgentNodeRun],
    ) -> list[str]:
        memory_ids: list[str] = []
        node_by_id = {node.node_id: node for node in nodes}
        existing = await self._repository.query(
            conversation_id=run.conversation_id,
            include_deleted=False,
        )
        memory_by_producer = {
            entry.producer_ref: entry
            for entry in existing
            if entry.producer_ref is not None
        }
        for node in _topological_node_runs(nodes):
            result_type = _node_result_type(node)
            dependencies: list[AgentMemoryDependency] = []
            reuse_proof: ProducerMemoryValidityProof | None = None
            if node.reused_from_producer_ref is not None:
                if (
                    node.reused_source_fingerprint is None
                    or node.reused_dependency_fingerprint is None
                    or node.producer_validity_proof_sha256 is None
                ):
                    raise AgentMemoryServiceError(
                        "复用节点缺少完整 producer 有效性证明。"
                    )
                reuse_proof = await self.require_active_producer(
                    run.conversation_id,
                    node.reused_from_producer_ref,
                    expected_source_fingerprint=node.reused_source_fingerprint,
                    expected_dependency_fingerprint=(
                        node.reused_dependency_fingerprint
                    ),
                    current_request_index=run.request_index,
                )
                if (
                    producer_validity_proof_sha256(reuse_proof)
                    != node.producer_validity_proof_sha256
                ):
                    raise AgentMemoryServiceError(
                        "复用节点的 producer 有效性证明已经变化。"
                    )
            for dependency_node_id in node.dependencies:
                dependency_node = node_by_id.get(dependency_node_id)
                dependency_memory = memory_by_producer.get(
                    _node_producer_ref(run, dependency_node)
                    if dependency_node is not None
                    else ""
                )
                if dependency_memory is None:
                    continue
                dependencies.append(
                    AgentMemoryDependency(
                        memory_id=dependency_memory.memory_id,
                        relation=_dependency_relation(
                            node=node,
                            result_type=result_type,
                            dependency=dependency_memory,
                        ),
                    )
                )
            if reuse_proof is not None and all(
                dependency.memory_id != reuse_proof.memory_id
                for dependency in dependencies
            ):
                dependencies.append(
                    AgentMemoryDependency(
                        memory_id=reuse_proof.memory_id,
                        relation=AgentMemoryDependencyRelation.BASIS,
                    )
                )
            has_resource = bool(node.source_refs or node.artifact_refs)
            kind = (
                AgentMemoryKind.RESOURCE_SUMMARY
                if has_resource
                else AgentMemoryKind.WORK_NOTE
            )
            content = f"节点执行记录：{node.node_id}；状态：{node.status.value}；结果指纹：{canonical_input_hash(node.output)}"
            producer_ref = _node_producer_ref(run, node)
            existing_node_memory = memory_by_producer.get(producer_ref)
            if (
                existing_node_memory is not None
                and existing_node_memory.content_sha256
                == memory_content_sha256(content)
            ):
                memory_ids.append(existing_node_memory.memory_id)
                continue
            supersedes_memory_id = None
            if _is_revision_result(node, result_type):
                supersedes_memory_id = next(
                    (
                        dependency.memory_id
                        for dependency in dependencies
                        if dependency.relation
                        is AgentMemoryDependencyRelation.REPAIR_SOURCE
                        and (
                            existing_dependency := next(
                                (
                                    entry
                                    for entry in memory_by_producer.values()
                                    if entry.memory_id == dependency.memory_id
                                ),
                                None,
                            )
                        )
                        is not None
                        and existing_dependency.result_type in _CANDIDATE_RESULT_TYPES
                    ),
                    None,
                )
            source_refs = _deduplicate(node.source_refs)[:_MAX_MEMORY_SOURCE_REFS]
            artifact_refs = _deduplicate(node.artifact_refs)[:_MAX_MEMORY_ARTIFACT_REFS]
            evidence_anchors = (
                await self.resolve_evidence_anchors(
                    source_refs=source_refs,
                    artifact_refs=artifact_refs,
                )
            )[:_MAX_MEMORY_EVIDENCE_ANCHORS]
            entry = await self.write(
                MemoryWriteCandidate(
                    kind=kind,
                    content=content,
                    source_refs=source_refs,
                    artifact_refs=artifact_refs,
                    run_ids=[run.run_id],
                    conversation_id=run.conversation_id,
                    created_request_index=run.request_index,
                    expires_after_request_index=(
                        run.request_index + 5 if has_resource else run.request_index + 3
                    ),
                    retention_priority=60 if has_resource else 50,
                    supersedes_memory_id=supersedes_memory_id,
                    producer_ref=producer_ref,
                    result_type=result_type,
                    evidence_anchors=evidence_anchors,
                    dependencies=dependencies,
                )
            )
            memory_ids.append(entry.memory_id)
            if entry.producer_ref is not None:
                memory_by_producer[entry.producer_ref] = entry
            if _is_rejecting_review(node, result_type):
                review_targets = [
                    dependency.memory_id
                    for dependency in dependencies
                    if dependency.relation
                    is AgentMemoryDependencyRelation.REVIEW_TARGET
                ]
                for target_memory_id in review_targets:
                    await self.invalidate(
                        target_memory_id,
                        validity=AgentMemoryValidity.REJECTED,
                        reason=(
                            f"审查节点“{node.objective}”发现阻断性问题；"
                            "该内容保留用于修复，但不能再作为当前正文或结论。"
                        ),
                        invalidated_by_memory_id=entry.memory_id,
                        exclude_memory_ids={entry.memory_id},
                    )
        return memory_ids

    async def record_verification(
        self,
        run: GeneralAgentRun,
        verification: GeneralAgentVerification,
        *,
        basis_sha256: str,
    ) -> list[str]:
        active_node_memories = await self._active_run_node_memories(run)
        dependencies = [
            AgentMemoryDependency(
                memory_id=entry.memory_id,
                relation=AgentMemoryDependencyRelation.BASIS,
            )
            for entry in active_node_memories
        ]
        evidence_anchors = await self.resolve_evidence_anchors(
            source_refs=[
                source_ref
                for entry in active_node_memories
                for source_ref in entry.source_refs
            ],
            artifact_refs=[
                artifact_ref
                for entry in active_node_memories
                for artifact_ref in entry.artifact_refs
            ],
        )
        summary = await self.write(
            MemoryWriteCandidate(
                kind=AgentMemoryKind.TASK_SUMMARY,
                content=f"校验执行记录：{verification.outcome}；依据指纹：{basis_sha256}",
                source_refs=[
                    f"run:{run.run_id}:verification",
                    f"result-basis:{basis_sha256}",
                ],
                run_ids=[run.run_id],
                conversation_id=run.conversation_id,
                created_request_index=run.request_index,
                expires_after_request_index=run.request_index + 5,
                retention_priority=75,
                producer_ref=(f"verification:{run.run_id}:{run.plan_revision}:final"),
                result_type="verified_answer",
                evidence_anchors=evidence_anchors,
                dependencies=dependencies,
                validity=(
                    AgentMemoryValidity.REJECTED
                    if verification.outcome == "failed"
                    else AgentMemoryValidity.ACTIVE
                ),
                invalidation_reason=(
                    "最终校验仍未通过；该回答只保留为失败记录，"
                    "不能作为后续请求的当前结论。"
                    if verification.outcome == "failed"
                    else ""
                ),
            )
        )
        memory_ids = [summary.memory_id]
        return memory_ids

    async def record_rejected_verification(
        self,
        run: GeneralAgentRun,
        verification: GeneralAgentVerification,
    ) -> str:
        """保留失败校验作为修复信息，但不允许其充当当前任务结论。"""

        entry = await self.write(
            MemoryWriteCandidate(
                kind=AgentMemoryKind.WORK_NOTE,
                content=f"校验执行记录：被否决；产物指纹={canonical_input_hash(verification.model_dump(mode='json'))}",
                source_refs=[f"run:{run.run_id}:rejected_verification"],
                run_ids=[run.run_id],
                conversation_id=run.conversation_id,
                created_request_index=run.request_index,
                expires_after_request_index=run.request_index + 3,
                retention_priority=85,
                producer_ref=(
                    f"verification:{run.run_id}:{run.plan_revision}:"
                    f"replan:{run.replan_count + 1}"
                ),
                result_type="rejected_verification",
                dependencies=await self._run_node_dependencies(run),
                validity=AgentMemoryValidity.REJECTED,
                invalidation_reason=(
                    "该回答未通过编排校验，仅可作为后续修复线索，不能作为当前结论。"
                ),
            )
        )
        return entry.memory_id

    async def resolve_evidence_anchors(
        self,
        *,
        source_refs: list[str],
        artifact_refs: list[str],
    ) -> list[AgentMemoryEvidenceAnchor]:
        """冻结当前可解析来源的内容指纹，供后续自动判旧。"""
        if self._evidence_resolver is None:
            return []
        references = _deduplicate(
            [
                *source_refs,
                *[f"artifact:{artifact_id}" for artifact_id in artifact_refs],
            ]
        )
        fingerprints = await asyncio.gather(
            *[
                self._evidence_resolver.fingerprint(reference)
                for reference in references
            ]
        )
        return [
            AgentMemoryEvidenceAnchor(
                reference=reference,
                content_sha256=fingerprint,
            )
            for reference, fingerprint in zip(
                references,
                fingerprints,
                strict=True,
            )
            if fingerprint is not None
        ]

    async def _run_node_dependencies(
        self,
        run: GeneralAgentRun,
    ) -> list[AgentMemoryDependency]:
        entries = await self._active_run_node_memories(run)
        return [
            AgentMemoryDependency(
                memory_id=entry.memory_id,
                relation=AgentMemoryDependencyRelation.BASIS,
            )
            for entry in entries
        ]

    async def _active_run_node_memories(
        self,
        run: GeneralAgentRun,
    ) -> list[AgentMemoryEntry]:
        producer_refs = {
            _node_producer_ref(run, node) for node in _current_successful_nodes(run)
        }
        entries = await self._repository.query(
            conversation_id=run.conversation_id,
            include_deleted=False,
        )
        return [
            entry
            for entry in entries
            if entry.producer_ref in producer_refs
            and entry.validity is AgentMemoryValidity.ACTIVE
        ]

    async def _invalidate_if_dependency_not_current(
        self,
        entry: AgentMemoryEntry,
    ) -> AgentMemoryEntry:
        for dependency in entry.dependencies:
            if dependency.relation not in _PROPAGATING_RELATIONS:
                continue
            upstream = await self._repository.get(dependency.memory_id)
            if upstream is None or upstream.validity is AgentMemoryValidity.ACTIVE:
                continue
            updated = await self._transition_validity(
                entry.memory_id,
                validity=AgentMemoryValidity.STALE,
                reason=(
                    f"依赖的运行记忆 {dependency.memory_id} 已不再有效，"
                    "需要根据当前产物重新生成。"
                ),
                invalidated_by_memory_id=dependency.memory_id,
            )
            return updated or entry
        return entry

    async def _transition_validity(
        self,
        memory_id: str,
        *,
        validity: AgentMemoryValidity,
        reason: str,
        invalidated_by_memory_id: str | None = None,
        exclude_memory_ids: set[str] | None = None,
        visited_memory_ids: set[str] | None = None,
    ) -> AgentMemoryEntry | None:
        entry = await self._repository.get(memory_id)
        if entry is None or entry.deleted_at is not None:
            return None
        excluded = exclude_memory_ids or set()
        visited = visited_memory_ids or set()
        if memory_id in visited or memory_id in excluded:
            return entry
        visited.add(memory_id)
        now = memory_now_iso()
        prior_reason = entry.invalidation_reason.strip()
        combined_reason = reason.strip()
        if prior_reason and prior_reason != combined_reason:
            combined_reason = f"{combined_reason} 此前状态说明：{prior_reason}"
        updated = entry.model_copy(
            update={
                "validity": validity,
                "previous_validity": entry.previous_validity or entry.validity,
                "invalidated_at": now,
                "invalidation_reason": combined_reason,
                "invalidated_by_memory_id": invalidated_by_memory_id,
                "updated_at": now,
            }
        )
        updated = await self._repository.save(updated)
        dependents = await self._repository.query(
            conversation_id=entry.conversation_id,
            include_deleted=False,
        )
        for dependent in dependents:
            if (
                dependent.memory_id in excluded
                or dependent.memory_id in visited
                or dependent.validity is not AgentMemoryValidity.ACTIVE
            ):
                continue
            relation = next(
                (
                    dependency.relation
                    for dependency in dependent.dependencies
                    if dependency.memory_id == memory_id
                ),
                None,
            )
            if relation not in _PROPAGATING_RELATIONS:
                continue
            await self._transition_validity(
                dependent.memory_id,
                validity=AgentMemoryValidity.STALE,
                reason=(
                    f"上游运行记忆 {memory_id} 已变为“{validity.value}”，"
                    "该下游结论需要重新生成。"
                ),
                invalidated_by_memory_id=memory_id,
                exclude_memory_ids=excluded,
                visited_memory_ids=visited,
            )
        return updated

    async def _validate_supersession(self, candidate: MemoryWriteCandidate) -> None:
        superseded_id = candidate.supersedes_memory_id
        if superseded_id is None:
            return
        superseded = await self._repository.get(superseded_id)
        if superseded is None or superseded.deleted_at is not None:
            raise AgentMemoryServiceError("被替代的运行记忆不存在或已经删除。")
        if superseded.conversation_id != candidate.conversation_id:
            raise AgentMemoryServiceError("运行记忆不能替代其他会话中的记忆。")
        clarification_resolution = (
            candidate.kind is AgentMemoryKind.WORK_NOTE
            and candidate.result_type == "clarification_response"
            and superseded.kind is AgentMemoryKind.UNRESOLVED_ISSUE
            and candidate.producer_ref is not None
            and superseded.producer_ref is not None
            and candidate.producer_ref.replace(
                ":clarification_response:",
                ":clarification_request:",
            )
            == superseded.producer_ref
        )
        if superseded.kind is not candidate.kind and not clarification_resolution:
            raise AgentMemoryServiceError("运行记忆只能替代同一类型的旧记忆。")

    async def _validate_dependencies(
        self,
        candidate: MemoryWriteCandidate,
        dependencies: list[AgentMemoryDependency],
    ) -> None:
        for dependency in dependencies:
            entry = await self._repository.get(dependency.memory_id)
            if entry is None or entry.deleted_at is not None:
                raise AgentMemoryServiceError("运行记忆依赖不存在或已经删除。")
            if entry.conversation_id != candidate.conversation_id:
                raise AgentMemoryServiceError("运行记忆不能依赖其他会话中的记录。")

    def _validate_candidate(
        self,
        candidate: MemoryWriteCandidate,
        normalized: str,
    ) -> None:
        if not normalized:
            raise AgentMemoryServiceError("运行记忆内容不能为空。")
        if candidate.sensitivity is AgentMemorySensitivity.RESTRICTED:
            raise AgentMemoryServiceError("受限敏感内容不得写入运行记忆。")
        if (
            candidate.validity is not AgentMemoryValidity.ACTIVE
            and not candidate.invalidation_reason.strip()
        ):
            raise AgentMemoryServiceError("失效运行记忆必须说明失效原因。")
        if any(pattern.search(normalized) for pattern in _SECRET_PATTERNS):
            raise AgentMemoryServiceError(
                "运行记忆疑似包含密钥或鉴权信息，已拒绝写入。"
            )
        if (
            candidate.kind is AgentMemoryKind.FACT_REFERENCE
            and not candidate.source_refs
        ):
            raise AgentMemoryServiceError("事实引用记忆必须携带稳定来源。")


class AgentMemoryServiceError(ValueError):
    """自动运行记忆不符合安全写入或来源约束。"""


def _clarification_request_ref(run_id: str, request_id: str) -> str:
    normalized_request_id = request_id.strip()
    if not normalized_request_id:
        raise AgentMemoryServiceError("澄清请求标识不能为空。")
    return f"run:{run_id}:clarification_request:{normalized_request_id}"


def _later_request_expiry(left: int | None, right: int | None) -> int | None:
    if left is None or right is None:
        return None
    return max(left, right)


def _lexical_scores(
    entries: list[AgentMemoryEntry],
    *,
    query_text: str,
) -> dict[str, float]:
    """计算当前候选集的轻量词法相关度，不再维护平行 JSON 索引。"""

    query_terms = _lexical_terms(query_text)
    scores: dict[str, float] = {}
    for entry in entries:
        indexed_terms = _lexical_terms(
            " ".join(
                [
                    entry.kind.value,
                    entry.content,
                    *entry.source_refs,
                    *entry.artifact_refs,
                ]
            )
        )
        if not query_terms:
            scores[entry.memory_id] = 0.0
            continue
        intersection = len(query_terms & indexed_terms)
        union = len(query_terms | indexed_terms)
        scores[entry.memory_id] = intersection / union if union else 0.0
    return scores


def _lexical_terms(value: str) -> set[str]:
    terms: set[str] = set()
    for match in _WORD_PATTERN.findall(value.lower()):
        if all("\u4e00" <= character <= "\u9fff" for character in match):
            terms.update(match)
            terms.update(match[index : index + 2] for index in range(len(match) - 1))
        else:
            terms.add(match)
    return terms


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _deduplicate_evidence_anchors(
    anchors: list[AgentMemoryEvidenceAnchor],
) -> list[AgentMemoryEvidenceAnchor]:
    by_reference: dict[str, AgentMemoryEvidenceAnchor] = {}
    for anchor in anchors:
        by_reference[anchor.reference] = anchor
    return list(by_reference.values())


def _deduplicate_dependencies(
    dependencies: list[AgentMemoryDependency],
) -> list[AgentMemoryDependency]:
    by_key: dict[
        tuple[str, AgentMemoryDependencyRelation],
        AgentMemoryDependency,
    ] = {}
    for dependency in dependencies:
        by_key[(dependency.memory_id, dependency.relation)] = dependency
    return list(by_key.values())


def _topological_node_runs(
    nodes: list[GeneralAgentNodeRun],
) -> list[GeneralAgentNodeRun]:
    node_by_id = {node.node_id: node for node in nodes}
    pending = list(nodes)
    ordered: list[GeneralAgentNodeRun] = []
    completed: set[str] = set()
    while pending:
        ready = [
            node
            for node in pending
            if not (set(node.dependencies) & set(node_by_id)) - completed
        ]
        if not ready:
            return nodes
        for node in ready:
            ordered.append(node)
            completed.add(node.node_id)
            pending.remove(node)
    return ordered


def _node_producer_ref(
    run: GeneralAgentRun,
    node: GeneralAgentNodeRun,
) -> str:
    return f"node:{run.run_id}:{node.plan_revision}:{node.node_id}"


def _node_result_type(node: GeneralAgentNodeRun) -> str:
    artifact_type = node.output.get("artifact_type")
    if isinstance(artifact_type, str) and artifact_type.strip():
        return artifact_type.strip()
    return node.capability_name


def _dependency_relation(
    *,
    node: GeneralAgentNodeRun,
    result_type: str,
    dependency: AgentMemoryEntry,
) -> AgentMemoryDependencyRelation:
    if (
        result_type in _REVIEW_RESULT_TYPES
        or node.capability_name in _REVIEW_CAPABILITIES
    ):
        if dependency.result_type in _CANDIDATE_RESULT_TYPES:
            return AgentMemoryDependencyRelation.REVIEW_TARGET
        return AgentMemoryDependencyRelation.BASIS
    if _is_revision_result(node, result_type):
        if (
            dependency.result_type in _CANDIDATE_RESULT_TYPES
            or dependency.result_type in _REVIEW_RESULT_TYPES
        ):
            return AgentMemoryDependencyRelation.REPAIR_SOURCE
    return AgentMemoryDependencyRelation.BASIS


def _is_revision_result(node: GeneralAgentNodeRun, result_type: str) -> bool:
    return (
        result_type == "revision_candidate"
        or node.capability_name in _REVISION_CAPABILITIES
    )


def _is_rejecting_review(node: GeneralAgentNodeRun, result_type: str) -> bool:
    if (
        result_type not in _REVIEW_RESULT_TYPES
        and node.capability_name not in _REVIEW_CAPABILITIES
    ):
        return False
    issues = node.output.get("issues")
    if isinstance(issues, list):
        for issue in issues:
            if isinstance(issue, dict) and str(issue.get("severity", "")).lower() in {
                "critical",
                "major",
            }:
                return True
    verdict = str(node.output.get("verdict", "")).strip().lower()
    return verdict in {
        "failed",
        "rejected",
        "不通过",
        "未通过",
        "存在严重冲突",
    }


def _current_successful_nodes(
    run: GeneralAgentRun,
) -> list[GeneralAgentNodeRun]:
    from taichu.application.general_agent.models import GeneralAgentNodeStatus

    return [
        node
        for node in run.node_runs
        if node.plan_revision == run.plan_revision
        and node.status is GeneralAgentNodeStatus.SUCCESS
    ]
