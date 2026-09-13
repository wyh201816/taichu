"""37 案确定性合成套件的隔离生产 Runtime 环境。"""

from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from time import monotonic
from typing import Any, cast

from langgraph.checkpoint.mongodb import MongoDBSaver
from langgraph.store.mongodb import MongoDBStore
from pymongo import AsyncMongoClient, MongoClient

from taichu.application.agent_memory.models import (
    AgentMemoryDependency,
    AgentMemoryEntry,
    AgentMemoryValidity,
    MemoryWriteCandidate,
)
from taichu.application.artifacts.models import IntermediateArtifactRecord
from taichu.application.capabilities import CapabilityContext
from taichu.application.contracts.general_agent_capability_results import (
    CapabilityResultOwner,
)
from taichu.application.evaluations.general_agent_benchmark.canonical import (
    canonical_sha256,
)
from taichu.application.evaluations.general_agent_benchmark.models import (
    FixtureSnapshotSpec,
)
from taichu.application.evaluations.general_agent_benchmark.memory_scenarios import (
    MemoryBehaviorArtifact,
    MemoryBehaviorCaseId,
    MemoryBehaviorProjector,
    MemoryBehaviorSeedFixture,
    MemoryBranchExchange,
    load_memory_behavior_seed,
)
from taichu.application.evaluations.general_agent_benchmark.observations import (
    ObservedArtifact,
    ObservedHumanDecision,
    ObservedTerminalState,
)
from taichu.application.evaluations.general_agent_benchmark.oracles import (
    AssertionEvaluationContext,
)
from taichu.application.evaluations.general_agent_benchmark.pressure import (
    PressureUnsafeRefusalProjector,
)
from taichu.application.evaluations.general_agent_benchmark.resource_observation import (
    ResourceStateItem,
)
from taichu.application.evaluations.general_agent_benchmark.runtime_observer import (
    FixtureIsolationFacts,
    RuntimeObservationFacts,
    RuntimeUsageFacts,
    ScriptConsumptionFacts,
    project_observed_effects,
)
from taichu.application.evaluations.general_agent_benchmark.run_lineage import (
    capture_run_lineage,
)
from taichu.application.evaluations.general_agent_benchmark.strict_driver import (
    InteractionKind,
    StrictScriptedDriver,
)
from taichu.application.evaluations.general_agent_benchmark.suite_loader import (
    AuthoredCaseSpec,
    FaultPlanAssetSpec,
    PressurePlanAssetSpec,
    load_fixture_manifest,
)
from taichu.application.evaluations.general_agent_benchmark.synthetic_suite import (
    SyntheticCaseObservation,
    project_invocation_identities,
    project_observed_human_decisions,
    project_observed_invocations,
)
from taichu.application.external_research.service import ExternalResearchService
from taichu.application.general_agent.context import (
    ContextAssembler,
)
from taichu.application.general_agent.events import GeneralAgentEventCenter
from taichu.application.general_agent.models import (
    GeneralAgentRun,
    GeneralAgentRunStatus,
)
from taichu.application.general_agent.orchestrator import OrchestratorAgent
from taichu.application.services.agent_memory_service import AgentMemoryService
from taichu.application.services.chapter_service import ChapterService
from taichu.application.services.invocation_policy_service import (
    InvocationPolicyService,
)
from taichu.application.services.knowledge_service import KnowledgeService
from taichu.application.services.model_role_router import ModelRoleRouter
from taichu.application.services.outline_service import OutlineService
from taichu.application.vector_graph.corpus import project_knowledge_card
from taichu.application.vector_graph.models import (
    VectorGraphEvidence,
    VectorGraphRetrievalResult,
    VectorGraphSourceType,
)
from taichu.application.vector_graph.service import VectorGraphRAGService
from taichu.application.subagents.registry import SubagentRegistry
from taichu.application.tools.registry import ToolRegistry
from taichu.domain.models import StructuredKnowledgeCard
from taichu.infrastructure.agent_memory import (
    LangGraphAgentMemoryRepository,
)
from taichu.infrastructure.artifacts import JsonIntermediateArtifactRepository
from taichu.infrastructure.evaluations.general_agent_benchmark.fixture_external_research import (
    FixtureExternalResearchBackend,
)
from taichu.infrastructure.evaluations.general_agent_benchmark.opik_integration import (
    opik,
    update_current_span,
)
from taichu.infrastructure.evaluations.general_agent_benchmark.fixture_manager import (
    CaseWorkspaceHandle,
    FixtureIsolationController,
    FixtureIsolationError,
    build_fixture_snapshot,
)
from taichu.infrastructure.evaluations.general_agent_benchmark.pressure_harness import (
    PressureHarnessResult,
    SyntheticPressureHarness,
)
from taichu.infrastructure.evaluations.general_agent_benchmark.runtime_factory import (
    BenchmarkRuntimeDependencies,
    GeneralAgentBenchmarkRuntimeFactory,
)
from taichu.infrastructure.evaluations.general_agent_benchmark.synthetic_recovery_harness import (
    SyntheticRecoveryHarness,
    SyntheticRecoveryHarnessResult,
)
from taichu.infrastructure.evaluations.general_agent_benchmark.resource_capture import (
    capture_case_resource_state,
    seal_resource_snapshot,
)
from taichu.infrastructure.evaluations.general_agent_benchmark.synthetic_runtime import (
    StrictSyntheticInteractionObserver,
    StrictSyntheticLLMGateway,
    SyntheticInjectedProcessTermination,
)
from taichu.infrastructure.general_agent_runs import (
    JsonGeneralAgentContextSnapshotRepository,
    JsonGeneralAgentEffectRepository,
    JsonGeneralAgentRunRepository,
)
from taichu.infrastructure.invocations import JsonlInvocationTraceRepository
from taichu.infrastructure.knowledge.mongo_repository import (
    MongoKnowledgeRepository,
)
from taichu.infrastructure.llm_replays import JsonLLMCallReplayRepository
from taichu.infrastructure.llm.adapter import GatewayChatModel
from taichu.infrastructure.storage.markdown_backend import (
    ProjectAssetStorageBackend,
)

_FIXTURE_TIME = "2026-07-27T00:00:00Z"
_CONVERSATION_ID = "benchmark_fixture_conversation"
_STRUCTURE_VERSION = "9f4e7132f36d6e03eb6d5251aa1735707ab84092f86960a586adfdcb8f93ae89"


class _SyntheticStoryContextService(VectorGraphRAGService):
    """合成评测中的确定性统一检索替身，不连接外部 Milvus。"""

    def __init__(
        self,
        repository: MongoKnowledgeRepository,
        chapter_service: ChapterService,
    ) -> None:
        self._synthetic_repository = repository
        self._synthetic_chapters = chapter_service

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int = 10,
    ) -> VectorGraphRetrievalResult:
        evidences: list[VectorGraphEvidence] = []
        normalized = query.casefold()
        for chapter in await self._synthetic_chapters.list_chapters():
            content = (await self._synthetic_chapters.read_chapter(chapter.id)).markdown
            if not any(
                term and term in content.casefold() for term in normalized.split()
            ):
                continue
            evidences.append(
                VectorGraphEvidence(
                    source_type=VectorGraphSourceType.MANUSCRIPT_CHUNK,
                    source_id=chapter.id,
                    source_ref=f"manuscript:{chapter.id}:0-{len(content)}",
                    title=chapter.title,
                    content=content,
                    content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    rank=len(evidences) + 1,
                    start_char=0,
                    end_char=len(content),
                    authority_verified=True,
                )
            )

        cards = await self._synthetic_repository.list_confirmed_cards()
        lookup = {card.id: card for card in cards}
        ranked = sorted(
            cards,
            key=lambda card: (
                not any(
                    value.casefold() in normalized
                    for value in (card.name, *card.aliases)
                    if value
                ),
                card.id,
            ),
        )[:top_k]
        for card in ranked:
            document = project_knowledge_card(card, lookup)
            evidences.append(
                VectorGraphEvidence(
                    source_type=document.source_type,
                    source_id=document.source_id,
                    source_ref=document.source_ref,
                    title=document.title,
                    content=document.content,
                    content_sha256=document.content_sha256,
                    rank=len(evidences) + 1,
                    authority_verified=True,
                )
            )
        evidences = evidences[:top_k]
        return VectorGraphRetrievalResult(
            query=query,
            evidences=evidences,
            source_refs=[item.source_ref for item in evidences],
        )


