"""LangGraph 官方 MongoDB Checkpointer 的真实持久化合同。"""

from __future__ import annotations

from typing import TypedDict
from uuid import uuid4

import pytest
from langgraph.checkpoint.mongodb import MongoDBSaver
from langgraph.graph import END, START, StateGraph
from pymongo import MongoClient

from taichu.config import settings


class _State(TypedDict):
    value: int


@pytest.mark.anyio
async def test_official_mongodb_saver_persists_and_deletes_thread() -> None:
    database_name = f"taichu_test_checkpoint_{uuid4().hex}"
    thread_id = f"checkpoint-test-{uuid4().hex}"
    client = MongoClient(
        settings.mongodb_uri,
        tz_aware=True,
        serverSelectionTimeoutMS=5_000,
    )
    try:
        saver = MongoDBSaver(
            client,
            db_name=database_name,
            checkpoint_collection_name="langgraph_checkpoints",
            writes_collection_name="langgraph_checkpoint_writes",
        )
        graph = StateGraph(_State)
        graph.add_node("increment", lambda state: {"value": state["value"] + 1})
        graph.add_edge(START, "increment")
        graph.add_edge("increment", END)
        compiled = graph.compile(checkpointer=saver)
        config = {"configurable": {"thread_id": thread_id}}

        result = await compiled.ainvoke({"value": 1}, config)

        assert result == {"value": 2}
        latest = await saver.aget_tuple(config)
        assert latest is not None
        assert latest.config["configurable"]["thread_id"] == thread_id
        history = [item async for item in saver.alist(config)]
        assert len(history) >= 2

        await saver.adelete_thread(thread_id)
        assert await saver.aget_tuple(config) is None
    finally:
        client.drop_database(database_name)
        client.close()
