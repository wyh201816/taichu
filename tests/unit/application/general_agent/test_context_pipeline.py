"""主 Agent 五级流水线：原文、来源、边界、原生协议与失败不变量。"""

import asyncio
from datetime import datetime, timedelta, UTC
from functools import wraps
import json
from hashlib import sha256

import pytest
from langchain.agents.middleware import ModelRequest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ChatMessage, SystemMessage
from langchain_core.outputs import ChatResult, ChatGeneration
from langgraph.store.memory import InMemoryStore
from pydantic import PrivateAttr, ValidationError

from taichu.application.general_agent.models import (
    GeneralAgentRun,
    GeneralAgentMessage,
    GeneralAgentContextEnvelope,
    GeneralAgentCurrentRequest,
    GeneralAgentNodeRun,
)
from taichu.application.general_agent.pipeline import (
    ContextPipeline,
    ContextCapacityError,
    project_envelope,
    message_id,
    apply_memory_patch,
)
from taichu.application.general_agent.pipeline_models import (
    WorkingMemoryPatch,
    SessionWorkingMemory,
    ContextPipelineState,
    ResultProjection,
)
from taichu.infrastructure.general_agent_runs.result_files import JsonContextResultStore
from taichu.infrastructure.llm.context_tokens import ModelContextTokens
from taichu.application.tools.read_runtime_result import (
    run as read_result,
    ReadRuntimeResultInput,
)
from taichu.application.capabilities import CapabilityContext
from taichu.application.invocations.models import InvocationContext


def async_test(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))

    return wrapped


class Model(BaseChatModel):
    model_id: str = "test-model"
    answers: list = []
    _calls: list = PrivateAttr(default_factory=list)

    @property
    def _llm_type(self):
        return "context-test-model"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self.bind(tools=tools, tool_choice=tool_choice, **kwargs)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self._calls.append((messages, kwargs))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, dict):
            choice = kwargs["tool_choice"]
            answer = AIMessage(
                content="",
                tool_calls=[{"id": "result", "name": choice, "args": answer}],
            )
        else:
            answer = AIMessage(content=answer)
        return ChatResult(generations=[ChatGeneration(message=answer)])


def make_run(*, rounds=0, length=30, **updates):
    now = "2026-09-13T00:00:00Z"
    messages = []
    for index in range(rounds):
        messages.extend(
            [
                GeneralAgentMessage(
                    role=role,
                    content=f"{index}:{role}:" + "文" * length,
                    created_at=now,
                    message_id="message_"
                    + sha256(f"{index}:{role}".encode()).hexdigest()[:32],
                    turn_id=f"old_{index}",
                    request_index=index + 1,
                    message_type="user_request"
                    if role == "user"
                    else "assistant_final",
                )
                for role in ("user", "assistant")
            ]
        )
    return GeneralAgentRun(
        run_id="general_run_20260913_000000_abcdef",
        task_id="conversation",
        conversation_id="conversation",
        request_index=rounds + 1,
        user_goal="保留当前原文\n  不得改写。",
        messages=messages,
        model_id="test-model",
        created_at=now,
        updated_at=now,
        started_at=now,
        **updates,
    )


def engine(tmp_path, answers):
    model = Model(answers=answers)
    counter = ModelContextTokens(
        windows={"test-model": 100_000, "deepseek-v4-flash": 100_000}
    )
    return ContextPipeline(
        model=model,
        counter=counter,
        result_store=JsonContextResultStore(tmp_path),
        store=InMemoryStore(),
    )


def request_for(run, pipeline):
    envelope = project_envelope(
        GeneralAgentContextEnvelope(
            phase="plan",
            stable_memory=["稳定规则"],
            current_request=GeneralAgentCurrentRequest(content=run.user_goal),
        ),
        run,
        run.context_pipeline,
    )
    messages = [
        ChatMessage(
            role="developer",
            content="{}",
            additional_kwargs={"context_layer": "history_summary"},
        )
    ]
    messages.extend(
        (HumanMessage if m.role == "user" else AIMessage)(
            content=m.content,
            id=message_id(m),
            additional_kwargs={"context_layer": "history_raw"},
        )
        for m in envelope.history_memory.messages
    )
    messages += [
        ChatMessage(
            role="developer",
            content=json.dumps(
                {"工作记忆": envelope.working_memory.model_dump(mode="json")},
                ensure_ascii=False,
            ),
            additional_kwargs={"context_layer": "working_memory"},
        ),
        HumanMessage(content=run.user_goal),
    ]
    return envelope, ModelRequest(
        model=pipeline.model,
        messages=messages,
        tools=[],
        system_message=SystemMessage(content="稳定规则"),
    )