class SyntheticFixtureRuntime:
    """每案创建独立 Markdown/Mongo/记忆/检查点并调用真实 Runtime。"""

    def __init__(
        self,
        *,
        sealed_fixture_root: Path,
        workspaces_root: Path,
        mongodb_uri: str = "mongodb://127.0.0.1:27017",
    ) -> None:
        self._sealed_root = sealed_fixture_root.resolve(strict=True)
        self._controller = FixtureIsolationController(
            sealed_root=self._sealed_root,
            workspaces_root=workspaces_root,
        )
        self._declared_fixture = load_fixture_manifest(
            self._sealed_root / "fixture-manifest.json"
        )
        self._snapshot = build_fixture_snapshot(
            self._sealed_root,
            fixture_id=self._declared_fixture.fixture_id,
        )
        self._factory = GeneralAgentBenchmarkRuntimeFactory(workspaces_root.resolve())
        self._mongodb_uri = mongodb_uri

    @opik.track(
        name="通用写作智能体评测案例",
        type="general",
        tags=["太初", "固定基准"],
        capture_input=False,
        capture_output=False,
    )
    async def execute(self, case: AuthoredCaseSpec) -> SyntheticCaseObservation:
        update_current_span(
            name=f"评测案例 · {case.name}",
            metadata={
                "case_id": case.case_id,
                "tracks": [track.value for track in case.applicable_tracks],
                "fault_injection": case.setup.fault_plan_ref is not None,
            },
        )
        handle = self._controller.create_workspace(
            snapshot=self._snapshot,
            case_execution_id=_case_execution_id(case.case_id),
        )
        client: AsyncMongoClient[Any] = AsyncMongoClient(
            self._mongodb_uri,
            tz_aware=True,
            serverSelectionTimeoutMS=5_000,
        )
        environment: dict[str, Any] | None = None
        case_started = monotonic()
        try:
            environment = await self._build_case_environment(
                case,
                workspace=handle.workspace_root,
                database_name=handle.mongo_database,
                client=client,
            )
            observer: StrictSyntheticInteractionObserver = environment["observer"]
            driver: StrictScriptedDriver = environment["driver"]
            before_resource_state = await capture_case_resource_state(
                workspace=handle.workspace_root,
                knowledge_repository=environment["knowledge_repository"],
            )
            node_statuses: list[str] = []
            effect_tools: list[str] = []
            observed_effects: list[Any] = []
            run_status = "unfinished"
            run_index = 0
            while True:
                try:
                    run = await environment["runtime"].run(
                        user_goal=(
                            case.user_request
                            if run_index == 0
                            else "继续使用上一轮返回的稳定标识完成更新。"
                        ),
                        conversation_id=_CONVERSATION_ID,
                        external_access_allowed=(
                            case.case_id == "external_research_grounded"
                        ),
                    )
                except SyntheticInjectedProcessTermination:
                    if case.case_id != "recovery_verification_interruption":
                        raise
                    run = await self._recover_checkpoint_case(environment)
                while run.status.value == "waiting_human":
                    request = run.pending_human_request
                    if request is None:
                        raise RuntimeError("Runtime 等待人工处理但没有人工请求。")
                    step = driver.current_step
                    if step is None or step.kind is not InteractionKind.HUMAN:
                        raise RuntimeError("严格脚本缺少当前 Runtime 人工决定。")
                    approved = _expected_approval(step)
                    second_confirmation = (
                        approved and request.second_confirmation_required
                    )
                    observer.record_human_decision(
                        request=request,
                        source_run_id=run.run_id,
                        approved=approved,
                        second_confirmation=second_confirmation,
                    )
                    run = await environment["runtime"].resume(
                        run.run_id,
                        approve=approved,
                        second_confirmation=second_confirmation,
                    )
                run_status = run.status.value
                node_statuses.extend(item.status.value for item in run.node_runs)
                run_effects = await environment["effects"].list_effects(run.run_id)
                observed_effects.extend(run_effects)
                effect_tools.extend(
                    item.tool_name
                    for item in run_effects
                    if item.status.value == "succeeded"
                )
                _bind_followup_response(environment["gateway"], run)
                next_step = driver.current_step
                if (
                    run_status == "completed"
                    and next_step is not None
                    and next_step.kind is InteractionKind.MODEL
                    and any(
                        matcher.path == "/phase" and matcher.expected == "plan"
                        for matcher in next_step.matchers
                    )
                ):
                    run_index += 1
                    continue
                break
            await _project_memory_observations(
                case,
                environment,
                run,
            )
            driver.finalize(
                script_identity="0" * 64,
                runtime_config_identity="1" * 64,
                normalized_result={"status": run_status},
            )
            recovery_result = await self._recovery_result(
                case,
                environment=environment,
            )
            observed_run = recovery_result.run if recovery_result is not None else run
            if recovery_result is not None:
                run_status = observed_run.status.value
                node_statuses.extend(
                    item.status.value for item in observed_run.node_runs
                )
                observed_effects = list(
                    await environment["effects"].list_effects(observed_run.run_id)
                )
                effect_tools = [
                    item.tool_name
                    for item in observed_effects
                    if item.status.value in {"succeeded", "reconciled"}
                ]
            sealed_after = build_fixture_snapshot(
                self._sealed_root,
                fixture_id=self._snapshot.fixture_id,
            )
            observed_artifacts = await _observed_artifacts(
                environment,
                observed_run,
            )
            special_artifacts = _recovery_special_artifacts(
                observed_run,
                recovery_result=recovery_result,
            )
            after_resource_state = await capture_case_resource_state(
                workspace=handle.workspace_root,
                knowledge_repository=environment["knowledge_repository"],
            )
            resource_targets = _resource_target_refs(
                tuple(observer.interaction_records),
                tuple(observed_effects),
            )
            resource_snapshots = tuple(
                snapshot
                for snapshot_ref in case.setup.resource_snapshot_refs
                for snapshot in (
                    seal_resource_snapshot(
                        snapshot_ref=snapshot_ref,
                        phase="before",
                        resources=_resources_for_snapshot(
                            snapshot_ref,
                            before_resource_state,
                        ),
                        target_refs=_targets_for_snapshot(
                            snapshot_ref,
                            resource_targets,
                        ),
                    ),
                    seal_resource_snapshot(
                        snapshot_ref=snapshot_ref,
                        phase="after",
                        resources=_resources_for_snapshot(
                            snapshot_ref,
                            after_resource_state,
                        ),
                        target_refs=_targets_for_snapshot(
                            snapshot_ref,
                            resource_targets,
                        ),
                    ),
                )
            )
            if recovery_result is not None:
                run_lineage = recovery_result.lineage
            else:
                all_runs, _ = await environment[
                    "dependencies"
                ].run_repository.list_runs(
                    page=1,
                    page_size=10_000,
                    status="all",
                )
                run_lineage = capture_run_lineage(
                    preexisting_run_ids=environment["preexisting_run_ids"],
                    observed_runs=tuple(all_runs),
                    returned_run_id=observed_run.run_id,
                    case_user_request_raw=case.user_request_raw,
                )
            external_backend: FixtureExternalResearchBackend = environment[
                "external_research_backend"
            ]
            observed_human_decisions = project_observed_human_decisions(
                tuple(observer.interaction_records)
            )
            observed_effect_states = project_observed_effects(tuple(observed_effects))
            capability_results = await environment[
                "capability_result_repository"
            ].list_for_run(
                CapabilityResultOwner(
                    conversation_id=observed_run.conversation_id,
                    run_id=observed_run.run_id,
                )
            )
            pressure_result = await self._pressure_result(
                case,
                workspace=handle.workspace_root,
            )
            terminal = _terminal_observation(
                observed_run,
                observer=observer,
                succeeded_effect_count=len(effect_tools),
            )
            unsafe_failure_artifacts: tuple[ObservedArtifact, ...] = ()
            if (
                recovery_result is not None
                and observed_run.status is GeneralAgentRunStatus.FAILED
                and any(
                    item.action == "stop"
                    and item.reason_code == "checkpoint_unrecoverable"
                    for item in recovery_result.recovery_decisions
                )
            ):
                terminal = ObservedTerminalState(
                    run_status="safe_failure",
                    stop_reason="checkpoint_invalid",
                    resumable=False,
                    pending_human_kind=None,
                )
            if (
                pressure_result is not None
                and pressure_result.unsafe_refusal is not None
            ):
                terminal = _unsafe_context_terminal_observation(
                    observed_run,
                    refusal=pressure_result.unsafe_refusal,
                    interaction_count=len(observer.interaction_records),
                    capability_result_count=len(capability_results),
                    effect_count=len(observed_effects),
                )
                unsafe_failure_artifacts = (
                    _unsafe_context_failure_artifact(
                        observed_run,
                        interaction_count=len(observer.interaction_records),
                        capability_result_count=len(capability_results),
                        effect_count=len(observed_effects),
                    ),
                )
            runtime_facts = RuntimeObservationFacts(
                invocations=project_observed_invocations(
                    tuple(observer.interaction_records)
                ),
                invocation_identities=project_invocation_identities(
                    tuple(observer.interaction_records)
                ),
                human_decisions=observed_human_decisions,
                effects=observed_effect_states,
                artifacts=(
                    *observed_artifacts,
                    *special_artifacts,
                    *unsafe_failure_artifacts,
                    *(
                        _human_decision_artifact(item)
                        for item in observed_human_decisions
                    ),
                ),
                resource_snapshots=resource_snapshots,
                recovery_decisions=(
                    recovery_result.recovery_decisions
                    if recovery_result is not None
                    else (
                        (
                            PressureUnsafeRefusalProjector().project_recovery_decision(
                                pressure_result.unsafe_refusal
                            ),
                        )
                        if pressure_result is not None
                        and pressure_result.unsafe_refusal is not None
                        else ()
                    )
                ),
                terminal=terminal,
                usage=RuntimeUsageFacts(
                    model_calls=(
                        len(environment["gateway"].requests)
                        + (
                            recovery_result.model_call_count
                            if recovery_result is not None
                            else 0
                        )
                    ),
                    total_tokens=0,
                    runtime_ms=max(
                        0,
                        int((monotonic() - case_started) * 1_000),
                    ),
                    context_tokens=(
                        observed_run.context_snapshot.envelope.estimated_token_count
                        if observed_run.context_snapshot is not None
                        else 0
                    ),
                ),
                fixture_isolation=FixtureIsolationFacts(
                    before_sha256=self._snapshot.snapshot_id.removeprefix("fixture_"),
                    after_sha256=sealed_after.snapshot_id.removeprefix("fixture_"),
                    changed_refs=_fixture_changed_refs(
                        self._snapshot,
                        sealed_after,
                    ),
                    external_backend_identity=external_backend.audit_identity,
                    network_attempt_count=(external_backend.network_attempt_count),
                ),
                script_consumption=ScriptConsumptionFacts(
                    declared_step_count=len(case.scripted_steps),
                    consumed_step_count=len(case.scripted_steps),
                    observed_interaction_count=len(observer.interaction_records),
                ),
            )
            assertion_context = (
                pressure_result.assertion_context
                if pressure_result is not None
                else None
            )
            if recovery_result is not None:
                assertion_context = (
                    assertion_context or AssertionEvaluationContext()
                ).model_copy(
                    update={
                        "recovery_reuse": (
                            recovery_result.assertion_context.recovery_reuse
                        ),
                        "checkpoint_availability": (
                            recovery_result.assertion_context.checkpoint_availability
                        ),
                    }
                )
            memory_carriers = tuple(environment.get("memory_carriers", ()))
            if memory_carriers:
                assertion_context = (
                    assertion_context or AssertionEvaluationContext()
                ).model_copy(update={"memory_carriers": memory_carriers})
            observation = SyntheticCaseObservation(
                interactions=tuple(observer.interaction_records),
                case_execution_id=handle.case_execution_id,
                fixture_snapshot_id=self._declared_fixture.snapshot_id,
                run=observed_run,
                run_lineage=run_lineage,
                runtime_facts=runtime_facts,
                assertion_context=assertion_context,
                normalized_result={
                    "case_id": case.case_id,
                    "status": terminal.run_status,
                    "node_statuses": node_statuses,
                    "effect_tools": effect_tools,
                    **(
                        {
                            "recovery": {
                                "triggered_ordinals": (
                                    tuple(sorted(recovery_result.triggered_ordinals))
                                ),
                                "decisions": tuple(
                                    {
                                        "action": item.action,
                                        "reason_code": item.reason_code,
                                        "checkpoint_revision_present": (
                                            item.checkpoint_revision is not None
                                        ),
                                    }
                                    for item in sorted(
                                        recovery_result.recovery_decisions,
                                        key=lambda decision: (
                                            {
                                                "resume": 0,
                                                "reuse_result": 1,
                                                "retry": 2,
                                                "reconcile_effect": 3,
                                                "stop": 4,
                                            }[decision.action],
                                            decision.action,
                                            decision.reason_code,
                                        ),
                                    )
                                ),
                            }
                        }
                        if recovery_result is not None
                        else (
                            {"recovery": environment["recovery_proof"]}
                            if "recovery_proof" in environment
                            else {}
                        )
                    ),
                },
            )
            await self._cleanup_successful_case(
                handle=handle,
                environment=environment,
                client=client,
            )
            update_current_span(
                name=f"评测案例 · {case.name}",
                metadata={
                    "case_id": case.case_id,
                    "case_execution_id": handle.case_execution_id,
                    "run_id": observed_run.run_id,
                    "fault_injection": case.setup.fault_plan_ref is not None,
                },
                output={
                    "运行状态": terminal.run_status,
                    "能力交互数": len(observer.interaction_records),
                    "资源变化数": len(observed_effects),
                },
            )
            return observation
        except Exception as execution_error:
            if environment is not None:
                try:
                    await self._cleanup_successful_case(
                        handle=handle,
                        environment=environment,
                        client=client,
                    )
                except Exception as cleanup_error:
                    raise ExceptionGroup(
                        "Synthetic 案例执行与密封清理同时失败。",
                        [execution_error, cleanup_error],
                    ) from execution_error
            else:
                await client.drop_database(handle.mongo_database)
            raise
        finally:
            try:
                if environment is not None:
                    await environment["runtime"].shutdown()
            finally:
                if environment is not None:
                    environment["checkpoint_client"].close()
                await client.close()

    async def _pressure_result(
        self,
        case: AuthoredCaseSpec,
        *,
        workspace: Path,
    ) -> PressureHarnessResult | None:
        pressure_ref = case.setup.pressure_plan_ref
        if pressure_ref is None:
            return None
        asset = next(
            (
                item
                for item in self._declared_fixture.scenario_assets
                if isinstance(item, PressurePlanAssetSpec)
                and item.asset_id == pressure_ref
            ),
            None,
        )
        if asset is None:
            raise FixtureIsolationError(
                f"案例引用的压力计划不存在或类型错误：{pressure_ref}。"
            )
        return await SyntheticPressureHarness(workspace=workspace).execute(
            asset=asset,
            memory_seed_ref=case.setup.memory_seed_ref,
            current_request=case.user_request_raw,
        )

    async def _recovery_result(
        self,
        case: AuthoredCaseSpec,
        *,
        environment: dict[str, Any],
    ) -> SyntheticRecoveryHarnessResult | None:
        fault_ref = case.setup.fault_plan_ref
        if fault_ref is None:
            return None
        asset = next(
            (
                item
                for item in self._declared_fixture.scenario_assets
                if isinstance(item, FaultPlanAssetSpec) and item.asset_id == fault_ref
            ),
            None,
        )
        if asset is None:
            raise FixtureIsolationError(
                f"案例引用的故障计划不存在或类型错误：{fault_ref}。"
            )
        return await SyntheticRecoveryHarness(
            workspace=environment["workspace"],
            factory=self._factory,
            dependencies=environment["dependencies"],
        ).execute(
            case=case,
            asset=asset,
        )

    async def _cleanup_successful_case(
        self,
        *,
        handle: CaseWorkspaceHandle,
        environment: dict[str, Any],
        client: AsyncMongoClient[Any],
    ) -> None:
        await environment["runtime"].delete_conversation(_CONVERSATION_ID)
        await client.drop_database(handle.mongo_database)
        self._controller.cleanup_workspace(handle)

    async def _build_case_environment(
        self,
        case: AuthoredCaseSpec,
        *,
        workspace: Path,
        database_name: str,
        client: AsyncMongoClient[Any],
    ) -> dict[str, Any]:
        storage = ProjectAssetStorageBackend(workspace)
        await _seed_manuscript(storage)
        chapter_service = ChapterService(storage)
        outline_service = OutlineService(storage)

        knowledge_repository = MongoKnowledgeRepository(
            self._mongodb_uri,
            database_name,
            client=client,
        )
        await knowledge_repository.initialize()
        await _seed_knowledge(knowledge_repository, self._sealed_root)
        knowledge_service = KnowledgeService(knowledge_repository)
        vector_graph_service = _SyntheticStoryContextService(
            knowledge_repository,
            chapter_service,
        )

        driver = StrictScriptedDriver(case.scripted_steps)
        observer = StrictSyntheticInteractionObserver(driver)
        gateway = StrictSyntheticLLMGateway(
            driver,
            observer=observer,
            crash_once_task_name=(
                "general_writing_orchestrator.verify"
                if case.case_id == "recovery_verification_interruption"
                else None
            ),
        )
        model_router = ModelRoleRouter(
            "synthetic-model",
            {
                name: "synthetic-model"
                for name in (
                    "canon_evidence",
                    "character",
                    "consistency_reviewer",
                    "drafting",
                    "external_research",
                    "narrative_reviewer",
                    "narrative_summary",
                    "revision",
                    "scene_planning",
                    "story_architecture",
                    "style_reviewer",
                    "worldbuilding",
                )
            },
        )
        policy = InvocationPolicyService()
        trace_repository = JsonlInvocationTraceRepository(workspace)
        artifact_repository = JsonIntermediateArtifactRepository(workspace)
        await _seed_artifacts(artifact_repository)
        checkpoint_client: MongoClient[Any] = MongoClient(
            self._mongodb_uri,
            tz_aware=True,
            serverSelectionTimeoutMS=5_000,
        )
        memory_collection_name = "langgraph_store"
        database = checkpoint_client[database_name]
        if memory_collection_name not in database.list_collection_names():
            database.create_collection(memory_collection_name)
        graph_store = MongoDBStore(database[memory_collection_name])
        memory_repository = LangGraphAgentMemoryRepository(graph_store)
        memory_service = AgentMemoryService(
            repository=memory_repository,
        )
        memory_fixture, memory_entries_by_ref = await _seed_memories(
            memory_service,
            self._sealed_root / "runtime_memory" / "seed.json",
        )
        external_research_backend = FixtureExternalResearchBackend(
            self._sealed_root / "external_sources"
        )
        context = CapabilityContext(
            capabilities={
                "llm": gateway,
                "chapter_service": chapter_service,
                "outline_service": outline_service,
                "knowledge_service": knowledge_service,
                "knowledge_repository": knowledge_repository,
                "vector_graph_rag_service": vector_graph_service,
                "external_research_service": ExternalResearchService(
                    external_research_backend
                ),
                "invocation_policy_service": policy,
                "invocation_trace_repository": trace_repository,
                "artifact_repository": artifact_repository,
                "model_role_router": model_router,
                "graph_store": graph_store,
            }
        )
        checkpointer = MongoDBSaver(
            checkpoint_client,
            db_name=database_name,
            checkpoint_collection_name="langgraph_checkpoints",
            writes_collection_name="langgraph_checkpoint_writes",
        )
        effects = JsonGeneralAgentEffectRepository(workspace)
        replay_repository = JsonLLMCallReplayRepository(workspace)
        run_repository = JsonGeneralAgentRunRepository(workspace)
        await _seed_conversation_run(run_repository)
        preexisting_runs, _ = await run_repository.list_runs(
            page=1,
            page_size=10_000,
            status="all",
        )
        dependencies = BenchmarkRuntimeDependencies(
            workspace=workspace,
            database_name=database_name,
            capability_context=context,
            llm=gateway,
            model_router=model_router,
            trace_repository=trace_repository,
            run_repository=run_repository,
            event_center=GeneralAgentEventCenter(),
            policy_service=policy,
            memory_service=memory_service,
            context_assembler=_runtime_context_assembler(
                case,
                workspace=workspace,
                fixture=self._declared_fixture,
                memory_service=memory_service,
            ),
            graph_checkpointer=checkpointer,
            graph_store=graph_store,
            effect_repository=effects,
            context_snapshot_repository=JsonGeneralAgentContextSnapshotRepository(
                workspace
            ),
            llm_replay_repository=replay_repository,
            interaction_observer=observer,
        )
        allowed = frozenset(
            {
                *(item.name for item in case.required_invocations),
                *_scripted_plan_capabilities(case),
                *(
                    step.name
                    for step in case.scripted_steps
                    if step.kind in {InteractionKind.TOOL, InteractionKind.SUBAGENT}
                ),
            }
        )
        isolated = self._factory.create(
            dependencies,
            allowed_capabilities=allowed,
        )
        return {
            "case": case,
            "runtime": isolated.runtime,
            "driver": driver,
            "observer": observer,
            "gateway": gateway,
            "memory_repository": memory_repository,
            "memory_service": memory_service,
            "memory_fixture": memory_fixture,
            "memory_entries_by_ref": memory_entries_by_ref,
            "artifact_repository": artifact_repository,
            "knowledge_repository": knowledge_repository,
            "external_research_backend": external_research_backend,
            "checkpointer": checkpointer,
            "checkpoint_client": checkpoint_client,
            "effects": effects,
            "replays": replay_repository,
            "dependencies": dependencies,
            "preexisting_run_ids": tuple(
                sorted(item.run_id for item in preexisting_runs)
            ),
            "allowed_capabilities": allowed,
            "capability_result_repository": (isolated.capability_result_repository),
            "workspace": workspace,
        }

    async def _recover_checkpoint_case(
        self,
        environment: dict[str, Any],
    ) -> Any:
        repository = environment["dependencies"].run_repository
        runs, _ = await repository.list_runs(
            page=1,
            page_size=10,
            status="all",
        )
        runs = [
            run for run in runs if run.run_id != "general_run_20260727_000000_seed00"
        ]
        if len(runs) != 1:
            raise RuntimeError("故障注入后未找到唯一的同一 Runtime run。")
        interrupted = runs[0]
        tool_name = "get_novel_structure"
        before_calls = sum(
            record.interaction.name == tool_name
            and record.interaction.outcome == "completed"
            for record in environment["observer"].capability_records
        )

        workspace: Path = environment["workspace"]
        before_checkpoint = await environment["checkpointer"].aget_tuple(
            {"configurable": {"thread_id": interrupted.conversation_id}}
        )
        if before_checkpoint is None:
            raise RuntimeError("进程终止后没有可恢复的真实 checkpoint。")
        await environment["runtime"].shutdown()
        environment["checkpoint_client"].close()
        reloaded_checkpoint_client: MongoClient[Any] = MongoClient(
            self._mongodb_uri,
            tz_aware=True,
            serverSelectionTimeoutMS=5_000,
        )
        reloaded_checkpointer = MongoDBSaver(
            reloaded_checkpoint_client,
            db_name=environment["dependencies"].database_name,
            checkpoint_collection_name="langgraph_checkpoints",
            writes_collection_name="langgraph_checkpoint_writes",
        )
        reloaded_database = reloaded_checkpoint_client[
            environment["dependencies"].database_name
        ]
        reloaded_graph_store = MongoDBStore(reloaded_database["langgraph_store"])
        reloaded_memory_repository = LangGraphAgentMemoryRepository(
            reloaded_graph_store
        )
        reloaded_memory_service = AgentMemoryService(
            repository=reloaded_memory_repository
        )
        reloaded_effects = JsonGeneralAgentEffectRepository(workspace)
        restarted_dependencies = replace(
            environment["dependencies"],
            run_repository=JsonGeneralAgentRunRepository(workspace),
            event_center=GeneralAgentEventCenter(),
            memory_service=reloaded_memory_service,
            context_assembler=_runtime_context_assembler(
                environment["case"],
                workspace=workspace,
                fixture=self._declared_fixture,
                memory_service=reloaded_memory_service,
            ),
            graph_checkpointer=reloaded_checkpointer,
            graph_store=reloaded_graph_store,
            effect_repository=reloaded_effects,
            context_snapshot_repository=JsonGeneralAgentContextSnapshotRepository(
                workspace
            ),
        )
        restarted = self._factory.create(
            restarted_dependencies,
            allowed_capabilities=environment["allowed_capabilities"],
        )
        environment["runtime"] = restarted.runtime
        environment["checkpointer"] = reloaded_checkpointer
        environment["checkpoint_client"] = reloaded_checkpoint_client
        environment["memory_repository"] = reloaded_memory_repository
        environment["memory_service"] = reloaded_memory_service
        environment["capability_result_repository"] = (
            restarted.capability_result_repository
        )
        environment["effects"] = reloaded_effects
        environment["dependencies"] = restarted_dependencies
        recovered_count = await restarted.runtime.recover_interrupted()

        completed = interrupted
        deadline = monotonic() + 600
        while monotonic() < deadline:
            await asyncio.sleep(0.05)
            completed = await restarted.runtime.get(interrupted.run_id)
            if completed.status.value in {
                "completed",
                "failed",
                "cancelled",
                "waiting_human",
            }:
                break
        await restarted.runtime.shutdown()

        after_calls = sum(
            record.interaction.name == tool_name
            and record.interaction.outcome == "completed"
            for record in environment["observer"].capability_records
        )
        after_checkpoint = await reloaded_checkpointer.aget_tuple(
            {"configurable": {"thread_id": interrupted.conversation_id}}
        )
        if after_checkpoint is None:
            raise RuntimeError("恢复完成后没有可读取的真实 checkpoint。")
        gateway: StrictSyntheticLLMGateway = environment["gateway"]
        environment["recovery_proof"] = {
            "fault_point": "general_writing_orchestrator.verify",
            "run_id_before": interrupted.run_id,
            "run_id_after": completed.run_id,
            "same_run": completed.run_id == interrupted.run_id,
            "recover_interrupted_count": recovered_count,
            "verify_attempts": sum(
                request.task_name == "general_writing_orchestrator.verify"
                for request in gateway.requests
            ),
            "checkpoint_before": _checkpoint_proof(before_checkpoint),
            "checkpoint_after": _checkpoint_proof(after_checkpoint),
            "no_rerun": {
                tool_name: {
                    "before": before_calls,
                    "after": after_calls,
                }
            },
        }
        return completed


