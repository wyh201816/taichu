"""LangGraph Store 自动运行记忆仓储测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from taichu.application.agent_memory.models import (
    AgentMemoryKind,
    AgentMemoryQuery,
    MemoryWriteCandidate,
)
from taichu.application.services.agent_memory_service import AgentMemoryService
from tests.fakes.agent_memory import in_memory_agent_memory_repository


def test_repository_uses_official_store_and_preserves_memory_policy(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        repository = in_memory_agent_memory_repository(tmp_path)
        service = AgentMemoryService(repository=repository)
        entry = await service.write(
            MemoryWriteCandidate(
                kind=AgentMemoryKind.WORK_NOTE,
                content="第三人称视角检查已经完成。",
                source_refs=["run:source:node"],
                run_ids=["run_source"],
                conversation_id="conversation_store",
                created_request_index=1,
                expires_after_request_index=3,
                expires_at="2026-07-20T00:00:00Z",
            )
        )
        assert await repository.get(entry.memory_id) == entry
        assert "lifecycle" not in entry.model_dump(mode="json")
        stored = await repository.store.aget(
            ("taichu", "general_agent_memory", "conversation_store"),
            entry.memory_id,
        )
        assert stored is not None
        selection = await service.retrieve(
            AgentMemoryQuery(
                conversation_id="conversation_store",
                current_request_index=1,
                query_text="第三人称视角",
                as_of="2026-07-19T00:00:00Z",
            ),
            refresh_evidence=False,
        )
        assert selection.selected_memory_ids == [entry.memory_id]

        purged = await repository.purge_expired(as_of="2026-07-21T00:00:00Z")
        assert purged == 1
        assert await repository.query(conversation_id="conversation_store") == []
        deleted = await repository.query(
            conversation_id="conversation_store",
            include_deleted=True,
        )
        assert deleted[0].deleted_at == "2026-07-21T00:00:00Z"

    asyncio.run(scenario())


