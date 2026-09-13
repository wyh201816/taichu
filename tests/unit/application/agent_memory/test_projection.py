"""需求 14.2—14.11：当前事实与修复事实的类型化投影。"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from typing import TypeVar

from taichu.application.agent_memory.models import (
    AgentMemoryDependencyRelation,
    AgentMemoryKind,
    AgentMemoryValidity,
    MemoryWriteCandidate,
)
from taichu.application.agent_memory.projection import (
    CurrentFactProjectionPolicy,
    MemoryProjectionCandidate,
    RepairProjection,
)
from taichu.application.general_agent.memory_policy import AgentMemoryPolicy
from taichu.application.general_agent.context import ContextAssembler
from taichu.application.general_agent.models import GeneralAgentRun
from taichu.application.services.agent_memory_service import AgentMemoryService
from tests.fakes.agent_memory import in_memory_agent_memory_repository

_ResultT = TypeVar("_ResultT")


def _run(awaitable: Coroutine[object, object, _ResultT]) -> _ResultT:
    return asyncio.run(awaitable)


def _service(root: Path) -> AgentMemoryService:
    return AgentMemoryService(
        repository=in_memory_agent_memory_repository(root),
        policy=AgentMemoryPolicy(),
    )


def _candidate(content: str, producer_ref: str) -> MemoryWriteCandidate:
    return MemoryWriteCandidate(
        kind=AgentMemoryKind.WORK_NOTE,
        content=content,
        run_ids=["general_run_fixture"],
        conversation_id="conversation_fixture",
        created_request_index=1,
        producer_ref=producer_ref,
    )


def test_current_fact_projection_only_accepts_active_scoped_basis_and_review_target(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = _service(tmp_path)
        basis = await service.write(
            _candidate("本轮事实依据", "node:run_current:2:basis")
        )
        review_target = await service.write(
            _candidate("本轮审查目标", "node:run_current:2:review_target")
        )
        repair_source = await service.write(
            _candidate("只供修复参考", "node:run_current:2:repair_source")
        )
        other_branch = await service.write(
            _candidate("无关并行分支", "node:run_current:2:parallel_other")
        )
        stale = await service.write(
            _candidate("上一轮已过时结论", "node:run_previous:1:stale")
        )
        await service.invalidate(
            stale.memory_id,
            validity=AgentMemoryValidity.STALE,
            reason="来源指纹已变化。",
        )

        projection = CurrentFactProjectionPolicy().project(
            (
                MemoryProjectionCandidate(
                    entry=basis,
                    role=AgentMemoryDependencyRelation.BASIS,
                ),
                MemoryProjectionCandidate(
                    entry=review_target,
                    role=AgentMemoryDependencyRelation.REVIEW_TARGET,
                ),
                MemoryProjectionCandidate(
                    entry=repair_source,
                    role=AgentMemoryDependencyRelation.REPAIR_SOURCE,
                ),
                MemoryProjectionCandidate(
                    entry=other_branch,
                    role=AgentMemoryDependencyRelation.BASIS,
                ),
                MemoryProjectionCandidate(
                    entry=await service.get(stale.memory_id),
                    role=AgentMemoryDependencyRelation.BASIS,
                ),
            ),
            allowed_producer_refs=frozenset(
                {
                    "node:run_current:2:basis",
                    "node:run_current:2:review_target",
                    "node:run_current:2:repair_source",
                }
            ),
        )

        assert {item.memory_id for item in projection.items} == {
            basis.memory_id,
            review_target.memory_id,
        }
        assert all(
            item.validity is AgentMemoryValidity.ACTIVE for item in projection.items
        )
        assert all(item.repair_only is False for item in projection.items)

    _run(scenario())


def test_repair_projection_isolatedly_exposes_all_invalid_states_and_provenance(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = _service(tmp_path)
        stale = await service.write(
            _candidate("已过时内容", "node:run_previous:1:stale")
        )
        rejected = await service.write(
            _candidate("已否决内容", "node:run_previous:1:rejected")
        )
        superseded = await service.write(
            _candidate("被替代内容", "node:run_previous:1:superseded")
        )
        replacement = await service.write(
            _candidate(
                "替代后的当前内容",
                "node:run_current:2:replacement",
            ).model_copy(update={"supersedes_memory_id": superseded.memory_id})
        )
        await service.invalidate(
            stale.memory_id,
            validity=AgentMemoryValidity.STALE,
            reason="来源指纹已变化。",
        )
        await service.invalidate(
            rejected.memory_id,
            validity=AgentMemoryValidity.REJECTED,
            reason="审查明确否决。",
        )
        stale = await service.get(stale.memory_id)
        rejected = await service.get(rejected.memory_id)
        superseded = await service.get(superseded.memory_id)
        assert stale is not None and rejected is not None and superseded is not None

        projection = RepairProjection.from_candidates(
            tuple(
                MemoryProjectionCandidate(
                    entry=entry,
                    role=AgentMemoryDependencyRelation.REPAIR_SOURCE,
                )
                for entry in (stale, rejected, superseded, replacement)
            ),
            allowed_producer_refs=frozenset(
                {
                    "node:run_previous:1:stale",
                    "node:run_previous:1:rejected",
                    "node:run_previous:1:superseded",
                    "node:run_current:2:replacement",
                }
            ),
        )

        assert {item.current_validity for item in projection.items} == {
            AgentMemoryValidity.STALE,
            AgentMemoryValidity.REJECTED,
            AgentMemoryValidity.SUPERSEDED,
        }
        assert all(
            item.previous_validity is AgentMemoryValidity.ACTIVE
            for item in projection.items
        )
        assert all(item.repair_only is True for item in projection.items)
        assert all(item.transition_reason for item in projection.items)
        assert all(item.state_hash for item in projection.items)
        assert replacement.memory_id not in {
            item.memory_id for item in projection.items
        }

    _run(scenario())


def test_context_snapshot_keeps_current_and_repair_projections_separate(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = _service(tmp_path)
        active = await service.write(
            _candidate("本轮有效结论", "node:run_current:2:active")
        )
        stale = await service.write(
            _candidate("上一轮污染文本", "node:run_previous:1:stale")
        )
        await service.invalidate(
            stale.memory_id,
            validity=AgentMemoryValidity.STALE,
            reason="上一轮来源已变化。",
        )
        timestamp = "2026-07-27T01:02:03Z"
        run = GeneralAgentRun(
            run_id="general_run_20260727_010203_abcdef",
            task_id="task_fixture",
            conversation_id="conversation_fixture",
            request_index=1,
            user_goal="继续分析当前线索。",
            created_at=timestamp,
            updated_at=timestamp,
            started_at=timestamp,
        )

        from tests.unit.application.general_agent.test_context_pipeline import engine

        pipeline = engine(tmp_path, [])
        pipeline.memory_service = service
        run = await pipeline.prepare(run, extract=False)
        snapshot = (
            await ContextAssembler(memory_service=service).assemble(
                run,
                phase="plan",
            )
        ).snapshot
        working = snapshot.envelope.working_memory

        current_by_id = working.session_memory.workflow_state
        repair_by_id = {item.memory_id: item for item in working.invalidated_memories}
        assert active.memory_id in current_by_id
        assert stale.memory_id not in current_by_id
        assert current_by_id[active.memory_id].source_ids == [active.memory_id]
        assert stale.memory_id in repair_by_id
        assert repair_by_id[stale.memory_id].projection_role == "repair_source"
        assert repair_by_id[stale.memory_id].repair_only is True
        assert repair_by_id[stale.memory_id].previous_validity == "active"

    _run(scenario())
