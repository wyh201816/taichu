"""需求 7.1—7.6：案例 18—21 的运行工作记忆最终行为合同。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, TypeVar

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ChatMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import PrivateAttr

from taichu.application.agent_memory.models import (
    AgentMemoryDependency,
    AgentMemoryDependencyRelation,
    AgentMemoryEntry,
    AgentMemoryKind,
    AgentMemoryValidity,
    MemoryWriteCandidate,
)
from taichu.application.capabilities import CapabilityContext
from taichu.application.evaluations.general_agent_benchmark.memory_scenarios import (
    MemoryAnswerContract,
    MemoryBehaviorProjector,
    MemoryBranchExchange,
    audit_memory_behavior,
    load_memory_behavior_seed,
)
from taichu.application.evaluations.general_agent_benchmark.claim_catalog import (
    DEFAULT_CLAIM_NORMALIZER_REGISTRY,
    load_claim_catalog,
)
from taichu.application.evaluations.general_agent_benchmark.suite_loader import (
    load_authored_suite,
    load_fixture_manifest,
)
from taichu.application.general_agent.context import ContextAssembler
from taichu.application.general_agent.models import (
    GeneralAgentContextSnapshot,
    GeneralAgentNodeKind,
    GeneralAgentNodeRun,
    GeneralAgentNodeStatus,
    GeneralAgentRun,
)
from taichu.application.general_agent.orchestrator import OrchestratorAgent
from taichu.application.invocations.models import InvocationContext
from taichu.application.services.agent_memory_service import AgentMemoryService
from taichu.application.services.model_role_router import ModelRoleRouter
from taichu.application.subagents.character import agent as character_agent
from taichu.application.subagents.contract import SubagentPlugin
from taichu.application.subagents.models import (
    AgentSourceRequest,
    CharacterInput,
    WorldbuildingInput,
)
from taichu.application.subagents.registry import SubagentRegistry
from taichu.application.subagents.worldbuilding import agent as worldbuilding_agent
from taichu.application.tools import (
    get_novel_structure,
    list_knowledge_catalog,
    read_knowledge_cards,
    read_manuscript,
    resolve_knowledge_identity,
    retrieve_story_context,
)
from taichu.application.tools.contract import ToolPlugin
from taichu.application.tools.registry import ToolRegistry
from tests.fakes.agent_memory import in_memory_agent_memory_repository
from taichu.infrastructure.artifacts import JsonIntermediateArtifactRepository

_ResultT = TypeVar("_ResultT")

_ACTIVE_STYLE = "先给结论，再说明依据。"
_OLD_STYLE = "回答只写结论。"
_STALE_SCOPE = "只讨论第一章。"
_STALE_DEPENDENT = "沿用旧范围，只输出第一章结论。"
_REJECTED_FACT = "第三道星纹由沈漪磨去。"

_ACTIVE_BASELINE_ANSWER = (
    "第一章钟声后归潮灯照出暗门，第二章钟声后旧航迹显现，所以钟鸣连接了两章。"
)
_ACTIVE_CONSTRAINED_ANSWER = (
    "结论：钟鸣连接了两章。\n依据：第一章钟声后归潮灯照出暗门；第二章钟声后旧航迹显现。"
)
_REJECTED_FINAL_ANSWER = (
    "刻痕来源：正文仅确认第三道星纹存在磨损，来源未知。角色认知：目前无人能确认磨损者。"
)
_FORMAL_ROOT = Path("tests/fixtures/evaluations/general_writing_agent_benchmark")


def _run(awaitable: Coroutine[object, object, _ResultT]) -> _ResultT:
    return asyncio.run(awaitable)


@dataclass(frozen=True, slots=True)
class _ProjectedToolCall:
    call_id: str
    name: str
    arguments_json: str


@dataclass(frozen=True, slots=True)
class _ProjectedMessage:
    role: str
    content: str
    tool_calls: tuple[_ProjectedToolCall, ...] = ()
    tool_call_id: str | None = None
    tool_name: str | None = None
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class _NativeModelRequest:
    messages: tuple[_ProjectedMessage, ...]
    task_name: str
    model_id: str


class _MemoryAwareChatModel(BaseChatModel):
    model_id: str = "memory-test-model"
    task_name: str = "测试模型调用"
    _requests: list[_NativeModelRequest] = PrivateAttr(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "taichu-test-memory-aware"

    @property
    def requests(self) -> tuple[_NativeModelRequest, ...]:
        return tuple(self._requests)

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable:
        formatted = [convert_to_openai_tool(tool, strict=True) for tool in tools]
        return self.bind(tools=formatted, tool_choice=tool_choice, **kwargs)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del messages, stop, run_manager, kwargs
        raise AssertionError("本测试只允许异步模型调用。")

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager
        task_name = str(kwargs.get("task_name", self.task_name))
        model_id = str(kwargs.get("model_id", self.model_id))
        request = _NativeModelRequest(
            messages=tuple(_project_message(message) for message in messages),
            task_name=task_name,
            model_id=model_id,
        )
        self._requests.append(request)
        visible = "\n".join(message.content for message in request.messages)
        payload: dict[str, Any]
        if request.task_name == "general_writing_orchestrator.plan":
            if _ACTIVE_STYLE in _developer_text(request):
                answer = _ACTIVE_CONSTRAINED_ANSWER
            elif "跨两章分析线索" in visible:
                answer = (
                    "chapter_001 的归潮灯线索与 chapter_002 的旧航迹线索"
                    "共同表明钟鸣跨两章推进同一谜团。"
                )
            else:
                answer = _ACTIVE_BASELINE_ANSWER
            payload = {
                "rationale": "只依据当前请求和当前有效运行工作记忆回答。",
                "direct_response": answer,
                "nodes": [],
            }
        elif request.task_name == "worldbuilding":
            payload = {
                "proposal": "刻痕来源：正文仅确认第三道星纹存在磨损，来源未知。",
                "rules": [],
                "costs": [],
                "constraints": [],
                "conflict_risks": [],
                "knowledge_proposals": [],
                "source_refs": [],
                "warnings": [],
            }
        elif request.task_name == "character":
            payload = {
                "analysis": "角色认知：目前无人能确认磨损者。",
                "proposals": [],
                "relationship_changes": [],
                "behavior_constraints": [],
                "risks": [],
                "knowledge_proposals": [],
                "source_refs": [],
                "warnings": [],
            }
        elif request.task_name == "general_writing_orchestrator.verify":
            payload = {
                "outcome": "satisfied",
                "final_answer": _REJECTED_FINAL_ANSWER,
                "issues": [],
                "should_replan": False,
            }
        else:  # pragma: no cover - 测试网关遇到未知调用即应暴露
            raise AssertionError(f"未声明的模型调用：{request.task_name}")
        tool_name = _selected_tool_name(
            tuple(kwargs.get("tools", ())),
            kwargs.get("tool_choice"),
        )
        message = AIMessage(
            content="" if tool_name else json.dumps(payload, ensure_ascii=False),
            tool_calls=(
                [
                    {
                        "id": f"memory_{len(payload)}_{tool_name}",
                        "name": tool_name,
                        "args": payload,
                        "type": "tool_call",
                    }
                ]
                if tool_name
                else []
            ),
            usage_metadata={
                "input_tokens": 100,
                "output_tokens": 50,
                "total_tokens": 150,
            },
            response_metadata={"model_id": request.model_id},
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


def _selected_tool_name(
    tools: tuple[dict[str, Any], ...],
    tool_choice: object,
) -> str | None:
    if not tools:
        return None
    selected = (
        tool_choice
        if isinstance(tool_choice, str)
        and tool_choice not in {"auto", "none", "required", "any"}
        else tools[-1]["function"]["name"]
    )
    return str(selected)


def _project_message(message: BaseMessage) -> _ProjectedMessage:
    role = (
        message.role
        if isinstance(message, ChatMessage)
        else {"human": "user", "ai": "assistant"}.get(message.type, message.type)
    )
    calls = (
        tuple(
            _ProjectedToolCall(
                call_id=str(call.get("id", "")),
                name=str(call.get("name", "")),
                arguments_json=json.dumps(call.get("args", {}), ensure_ascii=False),
            )
            for call in message.tool_calls
        )
        if isinstance(message, AIMessage)
        else ()
    )
    return _ProjectedMessage(
        role=role,
        content=str(message.content),
        tool_calls=calls,
        tool_call_id=(
            message.tool_call_id if isinstance(message, ToolMessage) else None
        ),
        tool_name=(message.name if isinstance(message, ToolMessage) else None),
        is_error=(
            message.status == "error" if isinstance(message, ToolMessage) else False
        ),
    )


class _TraceRepository:
    def __init__(self) -> None:
        self.records: list[object] = []

    async def append(self, record: object) -> None:
        self.records.append(record)


def _developer_text(request: _NativeModelRequest) -> str:
    return "\n".join(
        message.content for message in request.messages if message.role == "developer"
    )


def _memory_service(root: Path) -> AgentMemoryService:
    return AgentMemoryService(
        repository=in_memory_agent_memory_repository(root),
    )


def _run_state(
    *,
    conversation_id: str,
    goal: str,
    suffix: str,
    node_runs: list[GeneralAgentNodeRun] | None = None,
) -> GeneralAgentRun:
    return GeneralAgentRun(
        run_id=f"general_run_20260730_01010{suffix}_abcdef",
        task_id=conversation_id,
        conversation_id=conversation_id,
        request_index=2,
        user_goal=goal,
        plan_revision=1 if node_runs else 0,
        node_runs=node_runs or [],
        messages=[],
        created_at="2026-07-30T01:01:01Z",
        updated_at="2026-07-30T01:01:01Z",
        started_at="2026-07-30T01:01:01Z",
    )


async def _write_memory(
    service: AgentMemoryService,
    *,
    conversation_id: str,
    content: str,
    dependencies: list[AgentMemoryDependency] | None = None,
    supersedes_memory_id: str | None = None,
) -> AgentMemoryEntry:
    return await service.write(
        MemoryWriteCandidate(
            kind=AgentMemoryKind.USER_INSTRUCTION,
            content=content,
            source_refs=["run:fixture:memory_behavior"],
            run_ids=["run_fixture_memory_behavior"],
            conversation_id=conversation_id,
            created_request_index=1,
            retention_priority=100,
            dependencies=dependencies or [],
            supersedes_memory_id=supersedes_memory_id,
        )
    )


def _orchestrator(model: _MemoryAwareChatModel) -> OrchestratorAgent:
    tools = ToolRegistry(CapabilityContext(capabilities={}))
    subagents = SubagentRegistry(
        CapabilityContext(capabilities={"tool_registry": tools})
    )
    return OrchestratorAgent(
        llm=model,
        model_router=ModelRoleRouter(
            "memory-test-model",
            {"orchestrator": "memory-test-model"},
        ),
        tool_registry=tools,
        subagent_registry=subagents,
    )


async def _direct_answer(
    *,
    service: AgentMemoryService,
    run: GeneralAgentRun,
    gateway: _MemoryAwareChatModel,
) -> tuple[GeneralAgentContextSnapshot, _NativeModelRequest, str]:
    from tests.unit.application.general_agent.test_context_pipeline import engine
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as root:
        pipeline = engine(Path(root), [])
        pipeline.memory_service = service
        run = await pipeline.prepare(run, extract=False)
    snapshot = (
        await ContextAssembler(memory_service=service).assemble(run, phase="plan")
    ).snapshot
    plan = await _orchestrator(gateway).plan(run, context=snapshot.envelope)
    return snapshot, gateway.requests[-1], plan.direct_response


def _active_answer_contract() -> MemoryAnswerContract:
    return MemoryAnswerContract(
        required_fragments=(
            "结论：钟鸣连接了两章。",
            "依据：第一章钟声后归潮灯照出暗门",
            "第二章钟声后旧航迹显现",
        ),
        ordered_fragments=("结论：", "依据："),
        forbidden_fragments=(_OLD_STYLE,),
    )


def test_case_18_active_memory_changes_the_real_model_answer_and_state_only_fails(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        goal = (
            "请按我已保存的回答风格改写：第一章钟声后归潮灯照出暗门，"
            "第二章钟声后旧航迹显现，钟鸣连接了两章。"
        )
        baseline_service = _memory_service(tmp_path / "baseline")
        candidate_service = _memory_service(tmp_path / "candidate")
        active = await _write_memory(
            candidate_service,
            conversation_id="conversation_active",
            content=_ACTIVE_STYLE,
        )
        baseline_gateway = _MemoryAwareChatModel()
        candidate_gateway = _MemoryAwareChatModel()
        baseline = await _direct_answer(
            service=baseline_service,
            run=_run_state(
                conversation_id="conversation_baseline",
                goal=goal,
                suffix="1",
            ),
            gateway=baseline_gateway,
        )
        candidate = await _direct_answer(
            service=candidate_service,
            run=_run_state(
                conversation_id="conversation_active",
                goal=goal,
                suffix="2",
            ),
            gateway=candidate_gateway,
        )
        projector = MemoryBehaviorProjector()
        artifact = projector.project_active_pair(
            memory_seed_ref="memory_seed_runtime_default",
            target_memory=active,
            baseline_snapshot=baseline[0],
            candidate_snapshot=candidate[0],
            baseline_request=baseline[1],
            candidate_request=candidate[1],
            baseline_answer=baseline[2],
            candidate_answer=candidate[2],
            answer_contract=_active_answer_contract(),
            evidence_ref="evidence_memory_active_pair",
        )

        report = audit_memory_behavior(artifact)

        assert report.complete is True
        assert report.answer_changed is True
        assert report.target_memory_consumed is True
        assert baseline[2] == _ACTIVE_BASELINE_ANSWER
        assert candidate[2] == _ACTIVE_CONSTRAINED_ANSWER

        unchanged_answer = projector.project_active_pair(
            memory_seed_ref="memory_seed_runtime_default",
            target_memory=active,
            baseline_snapshot=baseline[0],
            candidate_snapshot=candidate[0],
            baseline_request=baseline[1],
            candidate_request=candidate[1],
            baseline_answer=baseline[2],
            candidate_answer=baseline[2],
            answer_contract=_active_answer_contract(),
            evidence_ref="evidence_memory_state_only",
        )
        unchanged_report = audit_memory_behavior(unchanged_answer)
        assert unchanged_report.complete is False
        assert any("最终答案" in item for item in unchanged_report.violations)

    _run(scenario())


def test_case_19_stale_memory_and_its_invalid_dependency_do_not_limit_answer(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = _memory_service(tmp_path)
        conversation_id = "conversation_stale"
        stale_source = await _write_memory(
            service,
            conversation_id=conversation_id,
            content=_STALE_SCOPE,
        )
        stale_dependent = await _write_memory(
            service,
            conversation_id=conversation_id,
            content=_STALE_DEPENDENT,
            dependencies=[
                AgentMemoryDependency(
                    memory_id=stale_source.memory_id,
                    relation=AgentMemoryDependencyRelation.BASIS,
                )
            ],
        )
        await service.invalidate(
            stale_source.memory_id,
            validity=AgentMemoryValidity.STALE,
            reason="当前请求已改为跨章分析。",
        )
        source_after = await service.get(stale_source.memory_id)
        dependent_after = await service.get(stale_dependent.memory_id)
        assert source_after is not None
        assert dependent_after is not None
        assert source_after.validity is AgentMemoryValidity.STALE
        assert dependent_after.validity is AgentMemoryValidity.STALE

        gateway = _MemoryAwareChatModel()
        snapshot, request, answer = await _direct_answer(
            service=service,
            run=_run_state(
                conversation_id=conversation_id,
                goal=(
                    "chapter_001 的钟鸣伴随归潮灯亮起，chapter_002 的钟鸣"
                    "伴随旧航迹显现。请跨两章分析线索，不受旧单章范围限制。"
                ),
                suffix="3",
            ),
            gateway=gateway,
        )
        projector = MemoryBehaviorProjector()
        kwargs = {
            "case_id": "memory_stale_dependency",
            "memory_seed_ref": "memory_seed_runtime_default",
            "invalid_memories": (source_after, dependent_after),
            "sentinel_refs": {
                source_after.memory_id: "sentinel_stale_scope",
                dependent_after.memory_id: "sentinel_stale_dependency",
            },
            "snapshot": snapshot,
            "orchestrator_request": request,
            "final_answer": answer,
            "answer_contract": MemoryAnswerContract(
                required_fragments=(
                    "chapter_001",
                    "chapter_002",
                    "归潮灯",
                    "旧航迹",
                ),
                forbidden_fragments=(_STALE_DEPENDENT,),
            ),
            "evidence_ref": "evidence_memory_stale",
        }
        artifact = projector.project_invalid_case(**kwargs)

        report = audit_memory_behavior(artifact)

        assert report.complete is True
        assert report.invalid_dependency_isolated is True
        assert all(
            occurrence.occurrence_count == 0
            for carrier in artifact.carriers
            for occurrence in carrier.invalid_occurrences
        )
        oracle_observations = projector.to_oracle_observations(artifact)
        assert oracle_observations
        assert {item.state for item in oracle_observations} == {"stale"}
        assert all(item.occurrence_count == 0 for item in oracle_observations)

        revived = projector.project_invalid_case(
            **{
                **kwargs,
                "final_answer": f"{answer}\n{_STALE_DEPENDENT}",
            }
        )
        assert audit_memory_behavior(revived).complete is False

    _run(scenario())


def test_case_20_rejected_memory_is_absent_from_both_real_subagent_branches(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = _memory_service(tmp_path / "memory")
        conversation_id = "conversation_rejected"
        rejected = await _write_memory(
            service,
            conversation_id=conversation_id,
            content=_REJECTED_FACT,
        )
        await service.invalidate(
            rejected.memory_id,
            validity=AgentMemoryValidity.REJECTED,
            reason="正文没有该事实。",
        )
        rejected_after = await service.get(rejected.memory_id)
        assert rejected_after is not None

        parent_run = _run_state(
            conversation_id=conversation_id,
            goal=(
                "当前事实只有“正文未确认第三道星纹是谁磨去”。"
                "请从刻痕来源和角色认知两个角度分析。"
            ),
            suffix="4",
        )
        gateway = _MemoryAwareChatModel()
        registry = _subagent_registry(tmp_path, gateway)
        safe_fact = "正文仅确认第三道星纹存在磨损，未确认磨损者。"
        mark_input = WorldbuildingInput(
            design_goal="分析刻痕来源。",
            hard_constraints=["不得把未知磨损者写成已确认事实。"],
            source_request=AgentSourceRequest(
                auto_collect=False,
                direct_context=safe_fact,
            ),
        )
        character_input = CharacterInput(
            character_goal="分析角色对磨损者的认知边界。",
            hard_constraints=["不得虚构角色已经知道磨损者。"],
            source_request=AgentSourceRequest(
                auto_collect=False,
                direct_context=safe_fact,
            ),
        )
        mark_envelope, character_envelope = await asyncio.gather(
            registry.invoke(
                "worldbuilding",
                mark_input,
                InvocationContext(
                    task_id=conversation_id,
                    run_id=parent_run.run_id,
                    caller_type="orchestrator",
                    caller_name="general_writing_assistant",
                    phase="dag:analyze_mark_source",
                ),
            ),
            registry.invoke(
                "character",
                character_input,
                InvocationContext(
                    task_id=conversation_id,
                    run_id=parent_run.run_id,
                    caller_type="orchestrator",
                    caller_name="general_writing_assistant",
                    phase="dag:analyze_character_knowledge",
                ),
            ),
        )
        requests = {request.task_name: request for request in gateway.requests}
        mark_node = _branch_node(
            node_id="analyze_mark_source",
            capability_name="worldbuilding",
            objective="分析刻痕来源。",
            resolved_input=mark_input.model_dump(mode="json"),
            envelope=mark_envelope,
        )
        character_node = _branch_node(
            node_id="analyze_character_knowledge",
            capability_name="character",
            objective="分析角色认知。",
            resolved_input=character_input.model_dump(mode="json"),
            envelope=character_envelope,
        )
        verify_run = parent_run.model_copy(
            update={
                "plan_revision": 1,
                "node_runs": [mark_node, character_node],
            }
        )
        verify_snapshot = (
            await ContextAssembler(memory_service=service).assemble(
                verify_run,
                phase="verify",
            )
        ).snapshot
        verification = await _orchestrator(gateway).verify(
            verify_run,
            context=verify_snapshot.envelope,
        )
        aggregate_request = gateway.requests[-1]
        branches = (
            MemoryBranchExchange.from_runtime(
                branch_id="branch_mark_source",
                node=mark_node,
                request=requests["worldbuilding"],
                envelope=mark_envelope,
                evidence_ref="evidence_branch_mark_source",
            ),
            MemoryBranchExchange.from_runtime(
                branch_id="branch_character_knowledge",
                node=character_node,
                request=requests["character"],
                envelope=character_envelope,
                evidence_ref="evidence_branch_character_knowledge",
            ),
        )
        projector = MemoryBehaviorProjector()
        kwargs = {
            "case_id": "memory_rejected_parallel_isolation",
            "memory_seed_ref": "memory_seed_runtime_default",
            "invalid_memories": (rejected_after,),
            "sentinel_refs": {
                rejected_after.memory_id: "sentinel_rejected_fact",
            },
            "snapshot": verify_snapshot,
            "orchestrator_request": aggregate_request,
            "final_answer": verification.final_answer,
            "answer_contract": MemoryAnswerContract(
                required_fragments=(
                    "刻痕来源",
                    "来源未知",
                    "角色认知",
                    "无人能确认磨损者",
                ),
                forbidden_fragments=(_REJECTED_FACT,),
            ),
            "branches": branches,
            "evidence_ref": "evidence_memory_rejected",
        }
        artifact = projector.project_invalid_case(**kwargs)
        report = audit_memory_behavior(artifact)

        assert report.complete is True
        assert report.branch_count == 2
        assert set(artifact.required_carrier_keys) == {
            carrier.key for carrier in artifact.carriers
        }

        for carrier_key in artifact.required_carrier_keys:
            polluted = projector.project_invalid_case(
                **{
                    **kwargs,
                    "carrier_overrides": {carrier_key: {"revived": _REJECTED_FACT}},
                }
            )
            polluted_report = audit_memory_behavior(polluted)
            assert polluted_report.complete is False, carrier_key

        serial_branches = (
            branches[0],
            branches[1].model_copy(update={"dependencies": (branches[0].node_id,)}),
        )
        hidden_serial_dependency = projector.project_invalid_case(
            **{
                **kwargs,
                "branches": serial_branches,
            }
        )
        assert audit_memory_behavior(hidden_serial_dependency).complete is False

    _run(scenario())


def test_case_21_superseded_old_answer_stays_only_in_redacted_repair_history(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = _memory_service(tmp_path)
        conversation_id = "conversation_superseded"
        old = await _write_memory(
            service,
            conversation_id=conversation_id,
            content=_OLD_STYLE,
        )
        latest = await _write_memory(
            service,
            conversation_id=conversation_id,
            content=_ACTIVE_STYLE,
            supersedes_memory_id=old.memory_id,
        )
        old_after = await service.get(old.memory_id)
        assert old_after is not None
        assert old_after.validity is AgentMemoryValidity.SUPERSEDED

        gateway = _MemoryAwareChatModel()
        snapshot, request, answer = await _direct_answer(
            service=service,
            run=_run_state(
                conversation_id=conversation_id,
                goal=(
                    "请修复旧回答：结论是钟鸣连接了两章；依据是第一章钟声后"
                    "归潮灯照出暗门，第二章钟声后旧航迹显现。"
                ),
                suffix="5",
            ),
            gateway=gateway,
        )
        projector = MemoryBehaviorProjector()
        kwargs = {
            "case_id": "memory_superseded_repair",
            "memory_seed_ref": "memory_seed_runtime_default",
            "invalid_memories": (old_after,),
            "sentinel_refs": {
                old_after.memory_id: "sentinel_superseded_style",
            },
            "snapshot": snapshot,
            "orchestrator_request": request,
            "final_answer": answer,
            "answer_contract": _active_answer_contract(),
            "latest_active_memory": latest,
            "evidence_ref": "evidence_memory_superseded",
        }
        artifact = projector.project_invalid_case(**kwargs)
        report = audit_memory_behavior(artifact)

        assert report.complete is True
        assert report.supersession_relation_valid is True
        assert old.memory_id in artifact.repair_memory_ids
        assert latest.memory_id in artifact.current_memory_ids
        assert _OLD_STYLE not in _developer_text(request)
        assert _OLD_STYLE not in answer

        old_answer_revived = projector.project_invalid_case(
            **{
                **kwargs,
                "final_answer": "结论：钟鸣连接了两章。回答只写结论。",
            }
        )
        assert audit_memory_behavior(old_answer_revived).complete is False

    _run(scenario())


def _subagent_registry(
    root: Path,
    gateway: _MemoryAwareChatModel,
) -> SubagentRegistry:
    tool_context = CapabilityContext(
        capabilities={
            "chapter_service": object(),
            "outline_service": object(),
            "vector_graph_rag_service": object(),
            "knowledge_service": object(),
            "knowledge_repository": object(),
        }
    )
    tools = ToolRegistry(tool_context)
    for module in (
        get_novel_structure,
        read_manuscript,
        retrieve_story_context,
        resolve_knowledge_identity,
        list_knowledge_catalog,
        read_knowledge_cards,
    ):
        tools.register(
            ToolPlugin(
                manifest=module.manifest,
                run=module.run,
                reconcile=getattr(module, "reconcile", None),
            )
        )
    traces = _TraceRepository()
    context = CapabilityContext(
        capabilities={
            "llm": gateway,
            "model_role_router": ModelRoleRouter(
                "memory-test-model",
                {
                    "worldbuilding": "memory-test-model",
                    "character": "memory-test-model",
                },
            ),
            "tool_registry": tools,
            "artifact_repository": JsonIntermediateArtifactRepository(root),
            "invocation_trace_repository": traces,
        }
    )
    registry = SubagentRegistry(context, trace_repository=traces)
    registry.register_all(
        (
            SubagentPlugin(
                manifest=worldbuilding_agent.manifest,
                run=worldbuilding_agent.run,
            ),
            SubagentPlugin(
                manifest=character_agent.manifest,
                run=character_agent.run,
            ),
        )
    )
    return registry


def _branch_node(
    *,
    node_id: str,
    capability_name: str,
    objective: str,
    resolved_input: dict[str, Any],
    envelope: Any,
) -> GeneralAgentNodeRun:
    return GeneralAgentNodeRun(
        node_id=node_id,
        plan_revision=1,
        kind=GeneralAgentNodeKind.SUBAGENT,
        capability_name=capability_name,
        objective=objective,
        status=GeneralAgentNodeStatus.SUCCESS,
        resolved_input=resolved_input,
        output=envelope.output.model_dump(mode="json"),
        source_refs=list(envelope.source_refs),
        artifact_refs=list(envelope.artifact_refs),
        trace_id=envelope.trace_id,
        started_at=envelope.started_at,
        finished_at=envelope.finished_at,
        duration_ms=envelope.duration_ms,
    )


def test_formal_cases_18_21_are_wired_to_claims_branches_and_sealed_seed() -> None:
    suite_path = _FORMAL_ROOT / "suite.json"
    suite_payload = json.loads(suite_path.read_text(encoding="utf-8"))
    suite = load_authored_suite(
        suite_path,
        expected_capability_catalog_hash=suite_payload["capability_catalog_hash"],
    )
    manifest = load_fixture_manifest(
        _FORMAL_ROOT / "fixtures" / "core_novel" / "fixture-manifest.json"
    )
    catalog = load_claim_catalog(
        _FORMAL_ROOT / "claim-catalog.json",
        registry=DEFAULT_CLAIM_NORMALIZER_REGISTRY,
        known_fixture_refs=frozenset(
            item.asset_id for item in manifest.scenario_assets
        ),
    )
    fixture = load_memory_behavior_seed(
        _FORMAL_ROOT / "fixtures" / "core_novel" / "runtime_memory" / "seed.json"
    )

    cases = {
        item.case_id: item for item in suite.cases if item.case_id.startswith("memory_")
    }
    assert tuple(cases) == (
        "memory_active_projection",
        "memory_stale_dependency",
        "memory_rejected_parallel_isolation",
        "memory_superseded_repair",
    )
    assert set(fixture.answer_contracts) == set(cases)
    entries = {item.memory_ref: item for item in fixture.entries}
    assert (
        entries["memory_active_style"].supersedes_memory_ref
        == "memory_superseded_old_style"
    )
    assert entries["memory_stale_dependent"].dependencies[0].memory_ref == (
        "memory_stale_scope"
    )
    assert {item.target_validity for item in fixture.entries} == set(
        AgentMemoryValidity
    )

    active_answer = (
        cases["memory_active_projection"].scripted_steps[0].response["direct_response"]
    )
    assert active_answer == _ACTIVE_CONSTRAINED_ANSWER
    stale_answer = (
        cases["memory_stale_dependency"].scripted_steps[0].response["direct_response"]
    )
    assert "chapter_001" in stale_answer and "chapter_002" in stale_answer
    superseded_answer = (
        cases["memory_superseded_repair"].scripted_steps[0].response["direct_response"]
    )
    assert superseded_answer == _ACTIVE_CONSTRAINED_ANSWER

    rejected = cases["memory_rejected_parallel_isolation"]
    plan_nodes = rejected.scripted_steps[0].response["nodes"]
    assert {item["capability_name"] for item in plan_nodes} == {
        "worldbuilding",
        "character",
    }
    assert all(item.get("dependencies", []) == [] for item in plan_nodes)
    assert {item.name for item in rejected.required_invocations} == {
        "worldbuilding",
        "character",
    }
    assertion_kinds = {
        item.kind for case in cases.values() for item in case.behavior_assertions
    }
    assert {
        "final_claims",
        "artifact_contract",
        "memory_carrier_absence",
    } <= assertion_kinds
    claim_ids = {item.claim_id for item in catalog.claims}
    assert {
        "memory_bell_connects_chapters",
        "memory_cross_chapter_clues",
        "memory_mark_source_unknown",
        "memory_no_confirmed_wearer",
        "memory_wrong_shen_yi_mark",
    } <= claim_ids
