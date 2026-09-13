"""通用写作助手顶层 LangGraph、生命周期和恢复服务。"""

from __future__ import annotations

import asyncio
import builtins
from collections.abc import Mapping
from datetime import UTC, datetime
from hashlib import sha256
import json
from typing import Any, Literal, TypedDict
from uuid import uuid4
from weakref import WeakValueDictionary

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore
from langgraph.types import Command, StateSnapshot, interrupt

from taichu.application.contracts.general_agent_run import GeneralAgentRunRepository
from taichu.application.contracts.general_agent_capability_results import (
    CapabilityResultOwner,
    GeneralAgentCapabilityResultRepository,
)
from taichu.application.contracts.general_agent_context_snapshot import (
    GeneralAgentContextSnapshotRepository,
)
from taichu.application.contracts.general_agent_tool_budget import (
    GeneralAgentToolBudgetOwner,
    GeneralAgentToolBudgetRepository,
)
from taichu.application.contracts.llm_replay import LLMCallReplayRepository
from taichu.application.contracts.general_agent_effects import (
    GeneralAgentEffectRepository,
)
from taichu.application.general_agent.context import (
    ContextAssembler,
    ContextAssemblyError,
    ContextAssemblyResult,
    ContextPhase,
)
from taichu.application.general_agent.events import GeneralAgentEventCenter
from taichu.application.general_agent.executor import (
    DynamicDagExecutor,
)
from taichu.application.general_agent.faults import (
    GeneralAgentFaultContext,
    GeneralAgentFaultHook,
    GeneralAgentFaultPoint,
    InjectedProcessTermination,
)
from taichu.application.general_agent.models import (
    GeneralAgentConversation,
    GeneralAgentContextSnapshot,
    GeneralAgentContextCategoryStat,
    GeneralAgentCompressionStats,
    GeneralAgentHumanRequest,
    GeneralAgentLifecycleEvent,
    GeneralAgentMessage,
    GeneralAgentMessageType,
    GeneralAgentNodeRun,
    GeneralAgentNodeStatus,
    GeneralAgentRun,
    GeneralAgentRunLimits,
    GeneralAgentRunStatus,
    GeneralAgentScope,
    RecoveryAction,
    RecoveryDecision,
    recovery_evidence_sha256,
    result_basis_sha256,
)
from taichu.application.general_agent.orchestrator import OrchestratorAgent
from taichu.application.general_agent.request_analysis import (
    explicit_chapter_orders,
    is_explicit_chapter_content_request,
)
from taichu.application.general_agent.recovery import (
    CheckpointHistorySummary,
    CheckpointPersistenceSummary,
    EffectRecord,
    EffectStatus,
    EffectSummary,
    GeneralAgentRecoveryCoordinator,
    GeneralAgentRecoveryIntegrityError,
    GeneralAgentRecoveryRequiresHumanError,
    GeneralAgentRecoverySnapshot,
)
from taichu.application.invocations.models import now_iso
from taichu.application.models.llm_replay import LLMCallReplayRecord
from taichu.application.services.invocation_policy_service import (
    InvocationPolicyService,
)
from taichu.application.services.agent_memory_service import AgentMemoryService

from taichu.application.general_agent.pipeline import (
    ContextPipeline,
    ContextCapacityError,
)

from taichu.application.general_agent.context import create_snapshot

_ACTIVE_STATUSES = {
    GeneralAgentRunStatus.INIT,
    GeneralAgentRunStatus.CLARIFYING,
    GeneralAgentRunStatus.PLANNING,
    GeneralAgentRunStatus.EXECUTING,
    GeneralAgentRunStatus.VERIFYING,
    GeneralAgentRunStatus.REPLANNING,
}
_TERMINAL_STATUSES = {
    GeneralAgentRunStatus.COMPLETED,
    GeneralAgentRunStatus.CANCELLED,
}


class _RuntimeGraphState(TypedDict, total=False):
    run: dict[str, Any]
    replan_guidance: str
    manual_compaction: bool


