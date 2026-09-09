"""通用 Agent Tool 调用预算的并发安全内存测试替身。"""

from __future__ import annotations

import asyncio

from taichu.application.contracts.general_agent_tool_budget import (
    GeneralAgentToolBudgetClaim,
    GeneralAgentToolBudgetClaimConflictError,
    GeneralAgentToolBudgetExceededError,
    GeneralAgentToolBudgetLimitConflictError,
    GeneralAgentToolBudgetOwner,
    GeneralAgentToolBudgetOwnerNotInitializedError,
    GeneralAgentToolBudgetRepository,
    GeneralAgentToolBudgetSnapshot,
)


class InMemoryGeneralAgentToolBudgetRepository(GeneralAgentToolBudgetRepository):
    def __init__(self) -> None:
        self._snapshots: dict[tuple[str, str], GeneralAgentToolBudgetSnapshot] = {}
        self._lock = asyncio.Lock()

    async def initialize(
        self,
        owner: GeneralAgentToolBudgetOwner,
        limit: int,
    ) -> GeneralAgentToolBudgetSnapshot:
        async with self._lock:
            key = _owner_key(owner)
            current = self._snapshots.get(key)
            if current is not None:
                if current.limit != limit:
                    raise GeneralAgentToolBudgetLimitConflictError
                return current
            snapshot = GeneralAgentToolBudgetSnapshot(
                owner=owner,
                limit=limit,
                used=0,
                remaining=limit,
            )
            self._snapshots[key] = snapshot
            return snapshot

    async def claim(
        self,
        owner: GeneralAgentToolBudgetOwner,
        claim: GeneralAgentToolBudgetClaim,
    ) -> GeneralAgentToolBudgetSnapshot:
        async with self._lock:
            key = _owner_key(owner)
            current = self._snapshots.get(key)
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
            claims = (*current.claims, claim)
            updated = current.model_copy(
                update={
                    "claims": claims,
                    "used": len(claims),
                    "remaining": current.limit - len(claims),
                }
            )
            self._snapshots[key] = updated
            return updated

    async def read(
        self,
        owner: GeneralAgentToolBudgetOwner,
    ) -> GeneralAgentToolBudgetSnapshot | None:
        async with self._lock:
            return self._snapshots.get(_owner_key(owner))

    async def delete(self, owner: GeneralAgentToolBudgetOwner) -> bool:
        async with self._lock:
            return self._snapshots.pop(_owner_key(owner), None) is not None


def _owner_key(owner: GeneralAgentToolBudgetOwner) -> tuple[str, str]:
    return owner.conversation_id, owner.run_id


__all__ = ["InMemoryGeneralAgentToolBudgetRepository"]
