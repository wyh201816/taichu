"""人工澄清的当前轮边界与运行记忆生命周期契约。"""

from __future__ import annotations

from typing import Literal

import pytest

from taichu.application.agent_memory.models import (
    AgentMemoryKind,
    AgentMemoryValidity,
)
from taichu.application.general_agent.context import (
    _current_request,
    _raw_history_messages,
)
from taichu.application.general_agent.models import (
    GeneralAgentExecutionPlan,
    GeneralAgentMessage,
    GeneralAgentMessageType,
    GeneralAgentRun,
)
from taichu.application.services.agent_memory_service import AgentMemoryService
from tests.fakes.agent_memory import in_memory_agent_memory_repository

_NOW = "2026-08-30T00:00:00Z"


def _run(*, messages: list[GeneralAgentMessage]) -> GeneralAgentRun:
    current_request = next(
        message
        for message in messages
        if message.turn_id == "general_run_20260830_000000_turn01"
        and message.message_type is GeneralAgentMessageType.USER_REQUEST
    )
    return GeneralAgentRun(
        run_id="general_run_20260830_000000_turn01",
        task_id="conversation_turn_boundary",
        conversation_id="conversation_turn_boundary",
        request_index=2,
        parent_run_id="general_run_20260829_000000_turn00",
        user_goal="重复文本",
        messages=messages,
        current_request_message_id=current_request.message_id,
        created_at=_NOW,
        updated_at=_NOW,
        started_at=_NOW,
    )


def _message(
    ordinal: int,
    *,
    role: Literal["user", "assistant"],
    content: str,
    turn_id: str,
    request_index: int,
    message_type: GeneralAgentMessageType,
    human_request_id: str | None = None,
) -> GeneralAgentMessage:
    return GeneralAgentMessage(
        role=role,
        content=content,
        created_at=_NOW,
        message_id=f"message_{ordinal:032x}",
        turn_id=turn_id,
        request_index=request_index,
        message_type=message_type,
        human_request_id=human_request_id,
    )


def test_same_text_human_reply_does_not_replace_current_request_boundary() -> None:
    run = _run(
        messages=[
            _message(
                1,
                role="user",
                content="上一轮",
                turn_id="general_run_20260829_000000_turn00",
                request_index=1,
                message_type=GeneralAgentMessageType.USER_REQUEST,
            ),
            _message(
                2,
                role="assistant",
                content="上一轮答复",
                turn_id="general_run_20260829_000000_turn00",
                request_index=1,
                message_type=GeneralAgentMessageType.ASSISTANT_FINAL,
            ),
            _message(
                3,
                role="user",
                content="重复文本",
                turn_id="general_run_20260830_000000_turn01",
                request_index=2,
                message_type=GeneralAgentMessageType.USER_REQUEST,
            ),
            _message(
                4,
                role="assistant",
                content="请确认具体范围。",
                turn_id="general_run_20260830_000000_turn01",
                request_index=2,
                message_type=GeneralAgentMessageType.HUMAN_PROMPT,
                human_request_id="human_clarification_1",
            ),
            _message(
                5,
                role="user",
                content="重复文本",
                turn_id="general_run_20260830_000000_turn01",
                request_index=2,
                message_type=GeneralAgentMessageType.HUMAN_RESPONSE,
                human_request_id="human_clarification_1",
            ),
        ]
    )

    current = _current_request(run)
    history = _raw_history_messages(run)

    assert current.content == "重复文本"
    assert current.human_responses == ["重复文本"]
    assert [message.content for message in history] == ["上一轮", "上一轮答复"]


@pytest.mark.anyio
async def test_clarification_response_resolves_issue_without_becoming_preference(
    tmp_path,
) -> None:
    service = AgentMemoryService(
        repository=in_memory_agent_memory_repository(tmp_path)
    )
    run = _run(
        messages=[
            _message(
                1,
                role="user",
                content="重复文本",
                turn_id="general_run_20260830_000000_turn01",
                request_index=2,
                message_type=GeneralAgentMessageType.USER_REQUEST,
            ),
        ]
    )
    plan = GeneralAgentExecutionPlan(
        rationale="缺少范围。",
        requires_clarification=True,
        clarification_question="请确认具体范围。",
    )

    unresolved_id = await service.record_clarification_request(
        run,
        request_id="human_clarification_1",
        plan=plan,
    )
    response_id = await service.resolve_clarification(
        run,
        request_id="human_clarification_1",
        content="只检查第一章。",
    )

    unresolved = await service.get(unresolved_id)
    response = await service.get(response_id)
    assert unresolved is not None
    assert unresolved.validity is AgentMemoryValidity.SUPERSEDED
    assert response is not None
    assert response.kind is AgentMemoryKind.WORK_NOTE
    assert response.supersedes_memory_id == unresolved.memory_id

    current = await service.list_active(
        run.conversation_id,
        current_request_index=run.request_index,
        refresh_evidence=False,
    )
    next_turn = await service.list_active(
        run.conversation_id,
        current_request_index=run.request_index + 1,
        refresh_evidence=False,
    )
    assert response.memory_id in {entry.memory_id for entry in current}
    assert response.memory_id not in {entry.memory_id for entry in next_turn}
    assert all(entry.kind is not AgentMemoryKind.USER_INSTRUCTION for entry in current)


def test_legacy_messages_receive_deterministic_read_only_identity_projection() -> None:
    payload = _run(
        messages=[
            _message(
                1,
                role="user",
                content="重复文本",
                turn_id="general_run_20260830_000000_turn01",
                request_index=2,
                message_type=GeneralAgentMessageType.USER_REQUEST,
            )
        ]
    ).model_dump(mode="json")
    payload.pop("current_request_message_id")
    for message in payload["messages"]:
        for field in (
            "message_id",
            "turn_id",
            "request_index",
            "message_type",
            "human_request_id",
        ):
            message.pop(field)

    first = GeneralAgentRun.model_validate(payload)
    second = GeneralAgentRun.model_validate(payload)

    assert first.current_request_message_id is not None
    assert first.current_request_message_id == second.current_request_message_id
    assert first.messages[0].message_id == second.messages[0].message_id
