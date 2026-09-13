"""在显式许可下读取已选择的公开外部来源。"""

from uuid import uuid4

from pydantic import BaseModel

from taichu.application.capabilities import CapabilityContext
from taichu.application.external_research.service import ExternalResearchService
from taichu.application.invocations.models import InvocationContext
from taichu.application.tools._shared import (
    EXTERNAL_RESEARCH_CALLERS,
    sha256_text,
)
from taichu.application.tools.contract import ToolManifest
from taichu.application.tools.models import (
    ReadExternalSourceInput,
    ReadExternalSourceOutput,
)


manifest = ToolManifest(
    name="read_external_source",
    description="仅在明确用户许可下读取公开 HTML 或纯文本外部来源。",
    input_schema=ReadExternalSourceInput,
    output_schema=ReadExternalSourceOutput,
    required_capabilities=frozenset({"external_research_service"}),
    exposures=frozenset({"agent_runtime"}),
    allowed_callers=EXTERNAL_RESEARCH_CALLERS,
    requires_external_access=True,
    default_timeout_seconds=30,
    retryable=True,
)


async def run(
    input_data: BaseModel,
    invocation: InvocationContext,
    context: CapabilityContext,
) -> BaseModel:
    del invocation
    tool_input = ReadExternalSourceInput.model_validate(input_data)
    document = await context.require(
        "external_research_service",
        ExternalResearchService,
    ).read(tool_input.url)
    content = document.content[: tool_input.max_content_chars]
    source_id = f"external_source_{uuid4().hex}"
    return ReadExternalSourceOutput(
        source_id=source_id,
        url=document.url,
        final_url=document.final_url,
        title=document.title or document.final_url,
        content=content,
        content_sha256=sha256_text(document.content),
        truncated=len(content) < len(document.content),
        source_refs=[document.final_url],
    )
