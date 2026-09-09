"""LangGraph 官方 MongoDB Store 的运行记忆持久化合同。"""

from __future__ import annotations

from uuid import uuid4

import pytest
from langgraph.store.mongodb import MongoDBStore
from pymongo import MongoClient

from taichu.application.agent_memory.models import (
    AgentMemoryKind,
    MemoryWriteCandidate,
)
from taichu.application.services.agent_memory_service import AgentMemoryService
from taichu.config import settings
from taichu.infrastructure.agent_memory import LangGraphAgentMemoryRepository


@pytest.mark.anyio
async def test_official_mongodb_store_persists_memory_across_instances() -> None:
    database_name = f"taichu_test_store_{uuid4().hex}"
    collection_name = "langgraph_store"
    client = MongoClient(
        settings.mongodb_uri,
        tz_aware=True,
        serverSelectionTimeoutMS=5_000,
    )
    try:
        database = client[database_name]
        database.create_collection(collection_name)
        first_store = MongoDBStore(database[collection_name])
        first_repository = LangGraphAgentMemoryRepository(first_store)
        service = AgentMemoryService(repository=first_repository)
        entry = await service.write(
            MemoryWriteCandidate(
                kind=AgentMemoryKind.WORK_NOTE,
                content="已完成当前章节的视角一致性检查。",
                run_ids=["general_run_store_test"],
                conversation_id="conversation_store_test",
                created_request_index=1,
            )
        )

        reloaded_store = MongoDBStore(database[collection_name])
        reloaded_repository = LangGraphAgentMemoryRepository(reloaded_store)

        assert await reloaded_repository.get(entry.memory_id) == entry
        assert await reloaded_repository.query(
            conversation_id=entry.conversation_id,
            run_id="general_run_store_test",
        ) == [entry]
        stored = database[collection_name].find_one({"key": entry.memory_id})
        assert stored is not None
        assert stored["namespace"] == [
            "taichu",
            "general_agent_memory",
            entry.conversation_id,
        ]
    finally:
        client.drop_database(database_name)
        client.close()