async def _observed_artifacts(
    environment: dict[str, Any],
    run: GeneralAgentRun,
) -> tuple[ObservedArtifact, ...]:
    repository: JsonIntermediateArtifactRepository = environment["artifact_repository"]
    artifact_ids = tuple(
        dict.fromkeys(
            artifact_id for node in run.node_runs for artifact_id in node.artifact_refs
        )
    )
    observed: list[ObservedArtifact] = []
    for artifact_id in artifact_ids:
        record = await repository.get(artifact_id)
        if record is None:
            continue
        producer = next(
            (
                node.node_id
                for node in run.node_runs
                if artifact_id in node.artifact_refs
            ),
            None,
        )
        observed.append(
            ObservedArtifact(
                artifact_id=record.artifact_id,
                artifact_kind=record.artifact_type,
                producer_node_id=producer,
                content_sha256=record.content_sha256,
                source_refs=tuple(record.source_refs),
                payload=record.payload,
            )
        )
    return tuple(observed)


def _human_decision_artifact(
    decision: ObservedHumanDecision,
) -> ObservedArtifact:
    payload = decision.model_dump(mode="json")
    content_sha256 = canonical_sha256(payload)
    return ObservedArtifact(
        artifact_id=f"human_decision_{content_sha256[:32]}",
        artifact_kind="human_intervention",
        producer_node_id=decision.node_id,
        content_sha256=content_sha256,
        payload=payload,
    )


