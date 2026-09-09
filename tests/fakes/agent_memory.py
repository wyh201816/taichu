"""测试中共享 LangGraph 官方内存 Store 的小型工厂。"""

from pathlib import Path

from langgraph.store.memory import InMemoryStore

from taichu.infrastructure.agent_memory import LangGraphAgentMemoryRepository

_STORES: dict[str, InMemoryStore] = {}


def in_memory_agent_memory_repository(
    scope: Path | str,
) -> LangGraphAgentMemoryRepository:
    """同一测试作用域复用 Store，模拟进程重建后的持久后端。"""

    key = str(Path(scope).resolve())
    store = _STORES.setdefault(key, InMemoryStore())
    return LangGraphAgentMemoryRepository(store)
