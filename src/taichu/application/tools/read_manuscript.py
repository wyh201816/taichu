"""按稳定章节范围读取 Markdown 正文。"""

from pydantic import BaseModel

from taichu.application.capabilities import CapabilityContext
from taichu.application.invocations.models import InvocationContext
from taichu.application.services.chapter_service import (
    ChapterNotFoundError,
    ChapterService,
)
from taichu.application.tools._shared import INTERNAL_READ_CALLERS, sha256_text
from taichu.application.tools.contract import ToolManifest
from taichu.application.tools.models import (
    ManuscriptChunk,
    ReadManuscriptInput,
    ReadManuscriptOutput,
)


manifest = ToolManifest(
    name="read_manuscript",
    description="按稳定章节 ID 或顺序范围读取 Markdown 正文。",
    input_schema=ReadManuscriptInput,
    output_schema=ReadManuscriptOutput,
    required_capabilities=frozenset({"chapter_service"}),
    exposures=frozenset({"agent_runtime"}),
    allowed_callers=INTERNAL_READ_CALLERS,
    retryable=True,
)


async def run(
    input_data: BaseModel,
    invocation: InvocationContext,
    context: CapabilityContext,
) -> BaseModel:
    del invocation
    tool_input = ReadManuscriptInput.model_validate(input_data)
    service = context.require("chapter_service", ChapterService)
    chapters = await service.list_chapters()
    if tool_input.chapter_ids:
        ordered_ids = list(dict.fromkeys(tool_input.chapter_ids))
    elif tool_input.recent_count is not None:
        ordered_ids = [
            item.id
            for item in sorted(chapters, key=lambda chapter: chapter.order)[
                -tool_input.recent_count :
            ]
        ]
    else:
        end_order = tool_input.end_order if tool_input.end_order is not None else 10**9
        ordered_ids = [
            item.id
            for item in chapters
            if int(tool_input.start_order or 0) <= item.order <= end_order
        ]
    chunks: list[ManuscriptChunk] = []
    missing: list[str] = []
    used = 0
    truncated = False
    for chapter_id in ordered_ids:
        try:
            content = await service.read_chapter(chapter_id)
        except ChapterNotFoundError:
            missing.append(chapter_id)
            continue
        remaining = tool_input.max_content_chars - used
        if remaining <= 0:
            truncated = True
            break
        text = content.markdown[:remaining]
        chunk_truncated = len(text) < len(content.markdown)
        chunks.append(
            ManuscriptChunk(
                chapter_id=content.chapter.id,
                title=content.chapter.title,
                order=content.chapter.order,
                content=text,
                content_sha256=sha256_text(content.markdown),
                start_char=0,
                end_char=len(text),
                truncated=chunk_truncated,
                source_ref=f"manuscript:{content.chapter.id}:0-{len(text)}",
            )
        )
        used += len(text)
        if chunk_truncated:
            truncated = True
            break
    return ReadManuscriptOutput(
        chunks=chunks,
        missing_chapter_ids=missing,
        total_content_chars=used,
        truncated=truncated or len(chunks) + len(missing) < len(ordered_ids),
        source_refs=[chunk.source_ref for chunk in chunks],
    )