def _recovery_special_artifacts(
    run: GeneralAgentRun,
    *,
    recovery_result: SyntheticRecoveryHarnessResult | None,
) -> tuple[ObservedArtifact, ...]:
    if recovery_result is None:
        return ()
    if (
        run.status is GeneralAgentRunStatus.WAITING_HUMAN
        and run.pending_human_request is not None
    ):
        payload: dict[str, Any] = {
            "run_id": run.run_id,
            "fault_triggered_ordinals": recovery_result.triggered_ordinals,
            "request": run.pending_human_request.model_dump(mode="json"),
        }
        content_sha256 = canonical_sha256(payload)
        return (
            ObservedArtifact(
                artifact_id=f"recovery_human_{content_sha256[:32]}",
                artifact_kind="human_intervention",
                producer_node_id=run.pending_human_request.node_id,
                content_sha256=content_sha256,
                payload=payload,
            ),
        )
    if run.status is GeneralAgentRunStatus.FAILED and any(
        item.action == "stop" and item.reason_code == "checkpoint_unrecoverable"
        for item in recovery_result.recovery_decisions
    ):
        payload = {
            "summary": "检查点已损坏或不兼容，Runtime 已安全停止且没有静默重跑。",
            "run_id": run.run_id,
            "resumable": run.resumable,
            "recovery_decisions": tuple(
                item.model_dump(mode="json")
                for item in recovery_result.recovery_decisions
            ),
        }
        content_sha256 = canonical_sha256(payload)
        return (
            ObservedArtifact(
                artifact_id=f"checkpoint_failure_{content_sha256[:32]}",
                artifact_kind="checkpoint_failure_report",
                producer_node_id=None,
                content_sha256=content_sha256,
                payload=payload,
            ),
        )
    return ()


