"""直接从 MongoDB 事实源分页浏览已确认知识目录。"""

from pydantic import BaseModel

from taichu.application.capabilities import CapabilityContext
from taichu.application.invocations.models import InvocationContext
from taichu.application.contracts.knowledge_repository import (
    StructuredKnowledgeRepository,
)
from taichu.application.tools._shared import INTERNAL_READ_CALLERS
from taichu.application.tools.contract import ToolManifest
from taichu.application.tools.models import (
    KnowledgeCatalogItem,
    ListKnowledgeCatalogInput,
    ListKnowledgeCatalogOutput,
)


manifest = ToolManifest(
    name="list_knowledge_catalog",
    description="按知识类型和分页条件浏览已确认知识的轻量目录。",
    input_schema=ListKnowledgeCatalogInput,
    output_schema=ListKnowledgeCatalogOutput,
    required_capabilities=frozenset({"knowledge_repository"}),
    exposures=frozenset({"agent_runtime"}),
    allowed_callers=INTERNAL_READ_CALLERS,
    retryable=True,
)


async def run(
    input_data: BaseModel,
    invocation: InvocationContext,
    context: CapabilityContext,
) -> BaseModel:
    tool_input = ListKnowledgeCatalogInput.model_validate(input_data)
    del invocation
    cards = await context.require(
        "knowledge_repository", StructuredKnowledgeRepository
    ).list_confirmed_cards()
    if tool_input.knowledge_types:
        cards = [card for card in cards if card.type in tool_input.knowledge_types]
    cards.sort(key=lambda card: (card.updated_at, card.id), reverse=True)
    selected = cards[tool_input.offset : tool_input.offset + tool_input.limit]
    items = [
        KnowledgeCatalogItem(
            card_id=card.id,
            knowledge_type=card.type,
            name=card.name,
            aliases=card.aliases,
            summary=card.summary,
            updated_at=card.updated_at,
        )
        for card in selected
    ]
    source_refs = [f"knowledge:{item.card_id}" for item in items]
    return ListKnowledgeCatalogOutput(
        items=items,
        total=len(cards),
        offset=tool_input.offset,
        limit=tool_input.limit,
        truncated=tool_input.offset + len(items) < len(cards),
        source_refs=source_refs,
    )
