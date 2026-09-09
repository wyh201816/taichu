"""现有传输层接入 LangChain ``BaseChatModel`` 的契约测试。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from taichu.infrastructure.llm.contracts import (
    LLMCost,
    LLMRequest,
    LLMResponse,
    LLMStreamEvent,
    LLMToolCall,
    LLMToolCallChunk,
    LLMUsage,
)
from taichu.application.invocations.config import model_call_config
from taichu.infrastructure.llm.adapter import GatewayChatModel


class _StructuredReply(BaseModel):
    status: Literal["ok"]
    native_schema_marker: str = Field(min_length=1)


class _CapabilityInput(BaseModel):
    chapter_order: int = Field(ge=1)


class _RecordingGateway:
    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        selected = (
            request.tool_choice
            if request.tool_choice not in {"auto", "none", "required"}
            else request.tools[-1].name
        )
        arguments = (
            '{"status":"ok","native_schema_marker":"来自原生工具参数"}'
            if selected == "_StructuredReply"
            else '{"chapter_order":1}'
        )
        return LLMResponse(
            text="",
            model_id=request.model_id,
            upstream_model=request.model_id,
            usage=LLMUsage(input_tokens=10, output_tokens=4, total_tokens=14),
            cost=LLMCost(),
            tool_calls=(
                LLMToolCall(
                    call_id="call_native_schema",
                    name=selected,
                    arguments_json=arguments,
                ),
            ),
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamEvent]:
        response = await self.complete(request)
        call = response.tool_calls[0]
        split_at = max(1, len(call.arguments_json) // 2)
        yield LLMStreamEvent(
            event_type="tool_call_delta",
            tool_call_chunk=LLMToolCallChunk(
                index=0,
                call_id=call.call_id,
                name=call.name,
                arguments_delta=call.arguments_json[:split_at],
            ),
            call_id="stream-call",
        )
        yield LLMStreamEvent(
            event_type="tool_call_delta",
            tool_call_chunk=LLMToolCallChunk(
                index=0,
                arguments_delta=call.arguments_json[split_at:],
            ),
            call_id="stream-call",
        )
        yield LLMStreamEvent(
            event_type="completed",
            response=response,
            usage=response.usage,
            call_id=response.call_id,
        )


@pytest.mark.anyio
async def test_structured_output_schema_is_sent_as_native_tool_parameter() -> None:
    gateway = _RecordingGateway()
    model = GatewayChatModel(gateway, model_id="test-model")

    result = await model.with_structured_output(
        _StructuredReply, include_raw=True
    ).ainvoke(
        [
            SystemMessage(content="只处理当前请求。"),
            HumanMessage(content="返回结构化结果。"),
        ],
        config=model_call_config(
            model_id="request-model",
            task_type="structured_service",
            task_name="结构化服务测试",
            chapter_ids=("chapter_001",),
            feature="原生结构化输出",
        ),
    )

    assert result["parsed"] == _StructuredReply(
        status="ok",
        native_schema_marker="来自原生工具参数",
    )
    request = gateway.requests[0]
    assert request.model_id == "request-model"
    assert request.task_type == "structured_service"
    assert request.task_name == "结构化服务测试"
    assert request.chapter_ids == ("chapter_001",)
    assert request.feature == "原生结构化输出"
    assert request.tool_choice == "required"
    assert request.tools[0].parameters["properties"]["native_schema_marker"]
    assert all(
        "native_schema_marker" not in message.content for message in request.messages
    )


@pytest.mark.anyio
async def test_named_tool_choice_is_preserved_for_forced_structured_output() -> None:
    gateway = _RecordingGateway()
    model = GatewayChatModel(gateway, model_id="test-model")
    bound = model.bind_tools(
        [_CapabilityInput, _StructuredReply],
        tool_choice="_StructuredReply",
    )

    message = await bound.ainvoke([HumanMessage(content="生成计划。")])

    assert message.tool_calls[0]["name"] == "_StructuredReply"
    request = gateway.requests[0]
    assert request.tool_choice == "_StructuredReply"
    assert [tool.name for tool in request.tools] == [
        "_CapabilityInput",
        "_StructuredReply",
    ]
    assert request.tools[0].parameters["properties"]["chapter_order"]


@pytest.mark.anyio
@pytest.mark.parametrize("strict", [None, False, True])
async def test_tool_binding_preserves_optional_fields_unless_strict_is_explicit(strict) -> None:
    from taichu.application.tools.models import ReadManuscriptInput

    gateway = _RecordingGateway()
    model = GatewayChatModel(gateway, model_id="test-model")
    options = {} if strict is None else {"strict": strict}
    await model.bind_tools(
        [ReadManuscriptInput, _StructuredReply], tool_choice="_StructuredReply", **options,
    ).ainvoke([HumanMessage(content="读取第99至100章。")])
    definition = gateway.requests[0].tools[0]
    assert definition.strict is (strict is True)
    required = definition.parameters.get("required", [])
    assert ("recent_count" in required) is (strict is True)
    assert definition.parameters["properties"]["chapter_ids"]["type"] == "array"


@pytest.mark.anyio
async def test_explicit_structured_strictness_keeps_official_parser_and_model_isolation() -> None:
    gateway = _RecordingGateway()
    model = GatewayChatModel(gateway, model_id="test-model")
    strict_chain = model.with_structured_output(_StructuredReply, strict=True, include_raw=True)
    relaxed_chain = model.with_structured_output(_StructuredReply, strict=False)
    strict_result = await strict_chain.ainvoke([HumanMessage(content="结构化结果。")])
    relaxed_result = await relaxed_chain.ainvoke([HumanMessage(content="结构化结果。")])
    assert isinstance(strict_result["parsed"], _StructuredReply)
    assert strict_result["parsing_error"] is None
    assert isinstance(relaxed_result, _StructuredReply)
    assert gateway.requests[0].tools[0].strict is True
    assert gateway.requests[1].tools[0].strict is False
    await model.bind_tools([_StructuredReply], tool_choice="_StructuredReply").ainvoke([HumanMessage(content="普通工具。")])
    assert gateway.requests[2].tools[0].strict is False


@pytest.mark.anyio
async def test_stream_uses_native_chunks_and_bound_request_settings() -> None:
    gateway = _RecordingGateway()
    model = GatewayChatModel(gateway, model_id="test-model")
    bound = model.bind_tools(
        [_StructuredReply],
        tool_choice="_StructuredReply",
    ).bind(
        model_id="stream-model",
        task_type="writing_continue",
        task_name="续写",
        taichu_run_id="writing-run-001",
        chapter_ids=("chapter_001",),
        feature="写作 AI",
    )

    chunks = [
        chunk
        async for chunk in bound.astream([HumanMessage(content="继续写。")])
    ]
    combined = chunks[0]
    for chunk in chunks[1:]:
        combined = combined + chunk

    assert combined.tool_calls[0]["name"] == "_StructuredReply"
    assert len(chunks) >= 3
    assert chunks[0].tool_call_chunks[0]["id"] == "call_native_schema"
    assert chunks[1].tool_call_chunks[0]["id"] is None
    request = gateway.requests[0]
    assert request.model_id == "stream-model"
    assert request.task_type == "writing_continue"
    assert request.task_name == "续写"
    assert request.run_id == "writing-run-001"
    assert request.chapter_ids == ("chapter_001",)
    assert request.feature == "写作 AI"
