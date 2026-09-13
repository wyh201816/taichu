"""执行有效性审计查询的确定性过滤、排序与去重，不裁剪上下文。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from taichu.application.agent_memory.models import (
    AgentMemoryEntry,
    AgentMemoryKind,
    AgentMemorySelection,
)

_KIND_PRIORITY = {
    AgentMemoryKind.USER_INSTRUCTION: 100,
    AgentMemoryKind.UNRESOLVED_ISSUE: 95,
    AgentMemoryKind.FACT_REFERENCE: 75,
    AgentMemoryKind.TASK_SUMMARY: 70,
    AgentMemoryKind.RESOURCE_SUMMARY: 55,
    AgentMemoryKind.WORK_NOTE: 50,
}
_PROTECTED_KINDS = {
    AgentMemoryKind.USER_INSTRUCTION,
    AgentMemoryKind.UNRESOLVED_ISSUE,
}


@dataclass(frozen=True, slots=True)
class AgentMemoryPolicy:
    """可注入、可回放的记忆选择策略。"""

    age_decay_days: int = 180
    minimum_relevance: float = 0.01

    def snapshot(self) -> dict[str, int | float | str]:
        return {"policy": "deterministic_lexical", **asdict(self)}

    def select(
        self,
        entries: list[AgentMemoryEntry],
        *,
        lexical_scores: dict[str, float],
        as_of: str,
    ) -> AgentMemorySelection:
        active = _exclude_superseded(entries)
        ranked = sorted(
            active,
            key=lambda entry: self._rank_key(
                entry,
                lexical_score=lexical_scores.get(entry.memory_id, 0.0),
                as_of=as_of,
            ),
        )

        deduplicated: list[AgentMemoryEntry] = []
        seen_hashes: set[str] = set()
        duplicate_count = 0
        for entry in ranked:
            if entry.content_sha256 in seen_hashes:
                duplicate_count += 1
                continue
            seen_hashes.add(entry.content_sha256)
            deduplicated.append(entry)

        protected = [entry for entry in deduplicated if entry.kind in _PROTECTED_KINDS]
        protected_chars = sum(_entry_char_count(entry) for entry in protected)

        selected = list(protected)
        selected_ids = {entry.memory_id for entry in selected}
        selected_chars = protected_chars
        dropped_budget = 0
        for entry in deduplicated:
            if entry.memory_id in selected_ids:
                continue
            relevance = lexical_scores.get(entry.memory_id, 0.0)
            if relevance < self.minimum_relevance:
                dropped_budget += 1
                continue
            entry_chars = _entry_char_count(entry)
            selected.append(entry)
            selected_ids.add(entry.memory_id)
            selected_chars += entry_chars

        selected.sort(
            key=lambda entry: self._rank_key(
                entry,
                lexical_score=lexical_scores.get(entry.memory_id, 0.0),
                as_of=as_of,
            )
        )
        return AgentMemorySelection(
            entries=selected,
            selected_memory_ids=[entry.memory_id for entry in selected],
            candidate_count=len(entries),
            selected_char_count=selected_chars,
            dropped_duplicate_count=duplicate_count,
            dropped_budget_count=dropped_budget,
            policy_snapshot={
                **self.snapshot(),
                "as_of": as_of,
            },
        )

    def _rank_key(
        self,
        entry: AgentMemoryEntry,
        *,
        lexical_score: float,
        as_of: str,
    ) -> tuple[float, str, str]:
        age_penalty = _age_days(entry.updated_at, as_of) / max(
            1,
            self.age_decay_days,
        )
        score = (
            _KIND_PRIORITY[entry.kind]
            + entry.retention_priority
            + lexical_score * 100
            - age_penalty
        )
        return (-score, _reverse_timestamp(entry.updated_at), entry.memory_id)


def _exclude_superseded(entries: list[AgentMemoryEntry]) -> list[AgentMemoryEntry]:
    superseded = {
        entry.supersedes_memory_id
        for entry in entries
        if entry.supersedes_memory_id is not None
    }
    return [entry for entry in entries if entry.memory_id not in superseded]


def _entry_char_count(entry: AgentMemoryEntry) -> int:
    return len(entry.content) + sum(len(item) for item in entry.source_refs) + 24


def _age_days(updated_at: str, as_of: str) -> float:
    try:
        updated = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        current = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return max(0.0, (current - updated).total_seconds() / 86_400)


def _reverse_timestamp(value: str) -> str:
    return "".join(chr(0x10FFFF - ord(character)) for character in value)