def _terminal_observation(
    run: GeneralAgentRun,
    *,
    observer: StrictSyntheticInteractionObserver,
    succeeded_effect_count: int,
) -> ObservedTerminalState:
    pending = run.pending_human_request
    if run.status is GeneralAgentRunStatus.WAITING_HUMAN:
        pending_kind = pending.kind if pending is not None else None
        return ObservedTerminalState(
            run_status=run.status.value,
            stop_reason=(
                "waiting_authorization"
                if pending_kind == "write_authorization"
                else "waiting_human"
            ),
            resumable=run.resumable,
            pending_human_kind=pending_kind,
        )
    denied_write = any(
        record.interaction.kind is InteractionKind.HUMAN
        and record.interaction.name == "write_authorization"
        and record.interaction.payload.get("approved") is False
        for record in observer.interaction_records
    )
    preview_only = (
        run.status is GeneralAgentRunStatus.COMPLETED
        and succeeded_effect_count == 0
        and any(
            node.capability_name == "preview_manuscript_patch" for node in run.node_runs
        )
    )
    semantic_status = (
        "write_rejected"
        if denied_write
        else "preview_only"
        if preview_only
        else run.status.value
    )
    stop_reason = (
        "write_rejected"
        if denied_write
        else "preview_only"
        if preview_only
        else "goal_satisfied"
        if run.status is GeneralAgentRunStatus.COMPLETED
        else (
            run.lifecycle_events[-1].reason
            if run.lifecycle_events
            else run.status.value
        )
    )
    return ObservedTerminalState(
        run_status=semantic_status,
        stop_reason=stop_reason,
        resumable=run.resumable,
        pending_human_kind=None,
    )


