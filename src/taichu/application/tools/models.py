"""第一版生产 Tool 的强类型输入输出模型。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from taichu.domain.models.chapter import ChapterStatus
from taichu.domain.models.structured_knowledge import (
    StructuredKnowledgeCard,
    StructuredKnowledgeType,
)
from taichu.application.vector_graph.models import VectorGraphSourceType


class ToolModel(BaseModel):
    """不可变且拒绝未知字段的 Tool Schema 基类。"""

    model_config = ConfigDict(frozen=True, extra="forbid")


class NovelChapterItem(ToolModel):
    chapter_id: str
    volume_id: str | None = None
    title: str
    order: int
    word_count: int
    status: ChapterStatus
    markdown_path: str
    updated_at: str


class NovelVolumeItem(ToolModel):
    volume_id: str
    title: str
    order: int
    chapters: list[NovelChapterItem] = Field(default_factory=list)


class GetNovelStructureInput(ToolModel):
    volume_ids: list[str] = Field(default_factory=list, max_length=50)
    statuses: set[ChapterStatus] = Field(default_factory=set)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=200, ge=1, le=500)


class GetNovelStructureOutput(ToolModel):
    structure_version: str
    current_volume_id: str | None = None
    current_chapter_id: str | None = None
    total_chapters: int = Field(ge=0)
    returned_chapters: int = Field(ge=0)
    volumes: list[NovelVolumeItem] = Field(default_factory=list)
    truncated: bool = False
    source_refs: list[str] = Field(default_factory=list)


class ReadManuscriptInput(ToolModel):
    chapter_ids: list[str] = Field(
        default_factory=list,
        max_length=100,
        description="已知的稳定章节 ID 数组，不是章节序号。按序号读取时省略此字段。",
    )
    start_order: int | None = Field(
        default=None, ge=0, description="要读取的起始章节序号，包含本章。"
    )
    end_order: int | None = Field(
        default=None, ge=0, description="要读取的结束章节序号，包含本章。"
    )
    recent_count: int | None = Field(default=None, ge=1, le=100)
    max_content_chars: int = Field(default=100_000, ge=100, le=200_000)

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        selectors = sum(
            (
                bool(self.chapter_ids),
                self.start_order is not None,
                self.recent_count is not None,
            )
        )
        if selectors == 0:
            raise ValueError("正文读取必须提供章节 ID、起始顺序或最近章节数。")
        if selectors > 1:
            raise ValueError("正文读取的章节 ID、顺序范围和最近章节数不能混用。")
        if self.recent_count is not None and self.end_order is not None:
            raise ValueError("按最近章节数读取时不能同时提供结束章节顺序。")
        if (
            self.start_order is not None
            and self.end_order is not None
            and self.end_order < self.start_order
        ):
            raise ValueError("结束章节顺序不能早于起始章节顺序。")
        return self


class ManuscriptChunk(ToolModel):
    chapter_id: str
    title: str
    order: int
    content: str
    content_sha256: str
    start_char: int = Field(ge=0)
    end_char: int = Field(ge=0)
    truncated: bool = False
    source_ref: str


class ReadManuscriptOutput(ToolModel):
    chunks: list[ManuscriptChunk] = Field(default_factory=list)
    missing_chapter_ids: list[str] = Field(default_factory=list)
    total_content_chars: int = Field(ge=0)
    truncated: bool = False
    source_refs: list[str] = Field(default_factory=list)


class RetrieveStoryContextInput(ToolModel):
    query: str = Field(min_length=1, max_length=20_000)
    max_passages: int = Field(default=10, ge=1, le=10)


class StoryContextEvidence(ToolModel):
    passage_id: str = ""
    source_type: VectorGraphSourceType
    source_id: str
    source_ref: str
    title: str
    content: str
    content_sha256: str = Field(min_length=64, max_length=64)
    rank: int = Field(ge=1)
    chunk_index: int = Field(default=0, ge=0)
    start_char: int | None = Field(default=None, ge=0)
    end_char: int | None = Field(default=None, ge=0)
    parent_start_char: int | None = Field(default=None, ge=0)
    parent_end_char: int | None = Field(default=None, ge=0)
    parent_chunk_indexes: list[int] = Field(default_factory=list)
    context_content: str | None = None
    context_source_ref: str | None = None
    context_start_char: int | None = Field(default=None, ge=0)
    context_end_char: int | None = Field(default=None, ge=0)
    context_chunk_indexes: list[int] = Field(default_factory=list)
    relation_ids: list[str] = Field(default_factory=list)
    relation_texts: list[str] = Field(default_factory=list)
    retrieval_channels: list[str] = Field(default_factory=list)
    reranker_score: float | None = Field(default=None, ge=0, le=1)
    authority_verified: bool = False


class RetrieveStoryContextOutput(ToolModel):
    query: str
    evidences: list[StoryContextEvidence] = Field(default_factory=list)
    retrieved_relations: list[str] = Field(default_factory=list)
    expanded_relations: list[str] = Field(default_factory=list)
    context_relations: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)


class ResolveKnowledgeIdentityInput(ToolModel):
    knowledge_type: StructuredKnowledgeType
    name: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=50)


class KnowledgeIdentityMatch(ToolModel):
    card_id: str
    knowledge_type: StructuredKnowledgeType
    canonical_name: str
    matched_aliases: list[str] = Field(default_factory=list)


class ResolveKnowledgeIdentityOutput(ToolModel):
    resolution: Literal["unique", "ambiguous", "not_found"]
    matches: list[KnowledgeIdentityMatch] = Field(default_factory=list)
    reason: str
    source_refs: list[str] = Field(default_factory=list)


class ListKnowledgeCatalogInput(ToolModel):
    knowledge_types: set[StructuredKnowledgeType] = Field(default_factory=set)
    offset: int = Field(default=0, ge=0, le=199)
    limit: int = Field(default=100, ge=1, le=200)

    @model_validator(mode="after")
    def validate_first_version_window(self) -> Self:
        if self.offset + self.limit > 200:
            raise ValueError("第一版知识目录单次分页窗口不能超过前 200 条。")
        return self


class KnowledgeCatalogItem(ToolModel):
    card_id: str
    knowledge_type: StructuredKnowledgeType
    name: str
    aliases: list[str] = Field(default_factory=list)
    summary: str
    updated_at: str


class ListKnowledgeCatalogOutput(ToolModel):
    items: list[KnowledgeCatalogItem] = Field(default_factory=list)
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)
    truncated: bool = False
    source_refs: list[str] = Field(default_factory=list)


class ReadKnowledgeCardsInput(ToolModel):
    card_ids: list[str] = Field(min_length=1, max_length=100)


class ReadKnowledgeCardsOutput(ToolModel):
    cards: list[StructuredKnowledgeCard] = Field(default_factory=list)
    missing_card_ids: list[str] = Field(default_factory=list)
    rejected_card_ids: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)


class GetKnowledgeChapterCoverageInput(ToolModel):
    pass


class KnowledgeChapterCoverageItem(ToolModel):
    chapter_id: str
    title: str
    order: int = Field(ge=0)
    referenced_card_count: int = Field(ge=1)


class GetKnowledgeChapterCoverageOutput(ToolModel):
    confirmed_card_count: int = Field(ge=0)
    referenced_card_count: int = Field(ge=0)
    earliest_chapter: KnowledgeChapterCoverageItem | None = None
    latest_chapter: KnowledgeChapterCoverageItem | None = None
    referenced_chapters: list[KnowledgeChapterCoverageItem] = Field(
        default_factory=list
    )
    unknown_chapter_ids: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)


class SearchExternalSourcesInput(ToolModel):
    query: str = Field(min_length=1, max_length=2_000)
    source_preferences: list[str] = Field(default_factory=list, max_length=20)
    date_range: str | None = Field(default=None, max_length=100)
    max_results: int = Field(default=8, ge=1, le=20)


class ExternalSearchItem(ToolModel):
    title: str
    url: str
    domain: str
    snippet: str
    published_at: str | None = None


class SearchExternalSourcesOutput(ToolModel):
    search_id: str
    query: str
    items: list[ExternalSearchItem] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)


class ReadExternalSourceInput(ToolModel):
    url: str = Field(min_length=8, max_length=4_000)
    max_content_chars: int = Field(default=20_000, ge=500, le=100_000)


class ReadExternalSourceOutput(ToolModel):
    source_id: str
    url: str
    final_url: str
    title: str
    content: str
    content_sha256: str
    truncated: bool = False
    source_refs: list[str] = Field(default_factory=list)


class ManuscriptPatchOperation(ToolModel):
    operation: Literal["replace_span", "append", "prepend"]
    start_char: int | None = Field(default=None, ge=0)
    end_char: int | None = Field(default=None, ge=0)
    text: str = Field(max_length=200_000)

    @model_validator(mode="after")
    def validate_span(self) -> Self:
        if self.operation == "replace_span":
            if self.start_char is None or self.end_char is None:
                raise ValueError("区间替换必须提供开始和结束字符位置。")
            if self.end_char < self.start_char:
                raise ValueError("替换结束位置不能早于开始位置。")
        elif self.start_char is not None or self.end_char is not None:
            raise ValueError("追加或前置操作不能携带字符位置。")
        return self


class PreviewManuscriptPatchInput(ToolModel):
    chapter_id: str = Field(min_length=1)
    base_content_sha256: str = Field(min_length=64, max_length=64)
    operations: list[ManuscriptPatchOperation] = Field(min_length=1, max_length=50)


class PreviewManuscriptPatchOutput(ToolModel):
    patch_id: str
    chapter_id: str
    base_content_sha256: str
    expected_content_sha256: str
    normalized_operations: list[ManuscriptPatchOperation]
    unified_diff: str
    old_char_count: int = Field(ge=0)
    new_char_count: int = Field(ge=0)
    source_refs: list[str] = Field(default_factory=list)


class ApplyManuscriptPatchInput(ToolModel):
    patch_id: str = Field(min_length=1)
    chapter_id: str = Field(min_length=1)
    base_content_sha256: str = Field(min_length=64, max_length=64)
    expected_content_sha256: str = Field(min_length=64, max_length=64)
    operations: list[ManuscriptPatchOperation] = Field(min_length=1, max_length=50)
    author_grant_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=8, max_length=128)


class ApplyManuscriptPatchOutput(ToolModel):
    chapter_id: str
    content_sha256: str
    word_count: int = Field(ge=0)
    unified_diff: str
    audit_ref: str
    source_refs: list[str] = Field(default_factory=list)


class CreateStructureItem(ToolModel):
    kind: Literal["volume", "chapter"]
    title: str = Field(min_length=1, max_length=200)
    volume_id: str | None = Field(default=None, max_length=128)
    after_chapter_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_parent(self) -> Self:
        if self.kind == "chapter" and not self.volume_id:
            raise ValueError("创建章节必须指定所属卷。")
        if self.kind == "volume" and (
            self.volume_id is not None or self.after_chapter_id is not None
        ):
            raise ValueError("创建卷不能指定章节位置。")
        return self


class CreateNovelStructureItemsInput(ToolModel):
    expected_structure_version: str = Field(min_length=64, max_length=64)
    items: list[CreateStructureItem] = Field(min_length=1, max_length=20)
    author_grant_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=8, max_length=128)


class StructureChangeResult(ToolModel):
    kind: Literal["volume", "chapter"]
    item_id: str
    action: str
    title: str


class NovelStructureWriteOutput(ToolModel):
    previous_structure_version: str
    structure_version: str
    changes: list[StructureChangeResult] = Field(default_factory=list)
    audit_ref: str
    source_refs: list[str] = Field(default_factory=list)


class UpdateStructureOperation(ToolModel):
    operation: Literal[
        "rename_volume",
        "rename_chapter",
        "move_volume",
        "move_chapter",
        "set_chapter_status",
    ]
    target_id: str = Field(min_length=1)
    title: str | None = Field(default=None, max_length=200)
    target_volume_id: str | None = Field(default=None, max_length=128)
    after_item_id: str | None = Field(default=None, max_length=128)
    chapter_status: ChapterStatus | None = None


class UpdateNovelStructureInput(ToolModel):
    expected_structure_version: str = Field(min_length=64, max_length=64)
    operations: list[UpdateStructureOperation] = Field(min_length=1, max_length=20)
    author_grant_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=8, max_length=128)


class DeleteStructureTarget(ToolModel):
    kind: Literal["volume", "chapter"]
    target_id: str = Field(min_length=1)


class DeleteNovelStructureItemsInput(ToolModel):
    expected_structure_version: str = Field(min_length=64, max_length=64)
    targets: list[DeleteStructureTarget] = Field(min_length=1, max_length=20)
    impact_acknowledgement: str = Field(min_length=3, max_length=1_000)
    author_grant_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=8, max_length=128)


class CreateConfirmedKnowledgeInput(ToolModel):
    knowledge_type: StructuredKnowledgeType
    card: dict[str, object]
    source_refs: list[str] = Field(min_length=1, max_length=100)
    author_grant_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=8, max_length=128)


class CreateConfirmedKnowledgeOutput(ToolModel):
    card: StructuredKnowledgeCard
    audit_ref: str
    source_refs: list[str] = Field(default_factory=list)


class UpdateConfirmedKnowledgeInput(ToolModel):
    card_id: str = Field(min_length=1)
    expected_updated_at: str = Field(min_length=1)
    updates: dict[str, object]
    merge_mode: Literal["merge", "overwrite"] = "overwrite"
    source_refs: list[str] = Field(min_length=1, max_length=100)
    author_grant_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=8, max_length=128)


class UpdateConfirmedKnowledgeOutput(ToolModel):
    card: StructuredKnowledgeCard
    changed_fields: list[str] = Field(default_factory=list)
    audit_ref: str
    source_refs: list[str] = Field(default_factory=list)
