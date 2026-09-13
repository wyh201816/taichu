"""把密封 PressurePlan 接到真实 ContextAssembler 与 Typed Oracle 投影。"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from taichu.application.general_agent.pipeline import ContextCapacityError, content_hash
from pathlib import Path
from typing import Mapping

from langgraph.store.memory import InMemoryStore

from taichu.application.agent_memory.models import (
    AgentMemoryKind,
    AgentMemoryValidity,
    MemoryWriteCandidate,
)
from taichu.application.evaluations.general_agent_benchmark.oracles import (
    AssertionEvaluationContext,
)
from taichu.application.evaluations.general_agent_benchmark.pressure import (
    PressureBehaviorArtifact,
    PressureBehaviorEvaluator,
    PressureContextPreservationProjector,
    PressureFixtureBlob,
    PressureKind,
    PressureMemoryIsolationProjector,
    PressureMemorySeed,
    PressurePlan,
    PressureResultContractProjector,
    PressureRetrievalFragmentSeed,
    PressureSeed,
    PressureSeedGenerator,
    PressureUnsafeRefusalArtifact,
    PressureUnsafeRefusalProjector,
)
from taichu.application.evaluations.general_agent_benchmark.suite_loader import (
    PressurePlanAssetSpec,
)
from taichu.application.general_agent.context import (
    _STABLE_MEMORY,
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
)
from taichu.application.services.agent_memory_service import AgentMemoryService
from taichu.infrastructure.agent_memory import (
    LangGraphAgentMemoryRepository,
)

from taichu.infrastructure.evaluations.general_agent_benchmark.context_projection import (
    DeterministicProjectionAssembler,
)

_PRESSURE_STORES: dict[Path, InMemoryStore] = {}


@dataclass(frozen=True, slots=True)
class PressureHarnessResult:
    """供 Synthetic 环境合并的只读 Oracle 投影。"""

    assertion_context: AssertionEvaluationContext
    behavior: PressureBehaviorArtifact | None
    unsafe_refusal: PressureUnsafeRefusalArtifact | None = None


class SyntheticPressureHarness:
    """只按 PressurePlan 载体构造压力，不读取 Benchmark case ID。"""

    def __init__(self, *, workspace: Path) -> None:
        self._workspace = workspace

    async def execute(
        self,
        *,
        asset: PressurePlanAssetSpec,
        memory_seed_ref: str | None,
        current_request: str,
    ) -> PressureHarnessResult:
        plan, seed = _sealed_pressure(asset)
        seed = _with_current_request(seed, current_request)
        root = self._workspace / "runtime" / "pressure" / asset.asset_id
        if plan.kind in {
            PressureKind.HISTORY,
            PressureKind.WORKING_MEMORY,
            PressureKind.NODE_OUTPUT,
            PressureKind.MULTI_SOURCE,
        }:
            snapshot = await _assemble_behavior_case(root, seed)
            behavior = PressureBehaviorEvaluator().evaluate(
                plan=plan,
                seed=seed,
                snapshot=snapshot,
            )
            context = PressureContextPreservationProjector().project(
                plan=plan,
                seed=seed,
                snapshot=snapshot,
            )
            return PressureHarnessResult(
                assertion_context=AssertionEvaluationContext(
                    context_preservation=(context,)
                ),
                behavior=behavior,
            )
        if plan.kind is PressureKind.EQUIVALENCE_PAIR:
            return await _equivalence_result(root, plan=plan, seed=seed)
        if plan.kind is PressureKind.INVALID_MEMORY:
            return await _invalid_memory_result(
                root,
                plan=plan,
                seed=seed,
                memory_seed_ref=memory_seed_ref,
            )
        if plan.kind is PressureKind.CURRENT_REQUEST:
            snapshot = await _assemble_current_request(root, seed)
            behavior = PressureBehaviorEvaluator().evaluate(
                plan=plan,
                seed=seed,
                snapshot=snapshot,
            )
            context = PressureContextPreservationProjector().project(
                plan=plan,
                seed=seed,
                snapshot=snapshot,
            )
            return PressureHarnessResult(
                assertion_context=AssertionEvaluationContext(
                    context_preservation=(context,)
                ),
                behavior=behavior,
            )
        return await _unsafe_refusal_result(root, plan=plan, seed=seed)


def _sealed_pressure(
    asset: PressurePlanAssetSpec,
) -> tuple[PressurePlan, PressureSeed]:
    kind = PressureKind(asset.carrier)
    repetitions, unit_size = {
        PressureKind.HISTORY: (36, 220),
        PressureKind.WORKING_MEMORY: (18, 700),
        PressureKind.NODE_OUTPUT: (96, 180),
        PressureKind.MULTI_SOURCE: (20, 520),
        PressureKind.EQUIVALENCE_PAIR: (18, 520),
        PressureKind.INVALID_MEMORY: (12, 420),
        PressureKind.CURRENT_REQUEST: (12, 1_000),
        PressureKind.UNSAFE_TOTAL: (120, 1_000),
    }[kind]
    invalid_refs = (
        ("sentinel_rejected", "sentinel_stale", "sentinel_superseded")
        if kind is PressureKind.INVALID_MEMORY
        else ()
    )
    plan = PressurePlan.seal(
        plan_id=asset.asset_id,
        kind=kind,
        fixture_blob_ref=f"pressure/{asset.asset_id}.txt",
        repetition_count=repetitions,
        unit_size=unit_size,
        protected_fact_refs=tuple(
            sorted(
                {
                    *asset.protected_refs,
                    "fact_pressure_anchor",
                }
            )
        ),
        invalid_sentinel_refs=invalid_refs,
        paired_case_ref=(
            "context_baseline_pair" if kind is PressureKind.EQUIVALENCE_PAIR else None
        ),
    )
    blob = PressureFixtureBlob.seal(
        blob_ref=plan.fixture_blob_ref,
        content="密封压力计划中的关键事实必须保持。",
    )
    return plan, PressureSeedGenerator().generate(plan, blob)


def _with_current_request(
    seed: PressureSeed,
    current_request: str,
) -> PressureSeed:
    """把压力种子绑定到正式案例逐字输入并重算密封身份。"""

    payload = seed.model_dump(
        mode="python",
        by_alias=True,
        exclude={"content_hash"},
    )
    payload["current_request"] = current_request
    payload["protected_facts"] = tuple(
        item.model_copy(update={"expected_text": current_request})
        if item.fact_ref == "current_request"
        else item
        for item in seed.protected_facts
    )
    return PressureSeed.seal(**payload)


def _memory_service(root: Path) -> AgentMemoryService:
    store = _PRESSURE_STORES.setdefault(root.resolve(), InMemoryStore())
    return AgentMemoryService(
        repository=LangGraphAgentMemoryRepository(store),
    )


async def _write_seed_memories(
    service: AgentMemoryService,
    seed: PressureSeed,
    *,
    protected_only: bool = False,
) -> None:
    items: tuple[PressureMemorySeed | PressureRetrievalFragmentSeed, ...] = (
        *seed.working_memories,
        *seed.retrieval_fragments,
    )
    for item in items:
        if protected_only and not item.protected_fact_refs:
            continue
        validity = (
            AgentMemoryValidity(item.validity)
            if isinstance(item, PressureMemorySeed)
            else AgentMemoryValidity.ACTIVE
        )
        await service.write(
            MemoryWriteCandidate(
                kind=AgentMemoryKind(item.kind),
                content=item.content,
                source_refs=list(item.source_refs),
                artifact_refs=list(item.artifact_refs),
                run_ids=["run_pressure_seed"],
                conversation_id="conversation_context_pressure",
                created_request_index=1,
                retention_priority=item.retention_priority,
                validity=validity,
                invalidation_reason=(
                    "该压力记忆已由密封计划标记为失效。"
                    if validity is not AgentMemoryValidity.ACTIVE
                    else ""
                ),
            )
        )


def _messages(seed: PressureSeed) -> list[GeneralAgentMessage]:
    return [
        GeneralAgentMessage(
            role=item.role,
            content=item.content,
            created_at=f"2026-07-30T10:{index % 60:02d}:00Z",
        )
        for index, item in enumerate(seed.history_messages)
    ]


def _dependency_plan(
    seed: PressureSeed,
) -> tuple[GeneralAgentExecutionPlan, list[GeneralAgentNodeRun]]:
    direct = next(item for item in seed.node_artifacts if item.direct_dependency)
    incidental = tuple(
        item for item in seed.node_artifacts if not item.direct_dependency
    )
    source = GeneralAgentPlanNode(
        node_id=direct.node_id,
        kind=GeneralAgentNodeKind.TOOL,
        capability_name="get_novel_structure",
        objective="读取下游消费所需的结构合同。",
    )
    incidental_nodes = [
        GeneralAgentPlanNode(
            node_id=item.node_id,
            kind=GeneralAgentNodeKind.TOOL,
            capability_name="read_manuscript",
            objective="读取可在预算压力下退出的旁支资料。",
        )
        for item in incidental
    ]
    consumer = GeneralAgentPlanNode(
        node_id="consume_contract",
        kind=GeneralAgentNodeKind.SUBAGENT,
        capability_name="story_architecture",
        objective="消费直接依赖的结构合同。",
        dependencies=[direct.node_id],
        input_bindings=[
            GeneralAgentInputBinding(
                source_node_id=direct.node_id,
                source_path=direct.required_output_paths[0],
                target_path="structure_items",
            )
        ],
    )
    plan = GeneralAgentExecutionPlan(
        rationale="先读取结构，再消费合同字段。",
        nodes=[*incidental_nodes, source, consumer],
        final_response_guidance="只依据保留下来的直接依赖回答。",
    )
    node_runs = [
        GeneralAgentNodeRun(
            node_id=item.node_id,
            plan_revision=1,
            kind=GeneralAgentNodeKind.TOOL,
            capability_name=(
                "get_novel_structure" if item.direct_dependency else "read_manuscript"
            ),
            objective=(
                "读取下游消费所需的结构合同。"
                if item.direct_dependency
                else "读取旁支资料。"
            ),
            status=GeneralAgentNodeStatus.SUCCESS,
            output=item.output,
            source_refs=list(item.source_refs),
            artifact_refs=list(item.artifact_refs),
        )
        for item in (*incidental, direct)
    ]
    return plan, node_runs


def _run(
    seed: PressureSeed,
    *,
    messages: list[GeneralAgentMessage] | None = None,
    plan: GeneralAgentExecutionPlan | None = None,
    node_runs: list[GeneralAgentNodeRun] | None = None,
) -> GeneralAgentRun:
    timestamp = "2026-07-30T12:00:00Z"
    return GeneralAgentRun(
        run_id="general_run_20260730_120000_abc123",
        task_id="conversation_context_pressure",
        conversation_id="conversation_context_pressure",
        request_index=50,
        user_goal=seed.current_request,
        author_constraints=list(seed.author_constraints),
        scope=GeneralAgentScope(scope_type="novel"),
        messages=messages or [],
        plan=plan,
        plan_revision=1 if plan is not None else 0,
        node_runs=node_runs or [],
        verification_issues=list(seed.todos),
        created_at=timestamp,
        updated_at=timestamp,
        started_at=timestamp,
    )


async def _assemble_behavior_case(
    root: Path,
    seed: PressureSeed,
) -> GeneralAgentContextSnapshot:
    service = _memory_service(root)
    await _write_seed_memories(service, seed)
    plan: GeneralAgentExecutionPlan | None = None
    node_runs: list[GeneralAgentNodeRun] = []
    if seed.node_artifacts:
        plan, node_runs = _dependency_plan(seed)
    return (
        await DeterministicProjectionAssembler(
            root=root,
            memory_service=service,
        ).assemble(
            _run(
                seed,
                messages=_messages(seed),
                plan=plan,
                node_runs=node_runs,
            ),
            phase="verify",
        )
    ).snapshot


async def _equivalence_result(
    root: Path,
    *,
    plan: PressurePlan,
    seed: PressureSeed,
) -> PressureHarnessResult:
    execution_plan, pressure_nodes = _dependency_plan(seed)
    direct = next(item for item in seed.node_artifacts if item.direct_dependency)
    baseline_service = _memory_service(root / "baseline")
    await _write_seed_memories(baseline_service, seed, protected_only=True)
    baseline_snapshot = (
        await DeterministicProjectionAssembler(
            root=root,
            memory_service=baseline_service,
        ).assemble(
            _run(
                seed,
                messages=(
                    [_messages(seed)[0], _messages(seed)[-1]]
                    if seed.history_messages
                    else []
                ),
                plan=execution_plan,
                node_runs=[
                    item for item in pressure_nodes if item.node_id == direct.node_id
                ],
            ),
            phase="verify",
        )
    ).snapshot
    pressure_service = _memory_service(root / "pressure")
    await _write_seed_memories(pressure_service, seed)
    pressure_snapshot = (
        await DeterministicProjectionAssembler(
            root=root,
            memory_service=pressure_service,
        ).assemble(
            _run(
                seed,
                messages=_messages(seed),
                plan=execution_plan,
                node_runs=pressure_nodes,
            ),
            phase="verify",
        )
    ).snapshot
    evaluator = PressureBehaviorEvaluator()
    baseline_behavior = evaluator.evaluate(
        plan=plan,
        seed=seed,
        snapshot=baseline_snapshot,
    )
    pressure_behavior = evaluator.evaluate(
        plan=plan,
        seed=seed,
        snapshot=pressure_snapshot,
    )
    projector = PressureResultContractProjector()
    resources: Mapping[str, str] = {
        "manuscript": "sealed",
        "knowledge": "sealed",
    }
    equivalence = projector.compare(
        plan=plan,
        baseline=projector.project_result(
            seed=seed,
            snapshot=baseline_snapshot,
            behavior=baseline_behavior,
            execution_plan=execution_plan,
            resource_before=resources,
            resource_after=resources,
        ),
        candidate=projector.project_result(
            seed=seed,
            snapshot=pressure_snapshot,
            behavior=pressure_behavior,
            execution_plan=execution_plan,
            resource_before=resources,
            resource_after=resources,
        ),
    )
    return PressureHarnessResult(
        assertion_context=AssertionEvaluationContext(
            result_contract_equivalences=(equivalence,)
        ),
        behavior=pressure_behavior,
    )


async def _invalid_memory_result(
    root: Path,
    *,
    plan: PressurePlan,
    seed: PressureSeed,
    memory_seed_ref: str | None,
) -> PressureHarnessResult:
    service = _memory_service(root)
    await _write_seed_memories(service, seed)
    execution_plan, node_runs = _dependency_plan(seed)
    run = _run(
        seed,
        messages=_messages(seed),
        plan=execution_plan,
        node_runs=node_runs,
    )
    snapshot = (
        await DeterministicProjectionAssembler(
            root=root,
            memory_service=service,
        ).assemble(run, phase="verify")
    ).snapshot
    fallback = (
        await DeterministicProjectionAssembler(
            root=root,
            memory_service=service,
        ).assemble(run, phase="verify")
    ).snapshot
    behavior = PressureBehaviorEvaluator().evaluate(
        plan=plan,
        seed=seed,
        snapshot=snapshot,
    )
    context = PressureContextPreservationProjector().project(
        plan=plan,
        seed=seed,
        snapshot=snapshot,
    )
    memories = PressureMemoryIsolationProjector().project(
        plan=plan,
        seed=seed,
        snapshot=snapshot,
        fallback_snapshot=fallback,
        behavior=behavior,
        memory_seed_ref=memory_seed_ref,
    )
    return PressureHarnessResult(
        assertion_context=AssertionEvaluationContext(
            context_preservation=(context,),
            memory_carriers=memories,
        ),
        behavior=behavior,
    )


async def _assemble_current_request(
    root: Path,
    seed: PressureSeed,
) -> GeneralAgentContextSnapshot:
    execution_plan, node_runs = _dependency_plan(seed)
    return (
        await DeterministicProjectionAssembler(
            root=root,
            memory_service=_memory_service(root),
        ).assemble(
            _run(seed, plan=execution_plan, node_runs=node_runs),
            phase="verify",
        )
    ).snapshot


async def _unsafe_refusal_result(
    root: Path,
    *,
    plan: PressurePlan,
    seed: PressureSeed,
) -> PressureHarnessResult:
    assembler = DeterministicProjectionAssembler(
        root=root,
        memory_service=_memory_service(root),
    )
    try:
        from taichu.infrastructure.llm.context_tokens import ModelContextTokens
        from langchain_core.messages import HumanMessage

        counter = ModelContextTokens(
            windows={"pressure": max(1, len(seed.current_request) // 2)}
        )
        if counter.count_request(
            [HumanMessage(content=seed.current_request)], [], "pressure"
        ) > counter.window("pressure"):
            raise ContextCapacityError(
                "必要输入超过模型窗口。",
                input_tokens=counter.count_request(
                    [HumanMessage(content=seed.current_request)], [], "pressure"
                ),
                context_window_tokens=counter.window("pressure"),
                current_request_sha256=sha256(
                    seed.current_request.encode()
                ).hexdigest(),
                stable_memory_sha256=content_hash(list(_STABLE_MEMORY)),
            )
        await assembler.assemble(_run(seed), phase="plan")
    except ContextCapacityError as error:
        refusal = PressureUnsafeRefusalArtifact.from_error(
            plan=plan,
            seed=seed,
            error=error,
        )
    else:
        raise RuntimeError("不安全压力计划未在规划前 fail-closed。")
    context = PressureUnsafeRefusalProjector().project_context(
        plan=plan,
        seed=seed,
        artifact=refusal,
    )
    return PressureHarnessResult(
        assertion_context=AssertionEvaluationContext(context_preservation=(context,)),
        behavior=None,
        unsafe_refusal=refusal,
    )


__all__ = [
    "PressureHarnessResult",
    "SyntheticPressureHarness",
]