def _unsafe_context_terminal_observation(
    run: GeneralAgentRun,
    *,
    refusal: Any,
    interaction_count: int,
    capability_result_count: int,
    effect_count: int,
) -> ObservedTerminalState:
    failure_evidence = _runtime_failure_evidence(run)
    reason_codes = {
        item["reason_code"]
        for item in failure_evidence
        if isinstance(item.get("reason_code"), str)
    }
    qualifies = (
        run.status is GeneralAgentRunStatus.FAILED
        and run.resumable is False
        and run.plan is None
        and not run.node_runs
        and interaction_count == 0
        and capability_result_count == 0
        and effect_count == 0
        and reason_codes == {"unsafe_context"}
        and refusal.reason_code == "unsafe_context"
        and refusal.run_status == "safe_failure"
        and refusal.resumable is False
    )
    if not qualifies:
        raise RuntimeError(
            "只有生产 Runtime 在规划前因 unsafe_context 不可恢复失败，"
            "且能力调用、CapabilityResult 与 Effect 均为零时，"
            "才能映射为 Benchmark 安全失败。"
        )
    return ObservedTerminalState(
        run_status="safe_failure",
        stop_reason="unsafe_context",
        resumable=False,
        pending_human_kind=None,
    )


def _runtime_failure_evidence(run: GeneralAgentRun) -> tuple[dict[str, Any], ...]:
    evidence: list[dict[str, Any]] = []
    for item in run.errors:
        try:
            payload = json.loads(item)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            evidence.append(payload)
    return tuple(evidence)


def _unsafe_context_failure_artifact(
    run: GeneralAgentRun,
    *,
    interaction_count: int,
    capability_result_count: int,
    effect_count: int,
) -> ObservedArtifact:
    payload = {
        "run_id": run.run_id,
        "run_status": run.status.value,
        "resumable": run.resumable,
        "plan_present": run.plan is not None,
        "node_count": len(run.node_runs),
        "interaction_count": interaction_count,
        "capability_result_count": capability_result_count,
        "effect_count": effect_count,
        "failure_evidence": _runtime_failure_evidence(run),
    }
    content_sha256 = canonical_sha256(payload)
    return ObservedArtifact(
        artifact_id=f"unsafe_context_failure_{content_sha256[:32]}",
        artifact_kind="runtime_safe_failure",
        producer_node_id=None,
        content_sha256=content_sha256,
        source_refs=(f"run:{run.run_id}",),
        payload=payload,
    )