@async_test
async def test_failed_message_remains_pending_after_recent_window_moves(tmp_path):
    p = engine(tmp_path, [ValueError("临时故障"), {}])
    first = make_run(rounds=1)
    failed = await p.extract(first)
    assert failed.context_pipeline.pending_message_ids
    later = make_run(rounds=5).model_copy(
        update={"context_pipeline": failed.context_pipeline}
    )
    recovered = await p.extract(later)
    assert not recovered.context_pipeline.pending_message_ids
    sent = json.dumps([m.content for m in p.model._calls[-1][0]], ensure_ascii=False)
    assert first.messages[0].content in sent
    assert any(isinstance(m, HumanMessage) for m in p.model._calls[-1][0])


@async_test
async def test_full_boundary_covers_completed_current_answer_and_next_turn(tmp_path):
    p = engine(
        tmp_path,
        [{"history_summary": "作者指定第三人称，助手已确认。", "working_memory": {}}],
    )
    run = make_run(rounds=0)
    run = run.model_copy(
        update={
            "messages": [
                GeneralAgentMessage(
                    role=role,
                    content="当前问题" if role == "user" else "已确认第三人称。",
                    created_at=run.created_at,
                    message_id="message_" + sha256(role.encode()).hexdigest()[:32],
                    turn_id=run.run_id,
                    request_index=run.request_index,
                    message_type="user_request"
                    if role == "user"
                    else "assistant_final",
                )
                for role in ("user", "assistant")
            ]
        }
    )
    await p.request_manual(run.conversation_id, run.context_pipeline)
    envelope, request = request_for(run, p)
    assert envelope.history_memory.messages[-1].content == "已确认第三人称。"
    with p.scope(run, envelope) as scope:
        await p.before_model(scope, request)
    later = scope.apply(run).model_copy(
        update={"run_id": "general_run_20260913_000001_abcdef", "request_index": 2}
    )
    assert not project_envelope(envelope, later, scope.state).history_memory.messages


@async_test
async def test_large_result_is_stable_durable_and_range_readable(tmp_path):
    p = engine(tmp_path, [])
    output = {"text": "完整行\n" * 20_000, "items": list(range(20_000))}
    node = GeneralAgentNodeRun(
        node_id="read",
        plan_revision=0,
        kind="tool",
        capability_name="read_manuscript",
        objective="读取正文",
        status="success",
        output=output,
    )
    run = await p.prepare(make_run(node_runs=[node]), extract=False)
    preview = next(iter(run.context_pipeline.results.values()))
    assert preview.truncated and preview.preview_tokens <= 4000
    assert await p.result_store.read(run.conversation_id, preview.result_ref) == output
    assert run.node_runs[0].output == output
    again = await p.prepare(run, extract=False)
    assert again.context_pipeline.results == run.context_pipeline.results
    result = await read_result(
        ReadRuntimeResultInput(
            result_ref=preview.result_ref, field_path=["items"], start=9990, end=10000
        ),
        InvocationContext(
            task_id="conversation",
            run_id=run.run_id,
            caller_type="orchestrator",
            caller_name="test",
            phase="read",
        ),
        CapabilityContext({"context_result_store": p.result_store}),
    )
    assert result.data == list(range(9990, 10000))
    with pytest.raises(ValueError, match="不属于"):
        await p.result_store.read("another-conversation", preview.result_ref)


@async_test
async def test_subagent_large_result_uses_same_preview(tmp_path):
    p = engine(tmp_path, [])
    node = GeneralAgentNodeRun(
        node_id="draft",
        plan_revision=0,
        kind="subagent",
        capability_name="drafting",
        objective="创作候选",
        status="success",
        output={"content": "段落\n" * 20000},
    )
    run = await p.prepare(make_run(node_runs=[node]), extract=False)
    assert next(iter(run.context_pipeline.results.values())).truncated
    assert not run.context_pipeline.folds


def test_preview_keeps_long_complete_line_when_it_fits():
    from taichu.application.general_agent.result_preview import result_preview

    line = "完整段落" * 200 + "\n"
    preview, _, size, truncated = result_preview(
        line * 10,
        reference="result_" + "a" * 64,
        counter=ModelContextTokens(),
        limit=4000,
    )
    assert truncated and size <= 4000
    assert preview["内容预览"]["文本预览"] == line


