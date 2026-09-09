"""Agent 模型调用追踪的身份与失败策略测试。"""

from __future__ import annotations

import logging

import pytest
from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage

from taichu.application.invocations.middleware import (
    ModelInvocationTraceMiddleware,
)
from taichu.application.invocations.models import (
    InvocationContext,
    InvocationTraceRecord,
)


class _TraceRepository:
    def __init__(self, *, fail: bool = False) -> None:
        self.records: list[InvocationTraceRecord] = []
        self._fail = fail

    async def append(self, record: InvocationTraceRecord) -> None:
        if self._fail:
            raise OSError("trace storage unavailable")
        self.records.append(record)


def _invocation() -> InvocationContext:
    return InvocationContext(
        task_id="conversation_trace",
        run_id="general_run_trace",
        call_id="call_trace_root",
        caller_type="subagent",
        caller_name="trace_test",
    )


def _request(messages: list[AnyMessage]) -> ModelRequest:
    return ModelRequest(
        model=FakeListChatModel(responses=["unused"]),
        messages=messages,
        tools=[],
    )


async def _success_handler(_: ModelRequest) -> ModelResponse:
    return ModelResponse(result=[AIMessage(content="完成")])


@pytest.mark.anyio
async def test_model_call_sequence_is_not_reported_as_retry_count() -> None:
    repository = _TraceRepository()
    middleware = ModelInvocationTraceMiddleware(
        repository=repository,
        invocation=_invocation(),
        requested_model_id="model-test",
        model_role="reviewer",
    )
    request = _request([HumanMessage(content="检查第一轮")])

    await middleware.awrap_model_call(request, _success_handler)
    await middleware.awrap_model_call(request, _success_handler)

    assert [record.call_sequence for record in repository.records] == [1, 2]
    assert [record.retry_count for record in repository.records] == [0, 0]
    assert [record.call_id for record in repository.records] == [
        "call_trace_root:model:1",
        "call_trace_root:model:2",
    ]


@pytest.mark.anyio
async def test_trace_input_hash_includes_tool_call_pairing_and_artifact_identity() -> None:
    repository = _TraceRepository()
    middleware = ModelInvocationTraceMiddleware(
        repository=repository,
        invocation=_invocation(),
        requested_model_id="model-test",
        model_role="reviewer",
    )
    first = _request(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "tool-call-a",
                        "name": "read_source",
                        "args": {"chapter": 1},
                        "type": "tool_call",
                    }
                ],
            ),
            ToolMessage(
                content="相同文本",
                tool_call_id="tool-call-a",
                artifact={"artifact_id": "artifact-a"},
            ),
        ]
    )
    second = _request(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "tool-call-b",
                        "name": "read_source",
                        "args": {"chapter": 1},
                        "type": "tool_call",
                    }
                ],
            ),
            ToolMessage(
                content="相同文本",
                tool_call_id="tool-call-b",
                artifact={"artifact_id": "artifact-b"},
            ),
        ]
    )

    await middleware.awrap_model_call(first, _success_handler)
    await middleware.awrap_model_call(second, _success_handler)

    assert repository.records[0].input_sha256 != repository.records[1].input_sha256


@pytest.mark.anyio
async def test_trace_write_failure_is_visible_but_does_not_hide_model_result(
    caplog: pytest.LogCaptureFixture,
) -> None:
    middleware = ModelInvocationTraceMiddleware(
        repository=_TraceRepository(fail=True),
        invocation=_invocation(),
        requested_model_id="model-test",
        model_role="reviewer",
    )

    with caplog.at_level(logging.ERROR):
        response = await middleware.awrap_model_call(
            _request([HumanMessage(content="继续")]),
            _success_handler,
        )

    assert response.result[0].content == "完成"
    assert "调用追踪写入失败" in caplog.text