def _runtime_context_assembler(
    case: AuthoredCaseSpec,
    *,
    workspace: Path,
    fixture: Any,
    memory_service: AgentMemoryService,
) -> ContextAssembler:
    pressure_ref = case.setup.pressure_plan_ref
    pressure_asset = next(
        (
            item
            for item in fixture.scenario_assets
            if isinstance(item, PressurePlanAssetSpec) and item.asset_id == pressure_ref
        ),
        None,
    )
    from taichu.infrastructure.evaluations.general_agent_benchmark.context_projection import (
        DeterministicProjectionAssembler,
    )

    return DeterministicProjectionAssembler(
        root=workspace,
        memory_service=memory_service,
        context_window_tokens=max(1, len(case.user_request_raw) // 2)
        if pressure_asset is not None and pressure_asset.carrier == "unsafe_total"
        else None,
    )


def _fixture_changed_refs(
    before: FixtureSnapshotSpec,
    after: FixtureSnapshotSpec,
) -> tuple[str, ...]:
    before_entries = {
        item.path: (item.kind, item.size_bytes, item.sha256)
        for item in before.manifest_entries
    }
    after_entries = {
        item.path: (item.kind, item.size_bytes, item.sha256)
        for item in after.manifest_entries
    }
    return tuple(
        path
        for path in sorted(set(before_entries) | set(after_entries))
        if before_entries.get(path) != after_entries.get(path)
    )


async def _seed_manuscript(storage: ProjectAssetStorageBackend) -> None:
    await storage.ensure_skeleton()
    base = "manuscripts/chapters/volume_001_fixture_core"
    await storage.move_chapter_markdown(
        "manuscripts/chapters/chapter_001.md",
        f"{base}/chapter_001.md",
    )
    await storage.move_chapter_markdown(
        "manuscripts/chapters/chapter_002.md",
        f"{base}/chapter_002.md",
    )
    await storage.write_outline(
        {
            "volumes": [
                {
                    "volume_id": "fixture_volume_core",
                    "name": "fixture_core",
                    "order": 1,
                    "chapters": [
                        {
                            "chapter_id": "chapter_001",
                            "display_title": "第1章",
                            "order": 1,
                            "markdown_path": (f"{base}/chapter_001.md"),
                        },
                        {
                            "chapter_id": "chapter_002",
                            "display_title": "第2章",
                            "order": 2,
                            "markdown_path": (f"{base}/chapter_002.md"),
                        },
                    ],
                },
                {
                    "volume_id": "fixture_delete_volume",
                    "name": "fixture_delete",
                    "order": 2,
                    "chapters": [],
                },
            ],
            "current_volume_id": "fixture_volume_core",
            "current_chapter_id": "chapter_001",
            "updated_at": _FIXTURE_TIME,
        }
    )
    await storage.write_manifest(
        {
            "schema_version": "1",
            "current_chapter_id": "chapter_001",
            "volumes": [
                {"id": "fixture_volume_core", "title": "fixture_core", "order": 1},
                {
                    "id": "fixture_delete_volume",
                    "title": "fixture_delete",
                    "order": 2,
                },
            ],
            "chapters": [
                _manifest_chapter(
                    "chapter_001",
                    1,
                    f"{base}/chapter_001.md",
                ),
                _manifest_chapter(
                    "chapter_002",
                    2,
                    f"{base}/chapter_002.md",
                ),
            ],
            "updated_at": _FIXTURE_TIME,
        }
    )


def _manifest_chapter(chapter_id: str, order: int, path: str) -> dict[str, Any]:
    return {
        "id": chapter_id,
        "volume_id": "fixture_volume_core",
        "title": f"第{order}章",
        "order": order,
        "markdown_path": path,
        "status": "draft",
        "word_count": 0,
        "created_at": _FIXTURE_TIME,
        "updated_at": _FIXTURE_TIME,
    }


async def _seed_knowledge(
    repository: MongoKnowledgeRepository,
    sealed_root: Path,
) -> None:
    payload = json.loads(
        (sealed_root / "knowledge" / "confirmed_cards.json").read_text(encoding="utf-8")
    )
    for item in payload:
        await repository.create_card(StructuredKnowledgeCard.model_validate(item))


async def _seed_artifacts(
    repository: JsonIntermediateArtifactRepository,
) -> None:
    for name, identity in (
        ("consistency", "a" * 32),
        ("narrative", "b" * 32),
        ("style", "c" * 32),
    ):
        await repository.save(
            IntermediateArtifactRecord(
                artifact_id=f"artifact_{identity}",
                artifact_type=f"{name}_review",
                producer=f"{name}_reviewer",
                task_id="benchmark_fixture",
                run_id="benchmark_fixture",
                call_id=f"fixture_{name}",
                input_sha256="a" * 64,
                content_sha256="b" * 64,
                payload={"verdict": f"{name} fixture review"},
                source_refs=[],
                created_at=_FIXTURE_TIME,
            )
        )


async def _seed_conversation_run(
    repository: JsonGeneralAgentRunRepository,
) -> None:
    """建立不进入历史消息的会话锚点，使夹具记忆按真实会话召回。"""
    await repository.save(
        GeneralAgentRun(
            run_id="general_run_20260727_000000_seed00",
            task_id=_CONVERSATION_ID,
            conversation_id=_CONVERSATION_ID,
            request_index=1,
            user_goal="初始化隔离评测会话。",
            status=GeneralAgentRunStatus.COMPLETED,
            messages=[],
            final_answer="",
            created_at=_FIXTURE_TIME,
            updated_at=_FIXTURE_TIME,
            started_at=_FIXTURE_TIME,
            finished_at=_FIXTURE_TIME,
        )
    )


async def _seed_memories(
    service: AgentMemoryService,
    fixture_path: Path,
) -> tuple[MemoryBehaviorSeedFixture, dict[str, AgentMemoryEntry]]:
    """经生产 MemoryService 写入并转换状态，不直接伪造 memory_id 或 validity。"""

    fixture = load_memory_behavior_seed(fixture_path)
    entries_by_ref: dict[str, AgentMemoryEntry] = {}
    for item in fixture.entries:
        dependencies = [
            AgentMemoryDependency(
                memory_id=entries_by_ref[dependency.memory_ref].memory_id,
                relation=dependency.relation,
            )
            for dependency in item.dependencies
        ]
        supersedes_memory_id = (
            entries_by_ref[item.supersedes_memory_ref].memory_id
            if item.supersedes_memory_ref is not None
            else None
        )
        entries_by_ref[item.memory_ref] = await service.write(
            MemoryWriteCandidate(
                kind=item.kind,
                content=item.content,
                conversation_id=_CONVERSATION_ID,
                created_request_index=1,
                producer_ref=item.producer_ref,
                dependencies=dependencies,
                supersedes_memory_id=supersedes_memory_id,
            )
        )

    for item in fixture.entries:
        if item.target_validity in {
            AgentMemoryValidity.ACTIVE,
            AgentMemoryValidity.SUPERSEDED,
        }:
            continue
        current = await service.get(entries_by_ref[item.memory_ref].memory_id)
        if current is not None and current.validity is item.target_validity:
            continue
        updated = await service.invalidate(
            entries_by_ref[item.memory_ref].memory_id,
            validity=item.target_validity,
            reason=item.invalidation_reason,
        )
        if updated is None:
            raise RuntimeError(f"运行工作记忆夹具失效失败：{item.memory_ref}")

    refreshed: dict[str, AgentMemoryEntry] = {}
    for item in fixture.entries:
        current = await service.get(entries_by_ref[item.memory_ref].memory_id)
        if current is None or current.validity is not item.target_validity:
            raise RuntimeError(
                "运行工作记忆夹具目标状态不一致："
                f"{item.memory_ref} -> {getattr(current, 'validity', None)}"
            )
        refreshed[item.memory_ref] = current
    return fixture, refreshed


def _expected_approval(step: Any) -> bool:
    for matcher in step.matchers:
        if matcher.path == "/approved" and isinstance(matcher.expected, bool):
            return matcher.expected
    raise RuntimeError("人工步骤缺少 approved 布尔匹配器。")


def _bind_followup_response(
    gateway: StrictSyntheticLLMGateway,
    run: Any,
) -> None:
    if not run.node_runs:
        return
    output = run.node_runs[-1].output
    if not isinstance(output, dict):
        return
    bindings: dict[str, Any] = {}
    changes = output.get("changes")
    if isinstance(changes, list) and changes and isinstance(changes[0], dict):
        bindings["fixture_created_structure_item_id"] = changes[0].get("item_id")
    structure_version = output.get("structure_version")
    if isinstance(structure_version, str):
        bindings["f" * 64] = structure_version
    card = output.get("card")
    if isinstance(card, dict):
        if isinstance(card.get("id"), str):
            bindings["fixture_created_knowledge_card_id"] = card["id"]
        if isinstance(card.get("updated_at"), str):
            bindings["fixture_created_knowledge_updated_at"] = card["updated_at"]
    gateway.set_response_bindings(
        {key: value for key, value in bindings.items() if value is not None}
    )


async def _project_memory_observations(
    case: AuthoredCaseSpec,
    environment: dict[str, Any],
    run: Any,
) -> None:
    if not case.case_id.startswith("memory_"):
        return
    artifact = await _memory_behavior_artifact(case, environment, run)
    projector = MemoryBehaviorProjector()
    environment["memory_behavior_artifact"] = artifact
    environment["memory_carriers"] = projector.to_oracle_observations(artifact)


async def _memory_behavior_artifact(
    case: AuthoredCaseSpec,
    environment: dict[str, Any],
    run: GeneralAgentRun,
) -> MemoryBehaviorArtifact:
    fixture: MemoryBehaviorSeedFixture = environment["memory_fixture"]
    entries: dict[str, AgentMemoryEntry] = environment["memory_entries_by_ref"]
    projector = MemoryBehaviorProjector()
    case_id = cast(MemoryBehaviorCaseId, case.case_id)
    if run.context_snapshot is None:
        raise RuntimeError("运行工作记忆行为案例缺少真实 Context Snapshot。")
    gateway: StrictSyntheticLLMGateway = environment["gateway"]
    evidence_ref = f"memory_behavior_{case.case_id}"
    contract = fixture.answer_contracts[case_id]
    if case_id == "memory_active_projection":
        (
            baseline_snapshot,
            baseline_request,
            baseline_answer,
        ) = await _active_memory_baseline(case, environment, run)
        return projector.project_active_pair(
            memory_seed_ref=fixture.memory_seed_ref,
            target_memory=entries["memory_active_style"],
            baseline_snapshot=baseline_snapshot,
            candidate_snapshot=run.context_snapshot,
            baseline_request=baseline_request,
            candidate_request=_orchestrator_request(gateway, phase="plan"),
            baseline_answer=baseline_answer,
            candidate_answer=run.final_answer,
            answer_contract=contract,
            evidence_ref=evidence_ref,
        )

    logical_refs = {
        "memory_stale_dependency": (
            "memory_stale_scope",
            "memory_stale_dependent",
        ),
        "memory_rejected_parallel_isolation": ("memory_rejected_fact",),
        "memory_superseded_repair": ("memory_superseded_old_style",),
    }[case_id]
    invalid_memories = tuple(entries[item] for item in logical_refs)
    sentinel_refs = {
        entries[item].memory_id: next(
            seed.sentinel_ref for seed in fixture.entries if seed.memory_ref == item
        )
        for item in logical_refs
    }
    branches = (
        _memory_branch_exchanges(environment, run)
        if case_id == "memory_rejected_parallel_isolation"
        else ()
    )
    return projector.project_invalid_case(
        case_id=case_id,
        memory_seed_ref=fixture.memory_seed_ref,
        invalid_memories=invalid_memories,
        sentinel_refs=sentinel_refs,
        snapshot=run.context_snapshot,
        orchestrator_request=_orchestrator_request(
            gateway,
            phase=(
                "verify" if case_id == "memory_rejected_parallel_isolation" else "plan"
            ),
        ),
        final_answer=run.final_answer,
        answer_contract=contract,
        evidence_ref=evidence_ref,
        branches=branches,
        latest_active_memory=(
            entries["memory_active_style"]
            if case_id == "memory_superseded_repair"
            else None
        ),
    )


async def _active_memory_baseline(
    case: AuthoredCaseSpec,
    environment: dict[str, Any],
    run: GeneralAgentRun,
) -> tuple[Any, Any, str]:
    """同一当前请求、同一生产装配与 Orchestrator，仅换成无目标记忆会话。"""

    fixture: MemoryBehaviorSeedFixture = environment["memory_fixture"]
    baseline_run = GeneralAgentRun(
        run_id="general_run_20260730_000000_membas",
        task_id="memory_active_baseline",
        conversation_id="benchmark_memory_active_baseline",
        request_index=run.request_index,
        user_goal=run.user_goal,
        scope=run.scope,
        author_constraints=list(run.author_constraints),
        external_access_allowed=run.external_access_allowed,
        limits=run.limits,
        created_at=_FIXTURE_TIME,
        updated_at=_FIXTURE_TIME,
        started_at=_FIXTURE_TIME,
    )
    assembled = await ContextAssembler(
        memory_service=environment["memory_service"]
    ).assemble(baseline_run, phase="plan")
    baseline_step = case.scripted_steps[0].model_copy(
        update={
            "step_id": "memory_active_baseline_plan",
            "response": {
                "rationale": "无目标运行工作记忆时按普通段落直接回答。",
                "direct_response": fixture.active_baseline_answer,
                "nodes": [],
            },
        }
    )
    driver = StrictScriptedDriver((baseline_step,))
    gateway = StrictSyntheticLLMGateway(driver)
    tools = ToolRegistry(CapabilityContext(capabilities={}))
    subagents = SubagentRegistry(
        CapabilityContext(capabilities={"tool_registry": tools})
    )
    orchestrator = OrchestratorAgent(
        llm=GatewayChatModel(gateway, model_id="synthetic-model"),
        model_router=ModelRoleRouter("synthetic-model"),
        tool_registry=tools,
        subagent_registry=subagents,
    )
    plan = await orchestrator.plan(
        baseline_run,
        context=assembled.snapshot.envelope,
    )
    if plan.direct_response != fixture.active_baseline_answer:
        raise RuntimeError("active 成对基线没有保留密封答案。")
    return assembled.snapshot, gateway.requests[-1], plan.direct_response


def _orchestrator_request(
    gateway: StrictSyntheticLLMGateway,
    *,
    phase: str,
) -> Any:
    suffix = f".{phase}"
    matches = [
        request for request in gateway.requests if request.task_name.endswith(suffix)
    ]
    if not matches:
        raise RuntimeError(f"缺少真实 Orchestrator {phase} 模型请求。")
    return matches[-1]


def _memory_branch_exchanges(
    environment: dict[str, Any],
    run: GeneralAgentRun,
) -> tuple[MemoryBranchExchange, ...]:
    gateway: StrictSyntheticLLMGateway = environment["gateway"]
    observer: StrictSyntheticInteractionObserver = environment["observer"]
    branches: list[MemoryBranchExchange] = []
    for branch_id, capability_name in (
        ("mark_source", "worldbuilding"),
        ("character_knowledge", "character"),
    ):
        node = next(
            item for item in run.node_runs if item.capability_name == capability_name
        )
        request = next(
            item for item in gateway.requests if item.task_name == capability_name
        )
        record = next(
            item
            for item in observer.capability_records
            if item.interaction.name == capability_name and item.node_id == node.node_id
        )
        result_payload = dict(record.response_payload or {})
        branches.append(
            MemoryBranchExchange(
                branch_id=branch_id,
                node_id=node.node_id,
                dependencies=tuple(node.dependencies),
                resolved_input=node.resolved_input,
                output=node.output,
                request_payload=tuple(
                    {
                        "role": message.role,
                        "content": message.content,
                        "tool_calls": [
                            {
                                "call_id": call.call_id,
                                "name": call.name,
                                "arguments_json": call.arguments_json,
                            }
                            for call in message.tool_calls
                        ],
                    }
                    for message in request.messages
                ),
                envelope_payload={
                    "invocation_id": record.call_id,
                    "capability_name": capability_name,
                    "status": record.interaction.outcome,
                    "output": result_payload,
                    "source_refs": record.source_refs,
                    "artifact_refs": record.artifact_refs,
                },
                result_payload=result_payload,
                evidence_ref=f"memory_branch_{branch_id}",
            )
        )
    return tuple(branches)


def _scripted_plan_capabilities(case: AuthoredCaseSpec) -> tuple[str, ...]:
    """计划可见能力与“必须实际调用”分离，允许合同声明零次调用。"""

    names: set[str] = set()
    for step in case.scripted_steps:
        response = step.response
        if step.kind is not InteractionKind.MODEL or not isinstance(response, dict):
            continue
        nodes = response.get("nodes")
        if not isinstance(nodes, list):
            continue
        for node in nodes:
            if not isinstance(node, dict):
                continue
            capability_name = node.get("capability_name")
            if isinstance(capability_name, str) and capability_name:
                names.add(capability_name)
    return tuple(sorted(names))


def _resource_target_refs(
    interactions: tuple[Any, ...],
    effects: tuple[Any, ...],
) -> tuple[str, ...]:
    """从真实能力输入/输出和 Effect scope 派生逻辑写入目标。"""

    targets: set[str] = set()
    for effect in effects:
        for scope in getattr(effect, "resource_scopes", ()):
            _append_scope_target(targets, str(scope))
    for record in interactions:
        name = getattr(getattr(record, "interaction", None), "name", "")
        request = getattr(record, "request_payload", None) or {}
        response = getattr(record, "response_payload", None) or {}
        if name in {
            "preview_manuscript_patch",
            "apply_manuscript_patch",
        }:
            chapter_id = request.get("chapter_id")
            if isinstance(chapter_id, str) and chapter_id:
                targets.add(f"manuscript:{chapter_id}")
        elif name in {
            "create_novel_structure_items",
            "update_novel_structure",
            "delete_novel_structure_items",
        }:
            changes = response.get("changes")
            if isinstance(changes, list):
                for change in changes:
                    if not isinstance(change, dict):
                        continue
                    kind = change.get("kind")
                    item_id = change.get("item_id")
                    if (
                        isinstance(kind, str)
                        and kind
                        and isinstance(item_id, str)
                        and item_id
                    ):
                        targets.add(f"structure:{kind}:{item_id}")
        elif name in {
            "create_confirmed_knowledge",
            "update_confirmed_knowledge",
        }:
            for payload in (request, response):
                for key in ("card_id", "id"):
                    card_id = payload.get(key)
                    if isinstance(card_id, str) and card_id:
                        targets.add(f"knowledge:{card_id}")
                card = payload.get("card")
                if isinstance(card, dict):
                    card_id = card.get("id")
                    if isinstance(card_id, str) and card_id:
                        targets.add(f"knowledge:{card_id}")
    return tuple(sorted(targets))


def _resources_for_snapshot(
    snapshot_ref: str,
    resources: tuple[ResourceStateItem, ...],
) -> tuple[ResourceStateItem, ...]:
    if snapshot_ref == "resource_snapshot_manuscript_chapter_001":
        return tuple(
            item for item in resources if item.resource_ref == "manuscript:chapter_001"
        )
    if snapshot_ref == "resource_snapshot_novel_structure":
        return tuple(
            item for item in resources if item.resource_ref.startswith("structure:")
        )
    if snapshot_ref == "resource_snapshot_confirmed_knowledge":
        return tuple(
            item for item in resources if item.resource_ref.startswith("knowledge:")
        )
    return resources


def _targets_for_snapshot(
    snapshot_ref: str,
    targets: tuple[str, ...],
) -> tuple[str, ...]:
    if snapshot_ref == "resource_snapshot_manuscript_chapter_001":
        return tuple(item for item in targets if item == "manuscript:chapter_001")
    if snapshot_ref == "resource_snapshot_novel_structure":
        return tuple(item for item in targets if item.startswith("structure:"))
    if snapshot_ref == "resource_snapshot_confirmed_knowledge":
        return tuple(item for item in targets if item.startswith("knowledge:"))
    return targets


def _append_scope_target(targets: set[str], scope: str) -> None:
    prefix, separator, value = scope.partition(":")
    if not separator or not value:
        return
    if prefix in {"chapter_id", "chapter_ids"}:
        targets.add(f"manuscript:{value}")
    elif prefix in {"card_id", "card_ids"}:
        targets.add(f"knowledge:{value}")
    elif prefix == "volume_id":
        targets.add(f"structure:volume:{value}")
    elif prefix == "parent_id":
        targets.add(f"structure:parent:{value}")


def _case_execution_id(case_id: str) -> str:
    from uuid import uuid4

    del case_id
    return f"benchmark_case_{uuid4().hex}"


def _checkpoint_proof(checkpoint: object) -> dict[str, Any]:
    config = getattr(checkpoint, "config", {})
    payload = getattr(checkpoint, "checkpoint", {})
    metadata = getattr(checkpoint, "metadata", {})
    configurable = config.get("configurable", {})
    checkpoint_id = configurable.get("checkpoint_id") or payload.get("id")
    return {
        "checkpoint_id": str(checkpoint_id),
        "source": str(metadata.get("source", "unknown")),
        "step": int(metadata.get("step", -1)),
    }


def fixture_structure_version() -> str:
    """供套件合同测试断言固定结构输入与环境 builder 一致。"""
    return _STRUCTURE_VERSION
