"""自动运行记忆与五层上下文预算测试。"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from typing import TypeVar


from taichu.application.agent_memory.models import (
    AgentMemoryDependency,
    AgentMemoryDependencyRelation,
    AgentMemoryEntry,
    AgentMemoryEvidenceAnchor,
    AgentMemoryKind,
    AgentMemoryQuery,
    AgentMemoryValidity,
    MemoryWriteCandidate,
)
from taichu.application.general_agent.context import (
    ContextAssembler,
)
from taichu.application.general_agent.models import (
    GeneralAgentMessage,
    GeneralAgentNodeKind,
    GeneralAgentNodeRun,
    GeneralAgentNodeStatus,
    GeneralAgentRun,
    GeneralAgentScope,
)
from taichu.application.general_agent.request_analysis import (
    explicit_chapter_orders,
    is_explicit_chapter_content_request,
    recent_chapter_count,
)
from taichu.application.general_agent.service import _chapter_source_quality_issues
from taichu.application.services.agent_memory_service import (
    AgentMemoryService,
)
from tests.fakes.agent_memory import in_memory_agent_memory_repository
from taichu.infrastructure.long_term_memory import MarkdownLongTermMemoryRetriever

_ResultT = TypeVar("_ResultT")


def _run(awaitable: Coroutine[object, object, _ResultT]) -> _ResultT:
    return asyncio.run(awaitable)


def test_memory_is_automatic_isolated_relevant_and_request_expiring(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = _memory_service(tmp_path)
        instruction = await _write(
            service,
            conversation_id="conversation_a",
            kind=AgentMemoryKind.USER_INSTRUCTION,
            content="叙事视角使用第三人称限知。",
            request_index=1,
        )
        resource = await _write(
            service,
            conversation_id="conversation_a",
            kind=AgentMemoryKind.RESOURCE_SUMMARY,
            content="第六章摘要：秦阳发现新的冲突线索。",
            request_index=1,
            expires_after_request_index=3,
        )
        await _write(
            service,
            conversation_id="conversation_b",
            kind=AgentMemoryKind.USER_INSTRUCTION,
            content="另一会话使用第一人称。",
            request_index=1,
        )

        query = AgentMemoryQuery(
            conversation_id="conversation_a",
            current_request_index=2,
            query_text="第六章冲突采用什么叙事视角",
        )
        first = await service.retrieve(query)
        second = await service.retrieve(query)
        assert first.selected_memory_ids == second.selected_memory_ids
        assert instruction.memory_id in first.selected_memory_ids
        assert resource.memory_id in first.selected_memory_ids
        assert all(item.conversation_id == "conversation_a" for item in first.entries)
        assert all("lifecycle" not in item.model_dump() for item in first.entries)

        expired = await service.retrieve(
            query.model_copy(update={"current_request_index": 4})
        )
        assert resource.memory_id not in expired.selected_memory_ids
        assert instruction.memory_id in expired.selected_memory_ids

    _run(scenario())


def test_task_summary_is_not_repeated_in_working_and_history_memory(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = _memory_service(tmp_path)
        await _write(
            service,
            conversation_id="conversation_long",
            kind=AgentMemoryKind.TASK_SUMMARY,
            content="请求：上一轮问题\n结果：上一轮模型回答",
            request_index=1,
        )
        result = await ContextAssembler(memory_service=service).assemble(
            _long_run(round_count=2),
            phase="plan",
        )

        envelope = result.snapshot.envelope
        assert envelope.history_memory.messages
        assert all(
            memory.kind != AgentMemoryKind.TASK_SUMMARY.value
            for memory in envelope.working_memory.memories
        )

    _run(scenario())


def test_context_assembler_retrieves_markdown_long_term_memory_each_phase(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        path = tmp_path / "long_term_memory.md"
        path.write_text(
            "## 战斗偏好\n关键词：战斗\n\n战斗场景使用短句。\n",
            encoding="utf-8",
        )
        assembler = ContextAssembler(
            memory_service=_memory_service(tmp_path),
            long_term_memory_retriever=MarkdownLongTermMemoryRetriever(path),
        )
        run = _long_run(round_count=1).model_copy(
            update={"user_goal": "写一段战斗场景"}
        )

        first = await assembler.assemble(run, phase="plan")
        assert [item.content for item in first.snapshot.envelope.long_term_memory] == [
            "战斗偏好\n战斗场景使用短句。"
        ]

        path.write_text(
            "## 战斗偏好\n关键词：战斗\n\n战斗场景使用短句，减少解释。\n",
            encoding="utf-8",
        )
        repeated = await assembler.assemble(
            run.model_copy(update={"context_snapshot": first.snapshot}),
            phase="plan",
        )
        assert repeated.reused_snapshot is False
        assert repeated.resume_differences == ("按当前请求召回的长期记忆已经变化。",)

    _run(scenario())


def test_rejected_memory_is_retained_but_removed_from_current_context(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = _memory_service(tmp_path)
        draft = await _write(
            service,
            conversation_id="conversation_validity",
            kind=AgentMemoryKind.RESOURCE_SUMMARY,
            content="候选正文第一版。",
            request_index=1,
        )
        review = await service.write(
            MemoryWriteCandidate(
                kind=AgentMemoryKind.WORK_NOTE,
                content="审查发现人物状态冲突。",
                run_ids=["run_review"],
                conversation_id="conversation_validity",
                created_request_index=1,
                dependencies=[
                    AgentMemoryDependency(
                        memory_id=draft.memory_id,
                        relation=AgentMemoryDependencyRelation.REVIEW_TARGET,
                    )
                ],
            )
        )
        await service.invalidate(
            draft.memory_id,
            validity=AgentMemoryValidity.REJECTED,
            reason="审查发现阻断性问题。",
            invalidated_by_memory_id=review.memory_id,
            exclude_memory_ids={review.memory_id},
        )

        active = await service.list_active(
            "conversation_validity",
            current_request_index=1,
        )
        invalidated = await service.list_invalidated(
            "conversation_validity",
            current_request_index=1,
        )
        draft_after = await service.get(draft.memory_id)
        review_after = await service.get(review.memory_id)

        assert draft_after is not None
        assert draft_after.validity is AgentMemoryValidity.REJECTED
        assert review_after is not None
        assert review_after.validity is AgentMemoryValidity.ACTIVE
        assert draft.memory_id not in {entry.memory_id for entry in active}
        assert draft.memory_id in {entry.memory_id for entry in invalidated}

        context = await ContextAssembler(memory_service=service).assemble(
            _long_run(round_count=2).model_copy(
                update={
                    "conversation_id": "conversation_validity",
                    "task_id": "conversation_validity",
                }
            ),
            phase="plan",
        )
        working = context.snapshot.envelope.working_memory
        assert draft.memory_id not in {memory.memory_id for memory in working.memories}
        assert draft.memory_id in {
            memory.memory_id for memory in working.invalidated_memories
        }

    _run(scenario())


def test_revision_supersedes_rejected_draft_without_invalidating_revision(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = _memory_service(tmp_path)
        run = _long_run(round_count=2).model_copy(
            update={
                "conversation_id": "conversation_revision",
                "task_id": "conversation_revision",
                "node_runs": [],
            }
        )
        draft = GeneralAgentNodeRun(
            node_id="draft",
            plan_revision=1,
            kind=GeneralAgentNodeKind.SUBAGENT,
            capability_name="drafting",
            objective="生成候选正文。",
            status=GeneralAgentNodeStatus.SUCCESS,
            output={
                "artifact_type": "manuscript_candidate",
                "text": "候选正文第一版。",
            },
            artifact_refs=["artifact_draft"],
        )
        review = GeneralAgentNodeRun(
            node_id="review",
            plan_revision=1,
            kind=GeneralAgentNodeKind.SUBAGENT,
            capability_name="consistency_reviewer",
            objective="审查候选正文。",
            dependencies=["draft"],
            status=GeneralAgentNodeStatus.SUCCESS,
            output={
                "artifact_type": "consistency_review",
                "verdict": "未通过",
                "issues": [
                    {
                        "severity": "major",
                        "problem": "人物状态冲突。",
                    }
                ],
            },
            artifact_refs=["artifact_review"],
        )
        revision = GeneralAgentNodeRun(
            node_id="revision",
            plan_revision=1,
            kind=GeneralAgentNodeKind.SUBAGENT,
            capability_name="revision",
            objective="根据审查意见修订正文。",
            dependencies=["draft", "review"],
            status=GeneralAgentNodeStatus.SUCCESS,
            output={
                "artifact_type": "revision_candidate",
                "text": "候选正文第二版。",
            },
            artifact_refs=["artifact_revision"],
        )

        memory_ids = await service.record_node_results(
            run,
            [draft, review, revision],
        )
        entries = [
            entry
            for memory_id in memory_ids
            if (entry := await service.get(memory_id)) is not None
        ]
        by_result_type = {entry.result_type: entry for entry in entries}

        assert (
            by_result_type["manuscript_candidate"].validity
            is AgentMemoryValidity.SUPERSEDED
        )
        assert (
            by_result_type["consistency_review"].validity is AgentMemoryValidity.STALE
        )
        assert (
            by_result_type["revision_candidate"].validity is AgentMemoryValidity.ACTIVE
        )
        assert (
            by_result_type["revision_candidate"].supersedes_memory_id
            == by_result_type["manuscript_candidate"].memory_id
        )
        from tests.unit.application.general_agent.test_context_pipeline import engine

        projected = await engine(tmp_path, []).prepare(
            run.model_copy(update={"node_runs": [draft, review, revision]}),
            extract=False,
        )
        context = await ContextAssembler(memory_service=service).assemble(
            projected,
            phase="verify",
        )
        assert [
            item["node_id"]
            for item in context.snapshot.envelope.working_memory.node_summaries
        ] == ["revision"]

        repeated_ids = await service.record_node_results(
            run,
            [draft, review, revision],
        )
        repeated_entries = [
            entry
            for memory_id in repeated_ids
            if (entry := await service.get(memory_id)) is not None
        ]
        assert repeated_ids == memory_ids
        assert {entry.result_type: entry.validity for entry in repeated_entries} == {
            "manuscript_candidate": AgentMemoryValidity.SUPERSEDED,
            "consistency_review": AgentMemoryValidity.STALE,
            "revision_candidate": AgentMemoryValidity.ACTIVE,
        }

    _run(scenario())


def test_node_memory_caps_large_source_reference_sets(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = _memory_service(tmp_path)
        run = _long_run(round_count=2).model_copy(
            update={
                "conversation_id": "conversation_many_sources",
                "task_id": "conversation_many_sources",
                "node_runs": [],
            }
        )
        node = GeneralAgentNodeRun(
            node_id="summarize_many_chapters",
            plan_revision=1,
            kind=GeneralAgentNodeKind.SUBAGENT,
            capability_name="narrative_summary",
            objective="归纳大量章节。",
            status=GeneralAgentNodeStatus.SUCCESS,
            output={"summary": "已完成归纳。"},
            source_refs=[f"manuscript:chapter_{index}:0-100" for index in range(223)],
        )

        memory_ids = await service.record_node_results(run, [node])
        entry = await service.get(memory_ids[0])

        assert entry is not None
        assert len(entry.source_refs) == 100
        assert entry.source_refs[0] == "manuscript:chapter_0:0-100"
        assert entry.source_refs[-1] == "manuscript:chapter_99:0-100"

    _run(scenario())


def test_changed_evidence_marks_memory_and_basis_dependents_stale(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        resolver = _MutableEvidenceResolver()
        service = AgentMemoryService(
            repository=in_memory_agent_memory_repository(tmp_path),
            evidence_resolver=resolver,
        )
        source = await service.write(
            MemoryWriteCandidate(
                kind=AgentMemoryKind.RESOURCE_SUMMARY,
                content="第十一章正文取证结果。",
                source_refs=["manuscript:chapter_011:0-100"],
                run_ids=["run_evidence"],
                conversation_id="conversation_evidence",
                created_request_index=1,
                evidence_anchors=[
                    AgentMemoryEvidenceAnchor(
                        reference="manuscript:chapter_011:0-100",
                        content_sha256="a" * 64,
                    )
                ],
            )
        )
        conclusion = await service.write(
            MemoryWriteCandidate(
                kind=AgentMemoryKind.WORK_NOTE,
                content="基于第十一章得出的阶段结论。",
                run_ids=["run_evidence"],
                conversation_id="conversation_evidence",
                created_request_index=1,
                dependencies=[
                    AgentMemoryDependency(
                        memory_id=source.memory_id,
                        relation=AgentMemoryDependencyRelation.BASIS,
                    )
                ],
            )
        )
        resolver.fingerprints["manuscript:chapter_011:0-100"] = "b" * 64

        await service.refresh_evidence_validity("conversation_evidence")
        source_after = await service.get(source.memory_id)
        conclusion_after = await service.get(conclusion.memory_id)

        assert source_after is not None
        assert source_after.validity is AgentMemoryValidity.STALE
        assert conclusion_after is not None
        assert conclusion_after.validity is AgentMemoryValidity.STALE

    _run(scenario())


def test_current_request_keeps_original_whitespace(tmp_path: Path) -> None:
    run = _long_run(round_count=2).model_copy(
        update={"user_goal": "  保留首尾空格和换行\n"}
    )
    result = _run(
        ContextAssembler(memory_service=_memory_service(tmp_path)).assemble(
            run,
            phase="plan",
        )
    )
    assert result.snapshot.envelope.current_request.content == run.user_goal


def test_legacy_run_groups_by_task_id_and_derives_request_index() -> None:
    run = _long_run(round_count=5)
    payload = run.model_dump(mode="json")
    payload.pop("conversation_id")
    payload.pop("request_index")
    migrated = GeneralAgentRun.model_validate(payload)
    assert migrated.conversation_id == migrated.task_id
    assert migrated.request_index == 3


def test_explicit_chapter_reference_supports_arabic_chinese_and_ranges() -> None:
    assert explicit_chapter_orders("正文第8章讲的什么") == [8]
    assert explicit_chapter_orders("总结第八章") == [8]
    assert explicit_chapter_orders("概括第8到10章") == [8, 9, 10]
    assert is_explicit_chapter_content_request("正文第8章讲的什么") is True
    assert is_explicit_chapter_content_request("设计第8章的新冲突") is False
    assert recent_chapter_count("请检查最近20章的内容") == 20
    assert recent_chapter_count("请检查最近二十章的内容") == 20
    assert recent_chapter_count("请检查当前章节") is None


def test_explicit_chapter_content_requires_manuscript_source() -> None:
    run = _long_run(round_count=2).model_copy(
        update={
            "user_goal": "正文第8章讲的什么",
            "plan_revision": 1,
            "node_runs": [
                GeneralAgentNodeRun(
                    node_id="canon_chapter",
                    plan_revision=1,
                    kind=GeneralAgentNodeKind.SUBAGENT,
                    capability_name="canon_evidence",
                    objective="概括第8章",
                    status=GeneralAgentNodeStatus.SUCCESS,
                    source_refs=[],
                )
            ],
        }
    )
    assert _chapter_source_quality_issues(run)

    sourced = run.model_copy(
        update={
            "node_runs": [
                run.node_runs[0].model_copy(
                    update={"source_refs": ["manuscript:chapter_008:0-1200"]}
                )
            ]
        }
    )
    assert _chapter_source_quality_issues(sourced) == []


def _memory_service(root: Path) -> AgentMemoryService:
    return AgentMemoryService(
        repository=in_memory_agent_memory_repository(root),
    )


class _MutableEvidenceResolver:
    def __init__(self) -> None:
        self.fingerprints = {
            "manuscript:chapter_011:0-100": "a" * 64,
        }

    async def fingerprint(self, reference: str) -> str | None:
        return self.fingerprints.get(reference)


async def _write(
    service: AgentMemoryService,
    *,
    conversation_id: str,
    kind: AgentMemoryKind,
    content: str,
    request_index: int,
    expires_after_request_index: int | None = None,
) -> AgentMemoryEntry:
    return await service.write(
        MemoryWriteCandidate(
            kind=kind,
            content=content,
            source_refs=["run:source:auto"],
            run_ids=["run_source"],
            conversation_id=conversation_id,
            created_request_index=request_index,
            expires_after_request_index=expires_after_request_index,
            retention_priority=80,
        )
    )


def _long_run(*, round_count: int = 30) -> GeneralAgentRun:
    timestamp = "2026-07-19T01:01:01Z"
    messages = [
        GeneralAgentMessage(
            role="user" if index % 2 == 0 else "assistant",
            content=f"第 {index + 1} 次内容：" + "叙事上下文" * 30,
            created_at=f"2026-07-19T01:{index:02d}:01Z",
        )
        for index in range(round_count)
    ]
    nodes = [
        GeneralAgentNodeRun(
            node_id=f"node_{index}",
            plan_revision=1,
            kind=GeneralAgentNodeKind.TOOL,
            capability_name="read_manuscript",
            objective=f"读取并检查第 {index + 1} 个范围。",
            status=GeneralAgentNodeStatus.SUCCESS,
            output={"content": "节点完整输出" * 150},
            source_refs=[f"chapter:chapter_{index + 1}"],
        )
        for index in range(20)
    ]
    return GeneralAgentRun(
        run_id="general_run_20260719_010101_abcdef",
        task_id="conversation_long",
        conversation_id="conversation_long",
        request_index=max(1, (round_count + 1) // 2),
        user_goal="把当前章节统一成第三人称限知视角。",
        author_constraints=["不得改变秦阳的姓名"],
        scope=GeneralAgentScope(
            scope_type="chapter",
            current_chapter_id="chapter_001",
            chapter_ids=["chapter_001"],
            direct_context="正文直接上下文" * 500,
        ),
        messages=messages,
        plan_revision=1,
        node_runs=nodes,
        created_at=timestamp,
        updated_at=timestamp,
        started_at=timestamp,
    )
