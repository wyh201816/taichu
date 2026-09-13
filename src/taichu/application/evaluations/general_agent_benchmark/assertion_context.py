"""从真实 Runtime 后态构建 Typed Oracle 的确定性断言上下文。"""

from __future__ import annotations

from typing import Any, Literal

from taichu.application.evaluations.general_agent_benchmark.canonical import (
    canonical_sha256,
)
from taichu.application.evaluations.general_agent_benchmark.oracles import (
    AssertionEvaluationContext,
    AuthorizationEffectObservation,
    DataflowIdentityObservation,
)
from taichu.application.evaluations.general_agent_benchmark.observations import (
    ObservedEffect,
    ObservedHumanDecision,
    ObservedInvocation,
    ObservedInvocationIdentity,
    ObservedResourceSnapshot,
)
from taichu.application.evaluations.general_agent_benchmark.suite_loader import (
    AuthoredCaseSpec,
    DataflowIdentityAssertionSpec,
)
from taichu.application.evaluations.general_agent_benchmark.resource_observation import (
    project_resource_diffs,
)
from taichu.application.general_agent.models import (
    GeneralAgentNodeRun,
    GeneralAgentNodeStatus,
    GeneralAgentPlanNode,
    GeneralAgentRun,
    result_basis_sha256,
)


def build_runtime_assertion_context(
    *,
    case: AuthoredCaseSpec,
    run: GeneralAgentRun,
    runs: tuple[GeneralAgentRun, ...] | None = None,
    invocations: tuple[ObservedInvocation, ...] = (),
    invocation_identities: tuple[ObservedInvocationIdentity, ...] = (),
    human_decisions: tuple[ObservedHumanDecision, ...] = (),
    effects: tuple[ObservedEffect, ...] = (),
    resource_snapshots: tuple[ObservedResourceSnapshot, ...] = (),
    base: AssertionEvaluationContext | None = None,
) -> AssertionEvaluationContext:
    """只从持久运行后态投影数据交接；证据不足时不补造观察。"""

    selected_runs = runs or (run,)
    current_nodes = tuple(
        node
        for source_run in selected_runs
        for node in source_run.node_runs
        if node.plan_revision == source_run.plan_revision
        and node.status is GeneralAgentNodeStatus.SUCCESS
    )
    plan_nodes = tuple(
        node
        for source_run in selected_runs
        if source_run.plan is not None
        for node in source_run.plan.nodes
    )
    dataflow = tuple(
        observed
        for assertion in case.behavior_assertions
        if isinstance(assertion, DataflowIdentityAssertionSpec)
        for observed in (
            _dataflow_observation(
                assertion,
                run=run,
                plan_nodes=plan_nodes,
                current_nodes=current_nodes,
                invocations=invocations,
                invocation_identities=invocation_identities,
            ),
        )
        if observed is not None
    )
    existing = base or AssertionEvaluationContext()
    authorizations = _authorization_observations(
        case=case,
        runs=selected_runs,
        human_decisions=human_decisions,
        effects=effects,
        invocation_identities=invocation_identities,
    )
    return existing.model_copy(
        update={
            "dataflow_identities": (
                *existing.dataflow_identities,
                *dataflow,
            ),
            "resource_diffs": (
                *existing.resource_diffs,
                *project_resource_diffs(resource_snapshots),
            ),
            "authorizations": (
                *existing.authorizations,
                *authorizations,
            ),
        }
    )


