"""任务级 Tool 调用预算的真实 MongoDB 原子性测试。"""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from uuid import uuid4

from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError

from taichu.application.contracts.general_agent_tool_budget import (
    GeneralAgentToolBudgetClaim,
    GeneralAgentToolBudgetClaimConflictError,
    GeneralAgentToolBudgetExceededError,
    GeneralAgentToolBudgetLimitConflictError,
    GeneralAgentToolBudgetOwner,
)
from taichu.config import settings
from taichu.infrastructure.general_agent_runs import (
    MongoGeneralAgentToolBudgetRepository,
)


class MongoGeneralAgentToolBudgetRepositoryIntegrationTest(
    unittest.IsolatedAsyncioTestCase
):
    async def asyncSetUp(self) -> None:
        self.database_name = f"taichu_test_tool_budget_{uuid4().hex}"
        self.first_client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            settings.mongodb_uri,
            tz_aware=True,
            serverSelectionTimeoutMS=1_000,
        )
        self.second_client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            settings.mongodb_uri,
            tz_aware=True,
            serverSelectionTimeoutMS=1_000,
        )
        try:
            await self.first_client.admin.command("ping")
            await self.second_client.admin.command("ping")
        except PyMongoError as error:
            await self.first_client.close()
            await self.second_client.close()
            raise unittest.SkipTest(f"本地 MongoDB 不可用：{error}") from error
        self.first = MongoGeneralAgentToolBudgetRepository(
            settings.mongodb_uri,
            self.database_name,
            client=self.first_client,
        )
        self.second = MongoGeneralAgentToolBudgetRepository(
            settings.mongodb_uri,
            self.database_name,
            client=self.second_client,
        )

    async def asyncTearDown(self) -> None:
        if not self.database_name.startswith("taichu_test_tool_budget_"):
            raise AssertionError("测试数据库前缀校验失败。")
        await self.first_client.drop_database(self.database_name)
        await self.first_client.close()
        await self.second_client.close()

    async def test_parallel_claims_compete_for_one_remaining_slot(self) -> None:
        owner = GeneralAgentToolBudgetOwner(
            conversation_id="conversation-budget-integration",
            run_id="general_run_20260830_000000_mongo1",
        )
        await self.first.initialize(owner, 1)
        await self.first.aclose()
        await self.first_client.admin.command("ping")

        results = await asyncio.gather(
            self.first.claim(owner, _claim("1", "a")),
            self.second.claim(owner, _claim("2", "b")),
            return_exceptions=True,
        )

        assert sum(not isinstance(result, Exception) for result in results) == 1
        assert (
            sum(
                isinstance(result, GeneralAgentToolBudgetExceededError)
                for result in results
            )
            == 1
        )
        snapshot = await self.first.read(owner)
        assert snapshot is not None
        assert snapshot.used == 1
        assert snapshot.remaining == 0
        winner = snapshot.claims[0]

        replay = await self.second.claim(owner, winner)
        assert replay.used == 1
        with self.assertRaises(GeneralAgentToolBudgetClaimConflictError):
            await self.second.claim(
                owner,
                winner.model_copy(update={"input_sha256": "f" * 64}),
            )
        with self.assertRaises(GeneralAgentToolBudgetLimitConflictError):
            await self.second.initialize(owner, 2)

        self.assertTrue(await self.first.delete(owner))
        self.assertIsNone(await self.second.read(owner))


def _claim(suffix: str, input_hex: str) -> GeneralAgentToolBudgetClaim:
    return GeneralAgentToolBudgetClaim(
        logical_call_id=f"tool_budget_call_{suffix * 64}",
        tool_name="read_test",
        input_sha256=input_hex * 64,
        call_id=f"call-{suffix}",
        parent_call_id="attempt-parent",
        caller_name="drafting",
        phase="drafting:model_tool",
        claimed_at="2026-08-30T00:00:00Z",
    )