@pytest.mark.parametrize("elapsed,cleared", [(3599, False), (3600, True), (3601, True)])
@async_test
async def test_idle_boundary_whitelist_and_recent_rounds(tmp_path, elapsed, cleared):
    p = engine(tmp_path, [])
    results = {}
    for index, capability in enumerate(
        ["read_manuscript", "drafting", "apply_manuscript_patch", "read_runtime_result"]
    ):
        results[str(index)] = ResultProjection(
            result_ref="result_" + "a" * 64,
            source_id=str(index),
            run_id="old",
            request_index=1 if index < 3 else 5,
            node_id="node",
            capability_name=capability,
            kind="tool",
            status="success",
            objective="读取",
            content_sha256="b" * 64,
            preview={"text": "内容"},
            original_tokens=3,
            preview_tokens=3,
            truncated=False,
        )
    now = datetime(2026, 9, 13, 0, 0, tzinfo=UTC)
    from unittest.mock import patch

    with patch(
        "taichu.application.general_agent.pipeline.now_iso",
        return_value=now.isoformat(),
    ):
        run = make_run(
            rounds=5,
            context_pipeline=ContextPipelineState(
                results=results,
                last_activity_at=(now - timedelta(seconds=elapsed)).isoformat(),
            ),
        )
        first = await p.prepare(run, extract=False)
        second = await p.prepare(first, extract=False)
    assert first.context_pipeline.results["0"].cleared is cleared
    assert not any(
        first.context_pipeline.results[key].cleared for key in ("1", "2", "3")
    )
    assert len(
        [e for e in second.context_pipeline.events if e.stage == "clear"]
    ) == int(cleared)


@async_test
async def test_empty_patch_native_output_and_cursor_replay(tmp_path):
    p = engine(tmp_path, [{}])
    run = make_run(rounds=1)
    updated = await p.extract(run)
    assert updated.context_pipeline.processed_sources
    assert updated.context_pipeline.working_memory == SessionWorkingMemory()
    again = await p.extract(updated)
    assert again.context_pipeline == updated.context_pipeline
    assert len(p.model._calls) == 1
    _, native = p.model._calls[0]
    assert native["tool_choice"] == "WorkingMemoryPatch"
    assert any(t["function"]["name"] == "WorkingMemoryPatch" for t in native["tools"])


def test_patch_add_correct_remove_and_source_validation():
    patch = WorkingMemoryPatch(
        constraints={"viewpoint": {"content": "第三人称", "source_ids": ["m1"]}}
    )
    memory = apply_memory_patch(SessionWorkingMemory(), patch, {"m1"})
    revised = apply_memory_patch(
        memory,
        WorkingMemoryPatch(
            constraints={"viewpoint": {"content": "第一人称", "source_ids": ["m2"]}}
        ),
        {"m1", "m2"},
    )
    assert revised.constraints["viewpoint"].content == "第一人称"
    assert not apply_memory_patch(
        revised, WorkingMemoryPatch(constraints={"viewpoint": None}), {"m2"}
    ).constraints
    with pytest.raises(ValueError, match="来源"):
        apply_memory_patch(SessionWorkingMemory(), patch, set())
    with pytest.raises(ValidationError):
        WorkingMemoryPatch.model_validate({"node_status": "success"})


@async_test
async def test_extraction_failure_does_not_advance_cursor(tmp_path):
    p = engine(tmp_path, [ValueError("模型失败"), {}])
    run = await p.extract(make_run(rounds=1))
    assert not run.context_pipeline.processed_sources
    assert run.context_pipeline.events[-1].status == "failed"
    run = await p.extract(run)
    assert run.context_pipeline.processed_sources


@async_test
async def test_invalid_source_model_output_is_not_reused_on_retry(tmp_path):
    p = engine(
        tmp_path,
        [
            {
                "constraints": {
                    "bad": {"content": "无依据约束", "source_ids": ["made_up"]}
                }
            },
            {},
        ],
    )
    run = make_run(rounds=1)
    failed = await p.extract(run)
    assert not failed.context_pipeline.processed_sources
    recovered = await p.extract(failed)
    assert recovered.context_pipeline.processed_sources
    assert len(p.model._calls) == 2


@pytest.mark.parametrize("rounds,folded", [(20, False), (21, True)])
@async_test
async def test_round_trigger_preserves_recent_five_originals(tmp_path, rounds, folded):
    p = engine(tmp_path, ["旧对话中约定保持第三人称，任务尚未完成。"])
    run = make_run(rounds=rounds)
    envelope, request = request_for(run, p)
    with p.scope(run, envelope) as scope:
        updated = await p.before_model(scope, request)
    assert bool(scope.state.folds) is folded
    assert updated.messages[-1].content == run.user_goal
    if folded:
        assert [m.content for m in scope.envelope.history_memory.messages[-5:]] == [
            m.content for m in run.messages[-5:]
        ]
        assert set(scope.state.folds[0].message_ids).isdisjoint(
            message_id(m) for m in run.messages[-5:]
        )
        assert p.model._calls[0][1].get("tool_choice", "none") == "none"