def _authorization_observations(
    *,
    case: AuthoredCaseSpec,
    runs: tuple[GeneralAgentRun, ...],
    human_decisions: tuple[ObservedHumanDecision, ...],
    effects: tuple[ObservedEffect, ...],
    invocation_identities: tuple[ObservedInvocationIdentity, ...],
) -> tuple[AuthorizationEffectObservation, ...]:
    decision_ref = case.setup.human_decision_ref
    if decision_ref is None:
        return ()
    successful_effects = tuple(
        item for item in effects if item.status in {"succeeded", "reconciled"}
    )
    request_ids = tuple(dict.fromkeys(item.request_id for item in human_decisions))
    grant_ids = tuple(
        dict.fromkeys(
            node.authorization_grant_id
            for source_run in runs
            for node in source_run.node_runs
            if node.authorization_grant_id is not None
            and any(decision.node_id == node.node_id for decision in human_decisions)
        )
    )
    decision: Literal[
        "approved",
        "denied",
        "confirmed",
        "cancelled",
        "pending",
    ]
    if not human_decisions:
        decision = "pending"
    elif any(not item.approved for item in human_decisions):
        decision = "denied"
    elif any(
        item.second_confirmation_required and item.second_confirmation
        for item in human_decisions
    ):
        decision = "confirmed"
    else:
        decision = "approved"
    requested_targets = tuple(
        dict.fromkeys(
            scope for item in human_decisions for scope in item.resource_scopes
        )
    )
    effected_targets = tuple(
        dict.fromkeys(
            scope for item in successful_effects for scope in item.resource_scopes
        )
    )
    unbound_effect_ids = tuple(
        item.effect_id
        for item in successful_effects
        if item.authorization_reference not in set(grant_ids)
    )
    preview_hashes = _identity_values(
        invocation_identities,
        capability_name="preview_manuscript_patch",
        direction="output",
        identity_field="preview_sha256",
    )
    apply_hashes = _identity_values(
        invocation_identities,
        capability_name="apply_manuscript_patch",
        direction="input",
        identity_field="preview_sha256",
    )
    return (
        AuthorizationEffectObservation(
            decision_ref=decision_ref,
            decision=decision,
            effect_count=len(successful_effects),
            requested_target_ref=(
                requested_targets[0] if len(requested_targets) == 1 else None
            ),
            requested_target_refs=requested_targets,
            effected_target_refs=effected_targets,
            decision_request_ids=request_ids,
            decision_grant_ids=grant_ids,
            unbound_effect_ids=unbound_effect_ids,
            preview_sha256=(preview_hashes[0] if len(preview_hashes) == 1 else None),
            applied_input_sha256=(apply_hashes[0] if len(apply_hashes) == 1 else None),
        ),
    )


def _identity_values(
    identities: tuple[ObservedInvocationIdentity, ...],
    *,
    capability_name: str,
    direction: str,
    identity_field: str,
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            item.identity
            for item in identities
            if item.capability_name == capability_name
            and item.direction == direction
            and item.identity_field == identity_field
        )
    )


def final_answer_provenance_refs(run: GeneralAgentRun) -> tuple[str, ...]:
    """只返回确实进入验证快照且被最终 basis 绑定的节点来源。"""

    if (
        not run.final_answer.strip()
        or run.final_answer_basis_sha256 is None
        or run.final_answer_basis_sha256 != result_basis_sha256(run)
    ):
        return ()
    current_nodes = tuple(
        item
        for item in run.node_runs
        if item.plan_revision == run.plan_revision
        and item.status is GeneralAgentNodeStatus.SUCCESS
        and _final_basis_contains(run, item)
    )
    return tuple(
        dict.fromkeys(
            source_ref for item in current_nodes for source_ref in item.source_refs
        )
    )


def _dataflow_observation(
    assertion: DataflowIdentityAssertionSpec,
    *,
    run: GeneralAgentRun,
    plan_nodes: tuple[GeneralAgentPlanNode, ...],
    current_nodes: tuple[GeneralAgentNodeRun, ...],
    invocations: tuple[ObservedInvocation, ...],
    invocation_identities: tuple[ObservedInvocationIdentity, ...],
) -> DataflowIdentityObservation | None:
    producers = tuple(
        item for item in current_nodes if item.capability_name == assertion.producer
    )
    if len(producers) != 1:
        return _invocation_dataflow_observation(
            assertion,
            invocations=invocations,
            invocation_identities=invocation_identities,
        )
    producer = producers[0]
    if assertion.consumer == "final_answer":
        return _final_answer_dataflow(assertion, run=run, producer=producer)

    consumers = tuple(
        item for item in current_nodes if item.capability_name == assertion.consumer
    )
    consumer_plans = tuple(
        item for item in plan_nodes if item.capability_name == assertion.consumer
    )
    if len(consumers) != 1 or not consumer_plans:
        return _invocation_dataflow_observation(
            assertion,
            invocations=invocations,
            invocation_identities=invocation_identities,
        )
    consumer = consumers[0]
    matching_bindings = tuple(
        binding
        for consumer_plan in consumer_plans
        for binding in consumer_plan.input_bindings
        if binding.source_node_id == producer.node_id
    )
    transferred: list[tuple[Any, Any]] = []
    for binding in matching_bindings:
        try:
            producer_value = _read_path(producer.output, binding.source_path)
            consumer_value = _read_path(
                consumer.resolved_input,
                binding.target_path,
            )
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        transferred.append((producer_value, consumer_value))
    if transferred:
        producer_values = tuple(item[0] for item in transferred)
        consumer_values = tuple(item[1] for item in transferred)
        return DataflowIdentityObservation(
            producer=assertion.producer,
            consumer=assertion.consumer,
            identity_field=assertion.identity_field,
            producer_identity=_bound_identity(
                producer_values,
                hash_content=assertion.identity_field
                in {"content_sha256", "output_sha256", "input_sha256"},
            ),
            consumer_identity=_bound_identity(
                consumer_values,
                hash_content=assertion.identity_field
                in {"content_sha256", "output_sha256", "input_sha256"},
            ),
            producer_record_sha256=canonical_sha256(producer.output),
            consumer_record_sha256=canonical_sha256(consumer.resolved_input),
            binding_refs=tuple(
                f"{item.source_node_id}:{item.source_path}->{item.target_path}"
                for item in matching_bindings
            ),
            source_refs=tuple(
                dict.fromkeys([*producer.source_refs, *consumer.source_refs])
            ),
        )
    shared_reference = _shared_reference_dataflow(
        assertion,
        producer=producer,
        consumer=consumer,
    )
    return shared_reference or _invocation_dataflow_observation(
        assertion,
        invocations=invocations,
        invocation_identities=invocation_identities,
    )


