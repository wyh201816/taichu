"""基于 LangGraph 官方 Store 的通用 Runtime 记忆仓储。"""

from __future__ import annotations

from langgraph.store.base import BaseStore, SearchItem

from taichu.application.agent_memory.models import (
    AgentMemoryEntry,
    AgentMemoryKind,
)

_MEMORY_NAMESPACE_PREFIX = ("taichu", "general_agent_memory")
_SEARCH_PAGE_SIZE = 500


class LangGraphAgentMemoryRepository:
    """把太初的记忆模型映射到 LangGraph namespace/key/value 契约。"""

    def __init__(self, store: BaseStore) -> None:
        self.store = store

    async def save(self, entry: AgentMemoryEntry) -> AgentMemoryEntry:
        await self.store.aput(
            self._namespace(entry.conversation_id),
            entry.memory_id,
            entry.model_dump(mode="json"),
        )
        return entry

    async def get(self, memory_id: str) -> AgentMemoryEntry | None:
        matches = await self._search_all(
            _MEMORY_NAMESPACE_PREFIX,
            filter={"memory_id": memory_id},
        )
        if not matches:
            return None
        if len(matches) != 1:
            raise AgentMemoryStoreError(
                f"运行记忆“{memory_id}”在 LangGraph Store 中不是唯一记录。"
            )
        return self._validate(matches[0])

    async def query(
        self,
        *,
        conversation_id: str | None = None,
        kinds: tuple[AgentMemoryKind, ...] = (),
        run_id: str | None = None,
        include_deleted: bool = False,
    ) -> list[AgentMemoryEntry]:
        namespace = (
            self._namespace(conversation_id)
            if conversation_id is not None
            else _MEMORY_NAMESPACE_PREFIX
        )
        items = await self._search_all(namespace)
        entries = [self._validate(item) for item in items]
        if kinds:
            expected_kinds = set(kinds)
            entries = [entry for entry in entries if entry.kind in expected_kinds]
        if run_id is not None:
            entries = [entry for entry in entries if run_id in entry.run_ids]
        if not include_deleted:
            entries = [entry for entry in entries if entry.deleted_at is None]
        return sorted(
            entries,
            key=lambda entry: (entry.updated_at, entry.memory_id),
            reverse=True,
        )

    async def delete(
        self,
        memory_id: str,
        *,
        deleted_at: str,
    ) -> AgentMemoryEntry | None:
        entry = await self.get(memory_id)
        if entry is None:
            return None
        deleted = entry.model_copy(
            update={
                "updated_at": deleted_at,
                "deleted_at": deleted_at,
            }
        )
        return await self.save(deleted)

    async def purge_expired(self, *, as_of: str) -> int:
        entries = await self.query(include_deleted=True)
        expired = [
            entry
            for entry in entries
            if entry.expires_at is not None
            and entry.expires_at <= as_of
            and entry.deleted_at is None
        ]
        for entry in expired:
            await self.save(
                entry.model_copy(
                    update={
                        "updated_at": as_of,
                        "deleted_at": as_of,
                    }
                )
            )
        return len(expired)

    async def _search_all(
        self,
        namespace: tuple[str, ...],
        *,
        filter: dict[str, object] | None = None,
    ) -> list[SearchItem]:
        result: list[SearchItem] = []
        offset = 0
        while True:
            page = await self.store.asearch(
                namespace,
                filter=filter,
                limit=_SEARCH_PAGE_SIZE,
                offset=offset,
            )
            result.extend(page)
            if len(page) < _SEARCH_PAGE_SIZE:
                return result
            offset += len(page)

    @staticmethod
    def _namespace(conversation_id: str) -> tuple[str, ...]:
        return (*_MEMORY_NAMESPACE_PREFIX, conversation_id)

    @staticmethod
    def _validate(item: SearchItem) -> AgentMemoryEntry:
        try:
            return AgentMemoryEntry.model_validate(item.value)
        except ValueError as error:
            raise AgentMemoryStoreError(
                f"LangGraph Store 中的运行记忆“{item.key}”校验失败。"
            ) from error


class AgentMemoryStoreError(ValueError):
    """LangGraph Store 中的运行记忆违反太初业务契约。"""