@async_test
async def test_manual_full_is_coalesced_and_historical_raw_does_not_return(tmp_path):
    p = engine(
        tmp_path,
        [{"history_summary": "作者要求第三人称，准备继续写作。", "working_memory": {}}],
    )
    run = make_run(rounds=8)
    first = await p.request_manual(run.conversation_id, run.context_pipeline)
    second = await p.request_manual(run.conversation_id, run.context_pipeline)
    assert first.request_id == second.request_id
    envelope, request = request_for(run, p)
    with p.scope(run, envelope) as scope:
        updated = await p.before_model(scope, request)
        # 参数修复仍拿着最初父输入时，重放同一活动边界，不能复活旧原文。
        retry = await p.before_model(scope, request)
        assert retry.messages == updated.messages
        assert len(p.model._calls) == 1
    assert not scope.envelope.history_memory.messages
    assert updated.messages[-1].content == run.user_goal
    restored = ContextPipelineState.model_validate_json(scope.state.model_dump_json())
    assert not project_envelope(envelope, run, restored).history_memory.messages
    assert (await p.manual_status(run.conversation_id, restored)).status == "completed"
    assert len(run.messages) == 16


@async_test
async def test_full_failure_keeps_history_and_pending_sources(tmp_path):
    p = engine(tmp_path, [ValueError("摘要服务不可用")])
    run = make_run(rounds=3)
    await p.request_manual(run.conversation_id, run.context_pipeline)
    envelope, request = request_for(run, p)
    with p.scope(run, envelope) as scope:
        with pytest.raises(ContextCapacityError, match="暂停"):
            await p.before_model(scope, request)
    assert not scope.state.folds
    assert len(scope.envelope.history_memory.messages) == 6
    assert (await p.manual_status(run.conversation_id, scope.state)).status == "failed"


@pytest.mark.parametrize("length,stage", [(13700, "fold"), (15100, "full")])
@async_test
async def test_window_threshold_dispatch_and_seventy_percent_target(
    tmp_path, length, stage
):
    answer = (
        "旧对话约定第三人称，尚需继续写作。"
        if stage == "fold"
        else {
            "history_summary": "旧对话约定第三人称，尚需继续写作。",
            "working_memory": {},
        }
    )
    p = engine(tmp_path, [answer])
    p.counter = ModelContextTokens(windows={"test-model": 1_000_000})
    run = make_run(rounds=10, length=length)
    envelope, request = request_for(run, p)
    with p.scope(run, envelope) as scope:
        updated = await p.before_model(scope, request)
    assert scope.state.events[-1].stage == stage
    assert scope.state.events[-1].status == "completed"
    assert scope.state.events[-1].after_tokens < 700_000
    assert updated.messages[-1].content == run.user_goal
    assert len(p.model._calls) == 1
    if stage == "full":
        assert not scope.envelope.history_memory.messages
        assert not any(event.stage == "fold" for event in scope.state.events)


@async_test
async def test_partial_failure_continues_with_identical_input(tmp_path):
    p = engine(tmp_path, [ValueError("临时模型故障")])
    p.counter = ModelContextTokens(windows={"test-model": 1_000_000})
    run = make_run(rounds=10, length=13700)
    envelope, request = request_for(run, p)
    with p.scope(run, envelope) as scope:
        updated = await p.before_model(scope, request)
    assert updated.messages == request.messages
    assert not scope.state.folds
    assert scope.state.last_fold_request_index == 0
    assert scope.state.events[-1].status == "failed"


@async_test
async def test_full_cannot_silently_trim_protected_content(tmp_path):
    p = engine(tmp_path, [{"history_summary": "旧对话", "working_memory": {}}])
    run = make_run(rounds=2)
    envelope, request = request_for(run, p)
    # 原生能力定义本身超过窗口；历史压缩不能偷偷删掉能力契约。
    request = request.override(
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "large_contract",
                    "description": "定义" * 50_000,
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
    )
    with p.scope(run, envelope) as scope:
        with pytest.raises(ContextCapacityError):
            await p.before_model(scope, request)
    assert scope.state.folds == []
    assert run.user_goal == request.messages[-1].content


@async_test
async def test_successful_extractor_call_is_reused_after_commit_interruption(tmp_path):
    p = engine(tmp_path, [{}])
    run = make_run(rounds=1)
    first = await p.extract(run)
    # 模拟模型已返回，但父图状态提交前中断；从原检查点重新执行。
    replayed = await p.extract(run)
    assert (
        replayed.context_pipeline.processed_sources
        == first.context_pipeline.processed_sources
    )
    assert (
        replayed.context_pipeline.working_memory
        == first.context_pipeline.working_memory
    )
    assert len(p.model._calls) == 1
