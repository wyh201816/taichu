"""按不可变引用回读本会话完整结果，范围选择不影响程序绑定。"""

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field
from taichu.application.capabilities import CapabilityContext
from taichu.application.contracts.context_pipeline import ContextResultStore
from taichu.application.invocations.models import InvocationContext
from taichu.application.tools._shared import INTERNAL_READ_CALLERS
from taichu.application.tools.contract import ToolManifest


class ReadRuntimeResultInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    result_ref: str = Field(pattern=r"^result_[a-f0-9]{64}$")
    field_path: list[str] = Field(
        default_factory=list,
        description="依次选择对象字段或数组下标，空数组表示完整结果。",
    )
    view: Literal["content", "fields"] = Field(
        default="content", description="读取内容或枚举对象字段。"
    )
    text_unit: Literal["lines", "characters"] = Field(
        default="lines", description="文本范围按完整行或字符选择。"
    )
    start: int | None = Field(
        default=None, ge=0, description="数组条目或文本行的起始位置，从零计数。"
    )
    end: int | None = Field(default=None, ge=0, description="结束位置，不含该位置。")


class ReadRuntimeResultOutput(BaseModel):
    result_ref: str
    field_path: list[str]
    total_count: int | None
    start: int | None
    end: int | None
    data: Any
    source_refs: list[str]


manifest = ToolManifest(
    name="read_runtime_result",
    description="按完整结果引用回读本会话工具或专业子 Agent 产物，可选择字段、数组范围或文本行范围。",
    input_schema=ReadRuntimeResultInput,
    output_schema=ReadRuntimeResultOutput,
    required_capabilities=frozenset({"context_result_store"}),
    exposures=frozenset({"agent_runtime"}),
    allowed_callers=INTERNAL_READ_CALLERS,
    retryable=True,
)


async def run(
    input_data: BaseModel, invocation: InvocationContext, context: CapabilityContext
) -> BaseModel:
    request = ReadRuntimeResultInput.model_validate(input_data)
    store = context.require("context_result_store", ContextResultStore)
    conversation_id = invocation.conversation_id or invocation.task_id
    data: Any = await store.read(conversation_id, request.result_ref)
    for part in request.field_path:
        if isinstance(data, dict) and part in data:
            data = data[part]
        elif isinstance(data, list) and part.isdecimal() and int(part) < len(data):
            data = data[int(part)]
        else:
            raise ValueError("完整结果中不存在指定字段或数组下标。")
    if request.view == "fields":
        if not isinstance(data, dict):
            raise ValueError("只有对象结果可以枚举字段。")
        data = list(data)
    values = (
        (
            list(data)
            if request.text_unit == "characters"
            else data.splitlines(keepends=True)
        )
        if isinstance(data, str)
        else data
    )
    total = len(values) if isinstance(values, (list, dict)) else None
    if request.start is not None or request.end is not None:
        if not isinstance(values, list):
            raise ValueError("范围回读只适用于数组或文本行。")
        start, end = (
            request.start or 0,
            request.end if request.end is not None else len(values),
        )
        if end < start:
            raise ValueError("结束位置不能早于起始位置。")
        selected = values[start:end]
        data = "".join(selected) if isinstance(data, str) else selected
    return ReadRuntimeResultOutput(
        result_ref=request.result_ref,
        field_path=request.field_path,
        total_count=total,
        start=request.start,
        end=request.end,
        data=data,
        source_refs=[request.result_ref],
    )
