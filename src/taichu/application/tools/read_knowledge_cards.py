"""按稳定 ID 读取完整已确认知识卡。"""

from pydantic import BaseModel

from taichu.application.capabilities import CapabilityContext
from taichu.application.invocations.models import InvocationContext
from taichu.application.services.knowledge_service import (
    KnowledgeCardNotFoundError,
    KnowledgeService,
)
from taichu.application.tools._shared import INTERNAL_READ_CALLERS
from taichu.application.tools.contract import ToolManifest
from taichu.application.tools.models import (
    ReadKnowledgeCardsInput,
    ReadKnowledgeCardsOutput,
)
from taichu.domain.models.structured_knowledge import (
    StructuredKnowledgeLifecycle,
)


manifest = ToolManifest(
    name="read_knowledge_cards",
    description="按稳定卡片 ID 定向读取完整已确认知识，隔离其他生命周期。",
    input_schema=ReadKnowledgeCardsInput,
    output_schema=ReadKnowledgeCardsOutput,
    required_capabilities=frozenset({"knowledge_service"}),
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
    tool_input = ReadKnowledgeCardsInput.model_validate(input_data)
    service = context.require("knowledge_service", KnowledgeService)
    cards = []
    missing = []
    rejected = []
    for card_id in dict.fromkeys(tool_input.card_ids):
        try:
            card = await service.get_card(card_id)
        except KnowledgeCardNotFoundError:
            missing.append(card_id)
            continue
        if card.lifecycle is not StructuredKnowledgeLifecycle.CONFIRMED:
            rejected.append(card_id)
            continue
        cards.append(card)
    return ReadKnowledgeCardsOutput(
        cards=cards,
        missing_card_ids=missing,
        rejected_card_ids=rejected,
        source_refs=[f"knowledge:{card.id}" for card in cards],
    )
