"""通过官方会话图验证手动压缩、提交中断与新请求隔离。"""

import asyncio

import pytest
from langgraph.store.memory import InMemoryStore

from taichu.application.capabilities import CapabilityContext
from taichu.application.general_agent.models import GeneralAgentRunStatus
from taichu.application.subagents.registry import SubagentRegistry
from taichu.application.tools.registry import ToolRegistry
from taichu.application.services.invocation_policy_service import (
    InvocationPolicyService,
)
from tests.unit.application.general_agent.test_context_pipeline import (
    engine,
    async_test,
)
from tests.unit.application.general_agent.test_runtime import (
    _runtime,
    _ScriptedChatModel,
    _TraceRepository,
)


def runtime_with_pipeline(root, answers, *, plans=2):
    policy, traces = InvocationPolicyService(), _TraceRepository()
    context = CapabilityContext(capabilities={"invocation_policy_service": policy})
    tools = ToolRegistry(context, traces)
    subagents = SubagentRegistry(
        CapabilityContext(capabilities={"tool_registry": tools}), traces
    )
    model = _ScriptedChatModel(
        plans=[
            {"rationale": "无需工具", "direct_response": "已记录约束", "nodes": []}
            for _ in range(plans)
        ],
        verification=[],
    )
    runtime = _runtime(root, model, tools, subagents, policy, traces)
    pipeline = engine(root, answers)
    pipeline.store = InMemoryStore()
    runtime._context_pipeline = pipeline
    runtime._executor.bind_context_boundary(runtime._after_node_batch)
    return runtime, pipeline


async def settle(runtime):
    for _ in range(100):
        tasks = list(runtime._tasks.values())
        if tasks:
            await asyncio.gather(*tasks)
        await asyncio.sleep(0)
        if not runtime._tasks:
            return
    pytest.fail("压缩任务未收敛")


@async_test
async def test_manual_failure_retry_uses_official_graph_and_does_not_swallow_next_turn(
    tmp_path,
):
    runtime, pipeline = runtime_with_pipeline(
        tmp_path,
        [
            {},
            ValueError("摘要暂不可用"),
            {"history_summary": "用户约束已记录。", "working_memory": {}},
            {},
        ],
    )
    run = await runtime.run(user_goal="请记住第三人称约束", model_id="test-model")
    assert run.status is GeneralAgentRunStatus.COMPLETED
    first = await runtime.request_context_compaction(run.conversation_id)
    second = await runtime.request_context_compaction(run.conversation_id)
    assert first.request_id == second.request_id
    await settle(runtime)
    assert (
        await runtime.context_compaction_status(run.conversation_id)
    ).status == "failed"
    await runtime.request_context_compaction(run.conversation_id)
    await settle(runtime)
    assert (
        await runtime.context_compaction_status(run.conversation_id)
    ).status == "completed"
    compacted = await runtime.get(run.run_id)
    assert len(compacted.messages) == len(run.messages)
    assert compacted.request_index == run.request_index
    following = await runtime.run(
        user_goal="继续确认约束",
        model_id="test-model",
        conversation_id=run.conversation_id,
    )
    assert following.status is GeneralAgentRunStatus.COMPLETED
    assert following.request_index == 2
    assert following.final_answer
    assert len(pipeline.model._calls) == 4
    await runtime.shutdown()


@async_test
async def test_new_request_is_checkpointed_before_background_execution(tmp_path):
    runtime, _ = runtime_with_pipeline(tmp_path, [{}])
    run = await runtime.create_run(
        user_goal="尚未调度的用户原文", model_id="test-model"
    )
    snapshot = await runtime._graph.aget_state(
        {"configurable": {"thread_id": run.conversation_id}}
    )
    assert snapshot.values["run"]["user_goal"] == "尚未调度的用户原文"
    assert snapshot.next == ("initialize",)
    await runtime.recover_interrupted()
    await settle(runtime)
    assert (await runtime.get(run.run_id)).status is GeneralAgentRunStatus.COMPLETED
    await runtime.shutdown()


@async_test
async def test_manual_request_arriving_at_completion_is_drained(tmp_path):
    runtime, _ = runtime_with_pipeline(
        tmp_path,
        [
            {},
            {"history_summary": "第一次摘要", "working_memory": {}},
            {"history_summary": "第二次摘要", "working_memory": {}},
        ],
    )
    run = await runtime.run(user_goal="记住叙事约束", model_id="test-model")
    committed, release, completed_again = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    original_execute = runtime._execute_manual_compaction
    executions = 0

    async def hold_first_completion(run_id):
        nonlocal executions
        executions += 1
        result = await original_execute(run_id)
        if executions == 1:
            committed.set()
            await release.wait()
        else:
            completed_again.set()
        return result

    runtime._execute_manual_compaction = hold_first_completion
    first = await runtime.request_context_compaction(run.conversation_id)
    await asyncio.wait_for(committed.wait(), timeout=5)
    second = await runtime.request_context_compaction(run.conversation_id)
    assert second.request_id != first.request_id
    release.set()
    await asyncio.wait_for(completed_again.wait(), timeout=5)
    await settle(runtime)
    assert (
        await runtime.context_compaction_status(run.conversation_id)
    ).status == "completed"
    assert len((await runtime.get(run.run_id)).messages) == len(run.messages)
    await runtime.shutdown()
