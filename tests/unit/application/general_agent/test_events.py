"""通用写作助手运行事件中心的订阅生命周期契约。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any, cast

import pytest

from taichu.application.general_agent.events import GeneralAgentEventCenter
from taichu.application.general_agent.models import GeneralAgentRun

_NOW = "2026-08-30T00:00:00Z"


def _run(
    *,
    checkpoint_revision: int,
    run_id: str = "general_run_20260830_000000_events",
) -> GeneralAgentRun:
    return GeneralAgentRun(
        run_id=run_id,
        task_id="conversation_events",
        conversation_id="conversation_events",
        request_index=1,
        user_goal="验证事件订阅背压。",
        checkpoint_revision=checkpoint_revision,
        created_at=_NOW,
        updated_at=_NOW,
        started_at=_NOW,
    )


@pytest.mark.anyio
async def test_slow_subscriber_keeps_latest_events_without_blocking_publish() -> None:
    center = GeneralAgentEventCenter(subscriber_queue_size=2)
    await center.publish(event_type="created", run=_run(checkpoint_revision=0))

    subscription = cast(
        AsyncGenerator[dict[str, Any], None],
        center.subscribe(),
    )
    snapshot = await anext(subscription)
    assert snapshot["event_type"] == "snapshot"

    for revision in (1, 2, 3):
        await asyncio.wait_for(
            center.publish(
                event_type="updated",
                run=_run(checkpoint_revision=revision),
            ),
            timeout=1,
        )

    retained = [await anext(subscription), await anext(subscription)]
    assert [event["checkpoint_revision"] for event in retained] == [2, 3]

    await subscription.aclose()
    assert not center._subscribers  # noqa: SLF001


def test_subscriber_queue_size_must_be_positive() -> None:
    with pytest.raises(ValueError, match="订阅队列容量必须大于零"):
        GeneralAgentEventCenter(subscriber_queue_size=0)


@pytest.mark.anyio
async def test_snapshot_replay_cache_is_bounded_and_keeps_latest_runs() -> None:
    center = GeneralAgentEventCenter(snapshot_cache_size=2)
    for index in range(3):
        await center.publish(
            event_type="updated",
            run=_run(
                checkpoint_revision=index,
                run_id=f"general_run_20260830_00000{index}_events",
            ),
        )

    assert await center.get_snapshot("general_run_20260830_000000_events") is None
    subscription = cast(
        AsyncGenerator[dict[str, Any], None],
        center.subscribe(),
    )
    replayed = [await anext(subscription), await anext(subscription)]
    assert [event["checkpoint_revision"] for event in replayed] == [1, 2]
    await subscription.aclose()
