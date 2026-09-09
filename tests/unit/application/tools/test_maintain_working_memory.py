"""高层编排 Agent 主动维护工作记忆的状态门禁。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from functools import wraps
from pathlib import Path
from typing import Any

import pytest
from langgraph.store.memory import InMemoryStore
from pydantic import ValidationError

from taichu.application.agent_memory.models import AgentMemoryValidity
from taichu.application.capabilities import CapabilityContext
from taichu.application.general_agent.context import ContextAssembler
from taichu.application.general_agent.models import (
    GeneralAgentNodeKind,
    GeneralAgentNodeRun,
    GeneralAgentNodeStatus,
    GeneralAgentRun,
)
from taichu.application.invocations.models import InvocationContext
from taichu.application.services.agent_memory_service import (
    AgentMemoryService,
    AgentMemoryServiceError,
)
from taichu.application.services.invocation_policy_service import (
    InvocationPolicyService,
)
from taichu.application.tools import maintain_working_memory
from taichu.application.tools.contract import (
    ToolPlugin,
    ToolReconciliationStatus,
)
from taichu.application.tools.models import (
    MaintainWorkingMemoryInput,
    MaintainWorkingMemoryOutput,
)
from taichu.application.tools.registry import ToolRegistry
from taichu.infrastructure.agent_memory import LangGraphAgentMemoryRepository
from taichu.infrastructure.general_agent_runs import JsonGeneralAgentRunRepository


def _async_test(
    test: Callable[..., Coroutine[Any, Any, None]],
) -> Callable[..., None]:
    @wraps(test)
    def run(*args: Any, **kwargs: Any) -> None:
        asyncio.run(test(*args, **kwargs))

    return run


@_async_test
async def test_orchestrator_can_record_replace_and_invalidate_guarded_state(
    tmp_path: Path,
) -> None:
    run = _run()
    run_repository = JsonGeneralAgentRunRepository(tmp_path)
    await run_repository.save(run)
    memory_service = AgentMemoryService(
        repository=LangGraphAgentMemoryRepository(InMemoryStore())
    )
    context = CapabilityContext(
        capabilities={
            "agent_memory_service": memory_service,
            "general_agent_run_repository": run_repository,
            "invocation_policy_service": InvocationPolicyService(),
        }
    )
    registry = ToolRegistry(context)
    registry.register(
        ToolPlugin(
            manifest=maintain_working_memory.manifest,
            run=maintain_working_memory.run,
            reconcile=maintain_working_memory.reconcile,
        )
    )
    invocation = InvocationContext(
        task_id=run.task_id,
        run_id=run.run_id,
        conversation_id=run.conversation_id,
        caller_type="test",
        caller_name="test",
    )

    remember_payload = {
        "operation": "remember",
        "kind": "task_summary",
        "content": "当前主线是秦阳逐步发现密道，但暂时不知道入口位置。",
        "retention": "until_replaced",
        "idempotency_key": "memory-remember-0001",
    }
    remembered_envelope = await registry.invoke(
        "maintain_working_memory",
        remember_payload,
        invocation,
    )
    remembered = MaintainWorkingMemoryOutput.model_validate(remembered_envelope.output)
    assert remembered.validity is AgentMemoryValidity.ACTIVE
    assert remembered.expires_after_request_index is None

    recorded_ids = await memory_service.record_node_results(
        run,
        [
            GeneralAgentNodeRun(
                node_id="remember_state",
                plan_revision=0,
                kind=GeneralAgentNodeKind.TOOL,
                capability_name="maintain_working_memory",
                objective="保存需要跨步骤保持的认知约束。",
                status=GeneralAgentNodeStatus.SUCCESS,
                output=remembered.model_dump(mode="json"),
            )
        ],
    )
    assert recorded_ids == [remembered.memory_id]
    assert len(await memory_service.list_for_conversation(run.conversation_id)) == 1

    reconciliation = await registry.reconcile(
        "maintain_working_memory",
        remember_payload,
        invocation,
    )
    assert reconciliation.status is ToolReconciliationStatus.SUCCEEDED

    assembled = await ContextAssembler(memory_service=memory_service).assemble(
        run,
        phase="plan",
    )
    projected = next(
        item
        for item in assembled.snapshot.envelope.working_memory.memories
        if item.memory_id == remembered.memory_id
    )
    assert projected.state_sha256 == remembered.state_sha256
    assert projected.validity == "active"

    replace_payload = {
        "operation": "replace",
        "content": "秦阳已从石碑裂纹推断出密道存在，但还不知道入口位置。",
        "target_memory_id": remembered.memory_id,
        "expected_target_state_sha256": remembered.state_sha256,
        "retention": "until_replaced",
        "idempotency_key": "memory-replace-0001",
    }
    replaced_envelope = await registry.invoke(
        "maintain_working_memory",
        replace_payload,
        invocation,
    )
    replacement = MaintainWorkingMemoryOutput.model_validate(replaced_envelope.output)
    old_entry = await memory_service.get(remembered.memory_id)
    assert old_entry is not None
    assert old_entry.validity is AgentMemoryValidity.SUPERSEDED
    assert replacement.supersedes_memory_id == remembered.memory_id

    with pytest.raises(AgentMemoryServiceError, match="已经失效、被替代或过期"):
        await registry.invoke(
            "maintain_working_memory",
            {
                "operation": "invalidate",
                "target_memory_id": remembered.memory_id,
                "expected_target_state_sha256": remembered.state_sha256,
                "invalidation_validity": "stale",
                "reason": "尝试使用已经被替代的旧状态。",
                "idempotency_key": "memory-stale-guard-0001",
            },
            invocation,
        )

    invalidated_envelope = await registry.invoke(
        "maintain_working_memory",
        {
            "operation": "invalidate",
            "target_memory_id": replacement.memory_id,
            "expected_target_state_sha256": replacement.state_sha256,
            "invalidation_validity": "rejected",
            "reason": "用户明确纠正：秦阳尚未发现石碑裂纹。",
            "idempotency_key": "memory-invalidate-0001",
        },
        invocation,
    )
    invalidated = MaintainWorkingMemoryOutput.model_validate(
        invalidated_envelope.output
    )
    assert invalidated.validity is AgentMemoryValidity.REJECTED
    active = await memory_service.list_active(
        run.conversation_id,
        current_request_index=run.request_index,
    )
    assert replacement.memory_id not in {entry.memory_id for entry in active}


def test_memory_tool_schema_requires_evidence_and_fresh_target_state() -> None:
    with pytest.raises(ValidationError, match="事实引用工作记忆必须提供"):
        MaintainWorkingMemoryInput.model_validate(
            {
                "operation": "remember",
                "kind": "fact_reference",
                "content": "主角当前境界",
                "idempotency_key": "memory-fact-0001",
            }
        )
    with pytest.raises(ValidationError, match="目标及其状态哈希"):
        MaintainWorkingMemoryInput.model_validate(
            {
                "operation": "replace",
                "content": "新的状态",
                "target_memory_id": "memory_20260901_010101_abcdef12",
                "idempotency_key": "memory-replace-0002",
            }
        )


def _run() -> GeneralAgentRun:
    timestamp = "2026-09-01T01:01:01Z"
    return GeneralAgentRun(
        run_id="general_run_20260901_010101_abcdef",
        task_id="conversation_working_memory_tool",
        conversation_id="conversation_working_memory_tool",
        request_index=7,
        user_goal="规划接下来三章，并持续维护主角对密道的认知状态。",
        created_at=timestamp,
        updated_at=timestamp,
        started_at=timestamp,
    )
