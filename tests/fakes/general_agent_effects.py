"""通用 Agent 副作用账本的内存测试替身。"""

from __future__ import annotations

from taichu.application.general_agent.recovery import EffectRecord


class InMemoryGeneralAgentEffectRepository:
    """仅供隔离测试使用，不作为生产 Runtime 的持久化降级。"""

    def __init__(self) -> None:
        self._records: list[EffectRecord] = []

    async def append(self, record: EffectRecord) -> None:
        self._records.append(record)

    async def latest(self, effect_id: str) -> EffectRecord | None:
        matches = [item for item in self._records if item.effect_id == effect_id]
        return matches[-1] if matches else None

    async def list_effects(self, run_id: str) -> list[EffectRecord]:
        return [item for item in self._records if item.run_id == run_id]

    async def delete_run(self, run_id: str) -> bool:
        before = len(self._records)
        self._records = [item for item in self._records if item.run_id != run_id]
        return len(self._records) != before


__all__ = ["InMemoryGeneralAgentEffectRepository"]