class GeneralAgentRuntimeService:
    """用独立业务状态承载高层编排 Agent 的完整执行循环。"""

    def __init__(
        self,
        *,
        repository: GeneralAgentRunRepository,
        event_center: GeneralAgentEventCenter,
        orchestrator: OrchestratorAgent,
        executor: DynamicDagExecutor,
        policy_service: InvocationPolicyService,
        memory_service: AgentMemoryService,
        context_assembler: ContextAssembler,
        capability_result_repository: GeneralAgentCapabilityResultRepository,
        graph_checkpointer: BaseCheckpointSaver[Any],
        effect_repository: GeneralAgentEffectRepository,
        context_snapshot_repository: GeneralAgentContextSnapshotRepository,
        llm_replay_repository: LLMCallReplayRepository,
        tool_budget_repository: GeneralAgentToolBudgetRepository | None = None,
        graph_store: BaseStore | None = None,
        fault_hook: GeneralAgentFaultHook | None = None,
        context_pipeline: ContextPipeline | None = None,
    ) -> None:
        self._repository = repository
        self._event_center = event_center
        self._orchestrator = orchestrator
        self._executor = executor
        self._policy_service = policy_service
        self._memory_service = memory_service
        self._executor.bind_memory_validity_provider(memory_service)
        self._context_assembler = context_assembler
        self._context_pipeline = context_pipeline
        self._executor.bind_context_boundary(self._after_node_batch)
        self._graph_checkpointer = graph_checkpointer
        self._graph_store = graph_store
        self._effect_repository = effect_repository
        self._capability_result_repository = capability_result_repository
        if (
            self._executor.capability_result_repository
            is not capability_result_repository
        ):
            raise ValueError(
                "Run Service 与动态执行器必须使用同一 CapabilityResult 仓储。"
            )
        self._context_snapshot_repository = context_snapshot_repository
        self._llm_replay_repository = llm_replay_repository
        self._tool_budget_repository = tool_budget_repository
        self._fault_hook = fault_hook
        self._recovery_coordinator = GeneralAgentRecoveryCoordinator(
            run_repository=repository,
            effect_repository=effect_repository,
            graph_checkpointer=self._graph_checkpointer,
            capability_result_repository=capability_result_repository,
            context_snapshot_repository=context_snapshot_repository,
        )
        self._tasks: dict[str, asyncio.Task[GeneralAgentRun]] = {}
        self._locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
        self._conversation_locks: WeakValueDictionary[str, asyncio.Lock] = (
            WeakValueDictionary()
        )
        self._shutting_down = False
        self._graph = self._build_graph()

    async def create_run(
        self,
        *,
        user_goal: str,
        model_id: str | None = None,
        conversation_id: str | None = None,
        start_new_conversation: bool | None = None,
        scope: GeneralAgentScope | None = None,
        author_constraints: list[str] | None = None,
        external_access_allowed: bool = False,
        limits: GeneralAgentRunLimits | None = None,
    ) -> GeneralAgentRun:
        goal = user_goal
        if not goal.strip():
            raise GeneralAgentRuntimeError("任务目标不能为空。")
        timestamp = datetime.now(UTC)
        run_id = f"general_run_{timestamp.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}"
        if start_new_conversation is True and conversation_id is not None:
            raise GeneralAgentRuntimeError("开启新对话时不能复用已有会话标识。")
        if start_new_conversation is False and conversation_id is None:
            raise GeneralAgentRuntimeError("继续当前对话时缺少会话标识。")
        resolved_conversation_id = (
            conversation_id.strip()
            if conversation_id is not None
            else (
                f"general_conversation_{timestamp.strftime('%Y%m%d_%H%M%S')}_"
                f"{uuid4().hex[:6]}"
            )
        )
        if not resolved_conversation_id:
            raise GeneralAgentRuntimeError("对话标识不能为空。")
        created_at = timestamp.isoformat().replace("+00:00", "Z")
        conversation_lock = self._conversation_locks.setdefault(
            resolved_conversation_id,
            asyncio.Lock(),
        )
        async with conversation_lock:
            (
                messages,
                parent_run_id,
                request_index,
                current_request_message_id,
            ) = await self._messages_for_new_turn(
                resolved_conversation_id,
                run_id=run_id,
                goal=goal,
                created_at=created_at,
                existing_conversation=conversation_id is not None,
            )
            run = GeneralAgentRun(
                run_id=run_id,
                task_id=resolved_conversation_id,
                conversation_id=resolved_conversation_id,
                request_index=request_index,
                parent_run_id=parent_run_id,
                user_goal=goal,
                model_id=model_id,
                scope=scope or GeneralAgentScope(),
                author_constraints=author_constraints or [],
                external_access_allowed=external_access_allowed,
                limits=limits or GeneralAgentRunLimits(),
                messages=messages,
                current_request_message_id=current_request_message_id,
                lifecycle_events=[
                    GeneralAgentLifecycleEvent(
                        status=GeneralAgentRunStatus.INIT,
                        reason="任务已创建。",
                        created_at=created_at,
                    )
                ],
                created_at=created_at,
                updated_at=created_at,
                started_at=created_at,
            )
            if parent_run_id:
                previous = _run_from_graph_snapshot(
                    await self._graph.aget_state(
                        {"configurable": {"thread_id": resolved_conversation_id}}
                    )
                )
                if previous is not None and previous.run_id == parent_run_id:
                    run = run.model_copy(
                        update={"context_pipeline": previous.context_pipeline}
                    )
            # 用户输入先进入官方线程，避免后台调度前进程中断只留下业务投影。
            await self._graph.aupdate_state(
                {"configurable": {"thread_id": resolved_conversation_id}},
                {"run": run.model_dump(mode="json"), "manual_compaction": False},
                as_node=START,
            )
            run = await self._project_run_snapshot(run, "context_state_inherited")
            return run

    async def run(
        self,
        *,
        user_goal: str,
        model_id: str | None = None,
        conversation_id: str | None = None,
        start_new_conversation: bool | None = None,
        scope: GeneralAgentScope | None = None,
        author_constraints: list[str] | None = None,
        external_access_allowed: bool = False,
        limits: GeneralAgentRunLimits | None = None,
    ) -> GeneralAgentRun:
        run = await self.create_run(
            user_goal=user_goal,
            model_id=model_id,
            conversation_id=conversation_id,
            start_new_conversation=start_new_conversation,
            scope=scope,
            author_constraints=author_constraints,
            external_access_allowed=external_access_allowed,
            limits=limits,
        )
        return await self._execute_run(run.run_id)

    async def start(
        self,
        *,
        user_goal: str,
        model_id: str | None = None,
        conversation_id: str | None = None,
        start_new_conversation: bool | None = None,
        scope: GeneralAgentScope | None = None,
        author_constraints: list[str] | None = None,
        external_access_allowed: bool = False,
        limits: GeneralAgentRunLimits | None = None,
    ) -> GeneralAgentRun:
        run = await self.create_run(
            user_goal=user_goal,
            model_id=model_id,
            conversation_id=conversation_id,
            start_new_conversation=start_new_conversation,
            scope=scope,
            author_constraints=author_constraints,
            external_access_allowed=external_access_allowed,
            limits=limits,
        )
        self._start_background(run.run_id)
        return run

    async def resume(
        self,
        run_id: str,
        *,
        answer: str = "",
        approve: bool | None = None,
        second_confirmation: bool = False,
        effect_resolution: str | None = None,
    ) -> GeneralAgentRun:
        source_run = await self._require_run(run_id)
        if source_run.status is GeneralAgentRunStatus.CANCELLED:
            raise GeneralAgentRuntimeError("已结束的任务不能恢复。")
        conversation_lock = self._conversation_locks.setdefault(
            source_run.conversation_id,
            asyncio.Lock(),
        )
        async with conversation_lock:
            graph_state = await self._graph_state_for_run(source_run)
            supplied_human_input = (
                bool(answer.strip())
                or approve is not None
                or effect_resolution is not None
            )
            if not graph_state.interrupts and graph_state.next:
                if not supplied_human_input:
                    return await self._execute_run(
                        source_run.run_id,
                        resume_from_graph=True,
                    )
                # 兼容进程终止在业务投影与官方 interrupt 提交之间的极短窗口。
                # 先从官方 checkpoint 原地推进到 interrupt，再消费同一次回答。
                await self._execute_run(
                    source_run.run_id,
                    resume_from_graph=True,
                    recovery_prepared=True,
                )
                graph_state = await self._graph_state_for_run(source_run)

            request = self._human_request_from_graph_state(
                graph_state,
                expected_run=source_run,
            )
            graph_run = _run_from_graph_snapshot(graph_state)
            if graph_run is not None and _runtime_projection_conflicts(
                source_run,
                graph_run,
            ):
                await self._record_projection_conflict(source_run)
            if request.kind == "clarification":
                reply = answer
                if not reply.strip():
                    raise GeneralAgentRuntimeError("提交澄清回答时必须提供作者回答。")
                return await self._execute_run(
                    source_run.run_id,
                    human_resume={
                        "request_id": request.request_id,
                        "kind": request.kind,
                        "answer": reply,
                    },
                )

            if request.kind == "write_authorization":
                if approve is None:
                    raise GeneralAgentRuntimeError("提交写入决定时必须明确批准或拒绝。")
                if (
                    approve
                    and request.second_confirmation_required
                    and not second_confirmation
                ):
                    raise GeneralAgentRuntimeError("该高风险写入需要二次确认。")
                return await self._execute_run(
                    source_run.run_id,
                    human_resume={
                        "request_id": request.request_id,
                        "kind": request.kind,
                        "approve": approve,
                        "second_confirmation": second_confirmation,
                    },
                )

            if request.kind == "effect_reconciliation":
                if effect_resolution not in {
                    "recheck",
                    "confirm_not_applied",
                    "cancel",
                }:
                    raise GeneralAgentRuntimeError(
                        "副作用核对必须选择重新核对、确认未写入后重试或停止任务。"
                    )
                return await self._execute_run(
                    source_run.run_id,
                    human_resume={
                        "request_id": request.request_id,
                        "kind": request.kind,
                        "effect_resolution": effect_resolution,
                    },
                )

            raise GeneralAgentRuntimeError("当前 LangGraph interrupt 类型尚不能接续。")

    async def cancel(self, run_id: str) -> GeneralAgentRun:
        run = await self._require_run(run_id)
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
        if run.status in _TERMINAL_STATUSES:
            return run
        run = _transition(run, GeneralAgentRunStatus.CANCELLED, "作者取消任务。")
        run = run.model_copy(update={"finished_at": now_iso(), "resumable": False})
        return await self._project_run_snapshot(run, "run_cancelled")

    async def get(self, run_id: str) -> GeneralAgentRun:
        return await self._require_run(run_id)

    async def list(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        status: str = "all",
    ) -> tuple[builtins.list[GeneralAgentRun], int]:
        return await self._repository.list_runs(
            page=page,
            page_size=page_size,
            status=status,
        )

    async def list_conversations(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[builtins.list[GeneralAgentConversation], int]:
        runs = await self._all_runs()
        grouped: dict[str, builtins.list[GeneralAgentRun]] = {}
        for run in runs:
            grouped.setdefault(run.conversation_id, []).append(run)

        conversations: builtins.list[GeneralAgentConversation] = []
        for conversation_id, items in grouped.items():
            ordered = sorted(items, key=lambda item: (item.created_at, item.run_id))
            first = ordered[0]
            latest = ordered[-1]
            conversations.append(
                GeneralAgentConversation(
                    conversation_id=conversation_id,
                    title=first.conversation_title or first.user_goal,
                    status=latest.status,
                    request_count=len(ordered),
                    latest_run_id=latest.run_id,
                    created_at=first.created_at,
                    updated_at=max(item.updated_at for item in ordered),
                )
            )

        conversations.sort(
            key=lambda item: (item.updated_at, item.conversation_id),
            reverse=True,
        )
        start = (page - 1) * page_size
        return conversations[start : start + page_size], len(conversations)

    async def get_conversation(
        self, conversation_id: str
    ) -> builtins.list[GeneralAgentRun]:
        runs = [
            run
            for run in await self._all_runs()
            if run.conversation_id == conversation_id
        ]
        if not runs:
            raise GeneralAgentConversationNotFoundError(conversation_id)
        return sorted(runs, key=lambda item: (item.created_at, item.run_id))

    async def delete_conversation(self, conversation_id: str) -> int:
        runs = await self.get_conversation(conversation_id)
        if any(
            (task := self._tasks.get(run.run_id)) is not None and not task.done()
            for run in runs
        ):
            raise GeneralAgentRuntimeError("对话中仍有任务正在运行，请先停止当前任务。")
        deleted_count = 0
        for run in runs:
            if await self.delete(run.run_id):
                deleted_count += 1
        await self._memory_service.delete_conversation_memories(conversation_id)
        return deleted_count

    async def delete(self, run_id: str) -> bool:
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            raise GeneralAgentRuntimeError("运行中的任务不能删除，请先取消。")
        run = await self._repository.get(run_id)
        if run is None:
            return False
        conversation_runs = await self.get_conversation(run.conversation_id)
        is_last_conversation_run = len(conversation_runs) == 1
        owner = CapabilityResultOwner(
            conversation_id=run.conversation_id,
            run_id=run.run_id,
        )
        try:
            await self._capability_result_repository.delete_run(owner)
            remaining_results = await self._capability_result_repository.list_for_run(
                owner
            )
        except Exception as error:
            raise GeneralAgentRuntimeError(
                f"能力结果清理失败，父运行保持不变：{error}"
            ) from error
        if remaining_results:
            raise GeneralAgentRuntimeError(
                "能力结果清理后仍存在运行结果，父运行保持不变。"
            )
        if self._tool_budget_repository is not None:
            budget_owner = GeneralAgentToolBudgetOwner(
                conversation_id=run.conversation_id,
                run_id=run.run_id,
            )
            try:
                await self._tool_budget_repository.delete(budget_owner)
                remaining_budget = await self._tool_budget_repository.read(budget_owner)
            except Exception as error:
                raise GeneralAgentRuntimeError(
                    f"Tool 调用预算清理失败，父运行保持不变：{error}"
                ) from error
            if remaining_budget is not None:
                raise GeneralAgentRuntimeError(
                    "Tool 调用预算清理后仍存在运行记录，父运行保持不变。"
                )
        deleted = await self._repository.delete(run_id)
        if deleted:
            await self._effect_repository.delete_run(run_id)
        if deleted and is_last_conversation_run:
            await self._graph_checkpointer.adelete_thread(run.conversation_id)
        elif deleted:
            # 删除最新一轮时同步推进官方活动边界，避免恢复时重建已删除投影。
            remaining = [item for item in conversation_runs if item.run_id != run_id]
            latest = remaining[-1]
            if latest.request_index < run.request_index:
                lock = self._conversation_locks.setdefault(
                    run.conversation_id, asyncio.Lock()
                )
                async with lock:
                    config = {"configurable": {"thread_id": run.conversation_id}}
                    official = _run_from_graph_snapshot(
                        await self._graph.aget_state(config)
                    )
                    if official is not None and official.run_id == run_id:
                        await self._graph.aupdate_state(
                            config,
                            {
                                "run": latest.model_dump(mode="json"),
                                "manual_compaction": False,
                            },
                            as_node="finish_context",
                        )
        await self._context_snapshot_repository.delete_run(run_id)
        await self._llm_replay_repository.delete_run(run_id)
        await self._event_center.delete_snapshot(run_id)
        await self._memory_service.delete_run_memories(
            run.conversation_id,
            run.run_id,
        )
        return deleted

    async def list_context_snapshots(
        self, run_id: str
    ) -> builtins.list[GeneralAgentContextSnapshot]:
        run = await self._require_run(run_id)
        snapshots = await self._context_snapshot_repository.list_for_run(run_id)
        if not snapshots and run.context_snapshot is not None:
            return [run.context_snapshot]
        return snapshots

    async def list_llm_replays(self, run_id: str) -> builtins.list[LLMCallReplayRecord]:
        await self._require_run(run_id)
        return await self._llm_replay_repository.list_for_run(run_id)

    async def list_effects(self, run_id: str) -> builtins.list[EffectRecord]:
        await self._require_run(run_id)
        return await self._effect_repository.list_effects(run_id)

    async def recovery_snapshot(
        self,
        run_id: str,
    ) -> GeneralAgentRecoverySnapshot:
        run = await self._require_run(run_id)
        checkpoints: builtins.list[CheckpointHistorySummary] = []
        config: RunnableConfig = {
            "configurable": {"thread_id": _runtime_thread_id(run)}
        }
        async for item in self._graph_checkpointer.alist(config):
            if not _checkpoint_belongs_to_run(item.checkpoint, run_id):
                continue
            metadata = item.metadata
            configurable = item.config.get("configurable", {})
            checkpoint_id = configurable.get("checkpoint_id") or item.checkpoint.get(
                "id"
            )
            if not isinstance(checkpoint_id, str) or not checkpoint_id:
                continue
            raw_step = metadata.get("step", -1)
            checkpoints.append(
                CheckpointHistorySummary(
                    checkpoint_id=checkpoint_id,
                    source=str(metadata.get("source", "unknown")),
                    step=int(raw_step) if isinstance(raw_step, int) else -1,
                    created_at=(
                        str(item.checkpoint["ts"])
                        if item.checkpoint.get("ts") is not None
                        else None
                    ),
                )
            )
        latest_checkpoint = checkpoints[0] if checkpoints else None
        checkpoint = CheckpointPersistenceSummary(
            status=("available" if latest_checkpoint is not None else "missing"),
            checkpoint_count=len(checkpoints),
            latest_checkpoint_id=(
                latest_checkpoint.checkpoint_id if latest_checkpoint else None
            ),
            latest_step=(latest_checkpoint.step if latest_checkpoint else None),
        )
        events = await self.list_effects(run_id)
        latest_effects: dict[str, EffectRecord] = {}
        for event in events:
            latest_effects[event.effect_id] = event
        effects = [
            EffectSummary(
                effect_id=event.effect_id,
                node_id=event.node_id,
                tool_name=event.tool_name,
                status=event.status,
                resource_scopes=event.resource_scopes,
                authorization_bound=event.authorization_reference is not None,
                reason=event.reason,
                updated_at=event.created_at,
            )
            for event in latest_effects.values()
        ]
        effects.sort(key=lambda item: (item.updated_at, item.effect_id))
        return GeneralAgentRecoverySnapshot(
            run_id=run_id,
            checkpoint=checkpoint,
            checkpoints=checkpoints,
            effects=effects,
        )

    async def _messages_for_new_turn(
        self,
        conversation_id: str,
        *,
        run_id: str,
        goal: str,
        created_at: str,
        existing_conversation: bool,
    ) -> tuple[builtins.list[GeneralAgentMessage], str | None, int, str]:
        if not existing_conversation:
            request = _new_message(
                turn_id=run_id,
                request_index=1,
                role="user",
                content=goal,
                created_at=created_at,
                message_type=GeneralAgentMessageType.USER_REQUEST,
            )
            assert request.message_id is not None
            return (
                [request],
                None,
                1,
                request.message_id,
            )

        previous_runs = await self.get_conversation(conversation_id)
        latest = previous_runs[-1]
        if (
            latest.status in _ACTIVE_STATUSES
            or latest.status is GeneralAgentRunStatus.WAITING_HUMAN
        ):
            raise GeneralAgentRuntimeError(
                "当前对话仍有任务正在处理，请等待完成或先处理待确认内容。"
            )

        latest = _with_assistant_final_message(latest)
        messages = list(latest.messages)
        request_index = latest.request_index + 1
        request = _new_message(
            turn_id=run_id,
            request_index=request_index,
            role="user",
            content=goal,
            created_at=created_at,
            message_type=GeneralAgentMessageType.USER_REQUEST,
        )
        messages.append(request)
        assert request.message_id is not None
        return messages, latest.run_id, request_index, request.message_id

    async def _all_runs(self) -> builtins.list[GeneralAgentRun]:
        runs, _ = await self._repository.list_runs(
            page=1,
            page_size=100_000,
            status="all",
        )
        return runs

    async def _graph_state_for_run(
        self,
        run: GeneralAgentRun,
    ) -> StateSnapshot:
        state = await self._graph.aget_state(
            {
                "configurable": {
                    "thread_id": _runtime_thread_id(run),
                }
            }
        )
        graph_run = _run_from_graph_snapshot(state)
        if graph_run is None:
            raise GeneralAgentRuntimeError("官方 LangGraph 检查点中没有可接续状态。")
        if (
            graph_run.conversation_id != run.conversation_id
            or graph_run.run_id != run.run_id
        ):
            raise GeneralAgentRuntimeError(
                "该会话的官方 LangGraph 线程已经推进到其他请求。"
            )
        return state

    @staticmethod
    def _human_request_from_graph_state(
        state: StateSnapshot,
        *,
        expected_run: GeneralAgentRun,
    ) -> GeneralAgentHumanRequest:
        graph_run = _run_from_graph_snapshot(state)
        if graph_run is None or graph_run.run_id != expected_run.run_id:
            raise GeneralAgentRuntimeError("官方 LangGraph interrupt 不属于当前运行。")
        if len(state.interrupts) != 1:
            if not state.interrupts:
                raise GeneralAgentRuntimeError(
                    "当前官方 LangGraph 状态没有待处理的人工接续。"
                )
            raise GeneralAgentRuntimeError(
                "当前官方 LangGraph 状态包含多个并行人工接续，无法安全匹配。"
            )
        request = GeneralAgentHumanRequest.model_validate(state.interrupts[0].value)
        projected = graph_run.pending_human_request
        if projected is None or projected.request_id != request.request_id:
            raise GeneralAgentRuntimeError(
                "官方 LangGraph interrupt 与图状态中的人工请求不一致。"
            )
        return request

    async def recover_interrupted(self) -> int:
        projections = await self._all_runs()
        by_run_id = {run.run_id: run for run in projections}
        conversations = {
            run.conversation_id: run
            for run in sorted(
                projections,
                key=lambda item: (item.request_index, item.created_at),
            )
        }
        recovered = 0
        for indexed_run in conversations.values():
            config: RunnableConfig = {
                "configurable": {
                    "thread_id": indexed_run.conversation_id,
                }
            }
            graph_state = await self._graph.aget_state(config)
            run = _run_from_graph_snapshot(graph_state)
            if run is None:
                if indexed_run.status in _ACTIVE_STATUSES:
                    try:
                        await self._prepare_recovery(indexed_run)
                    except GeneralAgentRecoveryRequiresHumanError as error:
                        await self._park_recovery_interrupt(
                            indexed_run.run_id,
                            error,
                        )
                    except GeneralAgentRecoveryIntegrityError as error:
                        await self._stop_unrecoverable_recovery(
                            indexed_run.run_id,
                            error,
                        )
                continue
            stored = by_run_id.get(run.run_id)
            if stored is None:
                # 只要同一 conversation 尚有索引投影，就能从官方状态重建
                # 当前 run 的展示记录；执行内容仍以 graph state 为准。
                await self._project_graph_run(run, "graph_projection_rebuilt")
            elif stored.status is GeneralAgentRunStatus.CANCELLED:
                # 取消是显式业务控制结果，不充当 checkpoint，也不得自动重试。
                continue
            elif _runtime_projection_conflicts(stored, run):
                await self._record_projection_conflict(stored)
            if graph_state.interrupts:
                await self._project_graph_run(
                    run,
                    "langgraph_interrupt_projected",
                )
                continue
            if stored is not None and stored.status in {
                GeneralAgentRunStatus.FAILED,
                GeneralAgentRunStatus.TIMEOUT,
            }:
                # 显式失败/超时需要作者决定是否重试；若官方已有 interrupt，
                # 上面的分支仍优先把真实待处理请求投影出来。
                continue
            if not graph_state.next:
                await self._project_graph_run(
                    run,
                    "langgraph_terminal_projected",
                )
                await self._drain_manual_request(run.run_id)
                continue
            if graph_state.values.get("manual_compaction"):
                await self._drain_manual_request(run.run_id)
                continue
            try:
                await self._prepare_recovery(run)
            except GeneralAgentRecoveryRequiresHumanError as error:
                await self._park_recovery_interrupt(run.run_id, error)
                continue
            except GeneralAgentRecoveryIntegrityError as error:
                await self._stop_unrecoverable_recovery(run.run_id, error)
                continue
            self._start_background(
                run.run_id,
                resume_from_graph=True,
                recovery_prepared=True,
            )
            recovered += 1
        return recovered

    async def _project_graph_run(
        self,
        run: GeneralAgentRun,
        event_type: str,
    ) -> GeneralAgentRun:
        """把官方图状态单向保存为业务审计与界面投影。"""

        projected = _with_visible_conversation_messages(run)
        stored = await self._repository.get(run.run_id)
        if stored is not None:
            # 展示标题由业务投影维护，不随图检查点回放覆盖。
            projected = projected.model_copy(
                update={"conversation_title": stored.conversation_title}
            )
        projected = await self._merge_durable_recovery_audit(projected)
        await self._repository.save(projected)
        await self._event_center.publish(event_type=event_type, run=projected)
        if event_type == "langgraph_interrupt_projected":
            request = projected.pending_human_request
            if request is not None and request.kind == "write_authorization":
                self._emit_fault(
                    GeneralAgentFaultPoint.AUTHORIZATION_REQUEST_DURABLE,
                    projected,
                    durable_identity=request.request_id,
                )
        return projected

    async def _record_projection_conflict(
        self,
        projection: GeneralAgentRun,
    ) -> None:
        warning = (
            "业务运行投影与官方 LangGraph 状态不一致；执行与恢复已按官方状态继续。"
        )
        if warning in projection.context_resume_differences:
            return
        updated = projection.model_copy(
            update={
                "context_resume_differences": [
                    *projection.context_resume_differences,
                    warning,
                ]
            }
        )
        await self._repository.save(updated)
        await self._event_center.publish(
            event_type="graph_projection_conflict_detected",
            run=updated,
        )

    async def shutdown(self) -> None:
        self._shutting_down = True
        tasks = [task for task in self._tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _apply_write_authorization(
        self,
        run: GeneralAgentRun,
        request: GeneralAgentHumanRequest,
        *,
        approve: bool,
        second_confirmation: bool,
    ) -> GeneralAgentRun:
        if request.node_id is None or request.tool_name is None:
            raise GeneralAgentRuntimeError("写入授权请求缺少目标节点。")
        source_node = _find_current_node(run, request.node_id)
        decision = "授权继续上一轮的写入操作。" if approve else "拒绝上一轮的写入操作。"
        run = _append_human_interaction(
            run,
            request_id=request.request_id,
            prompt=request.prompt,
            response=decision,
        )
        if not approve:
            final_answer = "已按你的决定拒绝写入，本次没有修改正文。"
            rejected_node = source_node.model_copy(
                update={
                    "status": GeneralAgentNodeStatus.SKIPPED,
                    "finished_at": now_iso(),
                    "error_type": "AuthorRejectedWrite",
                    "error_message": "作者拒绝了持久化写入。",
                }
            )
            run = run.model_copy(
                update={
                    "node_runs": _replace_current_node(run, rejected_node),
                    "pending_human_request": None,
                    "final_answer": final_answer,
                    "finished_at": now_iso(),
                    "resumable": False,
                }
            )
            run = _transition(
                run,
                GeneralAgentRunStatus.COMPLETED,
                "作者拒绝写入，本轮执行已结束。",
            )
            return await self._project_run_snapshot(run, "write_rejected")

        input_payload = dict(request.input_summary or source_node.resolved_input)
        grant = await self._policy_service.issue_author_write(
            task_id=run.task_id,
            tool_name=request.tool_name,
            input_payload=input_payload,
            resource_scopes=tuple(request.resource_scopes),
            second_confirmation=second_confirmation,
        )
        node_run = source_node.model_copy(
            update={
                "status": GeneralAgentNodeStatus.PENDING,
                "finished_at": None,
                "error_type": None,
                "error_message": None,
                "resolved_input": input_payload,
                "authorization_grant_id": grant.grant_id,
                "authorization_approved": True,
                "authorization_second_confirmation": second_confirmation,
                "authorization_resource_scopes": request.resource_scopes,
            }
        )
        run = run.model_copy(
            update={
                "node_runs": _replace_current_node(run, node_run),
                "pending_human_request": None,
            }
        )
        run = _transition(
            run,
            GeneralAgentRunStatus.EXECUTING,
            "作者已授权上一轮的确定输入，开始本轮写入执行。",
        )
        return await self._project_run_snapshot(run, "write_authorization_recorded")

    async def _apply_effect_resolution(
        self,
        run: GeneralAgentRun,
        request: GeneralAgentHumanRequest,
        *,
        resolution: str,
    ) -> GeneralAgentRun:
        if (
            request.node_id is None
            or request.tool_name is None
            or request.effect_id is None
        ):
            raise GeneralAgentRuntimeError("副作用核对请求缺少写入身份。")
        latest_effect = await self._effect_repository.latest(request.effect_id)
        if (
            latest_effect is None
            or latest_effect.run_id != run.run_id
            or latest_effect.node_id != request.node_id
            or latest_effect.tool_name != request.tool_name
        ):
            raise GeneralAgentRuntimeError("副作用核对请求与真实写入记录不匹配。")

        response_text = {
            "recheck": "重新核对真实资源后态。",
            "confirm_not_applied": "已核对并确认原写入没有生效，允许按原输入重试。",
            "cancel": "无法确认写入结果，停止本次任务。",
        }[resolution]
        run = _append_human_interaction(
            run,
            request_id=request.request_id,
            prompt=request.prompt,
            response=response_text,
        )

        if resolution == "cancel":
            node = _find_current_node_or_none(run, request.node_id)
            node_runs = run.node_runs
            if node is not None:
                stopped_node = node.model_copy(
                    update={
                        "status": GeneralAgentNodeStatus.SKIPPED,
                        "finished_at": now_iso(),
                        "error_type": "AuthorStoppedUnknownEffect",
                        "error_message": "作者选择停止未知副作用任务，系统未自动重写。",
                    }
                )
                node_runs = _replace_current_node(run, stopped_node)
            run = run.model_copy(
                update={
                    "node_runs": node_runs,
                    "pending_human_request": None,
                    "final_answer": "写入结果仍无法安全确认，已停止任务且没有自动重试。",
                    "finished_at": now_iso(),
                    "resumable": False,
                }
            )
            run = _transition(
                run,
                GeneralAgentRunStatus.CANCELLED,
                "作者停止了副作用未知的任务。",
            )
            return await self._project_run_snapshot(
                run, "effect_reconciliation_cancelled"
            )

        node = _find_current_node_or_none(run, request.node_id)
        if node is None:
            raise GeneralAgentRuntimeError(
                f"副作用所属节点“{request.node_id}”已不在当前计划中，只能停止任务。"
            )

        if resolution == "confirm_not_applied":
            evidence = {
                **latest_effect.evidence,
                "author_resolution": "confirmed_not_applied",
                "author_resolution_request_id": request.request_id,
            }
            await self._effect_repository.append(
                latest_effect.model_copy(
                    update={
                        "event_id": f"effect_event_{uuid4().hex}",
                        "status": EffectStatus.FAILED,
                        "evidence": evidence,
                        "reason": (
                            "作者核对真实资源后确认原写入未生效，允许按冻结输入安全重试。"
                        ),
                        "created_at": now_iso(),
                    }
                )
            )

        pending_node = node.model_copy(
            update={
                "status": GeneralAgentNodeStatus.PENDING,
                "finished_at": None,
                "effect_status": (
                    EffectStatus.FAILED
                    if resolution == "confirm_not_applied"
                    else EffectStatus.REQUIRES_HUMAN
                ),
                "error_type": None,
                "error_message": None,
            }
        )
        run = run.model_copy(
            update={
                "node_runs": _replace_current_node(run, pending_node),
                "pending_human_request": None,
                "finished_at": None,
                "resumable": True,
            }
        )
        run = _transition(
            run,
            GeneralAgentRunStatus.EXECUTING,
            (
                "作者要求重新执行只读副作用对账。"
                if resolution == "recheck"
                else "作者确认原写入未生效，按冻结输入继续执行。"
            ),
        )
        return await self._project_run_snapshot(run, "effect_reconciliation_resumed")

    def _start_background(
        self,
        run_id: str,
        *,
        resume_from_graph: bool = False,
        recovery_prepared: bool = False,
    ) -> None:
        current = self._tasks.get(run_id)
        if current is not None and not current.done():
            raise GeneralAgentRuntimeError("该任务已经在运行。")
        task = asyncio.create_task(
            self._execute_run(
                run_id,
                resume_from_graph=resume_from_graph,
                recovery_prepared=recovery_prepared,
            )
        )
        self._tasks[run_id] = task
        task.add_done_callback(lambda completed: self._task_finished(run_id, completed))

    def _task_finished(
        self,
        run_id: str,
        task: asyncio.Task[GeneralAgentRun],
    ) -> None:
        if self._tasks.get(run_id) is task:
            self._tasks.pop(run_id, None)
        if not task.cancelled():
            task.exception()
            if self._context_pipeline is not None:
                asyncio.create_task(self._drain_manual_request(run_id))

    async def _execute_run(
        self,
        run_id: str,
        *,
        resume_from_graph: bool = False,
        recovery_prepared: bool = False,
        human_resume: dict[str, Any] | None = None,
    ) -> GeneralAgentRun:
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            projection = await self._require_run(run_id)
            config: RunnableConfig = {
                "recursion_limit": max(
                    50,
                    len(projection.plan.nodes) * 3 + 20
                    if projection.plan is not None
                    else 50,
                ),
                "max_concurrency": projection.limits.max_concurrency,
                "configurable": {"thread_id": _runtime_thread_id(projection)},
            }
            run = projection
            if resume_from_graph or human_resume is not None:
                graph_state = await self._graph.aget_state(config)
                official_run = _run_from_graph_snapshot(graph_state)
                if official_run is None:
                    raise GeneralAgentRuntimeError(
                        "官方 LangGraph 检查点中没有可恢复状态。"
                    )
                if official_run.run_id != run_id:
                    raise GeneralAgentRuntimeError(
                        "该会话的官方 LangGraph 线程已经推进到其他请求。"
                    )
                run = official_run
            try:
                if human_resume is not None:
                    await self._recovery_coordinator.validate_owner(run)
                elif resume_from_graph and not recovery_prepared:
                    await self._prepare_recovery(run)
                else:
                    await self._recovery_coordinator.validate_owner(run)
                async with asyncio.timeout(run.limits.max_runtime_seconds):
                    graph_input: _RuntimeGraphState | Command[Any] | None
                    graph_input = (
                        Command(resume=human_resume)
                        if human_resume is not None
                        else {
                            "run": run.model_dump(mode="json"),
                            "manual_compaction": False,
                        }
                    )
                    result: dict[str, Any] | None = None
                    if resume_from_graph and human_resume is None:
                        graph_state = await self._graph.aget_state(config)
                        if graph_state.next:
                            graph_input = None
                        elif graph_state.values:
                            result = dict(graph_state.values)
                    if result is None:
                        interrupted = False
                        async for part in self._graph.astream(
                            graph_input,
                            config=config,
                            stream_mode="values",
                            version="v2",
                        ):
                            if not isinstance(part, dict):
                                continue
                            data = part.get("data")
                            if isinstance(data, dict):
                                result = dict(data)
                                projected = data.get("run")
                                if isinstance(projected, dict):
                                    await self._event_center.publish(
                                        event_type="langgraph_state",
                                        run=GeneralAgentRun.model_validate(projected),
                                    )
                            if part.get("interrupts"):
                                interrupted = True
                        if result is None:
                            raise GeneralAgentRuntimeError(
                                "LangGraph 流没有返回运行状态。"
                            )
                        if interrupted:
                            graph_state = await self._graph.aget_state(config)
                            interrupted_run = _run_from_graph_snapshot(graph_state)
                            if interrupted_run is None:
                                raise GeneralAgentRuntimeError(
                                    "LangGraph interrupt 缺少可投影运行状态。"
                                )
                            return await self._project_graph_run(
                                interrupted_run,
                                "langgraph_interrupt_projected",
                            )
                if result.get("__interrupt__"):
                    graph_state = await self._graph.aget_state(config)
                    interrupted_run = _run_from_graph_snapshot(graph_state)
                    if interrupted_run is None:
                        raise GeneralAgentRuntimeError(
                            "LangGraph interrupt 缺少可投影运行状态。"
                        )
                    return await self._project_graph_run(
                        interrupted_run,
                        "langgraph_interrupt_projected",
                    )
                completed = GeneralAgentRun.model_validate(result["run"])
                audited = await self._finalize_effect_recovery_decisions(completed)
                return await self._project_graph_run(
                    audited,
                    "langgraph_run_projected",
                )
            except asyncio.CancelledError:
                if self._shutting_down:
                    raise
                latest = await self._require_run(run_id)
                if latest.status is not GeneralAgentRunStatus.CANCELLED:
                    latest = _transition(
                        latest,
                        GeneralAgentRunStatus.CANCELLED,
                        "运行任务被取消。",
                    ).model_copy(update={"finished_at": now_iso(), "resumable": False})
                    await self._project_run_snapshot(latest, "run_cancelled")
                raise
            except TimeoutError:
                latest = await self._require_run(run_id)
                latest = _transition(
                    latest,
                    GeneralAgentRunStatus.TIMEOUT,
                    "任务超过运行时限。",
                ).model_copy(
                    update={
                        "final_answer": "",
                        "final_answer_basis_sha256": None,
                        "finished_at": now_iso(),
                        "resumable": True,
                    }
                )
                return await self._project_run_snapshot(latest, "run_timed_out")
            except InjectedProcessTermination:
                raise
            except GeneralAgentRecoveryRequiresHumanError as error:
                return await self._park_recovery_interrupt(run_id, error)
            except GeneralAgentRecoveryIntegrityError as error:
                return await self._stop_unrecoverable_recovery(run_id, error)
            except (ContextAssemblyError, ContextCapacityError) as error:
                latest = await self._require_run(run_id)
                failure_evidence = json.dumps(
                    {
                        "current_request_sha256": error.current_request_sha256,
                        "input_tokens": error.input_tokens,
                        "reason_code": error.reason_code,
                        "stable_memory_sha256": error.stable_memory_sha256,
                        "context_window_tokens": error.context_window_tokens,
                        "output_tokens": error.output_tokens,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                latest = latest.model_copy(
                    update={
                        "errors": [*latest.errors, failure_evidence],
                        "final_answer": str(error),
                        "final_answer_basis_sha256": None,
                        "pending_human_request": None,
                        "finished_at": now_iso(),
                        "resumable": False,
                    }
                )
                latest = _transition(
                    latest,
                    GeneralAgentRunStatus.FAILED,
                    "上下文无法安全组装，运行已在规划前停止。",
                )
                return await self._project_run_snapshot(
                    latest,
                    "unsafe_context_rejected",
                )
            except Exception as error:  # noqa: BLE001
                latest = await self._require_run(run_id)
                latest = latest.model_copy(
                    update={"errors": [*latest.errors, str(error)[:2_000]]}
                )
                latest = _transition(
                    latest,
                    GeneralAgentRunStatus.FAILED,
                    f"运行失败：{type(error).__name__}",
                ).model_copy(update={"finished_at": now_iso(), "resumable": True})
                return await self._project_run_snapshot(latest, "run_failed")

    def _build_graph(self) -> CompiledStateGraph:
        graph = StateGraph(_RuntimeGraphState)
        graph.add_node("initialize", self._initialize_node)
        graph.add_node("finish_context", self._finish_context_node)
        graph.add_node("manual_compact", self._manual_compact_node)
        graph.add_edge("finish_context", END)
        graph.add_edge("manual_compact", END)
        graph.add_node("plan", self._plan_node)
        graph.add_node("start_dag", self._start_dag_node)
        graph.add_node(
            "execute_dag",
            self._executor.build_graph(checkpoint=self._project_run_snapshot),
        )
        graph.add_node("finalize_dag", self._finalize_dag_node)
        graph.add_node("verify", self._verify_node)
        graph.add_node("human_input", self._human_input_node)
        graph.add_edge(START, "initialize")
        graph.add_conditional_edges(
            "initialize",
            self._route_after_initialize,
            {
                "manual": "manual_compact",
                "plan": "plan",
                "execute": "start_dag",
                "verify": "verify",
                "human": "human_input",
                "end": "finish_context",
            },
        )
        graph.add_conditional_edges(
            "plan",
            self._route_after_plan,
            {"execute": "start_dag", "human": "human_input", "end": "finish_context"},
        )
        graph.add_edge("start_dag", "execute_dag")
        graph.add_conditional_edges(
            "execute_dag",
            self._route_after_execute,
            {
                "finalize": "finalize_dag",
                "human": "human_input",
                "end": "finish_context",
            },
        )
        graph.add_edge("finalize_dag", "verify")
        graph.add_conditional_edges(
            "human_input",
            self._route_after_human_input,
            {"plan": "plan", "execute": "start_dag", "end": "finish_context"},
        )
        graph.add_conditional_edges(
            "verify",
            self._route_after_verify,
            {"plan": "plan", "end": "finish_context"},
        )
        return graph.compile(
            checkpointer=self._graph_checkpointer,
            store=self._graph_store,
        )

    async def _after_node_batch(self, run: GeneralAgentRun) -> GeneralAgentRun:
        # 刚完成的工具批次是活动；不能把长工具执行时间误判成会话空闲。
        run = run.model_copy(
            update={
                "context_pipeline": run.context_pipeline.model_copy(
                    update={"last_activity_at": now_iso()}
                )
            }
        )
        return await self._context_boundary(run)

    async def _drain_manual_request(self, run_id: str) -> None:
        if self._context_pipeline is None or self._shutting_down:
            return
        run = await self._repository.get(run_id)
        if (
            run is None
            or run.status in _ACTIVE_STATUSES
            or run.status is GeneralAgentRunStatus.WAITING_HUMAN
        ):
            return
        snapshot = await self._graph.aget_state(
            {"configurable": {"thread_id": run.conversation_id}}
        )
        if snapshot.next and not (
            snapshot.values.get("manual_compaction")
            and set(snapshot.next) <= {"manual_compact"}
        ):
            return  # 业务恢复点必须先继续，不能反复调度无法执行的压缩操作。
        if await self._context_pipeline.pending_manual(
            run.conversation_id, run.context_pipeline
        ):
            await self.request_context_compaction(run.conversation_id)

    async def _context_boundary(
        self, run: GeneralAgentRun, *, extract: bool = True
    ) -> GeneralAgentRun:
        if self._context_pipeline is None:
            return run
        run = await self._context_pipeline.prepare(run, extract=extract)
        if await self._context_pipeline.pending_manual(
            run.conversation_id, run.context_pipeline
        ):
            assembly = await self._context_assembler.assemble(run, phase="plan")
            run, _ = await self._call_orchestrator(run, assembly, "compact_context")
        return run

    async def _call_orchestrator(
        self,
        run: GeneralAgentRun,
        assembly: ContextAssemblyResult,
        method: str,
        **kwargs: Any,
    ) -> tuple[GeneralAgentRun, Any]:
        callback = getattr(self._orchestrator, method)
        if self._context_pipeline is None:
            return run, await callback(
                run, context=assembly.snapshot.envelope, **kwargs
            )
        with self._context_pipeline.scope(run, assembly.snapshot.envelope) as scope:

            async def save_request_snapshot(active_scope):
                snapshot = create_snapshot(
                    active_scope.apply(run),
                    active_scope.envelope,
                    assembly.snapshot.memory_refs,
                )
                await self._record_context_snapshot(snapshot)
                return snapshot

            scope.snapshot_callback = save_request_snapshot
            try:
                result = await callback(run, context=scope.envelope, **kwargs)
            except ContextCapacityError:
                scope.envelope = scope.envelope.model_copy(
                    update={"pipeline_events": scope.state.events}
                )
                failed_snapshot = await save_request_snapshot(scope)
                failed_run = _with_context_snapshot(
                    scope.apply(run), ContextAssemblyResult(snapshot=failed_snapshot)
                )
                await self._project_run_snapshot(failed_run, "context_capacity_paused")
                raise
            run = scope.apply(run)
            snapshot = scope.snapshot or await save_request_snapshot(scope)
            run = _with_context_snapshot(run, ContextAssemblyResult(snapshot=snapshot))
        return run, result

    async def _finish_context_node(
        self, state: _RuntimeGraphState
    ) -> _RuntimeGraphState:
        run = _with_visible_conversation_messages(
            GeneralAgentRun.model_validate(state["run"])
        )
        run = await self._context_boundary(run)
        run = await self._project_run_snapshot(run, "context_turn_completed")
        return {"run": run.model_dump(mode="json"), "manual_compaction": False}

    async def _manual_compact_node(
        self, state: _RuntimeGraphState
    ) -> _RuntimeGraphState:
        run = await self._context_boundary(GeneralAgentRun.model_validate(state["run"]))
        run = await self._project_run_snapshot(run, "manual_context_compacted")
        return {"run": run.model_dump(mode="json"), "manual_compaction": False}

    async def request_context_compaction(self, conversation_id: str) -> Any:
        if self._context_pipeline is None:
            raise GeneralAgentRuntimeError("上下文流水线尚未配置。")
        lock = self._conversation_locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            latest = (await self.get_conversation(conversation_id))[-1]
            request = await self._context_pipeline.request_manual(
                conversation_id, latest.context_pipeline
            )
            if (
                latest.run_id not in self._tasks
                and latest.status not in _ACTIVE_STATUSES
                and latest.status is not GeneralAgentRunStatus.WAITING_HUMAN
            ):
                task = asyncio.create_task(
                    self._execute_manual_compaction(latest.run_id)
                )
                self._tasks[latest.run_id] = task
                task.add_done_callback(
                    lambda completed: self._task_finished(latest.run_id, completed)
                )
            return request

    async def context_compaction_status(self, conversation_id: str) -> Any:
        if self._context_pipeline is None:
            return None
        latest = (await self.get_conversation(conversation_id))[-1]
        official = _run_from_graph_snapshot(
            await self._graph.aget_state(
                {"configurable": {"thread_id": conversation_id}}
            )
        )
        state = official.context_pipeline if official else latest.context_pipeline
        return await self._context_pipeline.manual_status(conversation_id, state)

    async def _execute_manual_compaction(self, run_id: str) -> GeneralAgentRun:
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            run = await self._require_run(run_id)
            config: RunnableConfig = {
                "configurable": {"thread_id": run.conversation_id}
            }
            snapshot = await self._graph.aget_state(config)
            official = _run_from_graph_snapshot(snapshot)
            if official is not None:
                run = official
            resuming_manual = snapshot.values.get("manual_compaction") and set(
                snapshot.next
            ) <= {"manual_compact"}
            if snapshot.next and not resuming_manual:
                return run  # 等待现有安全边界，不能跳过中断或未完成步骤。
            try:
                result = await self._graph.ainvoke(
                    None
                    if resuming_manual
                    else {
                        "run": run.model_dump(mode="json"),
                        "manual_compaction": True,
                    },
                    config=config,
                )
                return GeneralAgentRun.model_validate(result["run"])
            except Exception:
                # 摘要失败状态由流水线记录；原会话和图检查点仍可继续。
                return run

    async def _initialize_node(self, state: _RuntimeGraphState) -> _RuntimeGraphState:
        run = GeneralAgentRun.model_validate(state["run"])
        if run.status in {
            GeneralAgentRunStatus.INIT,
            GeneralAgentRunStatus.CLARIFYING,
            GeneralAgentRunStatus.PLANNING,
            GeneralAgentRunStatus.REPLANNING,
        }:
            run = _transition(run, GeneralAgentRunStatus.PLANNING, "开始高层规划。")
        run = await self._project_run_snapshot(run, "runtime_initialized")
        return {**state, "run": run.model_dump(mode="json")}

    def _route_after_initialize(self, state: _RuntimeGraphState) -> str:
        if state.get("manual_compaction"):
            return "manual"
        status = GeneralAgentRun.model_validate(state["run"]).status
        if status is GeneralAgentRunStatus.PLANNING:
            return "plan"
        if status is GeneralAgentRunStatus.EXECUTING:
            return "execute"
        if status is GeneralAgentRunStatus.VERIFYING:
            return "verify"
        if status is GeneralAgentRunStatus.WAITING_HUMAN:
            return "human"
        return "end"

    async def _plan_node(self, state: _RuntimeGraphState) -> _RuntimeGraphState:
        run = GeneralAgentRun.model_validate(state["run"])
        run = await self._context_boundary(run, extract=False)
        phase: ContextPhase = "replan" if state.get("replan_guidance", "") else "plan"
        assembly = await self._context_assembler.assemble(
            run,
            phase=phase,
            replan_guidance=state.get("replan_guidance", ""),
        )
        if self._context_pipeline is None:
            run = _with_context_snapshot(run, assembly)
            await self._record_context_snapshot(assembly.snapshot)
        run = await self._project_run_snapshot(run, "context_assembled")
        run, plan = await self._call_orchestrator(
            run, assembly, "plan", replan_guidance=state.get("replan_guidance", "")
        )
        if plan.requires_clarification:
            request = GeneralAgentHumanRequest(
                request_id=f"human_{uuid4().hex}",
                kind="clarification",
                prompt=plan.clarification_question,
                created_at=now_iso(),
            )
            run = run.model_copy(
                update={
                    "plan": plan,
                    "pending_human_request": request,
                }
            )
            unresolved_id = await self._memory_service.record_clarification_request(
                run,
                request_id=request.request_id,
                plan=plan,
            )
            run = _with_memory_refs(run, [unresolved_id])
            run = _transition(
                run,
                GeneralAgentRunStatus.WAITING_HUMAN,
                "高层编排 Agent 需要作者补充信息。",
            )
            run = await self._project_run_snapshot(run, "waiting_clarification")
            return {"run": run.model_dump(mode="json"), "replan_guidance": ""}
        run = run.model_copy(
            update={
                "plan": plan,
                "plan_revision": run.plan_revision + 1,
                "pending_human_request": None,
                "verification_issues": [],
                "final_answer": "",
                "final_answer_basis_sha256": None,
            }
        )
        if not plan.nodes and plan.direct_response.strip():
            final_answer = plan.direct_response.strip()
            run = run.model_copy(
                update={
                    "verification_issues": [],
                    "final_answer": final_answer,
                    "final_answer_basis_sha256": result_basis_sha256(run),
                    "finished_at": now_iso(),
                    "resumable": False,
                }
            )
            run = _transition(
                run,
                GeneralAgentRunStatus.COMPLETED,
                "编排 Agent 已生成可直接交付的回答。",
            )
            run = await self._project_run_snapshot(run, "direct_response_completed")
            return {"run": run.model_dump(mode="json"), "replan_guidance": ""}
        run = _transition(run, GeneralAgentRunStatus.EXECUTING, "动态执行计划已生成。")
        run = await self._project_run_snapshot(run, "plan_created")
        return {"run": run.model_dump(mode="json"), "replan_guidance": ""}

    def _route_after_plan(self, state: _RuntimeGraphState) -> str:
        status = GeneralAgentRun.model_validate(state["run"]).status
        if status is GeneralAgentRunStatus.EXECUTING:
            return "execute"
        if status is GeneralAgentRunStatus.WAITING_HUMAN:
            return "human"
        return "end"

    async def _start_dag_node(self, state: _RuntimeGraphState) -> _RuntimeGraphState:
        run = GeneralAgentRun.model_validate(state["run"])
        # 进入节点时，上一个 LangGraph super-step（规划结果）已经由官方
        # checkpointer 提交；故障注入必须绑定这个可恢复边界，不能依赖异步
        # stream 消费者事后猜测当前 ``StateSnapshot.next``。
        self._emit_fault(GeneralAgentFaultPoint.PLAN_CREATED, run)
        run = _transition(run, GeneralAgentRunStatus.EXECUTING, "执行动态能力图。")
        run = await self._project_run_snapshot(run, "dag_execution_started")
        return {**state, "run": run.model_dump(mode="json")}

    async def _finalize_dag_node(
        self,
        state: _RuntimeGraphState,
    ) -> _RuntimeGraphState:
        run = GeneralAgentRun.model_validate(state["run"])
        run = await self._finalize_effect_recovery_decisions(run)
        memory_ids = await self._memory_service.record_node_results(
            run,
            [
                node
                for node in run.node_runs
                if node.plan_revision == run.plan_revision
                and node.status is GeneralAgentNodeStatus.SUCCESS
            ],
        )
        if memory_ids:
            run = _with_memory_refs(run, memory_ids)
            run = await self._project_run_snapshot(run, "node_memories_recorded")
        run = await self._project_run_snapshot(run, "dag_execution_finished")
        return {**state, "run": run.model_dump(mode="json")}

    def _route_after_execute(self, state: _RuntimeGraphState) -> str:
        status = GeneralAgentRun.model_validate(state["run"]).status
        if status is GeneralAgentRunStatus.VERIFYING:
            return "finalize"
        if status is GeneralAgentRunStatus.WAITING_HUMAN:
            return "human"
        return "end"

    async def _human_input_node(
        self,
        state: _RuntimeGraphState,
    ) -> _RuntimeGraphState:
        run = GeneralAgentRun.model_validate(state["run"])
        request = run.pending_human_request
        if request is None:
            raise GeneralAgentRuntimeError("框架进入人工节点时没有待处理请求。")
        response = interrupt(request.model_dump(mode="json"))
        if not isinstance(response, dict):
            raise GeneralAgentRuntimeError("人工接续值必须是 JSON 对象。")
        if response.get("request_id") != request.request_id:
            raise GeneralAgentRuntimeError("人工接续值与当前 interrupt 不匹配。")
        if response.get("kind") != request.kind:
            raise GeneralAgentRuntimeError("人工接续类型与当前 interrupt 不匹配。")

        if request.kind == "clarification":
            reply = str(response.get("answer", ""))
            if not reply.strip():
                raise GeneralAgentRuntimeError("澄清回答不能为空。")
            run = _append_human_interaction(
                run,
                request_id=request.request_id,
                prompt=request.prompt,
                response=reply,
            )
            memory_id = await self._memory_service.resolve_clarification(
                run,
                request_id=request.request_id,
                content=reply,
            )
            run = _with_memory_refs(run, [memory_id]).model_copy(
                update={"pending_human_request": None}
            )
            run = _transition(
                run,
                GeneralAgentRunStatus.PLANNING,
                "已通过 LangGraph interrupt 收到作者澄清，继续同一任务。",
            )
            run = await self._project_run_snapshot(run, "clarification_resumed")
            return {**state, "run": run.model_dump(mode="json")}

        if request.kind == "write_authorization":
            approved = response.get("approve")
            if not isinstance(approved, bool):
                raise GeneralAgentRuntimeError("写入授权必须明确批准或拒绝。")
            run = await self._apply_write_authorization(
                run,
                request,
                approve=approved,
                second_confirmation=bool(response.get("second_confirmation", False)),
            )
            return {**state, "run": run.model_dump(mode="json")}

        if request.kind == "effect_reconciliation":
            resolution = response.get("effect_resolution")
            if resolution not in {
                "recheck",
                "confirm_not_applied",
                "cancel",
            }:
                raise GeneralAgentRuntimeError("副作用人工核对决定不受支持。")
            run = await self._apply_effect_resolution(
                run,
                request,
                resolution=str(resolution),
            )
            return {**state, "run": run.model_dump(mode="json")}

        raise GeneralAgentRuntimeError("当前人工核对类型尚不能自动接续。")

    def _route_after_human_input(self, state: _RuntimeGraphState) -> str:
        status = GeneralAgentRun.model_validate(state["run"]).status
        if status is GeneralAgentRunStatus.PLANNING:
            return "plan"
        if status is GeneralAgentRunStatus.EXECUTING:
            return "execute"
        return "end"

    async def _verify_node(self, state: _RuntimeGraphState) -> _RuntimeGraphState:
        run = GeneralAgentRun.model_validate(state["run"])
        # ``verify`` 被调度即证明 DAG 结果所在 super-step 已持久化；在任何
        # 校验副作用发生前注入，恢复时可从同一 conversation thread 原地继续。
        self._emit_fault(GeneralAgentFaultPoint.VERIFICATION_STARTED, run)
        run = _with_visible_conversation_messages(run)
        run = await self._merge_durable_recovery_audit(run)
        run = run.model_copy(
            update={"verification_attempt_count": (run.verification_attempt_count + 1)}
        )
        run = _transition(run, GeneralAgentRunStatus.VERIFYING, "校验执行结果。")
        run = await self._project_run_snapshot(run, "verification_started")
        blocking_failures = _blocking_failed_nodes(run)
        execution_issues = _execution_failure_issues(blocking_failures)
        source_quality_issues = _chapter_source_quality_issues(run)
        recovery_issues = [*execution_issues, *source_quality_issues]
        if recovery_issues and run.replan_count < run.limits.max_replans:
            run = run.model_copy(
                update={
                    "replan_count": run.replan_count + 1,
                    "verification_issues": recovery_issues,
                    "final_answer": "",
                    "final_answer_basis_sha256": None,
                }
            )
            run = _transition(
                run,
                GeneralAgentRunStatus.REPLANNING,
                "执行结果缺少必要来源或步骤失败，进入有限自动修复。",
            )
            run = await self._project_run_snapshot(run, "execution_recovery_requested")
            return {
                "run": run.model_dump(mode="json"),
                "replan_guidance": _execution_replan_guidance(recovery_issues),
            }
        run = await self._context_boundary(run)
        assembly = await self._context_assembler.assemble(run, phase="verify")
        if self._context_pipeline is None:
            run = _with_context_snapshot(run, assembly)
            await self._record_context_snapshot(assembly.snapshot)
        run = await self._project_run_snapshot(run, "verification_context_assembled")
        run, verification = await self._call_orchestrator(run, assembly, "verify")
        if verification.should_replan and run.replan_count < run.limits.max_replans:
            rejected_memory_id = (
                await self._memory_service.record_rejected_verification(
                    run,
                    verification,
                )
            )
            run = _with_memory_refs(run, [rejected_memory_id])
            run = run.model_copy(
                update={
                    "replan_count": run.replan_count + 1,
                    "verification_issues": verification.issues,
                    "final_answer": "",
                    "final_answer_basis_sha256": None,
                }
            )
            run = _transition(
                run,
                GeneralAgentRunStatus.REPLANNING,
                "结果未通过校验，进入有限重规划。",
            )
            run = await self._project_run_snapshot(run, "replanning_requested")
            return {
                "run": run.model_dump(mode="json"),
                "replan_guidance": verification.replan_guidance,
            }
        basis_sha256 = result_basis_sha256(run)
        memory_ids = await self._memory_service.record_verification(
            run,
            verification,
            basis_sha256=basis_sha256,
        )
        run = _with_memory_refs(run, memory_ids)
        unresolved_issues = list(
            dict.fromkeys([*source_quality_issues, *verification.issues])
        )
        final_status = (
            GeneralAgentRunStatus.FAILED
            if blocking_failures
            or source_quality_issues
            or verification.outcome == "failed"
            else GeneralAgentRunStatus.COMPLETED
        )
        run = run.model_copy(
            update={
                "verification_issues": unresolved_issues,
                "final_answer": verification.final_answer,
                "final_answer_basis_sha256": basis_sha256,
                "finished_at": now_iso(),
                "resumable": final_status is GeneralAgentRunStatus.FAILED,
            }
        )
        run = _transition(run, final_status, "任务结果已收敛。")
        run = await self._project_run_snapshot(run, "verification_finished")
        return {"run": run.model_dump(mode="json"), "replan_guidance": ""}

    def _route_after_verify(self, state: _RuntimeGraphState) -> str:
        status = GeneralAgentRun.model_validate(state["run"]).status
        return "plan" if status is GeneralAgentRunStatus.REPLANNING else "end"

    async def _project_run_snapshot(
        self, run: GeneralAgentRun, event_type: str
    ) -> GeneralAgentRun:
        """保存可重建的业务投影；不提供图恢复、resume 或线程身份。"""

        run = await self._merge_durable_recovery_audit(run)
        if event_type == "waiting_human_after_capability_checkpoint":
            run = await self._finalize_effect_recovery_decisions(run)
        updated = run.model_copy(
            update={
                "checkpoint_revision": run.checkpoint_revision + 1,
                "updated_at": now_iso(),
            }
        )
        if updated.status is GeneralAgentRunStatus.WAITING_HUMAN:
            # WAITING_HUMAN 只有在官方 interrupt checkpoint 已提交后才可见；
            # 此处仅把候选状态返回给图节点，外层由 _project_graph_run 单向投影。
            return updated
        await self._repository.save(updated)
        await self._event_center.publish(event_type=event_type, run=updated)
        return updated

    async def _prepare_recovery(self, run: GeneralAgentRun) -> None:
        self._emit_fault(
            GeneralAgentFaultPoint.CHECKPOINT_REVISION_VALIDATION,
            run,
        )
        preparation = await self._recovery_coordinator.prepare(run)
        await self._persist_recovery_decision(run, preparation.decision)

    async def _stop_recovery_for_human(
        self,
        run_id: str,
        error: GeneralAgentRecoveryRequiresHumanError,
    ) -> GeneralAgentRun:
        latest = await self._require_run(run_id)
        if error.decision is not None:
            latest = await self._persist_recovery_decision(
                latest,
                error.decision,
            )
        decision = error.decision
        effect_id = decision.effect_id if decision is not None else None
        if effect_id is None and decision is not None:
            effect_ids = decision.evidence.get("effect_ids")
            if isinstance(effect_ids, list) and effect_ids:
                effect_id = str(effect_ids[0])
        latest_effect = (
            await self._effect_repository.latest(effect_id)
            if effect_id is not None
            else None
        )
        latest = latest.model_copy(
            update={
                "pending_human_request": GeneralAgentHumanRequest(
                    request_id=f"human_{uuid4().hex}",
                    kind="effect_reconciliation",
                    prompt=str(error),
                    node_id=(latest_effect.node_id if latest_effect else None),
                    tool_name=(latest_effect.tool_name if latest_effect else None),
                    effect_id=(latest_effect.effect_id if latest_effect else None),
                    input_sha256=(
                        latest_effect.input_sha256 if latest_effect else None
                    ),
                    resource_scopes=(
                        latest_effect.resource_scopes if latest_effect else []
                    ),
                    created_at=now_iso(),
                ),
                "resumable": True,
            }
        )
        latest = _transition(
            latest,
            GeneralAgentRunStatus.WAITING_HUMAN,
            "写入副作用需要作者核对，恢复已安全停止。",
        )
        return await self._project_run_snapshot(
            latest,
            "recovery_requires_human",
        )

    async def _park_recovery_interrupt(
        self,
        run_id: str,
        error: GeneralAgentRecoveryRequiresHumanError,
    ) -> GeneralAgentRun:
        waiting = await self._stop_recovery_for_human(run_id, error)
        config: RunnableConfig = {
            "recursion_limit": 20,
            "configurable": {"thread_id": _runtime_thread_id(waiting)},
        }
        interrupted = False
        async for part in self._graph.astream(
            {"run": waiting.model_dump(mode="json")},
            config=config,
            stream_mode="values",
            version="v2",
        ):
            if isinstance(part, dict) and part.get("interrupts"):
                interrupted = True
        if not interrupted:
            raise GeneralAgentRuntimeError("副作用核对没有进入 LangGraph interrupt。")
        graph_state = await self._graph.aget_state(config)
        interrupted_run = _run_from_graph_snapshot(graph_state)
        if interrupted_run is None:
            raise GeneralAgentRuntimeError("副作用核对 interrupt 缺少可投影运行状态。")
        return await self._project_graph_run(
            interrupted_run,
            "langgraph_interrupt_projected",
        )

    async def _stop_unrecoverable_recovery(
        self,
        run_id: str,
        error: GeneralAgentRecoveryIntegrityError,
    ) -> GeneralAgentRun:
        latest = await self._require_run(run_id)
        if error.decision is not None:
            latest = await self._persist_recovery_decision(
                latest,
                error.decision,
            )
        latest = latest.model_copy(
            update={
                "errors": [*latest.errors, str(error)[:2_000]],
                "final_answer": "",
                "final_answer_basis_sha256": None,
                "pending_human_request": None,
                "finished_at": now_iso(),
                "resumable": False,
            }
        )
        latest = _transition(
            latest,
            GeneralAgentRunStatus.FAILED,
            "检查点不存在可验证的有效修订，恢复已安全停止。",
        )
        return await self._project_run_snapshot(
            latest,
            "recovery_unrecoverable",
        )

    async def _persist_recovery_decision(
        self,
        run: GeneralAgentRun,
        decision: RecoveryDecision,
    ) -> GeneralAgentRun:
        latest = await self._require_run(run.run_id)
        if decision.run_id != latest.run_id:
            raise GeneralAgentRuntimeError("恢复决定不能跨运行持久化。")
        if any(
            item.decision_id == decision.decision_id
            for item in latest.recovery_decisions
        ):
            return latest
        expected_ordinal = len(latest.recovery_decisions) + 1
        if decision.ordinal != expected_ordinal:
            decision = decision.model_copy(update={"ordinal": expected_ordinal})
        updated = latest.model_copy(
            update={
                "recovery_decisions": [
                    *latest.recovery_decisions,
                    decision,
                ],
                "updated_at": decision.created_at,
            }
        )
        await self._repository.save(updated)
        await self._event_center.publish(
            event_type="recovery_decision_recorded",
            run=updated,
        )
        return updated

    async def _merge_durable_recovery_audit(
        self,
        run: GeneralAgentRun,
    ) -> GeneralAgentRun:
        stored = await self._repository.get(run.run_id)
        if stored is None:
            return run
        durable_by_id = {item.decision_id: item for item in stored.recovery_decisions}
        for item in run.recovery_decisions:
            durable = durable_by_id.get(item.decision_id)
            durable_by_id[item.decision_id] = (
                item
                if durable is None
                else _merge_recovery_decision_versions(durable, item)
            )
        ordered_ids = [item.decision_id for item in stored.recovery_decisions]
        ordered_ids.extend(
            item.decision_id
            for item in run.recovery_decisions
            if item.decision_id not in set(ordered_ids)
        )
        decisions = [durable_by_id[item_id] for item_id in ordered_ids]
        differences = list(
            dict.fromkeys(
                [
                    *stored.context_resume_differences,
                    *run.context_resume_differences,
                ]
            )
        )
        return run.model_copy(
            update={
                "recovery_decisions": decisions,
                "context_resume_differences": differences,
            }
        )

    async def _finalize_effect_recovery_decisions(
        self,
        run: GeneralAgentRun,
    ) -> GeneralAgentRun:
        run = await self._merge_durable_recovery_audit(run)
        if self._effect_repository is None or not run.recovery_decisions:
            return run
        updated_decisions: list[RecoveryDecision] = []
        changed = False
        for decision in run.recovery_decisions:
            if (
                decision.action is not RecoveryAction.RECONCILE
                or decision.reason_code != "effect_reconciliation_started"
                or decision.effect_id is None
            ):
                updated_decisions.append(decision)
                continue
            latest = await self._effect_repository.latest(decision.effect_id)
            if latest is None:
                updated_decisions.append(decision)
                continue
            evidence = {
                **decision.evidence,
                "effect_status_after_recovery": latest.status.value,
            }
            resource_hash = latest.evidence.get(
                "actual_content_sha256"
            ) or latest.output.get("content_sha256")
            if isinstance(resource_hash, str):
                evidence["resource_content_sha256"] = resource_hash
            if latest.status is EffectStatus.RECONCILED:
                evidence["reconciliation_status"] = "succeeded"
                updated = decision.model_copy(
                    update={
                        "reason_code": "effect_reconciled",
                        "reason": "真实资源后态已确认原写入成功，恢复未重复写入。",
                        "evidence": evidence,
                        "evidence_sha256": recovery_evidence_sha256(evidence),
                    }
                )
            elif latest.status in {
                EffectStatus.UNKNOWN,
                EffectStatus.REQUIRES_HUMAN,
            }:
                evidence["reconciliation_status"] = "unknown"
                updated = decision.model_copy(
                    update={
                        "action": RecoveryAction.REQUIRES_HUMAN,
                        "reason_code": ("effect_reconciliation_requires_human"),
                        "reason": "真实资源后态无法确定原写入结果，恢复已禁止自动重写。",
                        "evidence": evidence,
                        "evidence_sha256": recovery_evidence_sha256(evidence),
                    }
                )
            else:
                updated_decisions.append(decision)
                continue
            updated_decisions.append(updated)
            changed = True
        if not changed:
            return run
        return run.model_copy(update={"recovery_decisions": updated_decisions})

    def _emit_fault(
        self,
        point: GeneralAgentFaultPoint,
        run: GeneralAgentRun,
        *,
        durable_identity: str | None = None,
    ) -> None:
        if self._fault_hook is None:
            return
        self._fault_hook.on_fault_point(
            point=point,
            context=GeneralAgentFaultContext(
                conversation_id=run.conversation_id,
                run_id=run.run_id,
                plan_revision=run.plan_revision,
                checkpoint_revision=run.checkpoint_revision,
                durable_identity=durable_identity,
            ),
        )

    async def _record_context_snapshot(
        self, snapshot: GeneralAgentContextSnapshot
    ) -> None:
        if self._context_snapshot_repository is not None:
            await self._context_snapshot_repository.save(snapshot)

    async def _require_run(self, run_id: str) -> GeneralAgentRun:
        run = await self._repository.get(run_id)
        if run is None:
            raise GeneralAgentRunNotFoundError(run_id)
        return run


def _transition(
    run: GeneralAgentRun,
    status: GeneralAgentRunStatus,
    reason: str,
) -> GeneralAgentRun:
    if run.status is status and run.lifecycle_events:
        return run.model_copy(update={"updated_at": now_iso()})
    event = GeneralAgentLifecycleEvent(
        status=status,
        reason=reason,
        created_at=now_iso(),
    )
    return run.model_copy(
        update={
            "status": status,
            "lifecycle_events": [*run.lifecycle_events, event],
            "updated_at": event.created_at,
        }
    )


def _merge_recovery_decision_versions(
    durable: RecoveryDecision,
    candidate: RecoveryDecision,
) -> RecoveryDecision:
    if durable == candidate:
        return durable
    pending_reason = "effect_reconciliation_started"
    terminal_reasons = {
        "effect_reconciled",
        "effect_reconciliation_requires_human",
    }
    if (
        durable.reason_code == pending_reason
        and candidate.reason_code in terminal_reasons
    ):
        return candidate
    if (
        candidate.reason_code == pending_reason
        and durable.reason_code in terminal_reasons
    ):
        return durable
    raise GeneralAgentRuntimeError("同一恢复决定出现互相冲突的持久版本。")


def _find_current_node(run: GeneralAgentRun, node_id: str) -> GeneralAgentNodeRun:
    item = _find_current_node_or_none(run, node_id)
    if item is not None:
        return item
    raise GeneralAgentRuntimeError(f"待授权节点“{node_id}”不存在。")


def _find_current_node_or_none(
    run: GeneralAgentRun,
    node_id: str,
) -> GeneralAgentNodeRun | None:
    for item in run.node_runs:
        if item.plan_revision == run.plan_revision and item.node_id == node_id:
            return item
    return None


def _runtime_thread_id(run: GeneralAgentRun) -> str:
    """LangGraph thread 表示长期会话；业务 run 只是线程内的一次请求。"""

    return run.conversation_id


def _run_from_graph_snapshot(
    snapshot: StateSnapshot,
) -> GeneralAgentRun | None:
    values = snapshot.values
    if not isinstance(values, Mapping):
        return None
    payload = values.get("run")
    if not isinstance(payload, dict):
        return None
    return GeneralAgentRun.model_validate(payload)


def _runtime_projection_conflicts(
    projection: GeneralAgentRun,
    graph_run: GeneralAgentRun,
) -> bool:
    """只比较执行字段；业务审计字段允许独立追加。"""

    fields = {
        "run_id",
        "conversation_id",
        "status",
        "plan",
        "plan_revision",
        "replan_count",
        "node_runs",
        "pending_human_request",
        "final_answer",
        "verification_attempt_count",
        "verification_issues",
    }
    return projection.model_dump(include=fields, mode="json") != graph_run.model_dump(
        include=fields,
        mode="json",
    )


def _checkpoint_belongs_to_run(
    checkpoint: Mapping[str, Any],
    run_id: str,
) -> bool:
    channel_values = checkpoint.get("channel_values")
    if not isinstance(channel_values, dict):
        return False
    payload = channel_values.get("run")
    return isinstance(payload, dict) and payload.get("run_id") == run_id


def _replace_current_node(
    run: GeneralAgentRun,
    replacement: GeneralAgentNodeRun,
) -> list[GeneralAgentNodeRun]:
    replaced = False
    node_runs: list[GeneralAgentNodeRun] = []
    for item in run.node_runs:
        if (
            item.plan_revision == replacement.plan_revision
            and item.node_id == replacement.node_id
        ):
            node_runs.append(replacement)
            replaced = True
        else:
            node_runs.append(item)
    if not replaced:
        raise GeneralAgentRuntimeError(f"待更新节点“{replacement.node_id}”不存在。")
    return node_runs


def _append_human_interaction(
    run: GeneralAgentRun,
    *,
    request_id: str,
    prompt: str,
    response: str,
) -> GeneralAgentRun:
    run = _with_human_prompt_message(
        run,
        request_id=request_id,
        prompt=prompt,
        created_at=run.updated_at,
    )
    existing = [
        message
        for message in run.messages
        if message.turn_id == run.run_id
        and message.request_index == run.request_index
        and message.message_type is GeneralAgentMessageType.HUMAN_RESPONSE
        and message.human_request_id == request_id
    ]
    if existing:
        if len(existing) != 1 or existing[0].content != response:
            raise GeneralAgentRuntimeError("同一人工请求已经记录了不同回答。")
        return run
    messages = list(run.messages)
    messages.append(
        _new_message(
            turn_id=run.run_id,
            request_index=run.request_index,
            role="user",
            content=response,
            created_at=now_iso(),
            message_type=GeneralAgentMessageType.HUMAN_RESPONSE,
            human_request_id=request_id,
            message_id=_logical_message_id(
                run.run_id,
                request_id,
                GeneralAgentMessageType.HUMAN_RESPONSE.value,
            ),
        )
    )
    return run.model_copy(update={"messages": messages})


def _with_visible_conversation_messages(run: GeneralAgentRun) -> GeneralAgentRun:
    request = run.pending_human_request
    if request is not None:
        run = _with_human_prompt_message(
            run,
            request_id=request.request_id,
            prompt=request.prompt,
            created_at=request.created_at,
        )
    return _with_assistant_final_message(run)


def _with_human_prompt_message(
    run: GeneralAgentRun,
    *,
    request_id: str,
    prompt: str,
    created_at: str,
) -> GeneralAgentRun:
    existing = [
        message
        for message in run.messages
        if message.turn_id == run.run_id
        and message.request_index == run.request_index
        and message.message_type is GeneralAgentMessageType.HUMAN_PROMPT
        and message.human_request_id == request_id
    ]
    if existing:
        if len(existing) != 1 or existing[0].content != prompt:
            raise GeneralAgentRuntimeError("同一人工请求已经记录了不同提示。")
        return run
    messages = [
        *run.messages,
        _new_message(
            turn_id=run.run_id,
            request_index=run.request_index,
            role="assistant",
            content=prompt,
            created_at=created_at,
            message_type=GeneralAgentMessageType.HUMAN_PROMPT,
            human_request_id=request_id,
            message_id=_logical_message_id(
                run.run_id,
                request_id,
                GeneralAgentMessageType.HUMAN_PROMPT.value,
            ),
        ),
    ]
    return run.model_copy(update={"messages": messages})


def _with_assistant_final_message(run: GeneralAgentRun) -> GeneralAgentRun:
    if (
        not run.final_answer.strip()
        or run.status in _ACTIVE_STATUSES
        or run.status is GeneralAgentRunStatus.WAITING_HUMAN
    ):
        return run
    existing = [
        message
        for message in run.messages
        if message.turn_id == run.run_id
        and message.request_index == run.request_index
        and message.message_type is GeneralAgentMessageType.ASSISTANT_FINAL
    ]
    if existing:
        if len(existing) != 1 or existing[0].content != run.final_answer:
            raise GeneralAgentRuntimeError("同一请求轮次已经记录了不同最终回答。")
        return run
    messages = [
        *run.messages,
        _new_message(
            turn_id=run.run_id,
            request_index=run.request_index,
            role="assistant",
            content=run.final_answer,
            created_at=run.finished_at or run.updated_at,
            message_type=GeneralAgentMessageType.ASSISTANT_FINAL,
            message_id=_logical_message_id(
                run.run_id,
                GeneralAgentMessageType.ASSISTANT_FINAL.value,
            ),
        ),
    ]
    return run.model_copy(update={"messages": messages})


def _new_message(
    *,
    turn_id: str,
    request_index: int,
    role: Literal["user", "assistant"],
    content: str,
    created_at: str,
    message_type: GeneralAgentMessageType,
    human_request_id: str | None = None,
    message_id: str | None = None,
) -> GeneralAgentMessage:
    return GeneralAgentMessage(
        role=role,
        content=content,
        created_at=created_at,
        message_id=message_id or f"message_{uuid4().hex}",
        turn_id=turn_id,
        request_index=request_index,
        message_type=message_type,
        human_request_id=human_request_id,
    )


def _logical_message_id(*parts: str) -> str:
    encoded = "\0".join(parts).encode("utf-8")
    return f"message_{sha256(encoded).hexdigest()[:32]}"


def _blocking_failed_nodes(run: GeneralAgentRun) -> list[GeneralAgentNodeRun]:
    if run.plan is None:
        return []
    plan_nodes = {node.node_id: node for node in run.plan.nodes}
    return [
        node
        for node in run.node_runs
        if node.plan_revision == run.plan_revision
        and node.status is GeneralAgentNodeStatus.FAILED
        and not plan_nodes[node.node_id].continue_on_failure
    ]


def _execution_failure_issues(
    nodes: list[GeneralAgentNodeRun],
) -> list[str]:
    return [
        (
            f"{node.capability_name}（节点 {node.node_id}）执行失败："
            f"{node.error_message or node.error_type or '未知错误'}"
        )
        for node in nodes
    ]


def _chapter_source_quality_issues(run: GeneralAgentRun) -> list[str]:
    """明确章节内容请求必须取得 Markdown 正文来源，空检索不能伪装成功。"""

    if not is_explicit_chapter_content_request(run.user_goal):
        return []
    current_nodes = [
        node
        for node in run.node_runs
        if node.plan_revision == run.plan_revision
        and node.status is GeneralAgentNodeStatus.SUCCESS
    ]
    source_refs = {
        source_ref for node in current_nodes for source_ref in node.source_refs
    }
    if any(source_ref.startswith("manuscript:") for source_ref in source_refs):
        return []
    orders = explicit_chapter_orders(run.user_goal)
    return [
        "当前请求明确指定章节顺序"
        f"{orders}，但执行结果没有取得 manuscript: 正文来源引用；"
        "必须改用 read_manuscript 按章节顺序直接读取，不能把空搜索视为完成。"
    ]


def _execution_replan_guidance(issues: list[str]) -> str:
    return (
        "上一版计划已在真实运行时校验或能力执行中失败。"
        "请根据失败位置和真实能力输入输出 Schema 重新规划；"
        "不要假定失败节点已经成功，也不要重复原错误交接地址。失败详情："
        + "；".join(issues)
    )


def _with_memory_refs(
    run: GeneralAgentRun,
    memory_ids: list[str],
) -> GeneralAgentRun:
    return run.model_copy(
        update={"memory_refs": _deduplicate([*run.memory_refs, *memory_ids])}
    )


def _with_context_snapshot(
    run: GeneralAgentRun,
    assembly: ContextAssemblyResult,
) -> GeneralAgentRun:
    envelope = assembly.snapshot.envelope
    stats = {item.category: item for item in envelope.category_stats}
    memory_ids = [reference.memory_id for reference in assembly.snapshot.memory_refs]
    compression = GeneralAgentCompressionStats(
        compressed=envelope.compressed,
        fallback_used=envelope.fallback_used,
        input_char_count=envelope.total_char_count,
        output_char_count=envelope.total_char_count,
        estimated_token_count=envelope.estimated_token_count,
        omitted_message_count=stats.get(
            "history_memory",
            GeneralAgentContextCategoryStat(category="history_memory"),
        ).omitted_count,
        omitted_node_count=stats.get(
            "working_memory",
            GeneralAgentContextCategoryStat(category="working_memory"),
        ).omitted_count,
        selected_memory_count=len(memory_ids),
    )
    return run.model_copy(
        update={
            "memory_refs": _deduplicate([*run.memory_refs, *memory_ids]),
            "context_snapshot_id": assembly.snapshot.snapshot_id,
            "context_snapshot": assembly.snapshot,
            "compression_stats": compression,
            "context_resume_differences": _deduplicate(
                [
                    *run.context_resume_differences,
                    *assembly.resume_differences,
                ]
            ),
        }
    )


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


class GeneralAgentRuntimeError(RuntimeError):
    """通用 Runtime 请求无法按当前状态执行。"""


class GeneralAgentRunNotFoundError(GeneralAgentRuntimeError):
    def __init__(self, run_id: str) -> None:
        super().__init__(f"通用写作助手任务“{run_id}”不存在。")


class GeneralAgentConversationNotFoundError(GeneralAgentRuntimeError):
    def __init__(self, conversation_id: str) -> None:
        super().__init__(f"通用写作助手对话“{conversation_id}”不存在。")