def _invocation_dataflow_observation(
    assertion: DataflowIdentityAssertionSpec,
    *,
    invocations: tuple[ObservedInvocation, ...],
    invocation_identities: tuple[ObservedInvocationIdentity, ...],
) -> DataflowIdentityObservation | None:
    """投影嵌套能力调用之间可由真实调用记录直接证明的数据交接。"""

    producers = tuple(
        item
        for item in invocations
        if item.capability_name == assertion.producer and item.output_sha256 is not None
    )
    consumers = tuple(
        item for item in invocations if item.capability_name == assertion.consumer
    )
    if len(producers) != 1 or len(consumers) != 1:
        return None
    producer = producers[0]
    consumer = consumers[0]

    producer_values = tuple(
        item
        for item in invocation_identities
        if item.call_id == producer.call_id
        and item.capability_name == assertion.producer
        and item.direction == "output"
        and item.identity_field == assertion.identity_field
    )
    consumer_values = tuple(
        item
        for item in invocation_identities
        if item.call_id == consumer.call_id
        and item.capability_name == assertion.consumer
        and item.direction == "input"
        and item.identity_field == assertion.identity_field
    )
    if len(producer_values) == 1 and len(consumer_values) == 1:
        return DataflowIdentityObservation(
            producer=assertion.producer,
            consumer=assertion.consumer,
            identity_field=assertion.identity_field,
            producer_identity=producer_values[0].identity,
            consumer_identity=consumer_values[0].identity,
            producer_record_sha256=producer_values[0].payload_sha256,
            consumer_record_sha256=consumer_values[0].payload_sha256,
            binding_refs=(
                f"{producer.call_id}:{producer_values[0].selector_path}",
                f"{consumer.call_id}:{consumer_values[0].selector_path}",
            ),
            source_refs=tuple(
                dict.fromkeys([*producer.source_refs, *consumer.source_refs])
            ),
        )

    if assertion.identity_field == "source_ref":
        shared = set(producer.source_refs) & set(consumer.source_refs)
    elif assertion.identity_field == "artifact_ref":
        shared = set(producer.artifact_refs) & set(consumer.artifact_refs)
    elif assertion.identity_field in {
        "content_sha256",
        "output_sha256",
        "input_sha256",
    }:
        if producer.output_sha256 != consumer.input_sha256:
            return None
        shared = {producer.output_sha256}
    else:
        return None
    if not shared:
        return None
    identity = _reference_identity(
        assertion.identity_field,
        shared,
    )
    return DataflowIdentityObservation(
        producer=assertion.producer,
        consumer=assertion.consumer,
        identity_field=assertion.identity_field,
        producer_identity=identity,
        consumer_identity=identity,
        producer_record_sha256=producer.output_sha256,
        consumer_record_sha256=consumer.input_sha256,
        source_refs=tuple(
            dict.fromkeys([*producer.source_refs, *consumer.source_refs])
        ),
    )


