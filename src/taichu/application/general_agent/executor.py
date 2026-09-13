"""按依赖和并发上限执行一次动态能力 DAG。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from copy import deepcopy
import json
from time import perf_counter
from typing import Annotated, Any, TypedDict
from uuid import NAMESPACE_URL, uuid4, uuid5

from langchain_core.messages import ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Overwrite, Send
from pydantic import ValidationError

from taichu.application.agent_memory.models import (
    producer_validity_proof_sha256,
)
from taichu.application.contracts.agent_memory import (
    ProducerMemoryValidityProvider,
)
from taichu.application.contracts.general_agent_capability_results import (
    CapabilityResultOwner,
    CapabilityResultRecord,
    GeneralAgentCapabilityResultRepository,
    ResultIdentityPayload,
    build_capability_result_record,
    canonical_capability_result_sha256,
    capability_result_id,
)
from taichu.application.contracts.general_agent_effects import (
    GeneralAgentEffectRepository,
)
from taichu.application.general_agent.models import (
    GeneralAgentHumanRequest,
    GeneralAgentNodeKind,
    GeneralAgentNodeRun,
    GeneralAgentNodeStatus,
    GeneralAgentPlanNode,
    GeneralAgentRun,
    GeneralAgentRunStatus,
)
from taichu.application.general_agent.faults import (
    GeneralAgentFaultContext,
    GeneralAgentFaultHook,
    GeneralAgentFaultPoint,
    InjectedProcessTermination,
)
from taichu.application.general_agent.recovery import EffectRecord, EffectStatus
from taichu.application.invocations.models import (
    InvocationBudget,
    InvocationContext,
    InvocationEnvelope,
    now_iso,
)
from taichu.application.services.invocation_policy_service import (
    InvocationPolicyService,
    canonical_input_hash,
)
from taichu.application.subagents.registry import SubagentRegistry
from taichu.application.tools.contract import (
    ToolAuthorizationPolicy,
    ToolReconciliationStatus,
    ToolSideEffect,
)
from taichu.application.tools.registry import ToolRegistry

RunProjectionCallback = Callable[
    [GeneralAgentRun, str],
    Awaitable[GeneralAgentRun],
]


def _merge_mapping(
    current: dict[str, dict[str, Any]],
    update: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {**current, **update}


class _DynamicDagState(TypedDict, total=False):
    run: dict[str, Any]
    node_results: Annotated[dict[str, dict[str, Any]], _merge_mapping]
    human_requests: Annotated[dict[str, dict[str, Any]], _merge_mapping]
    dispatched_node_id: str


class DynamicDagExecutor:
    """仅调度注册表中的真实 Tool 与专业子 Agent。"""

    def __init__(
        self,
        *,
        tool_registry: ToolRegistry,
        subagent_registry: SubagentRegistry,
        policy_service: InvocationPolicyService,
        capability_result_repository: GeneralAgentCapabilityResultRepository,
        capability_handler_identities: Mapping[tuple[str, str], str],
        effect_repository: GeneralAgentEffectRepository,
        fault_hook: GeneralAgentFaultHook | None = None,
        memory_validity_provider: ProducerMemoryValidityProvider | None = None,
    ) -> None:
        self._tool_registry = tool_registry
        self._subagent_registry = subagent_registry
        self._policy_service = policy_service
        self._capability_result_repository = capability_result_repository
        self._capability_handler_identities = dict(capability_handler_identities)
        self._effect_repository = effect_repository
        self._fault_hook = fault_hook
        self._memory_validity_provider = memory_validity_provider
        self._context_boundary: (
            Callable[[GeneralAgentRun], Awaitable[GeneralAgentRun]] | None
        ) = None

    @property
    def capability_result_repository(
        self,
    ) -> GeneralAgentCapabilityResultRepository:
        """暴露组合根身份检查所需的同一 Repository 实例。"""

        return self._capability_result_repository

    @property
    def fault_hook(self) -> GeneralAgentFaultHook | None:
        """暴露组合根身份检查所需的同一通用 Hook 实例。"""

        return self._fault_hook

    def bind_memory_validity_provider(
        self,
        provider: ProducerMemoryValidityProvider,
    ) -> None:
        self._memory_validity_provider = provider

    def bind_context_boundary(
        self, callback: Callable[[GeneralAgentRun], Awaitable[GeneralAgentRun]]
    ) -> None:
        self._context_boundary = callback

    async def execute(
        self,
        run: GeneralAgentRun,
        *,
        checkpoint: RunProjectionCallback,
        checkpointer: BaseCheckpointSaver[Any],
    ) -> GeneralAgentRun:
        """以独立官方图执行能力 DAG；产品运行时把同一图作为父图子图使用。"""

        if run.plan is None:
            raise DynamicDagExecutionError("通用 Runtime 没有可执行计划。")
        graph = self.build_graph(
            checkpoint=checkpoint,
            checkpointer=checkpointer,
        )
        config = {
            "recursion_limit": max(20, len(run.plan.nodes) * 3 + 10),
            "max_concurrency": run.limits.max_concurrency,
            "configurable": {"thread_id": run.conversation_id},
        }
        graph_input: _DynamicDagState = {
            "run": run.model_dump(mode="json"),
        }
        graph_state = await graph.aget_state(config)
        if graph_state.next:
            result = await graph.ainvoke(None, config=config)
        else:
            result = await graph.ainvoke(graph_input, config=config)
        return GeneralAgentRun.model_validate(result["run"])

    def build_graph(
        self,
        *,
        checkpoint: RunProjectionCallback,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
    ):
        """构建固定拓扑的 Send worker 图；动态计划只作为运行状态进入。"""

        async def prepare(state: _DynamicDagState) -> dict[str, Any]:
            return await self._prepare_graph_state(state, checkpoint)

        async def project(state: _DynamicDagState) -> dict[str, Any]:
            return await self._project_graph_state(state, checkpoint)

        graph = StateGraph(_DynamicDagState)
        graph.add_node("prepare_capability_dag", prepare)
        graph.add_node("execute_capability", self._execute_graph_node)
        graph.add_node("project_capability_results", project)
        graph.add_edge(START, "prepare_capability_dag")
        graph.add_conditional_edges(
            "prepare_capability_dag",
            self._dispatch_capabilities,
        )
        graph.add_edge("execute_capability", "project_capability_results")
        graph.add_conditional_edges(
            "project_capability_results",
            self._dispatch_capabilities,
        )
        return graph.compile(checkpointer=checkpointer)

    async def _ensure_node_runs(self, run: GeneralAgentRun) -> GeneralAgentRun:
        if run.plan is None:
            return run
        existing = {(item.plan_revision, item.node_id): item for item in run.node_runs}
        items = list(run.node_runs)
        for node in run.plan.nodes:
            key = (run.plan_revision, node.node_id)
            if key in existing:
                existing_item = existing[key]
                if existing_item.attempt_id is None:
                    replacement = existing_item.model_copy(
                        update={"attempt_id": _attempt_id(run, node.node_id)}
                    )
                    items[items.index(existing_item)] = replacement
                continue
            reused = await self._reused_node_run(run, node)
            if reused is not None:
                items.append(reused)
                continue
            items.append(
                GeneralAgentNodeRun(
                    node_id=node.node_id,
                    plan_revision=run.plan_revision,
                    kind=node.kind,
                    capability_name=node.capability_name,
                    objective=node.objective,
                    dependencies=node.dependencies,
                    attempt_id=_attempt_id(run, node.node_id),
                )
            )
        return run.model_copy(update={"node_runs": items})

    async def _reused_node_run(
        self,
        run: GeneralAgentRun,
        node: GeneralAgentPlanNode,
    ) -> GeneralAgentNodeRun | None:
        if node.reuse_from_node_id is None:
            return None
        candidates = [
            item
            for item in run.node_runs
            if item.plan_revision < run.plan_revision
            and item.node_id == node.reuse_from_node_id
            and item.status is GeneralAgentNodeStatus.SUCCESS
        ]
        if not candidates:
            raise DynamicDagExecutionError(
                f"节点“{node.node_id}”要求复用的成功节点"
                f"“{node.reuse_from_node_id}”不存在。"
            )
        source = max(candidates, key=lambda item: item.plan_revision)
        if (
            source.kind is not node.kind
            or source.capability_name != node.capability_name
        ):
            raise DynamicDagExecutionError(
                f"节点“{node.node_id}”不能复用能力类型或名称不同的"
                f"节点“{node.reuse_from_node_id}”。"
            )
        provider = self._memory_validity_provider
        if provider is None:
            raise DynamicDagExecutionError("节点复用缺少 producer 有效性证明服务。")
        producer_ref = f"node:{run.run_id}:{source.plan_revision}:{source.node_id}"
        try:
            observed = await provider.producer_validity_proof(
                run.conversation_id,
                producer_ref,
                current_request_index=run.request_index,
            )
            proof = await provider.require_active_producer(
                run.conversation_id,
                producer_ref,
                expected_source_fingerprint=observed.source_fingerprint,
                expected_dependency_fingerprint=observed.dependency_fingerprint,
                current_request_index=run.request_index,
            )
        except Exception as error:
            raise DynamicDagExecutionError(
                f"节点复用的 producer 有效性证明失败：{error}"
            ) from error
        return source.model_copy(
            update={
                "node_id": node.node_id,
                "plan_revision": run.plan_revision,
                "objective": node.objective,
                "dependencies": node.dependencies,
                "attempt_id": _attempt_id(run, node.node_id),
                "reused_from_producer_ref": producer_ref,
                "producer_validity_proof_sha256": (
                    producer_validity_proof_sha256(proof)
                ),
                "reused_source_plan_revision": source.plan_revision,
                "reused_source_fingerprint": proof.source_fingerprint,
                "reused_dependency_fingerprint": proof.dependency_fingerprint,
                "reconciliation_reason": (
                    f"复用计划修订 {source.plan_revision} 的成功节点"
                    f"“{source.node_id}”，未重复调用能力。"
                ),
            }
        )

    async def _prepare_graph_state(
        self,
        state: _DynamicDagState,
        checkpoint: RunProjectionCallback,
    ) -> dict[str, Any]:
        run = GeneralAgentRun.model_validate(state["run"])
        if run.plan is None:
            raise DynamicDagExecutionError("通用 Runtime 没有可执行计划。")
        run = await self._ensure_node_runs(run)
        run = await checkpoint(run, "dag_prepared")
        return {
            "run": run.model_dump(mode="json"),
            "node_results": Overwrite(
                {
                    item.node_id: item.model_dump(mode="json")
                    for item in _current_runs(run).values()
                }
            ),
            "human_requests": Overwrite({}),
        }

    def _dispatch_capabilities(
        self,
        state: _DynamicDagState,
    ) -> str | list[Send]:
        run = self._run_from_graph_state(state)
        if run.status in {
            GeneralAgentRunStatus.WAITING_HUMAN,
            GeneralAgentRunStatus.VERIFYING,
            GeneralAgentRunStatus.COMPLETED,
            GeneralAgentRunStatus.CANCELLED,
            GeneralAgentRunStatus.FAILED,
        }:
            return END
        ready = self._ready_node_ids(run)
        if not ready:
            return "project_capability_results"
        return [
            Send(
                "execute_capability",
                {
                    "run": state["run"],
                    "node_results": state.get("node_results", {}),
                    "human_requests": state.get("human_requests", {}),
                    "dispatched_node_id": node_id,
                },
            )
            for node_id in ready
        ]

    async def _execute_graph_node(
        self,
        state: _DynamicDagState,
    ) -> dict[str, Any]:
        run = self._run_from_graph_state(state)
        if run.plan is None:
            raise DynamicDagExecutionError("通用 Runtime 没有可执行计划。")
        node_id = state.get("dispatched_node_id", "")
        plan_nodes = {node.node_id: node for node in run.plan.nodes}
        plan_node = plan_nodes.get(node_id)
        item = _current_runs(run).get(node_id)
        if plan_node is None or item is None:
            raise DynamicDagExecutionError("LangGraph Send 指向了未知能力节点。")
        if item.status in {
            GeneralAgentNodeStatus.SUCCESS,
            GeneralAgentNodeStatus.FAILED,
            GeneralAgentNodeStatus.SKIPPED,
        }:
            return {"node_results": {item.node_id: item.model_dump(mode="json")}}
        if not self._dependencies_satisfied(
            item,
            _current_runs(run),
            plan_nodes,
        ):
            raise DynamicDagExecutionError(f"节点“{node_id}”在依赖尚未满足时被调度。")
        approval = self._first_write_approval(run, [item], plan_nodes)
        if approval is not None:
            waiting, request = approval
            return {
                "node_results": {waiting.node_id: waiting.model_dump(mode="json")},
                "human_requests": {waiting.node_id: request.model_dump(mode="json")},
            }
        running = item.model_copy(
            update={
                "status": GeneralAgentNodeStatus.RUNNING,
                "started_at": item.started_at or now_iso(),
                "error_type": None,
                "error_message": None,
            }
        )
        result, human_request = await self._execute_node(
            run,
            running,
            plan_node,
        )
        update: dict[str, Any] = {
            "node_results": {result.node_id: result.model_dump(mode="json")}
        }
        if human_request is not None:
            update["human_requests"] = {
                result.node_id: human_request.model_dump(mode="json")
            }
        return update

    async def _project_graph_state(
        self,
        state: _DynamicDagState,
        checkpoint: RunProjectionCallback,
    ) -> dict[str, Any]:
        run = self._run_from_graph_state(state)
        if self._context_boundary is not None:
            run = await self._context_boundary(run)
        if run.plan is None:
            raise DynamicDagExecutionError("通用 Runtime 没有可执行计划。")
        plan_nodes = {node.node_id: node for node in run.plan.nodes}
        while True:
            projected = self._mark_blocked_nodes(run, plan_nodes)
            if projected == run:
                break
            run = projected
        current = _current_runs(run)
        requests = [
            GeneralAgentHumanRequest.model_validate(payload)
            for node_id, payload in state.get("human_requests", {}).items()
            if node_id in current
            and current[node_id].status is GeneralAgentNodeStatus.WAITING_HUMAN
        ]
        if len(requests) > 1:
            raise DynamicDagExecutionError("同一执行轮次产生了多个写入授权请求。")
        if requests:
            run = run.model_copy(
                update={
                    "status": GeneralAgentRunStatus.WAITING_HUMAN,
                    "pending_human_request": requests[0],
                    "updated_at": now_iso(),
                }
            )
            run = await checkpoint(
                run,
                "waiting_human_after_capability_checkpoint",
            )
            return {"run": run.model_dump(mode="json")}
        if all(
            item.status
            in {
                GeneralAgentNodeStatus.SUCCESS,
                GeneralAgentNodeStatus.FAILED,
                GeneralAgentNodeStatus.SKIPPED,
            }
            for item in current.values()
        ):
            run = await checkpoint(
                run.model_copy(update={"updated_at": now_iso()}),
                "capability_dag_projected",
            )
            run = run.model_copy(
                update={
                    "status": GeneralAgentRunStatus.VERIFYING,
                    "updated_at": now_iso(),
                }
            )
            return {"run": run.model_dump(mode="json")}
        if not self._ready_node_ids(run):
            raise DynamicDagExecutionError(
                "能力 DAG 尚有节点，但没有可继续调度的节点。"
            )
        return {"run": run.model_dump(mode="json")}

    def _run_from_graph_state(self, state: _DynamicDagState) -> GeneralAgentRun:
        run = GeneralAgentRun.model_validate(state["run"])
        for payload in state.get("node_results", {}).values():
            run = _replace_node_run(
                run,
                GeneralAgentNodeRun.model_validate(payload),
            )
        return run

    def _ready_node_ids(self, run: GeneralAgentRun) -> list[str]:
        if run.plan is None:
            return []
        current = _current_runs(run)
        plan_nodes = {node.node_id: node for node in run.plan.nodes}
        selected: list[str] = []
        approval_selected = False
        for plan_node in run.plan.nodes:
            item = current[plan_node.node_id]
            if item.status is not GeneralAgentNodeStatus.PENDING:
                continue
            if not self._dependencies_satisfied(item, current, plan_nodes):
                continue
            needs_approval = self._needs_write_approval(item)
            if needs_approval and approval_selected:
                continue
            selected.append(item.node_id)
            approval_selected = approval_selected or needs_approval
        return selected

    def _needs_write_approval(self, item: GeneralAgentNodeRun) -> bool:
        if item.kind is not GeneralAgentNodeKind.TOOL:
            return False
        manifest = self._tool_registry.get_manifest(item.capability_name)
        return (
            manifest.authorization_policy is not ToolAuthorizationPolicy.NONE
            and item.authorization_grant_id is None
        )

    def _mark_blocked_nodes(
        self,
        run: GeneralAgentRun,
        plan_nodes: Mapping[str, GeneralAgentPlanNode],
    ) -> GeneralAgentRun:
        current = _current_runs(run)
        result = run
        for item in current.values():
            if item.status is not GeneralAgentNodeStatus.PENDING:
                continue
            failed_dependencies = [
                dependency
                for dependency in item.dependencies
                if current[dependency].status
                in {GeneralAgentNodeStatus.FAILED, GeneralAgentNodeStatus.SKIPPED}
                and not plan_nodes[dependency].continue_on_failure
            ]
            if not failed_dependencies:
                continue
            result = _replace_node_run(
                result,
                item.model_copy(
                    update={
                        "status": GeneralAgentNodeStatus.SKIPPED,
                        "finished_at": now_iso(),
                        "error_type": "UpstreamDependencyFailed",
                        "error_message": (
                            "上游节点失败，未执行：" + "、".join(failed_dependencies)
                        ),
                    }
                ),
            )
        return result

    def _dependencies_satisfied(
        self,
        item: GeneralAgentNodeRun,
        current: Mapping[str, GeneralAgentNodeRun],
        plan_nodes: Mapping[str, GeneralAgentPlanNode],
    ) -> bool:
        for dependency in item.dependencies:
            status = current[dependency].status
            if status is GeneralAgentNodeStatus.SUCCESS:
                continue
            if (
                status
                in {GeneralAgentNodeStatus.FAILED, GeneralAgentNodeStatus.SKIPPED}
                and plan_nodes[dependency].continue_on_failure
            ):
                continue
            return False
        return True

    def _first_write_approval(
        self,
        run: GeneralAgentRun,
        ready: list[GeneralAgentNodeRun],
        plan_nodes: Mapping[str, GeneralAgentPlanNode],
    ) -> tuple[GeneralAgentNodeRun, GeneralAgentHumanRequest] | None:
        for item in ready:
            if item.kind is not GeneralAgentNodeKind.TOOL:
                continue
            manifest = self._tool_registry.get_manifest(item.capability_name)
            if manifest.authorization_policy is ToolAuthorizationPolicy.NONE:
                continue
            if item.authorization_grant_id:
                continue
            resolved_input = self._prepare_input(run, plan_nodes[item.node_id])
            if "idempotency_key" in manifest.input_schema.model_fields:
                resolved_input.setdefault(
                    "idempotency_key",
                    f"{run.run_id}:{run.plan_revision}:{item.node_id}",
                )
            updated = item.model_copy(
                update={
                    "status": GeneralAgentNodeStatus.WAITING_HUMAN,
                    "resolved_input": resolved_input,
                }
            )
            scopes = _resource_scopes(item.capability_name, resolved_input)
            request = GeneralAgentHumanRequest(
                request_id=f"human_{uuid4().hex}",
                kind="write_authorization",
                prompt=(
                    f"通用写作助手准备调用“{item.capability_name}”执行持久化修改。"
                    "请核对输入与作用范围后决定是否授权。"
                ),
                node_id=item.node_id,
                tool_name=item.capability_name,
                input_sha256=canonical_input_hash(resolved_input),
                input_summary=resolved_input,
                resource_scopes=scopes,
                second_confirmation_required=(
                    manifest.authorization_policy
                    is ToolAuthorizationPolicy.SECOND_CONFIRMATION
                ),
                created_at=now_iso(),
            )
            return updated, request
        return None

    async def _execute_node(
        self,
        run: GeneralAgentRun,
        item: GeneralAgentNodeRun,
        plan_node: GeneralAgentPlanNode,
    ) -> tuple[GeneralAgentNodeRun, GeneralAgentHumanRequest | None]:
        timer = perf_counter()
        try:
            resolved_input = self._prepare_input(run, plan_node)
            external_grant_id = await self._external_grant(run, item, resolved_input)
            if item.kind is GeneralAgentNodeKind.TOOL and item.authorization_approved:
                item = await self._renew_author_grant(run, item, resolved_input)
            elif item.authorization_grant_id:
                resolved_input["author_grant_id"] = item.authorization_grant_id
            invocation = self._invocation_context(
                run,
                item,
                external_grant_id=external_grant_id,
            )
            if item.kind is GeneralAgentNodeKind.TOOL:
                tool_manifest = self._tool_registry.get_manifest(item.capability_name)
                if tool_manifest.side_effect in {
                    ToolSideEffect.WRITE,
                    ToolSideEffect.HIGH_RISK_WRITE,
                }:
                    return await self._execute_write_tool(
                        run,
                        item,
                        resolved_input,
                        invocation,
                        timer=timer,
                    )
                result_identity = self._result_identity(
                    run,
                    item,
                    resolved_input,
                    input_schema=tool_manifest.input_schema,
                    output_schema=tool_manifest.output_schema,
                )
                completed = await self._completed_result(result_identity)
                if completed is not None:
                    return (
                        self._successful_capability_result(
                            item,
                            resolved_input,
                            completed,
                            action="reuse",
                            timer=timer,
                        ),
                        None,
                    )
                envelope = await self._invoke_langchain_tool(
                    item.capability_name,
                    resolved_input,
                    invocation,
                )
            else:
                subagent_manifest = self._subagent_registry.get_manifest(
                    item.capability_name
                )
                result_identity = self._result_identity(
                    run,
                    item,
                    resolved_input,
                    input_schema=subagent_manifest.input_schema,
                    output_schema=subagent_manifest.output_schema,
                )
                completed = await self._completed_result(result_identity)
                if completed is not None:
                    return (
                        self._successful_capability_result(
                            item,
                            resolved_input,
                            completed,
                            action="reuse",
                            timer=timer,
                        ),
                        None,
                    )
                envelope = await self._invoke_subagent(
                    run=run,
                    item=item,
                    resolved_input=resolved_input,
                    invocation=invocation,
                    attempt_id=result_identity.attempt_id,
                )
            record = build_capability_result_record(
                identity=result_identity,
                output=envelope.output.model_dump(mode="json"),
                source_refs=tuple(envelope.source_refs),
                artifact_refs=tuple(envelope.artifact_refs),
                trace_id=envelope.trace_id,
                committed_at=now_iso(),
            )
            committed = await self._capability_result_repository.commit_completed(
                result_identity.owner,
                record,
            )
            self._emit_fault(
                point=GeneralAgentFaultPoint.CAPABILITY_RESULT_COMMITTED,
                run=run,
                item=item,
                durable_identity=committed.result_id,
                attempt_id=result_identity.attempt_id,
            )
            return (
                self._successful_capability_result(
                    item,
                    resolved_input,
                    committed,
                    action="commit",
                    timer=timer,
                ),
                None,
            )
        except InjectedProcessTermination:
            raise
        except Exception as error:  # noqa: BLE001
            return (
                item.model_copy(
                    update={
                        "status": GeneralAgentNodeStatus.FAILED,
                        "finished_at": now_iso(),
                        "duration_ms": max(0, round((perf_counter() - timer) * 1000)),
                        "error_type": type(error).__name__,
                        "error_message": _runtime_error_message(error)[:2_000],
                    }
                ),
                None,
            )

    def _result_identity(
        self,
        run: GeneralAgentRun,
        item: GeneralAgentNodeRun,
        resolved_input: dict[str, Any],
        *,
        input_schema: type[Any],
        output_schema: type[Any],
    ) -> ResultIdentityPayload:
        attempt_id = item.attempt_id or _attempt_id(run, item.node_id)
        key = (item.kind.value, item.capability_name)
        handler_identity = self._capability_handler_identities.get(key)
        if not handler_identity:
            raise DynamicDagExecutionError(
                f"能力“{item.capability_name}”缺少稳定 Handler 身份。"
            )
        parsed_input = input_schema.model_validate(resolved_input)
        return ResultIdentityPayload(
            owner=_capability_result_owner(run),
            plan_revision=run.plan_revision,
            node_id=item.node_id,
            attempt_id=attempt_id,
            capability_kind=item.kind.value,
            capability_name=item.capability_name,
            input_sha256=canonical_input_hash(parsed_input),
            handler_identity_sha256=canonical_capability_result_sha256(
                handler_identity
            ),
            input_schema_sha256=canonical_capability_result_sha256(
                input_schema.model_json_schema()
            ),
            output_schema_sha256=canonical_capability_result_sha256(
                output_schema.model_json_schema()
            ),
        )

    async def _completed_result(
        self,
        identity: ResultIdentityPayload,
    ) -> CapabilityResultRecord | None:
        return await self._capability_result_repository.get_completed(
            identity.owner,
            capability_result_id(identity),
        )

    @staticmethod
    def _successful_capability_result(
        item: GeneralAgentNodeRun,
        resolved_input: dict[str, Any],
        record: CapabilityResultRecord,
        *,
        action: str,
        timer: float,
    ) -> GeneralAgentNodeRun:
        action_label = "复用" if action == "reuse" else "首次调用或安全重试后提交"
        return item.model_copy(
            update={
                "status": GeneralAgentNodeStatus.SUCCESS,
                "resolved_input": resolved_input,
                "output": record.output,
                "source_refs": list(record.source_refs),
                "artifact_refs": list(record.artifact_refs),
                "trace_id": record.trace_id,
                "reconciliation_reason": (
                    f"{action_label}已完成能力结果 {record.result_id}；"
                    f"identity={record.identity_payload_sha256}；"
                    f"record={record.content_sha256}"
                ),
                "duplicate_execution_protected": True,
                "finished_at": now_iso(),
                "duration_ms": max(
                    0,
                    round((perf_counter() - timer) * 1000),
                ),
                "error_type": None,
                "error_message": None,
            }
        )

    async def _execute_write_tool(
        self,
        run: GeneralAgentRun,
        item: GeneralAgentNodeRun,
        resolved_input: dict[str, Any],
        invocation: InvocationContext,
        *,
        timer: float,
    ) -> tuple[GeneralAgentNodeRun, GeneralAgentHumanRequest | None]:
        effect_id = _effect_id(run, item.node_id)
        latest = await self._effect_repository.latest(effect_id)
        if latest is not None and latest.status in {
            EffectStatus.SUCCEEDED,
            EffectStatus.RECONCILED,
        }:
            return self._successful_write_result(
                item,
                resolved_input,
                output=latest.output,
                effect_id=effect_id,
                effect_status=latest.status,
                reason="复用已落盘的副作用成功证据。",
                timer=timer,
            )
        if latest is not None and latest.status in {
            EffectStatus.STARTED,
            EffectStatus.UNKNOWN,
            EffectStatus.REQUIRES_HUMAN,
        }:
            reconciliation = await self._tool_registry.reconcile(
                item.capability_name,
                resolved_input,
                invocation,
            )
            if reconciliation.status is ToolReconciliationStatus.SUCCEEDED:
                record = await self._append_effect(
                    run,
                    item,
                    resolved_input,
                    status=EffectStatus.RECONCILED,
                    output=reconciliation.output,
                    evidence=reconciliation.evidence,
                    reason=reconciliation.reason,
                )
                return self._successful_write_result(
                    item,
                    resolved_input,
                    output=reconciliation.output,
                    effect_id=record.effect_id,
                    effect_status=record.status,
                    reason=reconciliation.reason or "真实资源对账确认写入已生效。",
                    timer=timer,
                )
            if reconciliation.status is ToolReconciliationStatus.UNKNOWN:
                record = await self._append_effect(
                    run,
                    item,
                    resolved_input,
                    status=EffectStatus.REQUIRES_HUMAN,
                    evidence=reconciliation.evidence,
                    reason=reconciliation.reason,
                )
                waiting = item.model_copy(
                    update={
                        "status": GeneralAgentNodeStatus.WAITING_HUMAN,
                        "resolved_input": resolved_input,
                        "effect_id": record.effect_id,
                        "effect_status": record.status,
                        "reconciliation_reason": reconciliation.reason,
                        "duplicate_execution_protected": True,
                        "error_type": "SideEffectRequiresHuman",
                        "error_message": (
                            reconciliation.reason
                            or "真实写入结果无法自动确认，已阻止重复执行。"
                        ),
                    }
                )
                request = GeneralAgentHumanRequest(
                    request_id=f"human_{uuid4().hex}",
                    kind="effect_reconciliation",
                    prompt=(
                        "写入请求发出后服务中断，系统无法自动确认真实资源是否已变化。"
                        "已停止自动重试，请先核对资源状态。"
                    ),
                    node_id=item.node_id,
                    tool_name=item.capability_name,
                    effect_id=record.effect_id,
                    input_sha256=canonical_input_hash(resolved_input),
                    input_summary={},
                    resource_scopes=_resource_scopes(
                        item.capability_name, resolved_input
                    ),
                    created_at=now_iso(),
                )
                return waiting, request
        if latest is None:
            await self._append_effect(
                run,
                item,
                resolved_input,
                status=EffectStatus.PREPARED,
                reason="确定输入、授权和资源范围已冻结。",
            )
        started = await self._append_effect(
            run,
            item,
            resolved_input,
            status=EffectStatus.STARTED,
            reason=(
                "真实写入即将开始。"
                if latest is None
                else "对账确认未生效，按原确定输入安全重试。"
            ),
        )
        try:
            envelope = await self._invoke_langchain_tool(
                item.capability_name,
                resolved_input,
                invocation,
            )
            self._emit_fault(
                point=GeneralAgentFaultPoint.RESOURCE_WRITE_APPLIED,
                run=run,
                item=item,
                durable_identity=started.effect_id,
                attempt_id=started.attempt_id,
            )
            output = envelope.output.model_dump(mode="json")
            record = await self._append_effect(
                run,
                item,
                resolved_input,
                status=EffectStatus.SUCCEEDED,
                output=output,
                evidence={
                    "source_refs": envelope.source_refs,
                    "artifact_refs": envelope.artifact_refs,
                    "trace_id": envelope.trace_id,
                },
                reason="真实写入和返回结果均已落盘。",
            )
            return (
                item.model_copy(
                    update={
                        "status": GeneralAgentNodeStatus.SUCCESS,
                        "resolved_input": resolved_input,
                        "output": output,
                        "source_refs": envelope.source_refs,
                        "artifact_refs": envelope.artifact_refs,
                        "trace_id": envelope.trace_id,
                        "effect_id": record.effect_id,
                        "effect_status": record.status,
                        "duplicate_execution_protected": True,
                        "finished_at": now_iso(),
                        "duration_ms": max(0, round((perf_counter() - timer) * 1000)),
                        "error_type": None,
                        "error_message": None,
                    }
                ),
                None,
            )
        except InjectedProcessTermination:
            raise
        except Exception as error:
            reconciliation = await self._tool_registry.reconcile(
                item.capability_name,
                resolved_input,
                invocation,
            )
            if reconciliation.status is ToolReconciliationStatus.SUCCEEDED:
                record = await self._append_effect(
                    run,
                    item,
                    resolved_input,
                    status=EffectStatus.RECONCILED,
                    output=reconciliation.output,
                    evidence=reconciliation.evidence,
                    reason=reconciliation.reason,
                )
                return self._successful_write_result(
                    item,
                    resolved_input,
                    output=reconciliation.output,
                    effect_id=record.effect_id,
                    effect_status=record.status,
                    reason=reconciliation.reason,
                    timer=timer,
                )
            status = (
                EffectStatus.FAILED
                if reconciliation.status is ToolReconciliationStatus.NOT_APPLIED
                else EffectStatus.UNKNOWN
            )
            await self._append_effect(
                run,
                item,
                resolved_input,
                status=status,
                evidence=reconciliation.evidence,
                reason=reconciliation.reason or str(error),
            )
            raise

    async def _invoke_langchain_tool(
        self,
        name: str,
        input_data: dict[str, Any],
        invocation: InvocationContext,
    ) -> InvocationEnvelope[Any]:
        tool = self._tool_registry.bind_langchain_tool(name, invocation)
        result = await tool.ainvoke(
            {
                "type": "tool_call",
                "name": name,
                "args": input_data,
                "id": invocation.call_id,
            }
        )
        if not isinstance(result, ToolMessage):
            raise DynamicDagExecutionError(
                f"LangChain Tool“{name}”没有返回 ToolMessage。"
            )
        artifact = result.artifact
        if not isinstance(artifact, InvocationEnvelope):
            raise DynamicDagExecutionError(
                f"LangChain Tool“{name}”没有返回太初调用证据。"
            )
        return artifact

    async def _invoke_subagent(
        self,
        *,
        run: GeneralAgentRun,
        item: GeneralAgentNodeRun,
        resolved_input: dict[str, Any],
        invocation: InvocationContext,
        attempt_id: str,
    ) -> Any:
        if self._fault_hook is None:
            return await self._subagent_registry.invoke(
                item.capability_name,
                resolved_input,
                invocation,
            )

        pending = asyncio.create_task(
            self._subagent_registry.invoke(
                item.capability_name,
                resolved_input,
                invocation,
            )
        )
        await asyncio.sleep(0)
        try:
            self._emit_fault(
                point=GeneralAgentFaultPoint.SUBAGENT_STARTED,
                run=run,
                item=item,
                durable_identity=attempt_id,
                attempt_id=attempt_id,
            )
        except InjectedProcessTermination:
            if not pending.done():
                pending.cancel()
                with suppress(asyncio.CancelledError):
                    await pending
            else:
                with suppress(asyncio.CancelledError, Exception):
                    pending.result()
            raise
        return await pending

    def _emit_fault(
        self,
        *,
        point: GeneralAgentFaultPoint,
        run: GeneralAgentRun,
        item: GeneralAgentNodeRun,
        durable_identity: str | None = None,
        attempt_id: str | None = None,
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
                node_id=item.node_id,
                attempt_id=attempt_id or item.attempt_id,
                capability_kind=item.kind.value,
                capability_name=item.capability_name,
                durable_identity=durable_identity,
            ),
        )

    async def _append_effect(
        self,
        run: GeneralAgentRun,
        item: GeneralAgentNodeRun,
        resolved_input: dict[str, Any],
        *,
        status: EffectStatus,
        output: dict[str, Any] | None = None,
        evidence: dict[str, Any] | None = None,
        reason: str = "",
    ) -> EffectRecord:
        idempotency_key = resolved_input.get("idempotency_key")
        if not isinstance(idempotency_key, str) or not idempotency_key:
            raise DynamicDagExecutionError("写入工具缺少稳定幂等键。")
        record = EffectRecord(
            event_id=f"effect_event_{uuid4().hex}",
            effect_id=_effect_id(run, item.node_id),
            attempt_id=item.attempt_id or _attempt_id(run, item.node_id),
            run_id=run.run_id,
            plan_revision=run.plan_revision,
            node_id=item.node_id,
            tool_name=item.capability_name,
            status=status,
            input_sha256=canonical_input_hash(resolved_input),
            idempotency_key=idempotency_key,
            resource_scopes=(
                item.authorization_resource_scopes
                or _resource_scopes(item.capability_name, resolved_input)
            ),
            authorization_reference=item.authorization_grant_id,
            output=output or {},
            evidence=evidence or {},
            reason=reason,
            created_at=now_iso(),
        )
        await self._effect_repository.append(record)
        return record

    def _successful_write_result(
        self,
        item: GeneralAgentNodeRun,
        resolved_input: dict[str, Any],
        *,
        output: dict[str, Any],
        effect_id: str,
        effect_status: EffectStatus,
        reason: str,
        timer: float,
    ) -> tuple[GeneralAgentNodeRun, None]:
        refs = output.get("source_refs", [])
        source_refs = (
            [value for value in refs if isinstance(value, str)]
            if isinstance(refs, list)
            else []
        )
        return (
            item.model_copy(
                update={
                    "status": GeneralAgentNodeStatus.SUCCESS,
                    "resolved_input": resolved_input,
                    "output": output,
                    "source_refs": source_refs,
                    "effect_id": effect_id,
                    "effect_status": effect_status,
                    "reconciliation_reason": reason,
                    "duplicate_execution_protected": True,
                    "finished_at": now_iso(),
                    "duration_ms": max(0, round((perf_counter() - timer) * 1000)),
                    "error_type": None,
                    "error_message": None,
                }
            ),
            None,
        )

    async def _external_grant(
        self,
        run: GeneralAgentRun,
        item: GeneralAgentNodeRun,
        resolved_input: dict[str, Any],
    ) -> str | None:
        if not self._requires_external(item):
            return None
        if not run.external_access_allowed:
            raise DynamicDagExecutionError("本次任务没有外部研究许可。")
        reference = await self._policy_service.issue_external_access(
            task_id=run.task_id,
            user_intent_ref=run.user_goal,
            allowed_tools=frozenset(
                {"search_external_sources", "read_external_source"}
            ),
        )
        if item.kind is GeneralAgentNodeKind.SUBAGENT:
            resolved_input["external_access_grant_id"] = reference.grant_id
        return reference.grant_id

    async def _renew_author_grant(
        self,
        run: GeneralAgentRun,
        item: GeneralAgentNodeRun,
        resolved_input: dict[str, Any],
    ) -> GeneralAgentNodeRun:
        """按作者已确认的冻结输入和原资源范围签发本次进程内授权。"""
        manifest = self._tool_registry.get_manifest(item.capability_name)
        normalized_payload = dict(resolved_input)
        normalized_payload["author_grant_id"] = item.authorization_grant_id
        normalized_input = manifest.input_schema.model_validate(normalized_payload)
        scopes = item.authorization_resource_scopes or _resource_scopes(
            item.capability_name,
            resolved_input,
        )
        grant = await self._policy_service.issue_author_write(
            task_id=run.task_id,
            tool_name=item.capability_name,
            input_payload=normalized_input,
            resource_scopes=tuple(scopes),
            second_confirmation=item.authorization_second_confirmation,
        )
        resolved_input["author_grant_id"] = grant.grant_id
        return item.model_copy(
            update={
                "authorization_grant_id": grant.grant_id,
                "authorization_resource_scopes": scopes,
            }
        )

    def _invocation_context(
        self,
        run: GeneralAgentRun,
        item: GeneralAgentNodeRun,
        *,
        external_grant_id: str | None,
    ) -> InvocationContext:
        context_envelope = (
            run.context_snapshot.envelope if run.context_snapshot is not None else None
        )
        invocation_scope = (
            dict(context_envelope.scope)
            if context_envelope is not None
            else run.scope.model_dump(mode="json")
        )
        if context_envelope is not None:
            invocation_scope["runtime_memory_notice"] = (
                "运行记忆只用于延续任务上下文，不是小说事实；涉及事实必须重新取证。"
            )
            invocation_scope["runtime_memories"] = [
                memory.model_dump(mode="json")
                for memory in context_envelope.runtime_memories
            ]
            invocation_scope["session_memory"] = (
                context_envelope.working_memory.session_memory.model_dump(mode="json")
            )
        return InvocationContext(
            task_id=run.task_id,
            run_id=run.run_id,
            conversation_id=run.conversation_id,
            call_id=item.attempt_id or _attempt_id(run, item.node_id),
            caller_type="orchestrator",
            caller_name="general_writing_orchestrator",
            phase=f"dag:{item.node_id}",
            user_goal=(
                context_envelope.current_goal
                if context_envelope is not None
                else run.user_goal
            ),
            model_id=run.model_id,
            author_constraints=(
                context_envelope.author_constraints
                if context_envelope is not None
                else run.author_constraints
            ),
            scope=invocation_scope,
            external_access_grant_id=external_grant_id,
            budget=InvocationBudget(
                max_input_chars=(
                    max(120_000, context_envelope.total_char_count)
                    if context_envelope is not None
                    else 120_000
                ),
                max_tool_calls=run.limits.max_total_tool_calls,
            ),
        )

    def _prepare_input(
        self,
        run: GeneralAgentRun,
        node: GeneralAgentPlanNode,
    ) -> dict[str, Any]:
        payload = deepcopy(node.input_data)
        current = _current_runs(run)
        for binding in node.input_bindings:
            try:
                source = current[binding.source_node_id].output
                value = _read_path(source, binding.source_path)
                _write_path(payload, binding.target_path, value)
            except DynamicDagExecutionError as error:
                raise DynamicDagExecutionError(
                    f"节点“{node.node_id}”的数据交接失败：从上游节点"
                    f"“{binding.source_node_id}”读取“{binding.source_path}”并写入"
                    f"“{binding.target_path}”时出错；{error}"
                ) from error
        if node.kind is GeneralAgentNodeKind.SUBAGENT:
            subagent_manifest = self._subagent_registry.get_manifest(
                node.capability_name
            )
            if "source_request" in subagent_manifest.input_schema.model_fields:
                source_request = payload.setdefault("source_request", {})
                if not isinstance(source_request, dict):
                    raise DynamicDagExecutionError("source_request 必须是对象。")
                auto_collect_declared = "auto_collect" in source_request
                artifact_refs: list[str] = []
                for dependency in node.dependencies:
                    artifact_refs.extend(current[dependency].artifact_refs)
                if artifact_refs:
                    existing = source_request.get("upstream_artifact_refs", [])
                    if not isinstance(existing, list):
                        raise DynamicDagExecutionError(
                            "upstream_artifact_refs 必须是数组。"
                        )
                    source_request["upstream_artifact_refs"] = list(
                        dict.fromkeys([*existing, *artifact_refs])
                    )
                tool_contexts: list[str] = []
                tool_source_refs: list[str] = []
                for dependency in node.dependencies:
                    source_run = current[dependency]
                    if (
                        source_run.kind is not GeneralAgentNodeKind.TOOL
                        or source_run.status is not GeneralAgentNodeStatus.SUCCESS
                        or not source_run.output
                    ):
                        continue
                    tool_contexts.append(
                        f"[上游工具：{source_run.capability_name}]\n"
                        + json.dumps(
                            source_run.output,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                    )
                    tool_source_refs.extend(source_run.source_refs)
                if tool_contexts:
                    existing_context = source_request.get("direct_context", "")
                    if not isinstance(existing_context, str):
                        raise DynamicDagExecutionError("direct_context 必须是字符串。")
                    if not existing_context.strip():
                        combined_context = "\n\n".join(tool_contexts)
                        if len(combined_context) > 100_000:
                            combined_context = (
                                combined_context[:99_960]
                                + "\n\n[上游工具结果因上下文上限被截断]"
                            )
                        source_request["direct_context"] = combined_context
                    existing_refs = source_request.get("direct_source_refs", [])
                    if not isinstance(existing_refs, list):
                        raise DynamicDagExecutionError(
                            "direct_source_refs 必须是数组。"
                        )
                    source_request["direct_source_refs"] = list(
                        dict.fromkeys([*existing_refs, *tool_source_refs])
                    )[:100]
                if (artifact_refs or tool_contexts) and not auto_collect_declared:
                    source_request["auto_collect"] = False
        if node.kind is GeneralAgentNodeKind.TOOL:
            tool_manifest = self._tool_registry.get_manifest(node.capability_name)
            if "idempotency_key" in tool_manifest.input_schema.model_fields:
                payload.setdefault(
                    "idempotency_key",
                    f"{run.run_id}:{run.plan_revision}:{node.node_id}",
                )
            inserted_author_placeholder = (
                "author_grant_id" in tool_manifest.input_schema.model_fields
                and not payload.get("author_grant_id")
            )
            if inserted_author_placeholder:
                payload["author_grant_id"] = "pending_author_grant"
            payload = tool_manifest.input_schema.model_validate(payload).model_dump(
                mode="json"
            )
            if inserted_author_placeholder:
                payload.pop("author_grant_id", None)
        return payload

    def _requires_external(self, item: GeneralAgentNodeRun) -> bool:
        if item.kind is GeneralAgentNodeKind.SUBAGENT:
            return item.capability_name == "external_research"
        return self._tool_registry.get_manifest(
            item.capability_name
        ).requires_external_access


def _current_runs(run: GeneralAgentRun) -> dict[str, GeneralAgentNodeRun]:
    return {
        item.node_id: item
        for item in run.node_runs
        if item.plan_revision == run.plan_revision
    }


def _replace_node_run(
    run: GeneralAgentRun,
    replacement: GeneralAgentNodeRun,
) -> GeneralAgentRun:
    items = list(run.node_runs)
    for index, item in enumerate(items):
        if (
            item.plan_revision == replacement.plan_revision
            and item.node_id == replacement.node_id
        ):
            items[index] = replacement
            return run.model_copy(update={"node_runs": items})
    items.append(replacement)
    return run.model_copy(update={"node_runs": items})


def _read_path(payload: Any, path: str) -> Any:
    current = payload
    parts = path.removeprefix("output.").split(".")
    for part in parts:
        if isinstance(current, dict):
            if part not in current:
                raise DynamicDagExecutionError(f"上游输出不存在字段“{path}”。")
            current = current[part]
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            if index >= len(current):
                raise DynamicDagExecutionError(f"上游输出数组越界：“{path}”。")
            current = current[index]
        else:
            raise DynamicDagExecutionError(f"无法读取上游输出路径“{path}”。")
    return deepcopy(current)


def _write_path(payload: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    current: Any = payload
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        next_is_index = not last and parts[index + 1].isdigit()
        if isinstance(current, dict):
            if part.isdigit():
                raise DynamicDagExecutionError(f"无法写入节点输入路径“{path}”。")
            if last:
                current[part] = value
                return
            child = current.get(part)
            if child is None:
                child = [] if next_is_index else {}
                current[part] = child
            if not isinstance(child, (dict, list)):
                raise DynamicDagExecutionError(f"无法写入节点输入路径“{path}”。")
            current = child
            continue
        if isinstance(current, list) and part.isdigit():
            list_index = int(part)
            if list_index > len(current):
                raise DynamicDagExecutionError(f"节点输入数组写入越界：“{path}”。")
            if last:
                if list_index == len(current):
                    current.append(value)
                else:
                    current[list_index] = value
                return
            if list_index == len(current):
                current.append([] if next_is_index else {})
            child = current[list_index]
            if not isinstance(child, (dict, list)):
                raise DynamicDagExecutionError(f"无法写入节点输入路径“{path}”。")
            current = child
            continue
        raise DynamicDagExecutionError(f"无法写入节点输入路径“{path}”。")


def _runtime_error_message(error: Exception) -> str:
    if not isinstance(error, ValidationError):
        return str(error)
    details: list[str] = []
    for item in error.errors(include_url=False):
        location = ".".join(str(part) for part in item.get("loc", ())) or "输入"
        details.append(f"{location}：{item.get('msg', '不符合要求')}")
    return "能力输入未通过运行时校验：" + "；".join(details)


def _resource_scopes(tool_name: str, payload: Mapping[str, Any]) -> list[str]:
    scopes: list[str] = []
    for key in ("chapter_id", "card_id", "volume_id", "parent_id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            scopes.append(f"{key}:{value}")
    for key in ("chapter_ids", "card_ids", "item_ids"):
        value = payload.get(key)
        if isinstance(value, list):
            scopes.extend(f"{key}:{item}" for item in value if isinstance(item, str))
    return scopes or [f"tool:{tool_name}"]


def _attempt_id(run: GeneralAgentRun, node_id: str) -> str:
    value = uuid5(
        NAMESPACE_URL,
        f"taichu:{run.run_id}:{run.plan_revision}:{node_id}:attempt",
    )
    return f"attempt_{value.hex}"


def _capability_result_owner(run: GeneralAgentRun) -> CapabilityResultOwner:
    return CapabilityResultOwner(
        conversation_id=run.conversation_id,
        run_id=run.run_id,
    )


def _effect_id(run: GeneralAgentRun, node_id: str) -> str:
    value = uuid5(
        NAMESPACE_URL,
        f"taichu:{run.run_id}:{run.plan_revision}:{node_id}:effect",
    )
    return f"effect_{value.hex}"


class DynamicDagExecutionError(RuntimeError):
    """动态执行图无法继续推进。"""
