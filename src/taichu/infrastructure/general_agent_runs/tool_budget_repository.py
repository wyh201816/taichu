"""基于 MongoDB 原子单文档更新的任务级 Tool 调用预算仓储。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pymongo import AsyncMongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from taichu.application.contracts.general_agent_tool_budget import (
    GeneralAgentToolBudgetClaim,
    GeneralAgentToolBudgetClaimConflictError,
    GeneralAgentToolBudgetExceededError,
    GeneralAgentToolBudgetLimitConflictError,
    GeneralAgentToolBudgetOwner,
    GeneralAgentToolBudgetOwnerNotInitializedError,
    GeneralAgentToolBudgetRecordCorruptError,
    GeneralAgentToolBudgetSnapshot,
    GeneralAgentToolBudgetUnavailableError,
)
from taichu.application.invocations.models import now_iso

DEFAULT_TOOL_BUDGET_COLLECTION = "general_agent_tool_budgets"


class MongoGeneralAgentToolBudgetRepository:
    """用 Mongo 条件更新保证多个 worker 共享同一个硬上限。"""

    def __init__(
        self,
        uri: str,
        database_name: str,
        *,
        collection_name: str = DEFAULT_TOOL_BUDGET_COLLECTION,
        client: Any | None = None,
        server_selection_timeout_ms: int = 5_000,
    ) -> None:
        self._owns_client = client is None
        self._client = client or AsyncMongoClient(
            uri,
            tz_aware=True,
            serverSelectionTimeoutMS=server_selection_timeout_ms,
        )
        self._collection = self._client[database_name][collection_name]

    async def initialize(
        self,
        owner: GeneralAgentToolBudgetOwner,
        limit: int,
    ) -> GeneralAgentToolBudgetSnapshot:
        """首次写入固定上限；后续不同上限必须失败关闭。"""

        document_id = _document_id(owner)
        timestamp = now_iso()
        document = {
            "_id": document_id,
            "conversation_id": owner.conversation_id,
            "run_id": owner.run_id,
            "limit": limit,
            "used": 0,
            "claims": [],
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        try:
            await self._collection.update_one(
                {"_id": document_id},
                {"$setOnInsert": document},
                upsert=True,
            )
        except DuplicateKeyError:
            # 两个并发首次调用可以同时看见空记录；_id 唯一性决定胜者。
            pass
        except PyMongoError as error:
            raise GeneralAgentToolBudgetUnavailableError from error
        snapshot = await self.read(owner)
        if snapshot is None:
            raise GeneralAgentToolBudgetRecordCorruptError(
                "Tool 调用预算初始化后无法回读。"
            )
        if snapshot.limit != limit:
            raise GeneralAgentToolBudgetLimitConflictError
        return snapshot

    async def claim(
        self,
        owner: GeneralAgentToolBudgetOwner,
        claim: GeneralAgentToolBudgetClaim,
    ) -> GeneralAgentToolBudgetSnapshot:
        """原子占用一个逻辑调用槽；重复恢复调用不重复扣减。"""

        document_id = _document_id(owner)
        try:
            updated = await self._collection.find_one_and_update(
                {
                    "_id": document_id,
                    "conversation_id": owner.conversation_id,
                    "run_id": owner.run_id,
                    "$expr": {"$lt": ["$used", "$limit"]},
                    "claims": {
                        "$not": {
                            "$elemMatch": {
                                "logical_call_id": claim.logical_call_id,
                            }
                        }
                    },
                },
                {
                    "$push": {"claims": claim.model_dump(mode="json")},
                    "$inc": {"used": 1},
                    "$set": {"updated_at": now_iso()},
                },
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError as error:
            raise GeneralAgentToolBudgetUnavailableError from error
        if updated is not None:
            return _document_to_snapshot(updated, expected_owner=owner)

        current = await self.read(owner)
        if current is None:
            raise GeneralAgentToolBudgetOwnerNotInitializedError
        existing = next(
            (
                item
                for item in current.claims
                if item.logical_call_id == claim.logical_call_id
            ),
            None,
        )
        if existing is not None:
            if (
                existing.tool_name != claim.tool_name
                or existing.input_sha256 != claim.input_sha256
            ):
                raise GeneralAgentToolBudgetClaimConflictError
            return current
        if current.used >= current.limit:
            raise GeneralAgentToolBudgetExceededError
        # 条件更新在记录未满且没有重复调用时不应落空；出现则按损坏处理。
        raise GeneralAgentToolBudgetRecordCorruptError(
            "Tool 调用预算原子占位未产生确定性结果。"
        )

    async def read(
        self,
        owner: GeneralAgentToolBudgetOwner,
    ) -> GeneralAgentToolBudgetSnapshot | None:
        try:
            document = await self._collection.find_one({"_id": _document_id(owner)})
        except PyMongoError as error:
            raise GeneralAgentToolBudgetUnavailableError from error
        if document is None:
            return None
        return _document_to_snapshot(document, expected_owner=owner)

    async def delete(self, owner: GeneralAgentToolBudgetOwner) -> bool:
        try:
            result = await self._collection.delete_one(
                {
                    "_id": _document_id(owner),
                    "conversation_id": owner.conversation_id,
                    "run_id": owner.run_id,
                }
            )
        except PyMongoError as error:
            raise GeneralAgentToolBudgetUnavailableError from error
        return result.deleted_count == 1

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.close()


def _document_id(owner: GeneralAgentToolBudgetOwner) -> str:
    payload = json.dumps(
        owner.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"general_agent_tool_budget_{hashlib.sha256(payload).hexdigest()}"


def _document_to_snapshot(
    document: dict[str, Any],
    *,
    expected_owner: GeneralAgentToolBudgetOwner,
) -> GeneralAgentToolBudgetSnapshot:
    try:
        owner = GeneralAgentToolBudgetOwner(
            conversation_id=document["conversation_id"],
            run_id=document["run_id"],
        )
        claims = tuple(
            GeneralAgentToolBudgetClaim.model_validate(item)
            for item in document["claims"]
        )
        snapshot = GeneralAgentToolBudgetSnapshot(
            owner=owner,
            limit=document["limit"],
            used=document["used"],
            remaining=document["limit"] - document["used"],
            claims=claims,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise GeneralAgentToolBudgetRecordCorruptError from error
    if owner != expected_owner:
        raise GeneralAgentToolBudgetRecordCorruptError(
            "Tool 调用预算文档所有者与键不一致。"
        )
    return snapshot


__all__ = [
    "DEFAULT_TOOL_BUDGET_COLLECTION",
    "MongoGeneralAgentToolBudgetRepository",
]
