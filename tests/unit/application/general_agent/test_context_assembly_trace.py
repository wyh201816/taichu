"""需求 9.9、10.2、10.7、11.2、12.1：五层 AssemblyTrace。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from taichu.application.general_agent.context import (
    ContextAssembler,
)
from taichu.application.general_agent.models import (
    GeneralAgentContextSnapshot,
    GeneralAgentExecutionPlan,
    GeneralAgentInputBinding,
    GeneralAgentMessage,
    GeneralAgentNodeKind,
    GeneralAgentNodeRun,
    GeneralAgentNodeStatus,
    GeneralAgentPlanNode,
    GeneralAgentRun,
    GeneralAgentScope,
    context_snapshot_sha256,
)
from taichu.application.services.agent_memory_service import AgentMemoryService
from tests.fakes.agent_memory import in_memory_agent_memory_repository
from taichu.infrastructure.general_agent_runs.context_snapshot_repository import (
    JsonGeneralAgentContextSnapshotRepository,
)


def _memory_service(root: Path) -> AgentMemoryService:
    return AgentMemoryService(
        repository=in_memory_agent_memory_repository(root),
    )


def _run_with_large_result() -> GeneralAgentRun:
    timestamp = "2026-07-30T12:00:00Z"
    source_node = GeneralAgentPlanNode(
        node_id="source",
        kind=GeneralAgentNodeKind.TOOL,
        capability_name="get_novel_structure",
        objective="读取完整结构。",
    )
    consumer_node = GeneralAgentPlanNode(
        node_id="consumer",
        kind=GeneralAgentNodeKind.SUBAGENT,
        capability_name="story_architecture",
        objective="消费结构中的条目。",
        dependencies=["source"],
        input_bindings=[
            GeneralAgentInputBinding(
                source_node_id="source",
                source_path="items",
                target_path="structure_items",
            )
        ],
    )
    plan = GeneralAgentExecutionPlan(
        rationale="先读取结构，再交给架构分析。",
        nodes=[source_node, consumer_node],
        final_response_guidance="基于结构回答。",
    )
    output = {
        "items": [
            {
                "item_id": f"item_{index:03d}",
                "title": f"结构条目 {index}",
                "summary": "海雾中的灯塔线索" * 20,
            }
            for index in range(80)
        ],
        "total": 80,
    }
    node_run = GeneralAgentNodeRun(
        node_id="source",
        plan_revision=1,
        kind=GeneralAgentNodeKind.TOOL,
        capability_name="get_novel_structure",
        objective="读取完整结构。",
        status=GeneralAgentNodeStatus.SUCCESS,
        output=output,
        source_refs=["structure:root"],
        artifact_refs=["artifact_structure"],
    )
    messages = [
        GeneralAgentMessage(
            role="user" if index % 2 == 0 else "assistant",
            content=f"历史消息 {index}：" + "旧上下文" * 100,
            created_at=f"2026-07-30T11:{index:02d}:00Z",
        )
        for index in range(20)
    ]
    return GeneralAgentRun(
        run_id="general_run_20260730_120000_abc123",
        task_id="conversation_trace",
        conversation_id="conversation_trace",
        request_index=11,
        user_goal="  请保留这段请求的首尾空格。\n第二行也必须保留。  ",
        author_constraints=["不得改变灯塔火焰颜色"],
        scope=GeneralAgentScope(scope_type="novel"),
        messages=messages,
        plan=plan,
        plan_revision=1,
        node_runs=[node_run],
        created_at=timestamp,
        updated_at=timestamp,
        started_at=timestamp,
    )


def test_new_snapshot_contains_complete_five_layer_assembly_trace(
    tmp_path: Path,
) -> None:
    async def scenario() -> GeneralAgentContextSnapshot:
        return (
            await ContextAssembler(
                memory_service=_memory_service(tmp_path),
            ).assemble(_run_with_large_result(), phase="verify")
        ).snapshot

    snapshot = asyncio.run(scenario())
    trace = snapshot.assembly_trace

    assert trace is not None
    assert [layer.layer for layer in trace.layers] == [
        "stable_memory",
        "working_memory",
        "long_term_memory",
        "history_memory",
        "current_request",
    ]
    assert all(layer.pre_char_count >= layer.post_char_count for layer in trace.layers)
    assert all(layer.pre_token_estimate >= 0 for layer in trace.layers)
    assert "current_request" in trace.protected_refs
    assert "stable_memory" in trace.protected_refs
    assert trace.current_request_sha256
    assert trace.stable_memory_sha256
    assert trace.digest_used is False
    assert not snapshot.envelope.pipeline_events
    assert "total_char_budget" not in snapshot.policy_snapshot
    assert (
        snapshot.envelope.current_request.content == _run_with_large_result().user_goal
    )


def test_snapshot_repository_round_trips_new_trace(tmp_path: Path) -> None:
    async def scenario() -> None:
        snapshot = (
            await ContextAssembler(
                memory_service=_memory_service(tmp_path / "memory")
            ).assemble(_run_with_large_result(), phase="plan")
        ).snapshot
        repository = JsonGeneralAgentContextSnapshotRepository(tmp_path / "assets")
        await repository.save(snapshot)

        loaded = await repository.list_for_run(snapshot.run_id)

        assert loaded == [snapshot]
        assert loaded[0].assembly_trace is not None
        assert loaded[0].assembly_trace.trace_sha256 == (
            snapshot.assembly_trace.trace_sha256
        )

    asyncio.run(scenario())


def test_pre_trace_snapshot_is_read_only_compatible_and_not_backfilled(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        snapshot = (
            await ContextAssembler(
                memory_service=_memory_service(tmp_path / "memory")
            ).assemble(_run_with_large_result(), phase="plan")
        ).snapshot
        legacy_payload = snapshot.model_dump(
            mode="json",
            exclude={"content_sha256", "assembly_trace"},
        )
        legacy_payload["content_sha256"] = context_snapshot_sha256(legacy_payload)

        restored = GeneralAgentContextSnapshot.model_validate(legacy_payload)
        assert restored.assembly_trace is None

        repository = JsonGeneralAgentContextSnapshotRepository(tmp_path / "assets")
        directory = (
            tmp_path
            / "assets"
            / "derived"
            / "general_agent_context_snapshots"
            / snapshot.run_id
        )
        directory.mkdir(parents=True)
        path = directory / f"{snapshot.snapshot_id}.json"
        path.write_text(
            json.dumps(legacy_payload, ensure_ascii=False),
            encoding="utf-8",
        )

        loaded = await repository.list_for_run(snapshot.run_id)
        assert len(loaded) == 1
        assert loaded[0].assembly_trace is None

    asyncio.run(scenario())
