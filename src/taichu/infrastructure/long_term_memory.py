"""Markdown 长期记忆的确定性解析与按需词法召回。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re

from taichu.application.general_agent.models import GeneralAgentContextMemory

_SECTION = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_WORD = re.compile(r"[a-z0-9_]+|[\u3400-\u9fff]", re.I)


@dataclass(frozen=True, slots=True)
class _MarkdownMemory:
    title: str
    keywords: tuple[str, ...]
    global_scope: bool
    content: str
    ordinal: int


class MarkdownLongTermMemoryRetriever:
    """把一个 Markdown 文档中的二级标题条目按当前请求投影。"""

    def __init__(self, path: Path) -> None:
        self._path = path

    async def retrieve(
        self,
        query: str,
    ) -> list[GeneralAgentContextMemory]:
        if not self._path.is_file():
            return []
        text = await asyncio.to_thread(self._path.read_text, encoding="utf-8")
        query_terms = _terms(query)
        ranked = sorted(
            ((_score(item, query_terms), item) for item in _parse_memories(text)),
            key=lambda pair: (-pair[0], pair[1].ordinal),
        )
        result: list[GeneralAgentContextMemory] = []
        used_chars = 0
        for score, item in ranked:
            if score <= 0:
                continue
            content = f"{item.title}\n{item.content}".strip()
            digest = sha256(content.encode("utf-8")).hexdigest()
            result.append(
                GeneralAgentContextMemory(
                    memory_id=f"long_term_md_{digest[:32]}",
                    kind="user_preference",
                    content=content,
                    source_refs=[
                        f"workspace-long-term-memory:{item.ordinal}:{digest[:12]}"
                    ],
                    content_sha256=digest,
                    basis_sha256=digest,
                    result_type="markdown_long_term_memory",
                    producer_ref="workspace:long_term_memory.md",
                )
            )
            used_chars += len(content)
        return result


def _parse_memories(text: str) -> list[_MarkdownMemory]:
    clean = _COMMENT.sub("", text)
    matches = list(_SECTION.finditer(clean))
    result: list[_MarkdownMemory] = []
    for ordinal, match in enumerate(matches, start=1):
        block = clean[
            match.end() : matches[ordinal].start() if ordinal < len(matches) else None
        ]
        keywords: tuple[str, ...] = ()
        global_scope = False
        body: list[str] = []
        for line in block.strip().splitlines():
            stripped = line.strip()
            if stripped.startswith("关键词："):
                keywords = tuple(
                    part.strip()
                    for part in re.split(r"[、,，]", stripped.removeprefix("关键词："))
                    if part.strip()
                )
                continue
            if stripped == "适用范围：全局":
                global_scope = True
                continue
            body.append(line)
        content = "\n".join(body).strip()
        if content:
            result.append(
                _MarkdownMemory(
                    title=match.group(1).strip(),
                    keywords=keywords,
                    global_scope=global_scope,
                    content=content,
                    ordinal=ordinal,
                )
            )
    return result


def _terms(value: str) -> set[str]:
    normalized = "".join(_WORD.findall(value.casefold()))
    chinese = [char for char in normalized if "\u3400" <= char <= "\u9fff"]
    return {
        *re.findall(r"[a-z0-9_]+", normalized),
        *chinese,
        *("".join(chinese[index : index + 2]) for index in range(len(chinese) - 1)),
    }


def _score(item: _MarkdownMemory, query_terms: set[str]) -> float:
    document = " ".join((item.title, *item.keywords, item.content)).casefold()
    score = 1.0 if item.global_scope else 0.0
    for term in query_terms:
        if term in document:
            score += 4.0 if len(term) > 1 else 0.2
    return score
