"""确定性统计已确认知识库引用的章节覆盖范围。"""

from collections import defaultdict

from pydantic import BaseModel

from taichu.application.capabilities import CapabilityContext
from taichu.application.invocations.models import InvocationContext
from taichu.application.services.chapter_service import ChapterService
from taichu.application.services.knowledge_service import KnowledgeService
from taichu.application.tools._shared import INTERNAL_READ_CALLERS
from taichu.application.tools.contract import ToolManifest
from taichu.application.tools.models import (
    GetKnowledgeChapterCoverageInput,
    GetKnowledgeChapterCoverageOutput,
    KnowledgeChapterCoverageItem,
)
from taichu.domain.models.structured_knowledge import (
    KnowledgeSchemaFieldType,
    knowledge_type_schema,
)


manifest = ToolManifest(
    name="get_knowledge_chapter_coverage",
    description=(
        "扫描全部已确认知识卡的章节引用字段，并按正文目录顺序确定知识库最早、"
        "最晚引用章节及完整覆盖分布；用于回答全库章节覆盖范围，不使用相关性召回。"
    ),
    input_schema=GetKnowledgeChapterCoverageInput,
    output_schema=GetKnowledgeChapterCoverageOutput,
    required_capabilities=frozenset({"chapter_service", "knowledge_service"}),
    exposures=frozenset({"agent_runtime"}),
    allowed_callers=INTERNAL_READ_CALLERS,
    retryable=True,
)


async def run(
    input_data: BaseModel,
    invocation: InvocationContext,
    context: CapabilityContext,
) -> BaseModel:
    GetKnowledgeChapterCoverageInput.model_validate(input_data)
    cards = await context.require(
        "knowledge_service",
        KnowledgeService,
    ).retrieve_complete_confirmed_catalog(
        run_id=invocation.run_id,
        stage=invocation.phase,
    )
    chapters = await context.require("chapter_service", ChapterService).list_chapters()
    chapter_by_id = {chapter.id: chapter for chapter in chapters}

    cards_by_chapter: dict[str, set[str]] = defaultdict(set)
    referenced_cards: set[str] = set()
    for card in cards:
        chapter_fields = [
            field.field_key
            for field in knowledge_type_schema(card.type).fields
            if field.field_type is KnowledgeSchemaFieldType.CHAPTER_REF
        ]
        for field_key in chapter_fields:
            chapter_id = getattr(card, field_key, None)
            if not isinstance(chapter_id, str) or not chapter_id:
                continue
            cards_by_chapter[chapter_id].add(card.id)
            referenced_cards.add(card.id)

    known_items = sorted(
        (
            KnowledgeChapterCoverageItem(
                chapter_id=chapter_id,
                title=chapter_by_id[chapter_id].title,
                order=chapter_by_id[chapter_id].order,
                referenced_card_count=len(card_ids),
            )
            for chapter_id, card_ids in cards_by_chapter.items()
            if chapter_id in chapter_by_id
        ),
        key=lambda item: (item.order, item.chapter_id),
    )
    unknown_chapter_ids = sorted(set(cards_by_chapter) - set(chapter_by_id))
    return GetKnowledgeChapterCoverageOutput(
        confirmed_card_count=len(cards),
        referenced_card_count=len(referenced_cards),
        earliest_chapter=known_items[0] if known_items else None,
        latest_chapter=known_items[-1] if known_items else None,
        referenced_chapters=known_items,
        unknown_chapter_ids=unknown_chapter_ids,
        source_refs=[
            "manuscript:manifest",
            *[f"knowledge:{card_id}" for card_id in sorted(referenced_cards)],
        ],
    )
