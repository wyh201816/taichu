"""统一召回正文、已确认知识卡与多跳关系证据。"""

from pydantic import BaseModel

from taichu.application.capabilities import CapabilityContext
from taichu.application.invocations.models import InvocationContext
from taichu.application.tools._shared import INTERNAL_READ_CALLERS
from taichu.application.tools.contract import ToolManifest
from taichu.application.tools.models import (
    RetrieveStoryContextInput,
    RetrieveStoryContextOutput,
    StoryContextEvidence,
)
from taichu.application.vector_graph.service import VectorGraphRAGService


manifest = ToolManifest(
    name="retrieve_story_context",
    description=(
        "在正文与已确认知识卡之间先执行 BM25/Dense Passage 双路召回和 "
        "Milvus RRF，再由命中 Passage 驱动受控图扩展，合并 Graph Passage "
        "后执行一次 BGE 重排，并返回完成查询感知装配与权威回源的证据。"
    ),
    input_schema=RetrieveStoryContextInput,
    output_schema=RetrieveStoryContextOutput,
    required_capabilities=frozenset({"vector_graph_rag_service"}),
    exposures=frozenset({"agent_runtime"}),
    allowed_callers=INTERNAL_READ_CALLERS,
    default_timeout_seconds=120,
    retryable=True,
)


async def run(
    input_data: BaseModel,
    invocation: InvocationContext,
    context: CapabilityContext,
) -> BaseModel:
    del invocation
    tool_input = RetrieveStoryContextInput.model_validate(input_data)
    result = await context.require(
        "vector_graph_rag_service", VectorGraphRAGService
    ).retrieve(tool_input.query, top_k=tool_input.max_passages)
    return RetrieveStoryContextOutput(
        query=result.query,
        evidences=[
            StoryContextEvidence.model_validate(item.model_dump(mode="json"))
            for item in result.evidences
        ],
        retrieved_relations=result.retrieved_relations,
        expanded_relations=result.expanded_relations,
        context_relations=result.context_relations,
        source_refs=result.source_refs,
    )