def _final_answer_dataflow(
    assertion: DataflowIdentityAssertionSpec,
    *,
    run: GeneralAgentRun,
    producer: GeneralAgentNodeRun,
) -> DataflowIdentityObservation | None:
    if not run.final_answer.strip() or not _final_basis_contains(run, producer):
        return None
    identity = _producer_identity(assertion.identity_field, producer)
    if identity is None:
        return None
    return DataflowIdentityObservation(
        producer=assertion.producer,
        consumer=assertion.consumer,
        identity_field=assertion.identity_field,
        producer_identity=identity,
        consumer_identity=identity,
        producer_record_sha256=canonical_sha256(producer.output),
        source_refs=tuple(producer.source_refs),
    )


def _final_basis_contains(
    run: GeneralAgentRun,
    producer: GeneralAgentNodeRun,
) -> bool:
    if (
        run.final_answer_basis_sha256 is None
        or run.final_answer_basis_sha256 != result_basis_sha256(run)
        or run.context_snapshot is None
        or run.context_snapshot.phase != "verify"
    ):
        return False
    summaries = run.context_snapshot.envelope.working_memory.node_summaries
    matching = tuple(
        item for item in summaries if item.get("node_id") == producer.node_id
    )
    if len(matching) != 1:
        return False
    output_summary = matching[0].get("output_summary")
    if output_summary == producer.output:
        return True
    # 截断后消费的是该次结果的预览。用不可变结果引用绑定真实输出，
    # 不再把“模型必须收到全文”当作数据流身份成立的前提。
    if not isinstance(output_summary, dict) or output_summary.get("已截断") is not True:
        return False
    source_id = f"node:{run.run_id}:{producer.plan_revision}:{producer.node_id}"
    expected_reference = "result_" + canonical_sha256(
        {
            "conversation_id": run.conversation_id,
            "source_id": source_id,
            "output": producer.output,
        }
    )
    return (
        matching[0].get("source_id") == source_id
        and matching[0].get("result_ref") == expected_reference
        and output_summary.get("完整结果引用") == expected_reference
    )


def _producer_identity(
    identity_field: str,
    producer: GeneralAgentNodeRun,
) -> str | None:
    if identity_field in {"content_sha256", "output_sha256"}:
        return canonical_sha256(producer.output)
    if identity_field == "source_ref":
        return producer.source_refs[0] if producer.source_refs else None
    if identity_field == "artifact_ref":
        return producer.artifact_refs[0] if producer.artifact_refs else None
    if identity_field in {
        "resource_id",
        "revision",
        "preview_sha256",
        "result_id",
        "claim_id",
        "input_sha256",
    }:
        return None
    return None


def _shared_reference_dataflow(
    assertion: DataflowIdentityAssertionSpec,
    *,
    producer: GeneralAgentNodeRun,
    consumer: GeneralAgentNodeRun,
) -> DataflowIdentityObservation | None:
    candidates = (
        set(producer.source_refs) & set(consumer.source_refs)
        if assertion.identity_field == "source_ref"
        else set(producer.artifact_refs) & set(consumer.artifact_refs)
        if assertion.identity_field == "artifact_ref"
        else set()
    )
    if not candidates:
        return None
    identity = _reference_identity(
        assertion.identity_field,
        candidates,
    )
    return DataflowIdentityObservation(
        producer=assertion.producer,
        consumer=assertion.consumer,
        identity_field=assertion.identity_field,
        producer_identity=identity,
        consumer_identity=identity,
        source_refs=tuple(
            dict.fromkeys([*producer.source_refs, *consumer.source_refs])
        ),
    )


def _read_path(payload: Any, path: str) -> Any:
    current = payload
    for part in path.removeprefix("output.").split("."):
        if isinstance(current, dict):
            current = current[part]
            continue
        if isinstance(current, list) and part.isdigit():
            current = current[int(part)]
            continue
        raise TypeError(path)
    return current


def _identity_value(value: Any) -> str:
    if isinstance(value, str) and value:
        return value
    return canonical_sha256(value)


def _bound_identity(
    values: tuple[Any, ...],
    *,
    hash_content: bool,
) -> str:
    if len(values) == 1 and not hash_content:
        return _identity_value(values[0])
    payload: Any = values[0] if len(values) == 1 else values
    return canonical_sha256(payload)


def _reference_identity(identity_field: str, references: set[str]) -> str:
    ordered = tuple(sorted(references))
    if len(ordered) == 1:
        return ordered[0]
    prefix = (
        "source_refs_sha256"
        if identity_field == "source_ref"
        else "artifact_refs_sha256"
    )
    return f"{prefix}:{canonical_sha256(ordered)}"


__all__ = [
    "build_runtime_assertion_context",
    "final_answer_provenance_refs",
]
