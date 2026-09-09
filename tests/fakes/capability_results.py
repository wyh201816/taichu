"""测试中共享 LangGraph 官方内存 Store 的能力结果仓储工厂。"""

from pathlib import Path

from langgraph.store.memory import InMemoryStore

from taichu.infrastructure.general_agent_runs import (
    LangGraphGeneralAgentCapabilityResultRepository,
)

_STORES: dict[str, InMemoryStore] = {}


def in_memory_capability_result_repository(
    scope: Path | str,
) -> LangGraphGeneralAgentCapabilityResultRepository:
    key = str(Path(scope).resolve())
    store = _STORES.setdefault(key, InMemoryStore())
    return LangGraphGeneralAgentCapabilityResultRepository(store)
