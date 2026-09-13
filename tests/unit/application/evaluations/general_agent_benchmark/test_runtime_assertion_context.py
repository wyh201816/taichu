"""真实 Runtime 后态到数据流断言上下文的确定性投影。"""

from __future__ import annotations

import json
from pathlib import Path

from taichu.application.evaluations.general_agent_benchmark.assertion_context import (
    build_runtime_assertion_context,
)
from taichu.application.evaluations.general_agent_benchmark.observations import (
    ObservedInvocation,
    ObservedInvocationIdentity,
)
from taichu.application.evaluations.general_agent_benchmark.suite_loader import (
    AuthoredCaseSpec,
    load_authored_suite,
)
from taichu.application.general_agent.models import (
    GeneralAgentContextEnvelope,
    GeneralAgentContextSnapshot,
    GeneralAgentCurrentRequest,
    GeneralAgentExecutionPlan,
    GeneralAgentNodeKind,
    GeneralAgentNodeRun,
    GeneralAgentNodeStatus,
    GeneralAgentRun,
    GeneralAgentRunStatus,
    GeneralAgentWorkingMemory,
    context_snapshot_sha256,
    result_basis_sha256,
)

_ROOT = Path("tests/fixtures/evaluations/general_writing_agent_benchmark")
_SUITE_PATH = _ROOT / "suite.json"
_MANIFEST_PATH = _ROOT / "fixtures" / "core_novel" / "fixture-manifest.json"
_NOW = "2026-07-30T12:00:00Z"


def _case(case_id: str) -> AuthoredCaseSpec:
    payload = json.loads(_SUITE_PATH.read_text(encoding="utf-8"))
    suite = load_authored_suite(
        _SUITE_PATH,
        expected_capability_catalog_hash=payload["capability_catalog_hash"],
        fixture_manifest_path=_MANIFEST_PATH,
    )
    return next(item for item in suite.cases if item.case_id == case_id)


def _run(
    case: AuthoredCaseSpec,
    *,
    nodes: list[GeneralAgentNodeRun],
    include_summaries: bool = True,
) -> GeneralAgentRun:
    plan_step = next(
        step for step in case.scripted_steps if step.name == "orchestrator_plan"
    )
    plan = GeneralAgentExecutionPlan.model_validate(plan_step.response)
    node_summaries = (
        [
            {
                "node_id": node.node_id,
                "capability_name": node.capability_name,
                "objective": node.objective,
                "status": node.status.value,
                "source_refs": node.source_refs,
                "artifact_refs": node.artifact_refs,
                "error": None,
                "output_summary": node.output,
            }
            for node in nodes
        ]
        if include_summaries
        else []
    )
    envelope = GeneralAgentContextEnvelope(
        phase="verify",
        working_memory=GeneralAgentWorkingMemory(node_summaries=node_summaries),
        current_request=GeneralAgentCurrentRequest(content=case.user_request_raw),
    )
    snapshot_payload = {
        "snapshot_id": "context_20260730_120000_assert01",
        "phase": "verify",
        "conversation_id": "benchmark_context_conversation",
        "run_id": "general_run_20260730_120000_abc123",
        "created_at": _NOW,
        "policy_snapshot": {},
        "memory_refs": [],
        "envelope": envelope.model_dump(mode="json"),
        "assembly_trace": None,
    }
    snapshot = GeneralAgentContextSnapshot(
        **snapshot_payload,
        content_sha256=context_snapshot_sha256(snapshot_payload),
    )
    run = GeneralAgentRun(
        run_id="general_run_20260730_120000_abc123",
        task_id="benchmark_context_task",
        conversation_id="benchmark_context_conversation",
        request_index=1,
        user_goal=case.user_request_raw,
        status=GeneralAgentRunStatus.COMPLETED,
        plan=plan,
        plan_revision=1,
        node_runs=nodes,
        context_snapshot_id=snapshot.snapshot_id,
        context_snapshot=snapshot,
        created_at=_NOW,
        updated_at=_NOW,
        started_at=_NOW,
        finished_at=_NOW,
    )
    return run.model_copy(
        update={
            "final_answer": "基于本轮真实节点结果完成回答。",
            "final_answer_basis_sha256": result_basis_sha256(run),
        }
    )


def _node(
    *,
    node_id: str,
    capability_name: str,
    dependencies: list[str] | None = None,
    resolved_input: dict[str, object] | None = None,
    output: dict[str, object] | None = None,
    source_refs: list[str] | None = None,
) -> GeneralAgentNodeRun:
    return GeneralAgentNodeRun(
        node_id=node_id,
        plan_revision=1,
        kind=GeneralAgentNodeKind.TOOL,
        capability_name=capability_name,
        objective=f"执行 {capability_name}",
        dependencies=dependencies or [],
        status=GeneralAgentNodeStatus.SUCCESS,
        resolved_input=resolved_input or {},
        output=output or {},
        source_refs=source_refs or [],
        started_at=_NOW,
        finished_at=_NOW,
    )


def test_bound_resource_identity_is_derived_from_actual_resolved_input() -> None:
    case = _case("knowledge_catalog_identity_read")
    catalog = _node(
        node_id="list_catalog",
        capability_name="list_knowledge_catalog",
        output={
            "items": [
                {
                    "knowledge_type": "item",
                    "name": "归潮灯",
                }
            ]
        },
    )
    resolved = _node(
        node_id="resolve_lamp",
        capability_name="resolve_knowledge_identity",
        dependencies=["list_catalog"],
        resolved_input={
            "knowledge_type": "item",
            "name": "归潮灯",
        },
        output={"matches": [{"card_id": "fixture_item_tide_lamp"}]},
    )
    read = _node(
        node_id="read_lamp_card",
        capability_name="read_knowledge_cards",
        dependencies=["resolve_lamp"],
        resolved_input={"card_ids": ["fixture_item_tide_lamp"]},
        output={"cards": [{"id": "fixture_item_tide_lamp"}]},
    )

    context = build_runtime_assertion_context(
        case=case,
        run=_run(case, nodes=[catalog, resolved, read]),
    )
    by_edge = {
        (item.producer, item.consumer): item for item in context.dataflow_identities
    }

    catalog_to_resolve = by_edge[
        ("list_knowledge_catalog", "resolve_knowledge_identity")
    ]
    resolve_to_read = by_edge[("resolve_knowledge_identity", "read_knowledge_cards")]
    assert catalog_to_resolve.producer_identity == catalog_to_resolve.consumer_identity
    assert (
        resolve_to_read.producer_identity
        == resolve_to_read.consumer_identity
        == "fixture_item_tide_lamp"
    )


def test_wrong_bound_resource_identity_is_projected_as_a_real_mismatch() -> None:
    case = _case("knowledge_catalog_identity_read")
    resolved = _node(
        node_id="resolve_lamp",
        capability_name="resolve_knowledge_identity",
        output={"matches": [{"card_id": "fixture_item_tide_lamp"}]},
    )
    read = _node(
        node_id="read_lamp_card",
        capability_name="read_knowledge_cards",
        dependencies=["resolve_lamp"],
        resolved_input={"card_ids": ["wrong_card"]},
        output={"cards": [{"id": "wrong_card"}]},
    )

    context = build_runtime_assertion_context(
        case=case,
        run=_run(case, nodes=[resolved, read]),
    )
    observed = next(
        item
        for item in context.dataflow_identities
        if item.producer == "resolve_knowledge_identity"
        and item.consumer == "read_knowledge_cards"
    )

    assert observed.producer_identity == "fixture_item_tide_lamp"
    assert observed.consumer_identity == "wrong_card"


def test_final_answer_consumption_requires_matching_basis_and_verify_projection() -> (
    None
):
    case = _case("single_manuscript_search")
    search = _node(
        node_id="search_fixture",
        capability_name="retrieve_story_context",
        resolved_input={"query": "归潮灯"},
        output={
            "evidences": [
                {
                    "source_id": "chapter_001",
                    "content": "归潮灯照出了墙后暗门。",
                }
            ]
        },
        source_refs=["manuscript:chapter_001:0-100"],
    )
    proven = _run(case, nodes=[search])
    missing_projection = _run(
        case,
        nodes=[search],
        include_summaries=False,
    )

    proven_context = build_runtime_assertion_context(case=case, run=proven)
    missing_context = build_runtime_assertion_context(
        case=case,
        run=missing_projection,
    )

    assert any(
        item.producer == "retrieve_story_context"
        and item.consumer == "final_answer"
        and item.producer_identity == item.consumer_identity
        for item in proven_context.dataflow_identities
    )
    assert not missing_context.dataflow_identities


def test_truncated_result_identity_requires_the_same_immutable_reference() -> None:
    from taichu.application.general_agent.context import create_snapshot
    from taichu.application.evaluations.general_agent_benchmark.canonical import (
        canonical_sha256,
    )

    case = _case("single_manuscript_search")
    producer = _node(
        node_id="search_fixture",
        capability_name="retrieve_story_context",
        output={"evidences": [{"content": "原始正文" * 1000}]},
    )
    run = _run(case, nodes=[producer])
    source = f"node:{run.run_id}:{producer.plan_revision}:{producer.node_id}"
    reference = "result_" + canonical_sha256(
        {
            "conversation_id": run.conversation_id,
            "source_id": source,
            "output": producer.output,
        }
    )
    original = run.context_snapshot.envelope
    for correct in (True, False):
        row = {
            "node_id": producer.node_id,
            "source_id": source,
            "result_ref": reference,
            "output_summary": {
                "已截断": True,
                "完整结果引用": reference if correct else "result_" + "0" * 64,
            },
        }
        envelope = original.model_copy(
            update={
                "working_memory": original.working_memory.model_copy(
                    update={"node_summaries": [row]}
                )
            }
        )
        projected = run.model_copy(
            update={"context_snapshot": create_snapshot(run, envelope)}
        )
        observed = build_runtime_assertion_context(case=case, run=projected)
        assert bool(observed.dataflow_identities) is correct


def test_nested_tool_to_subagent_source_flow_uses_real_invocation_refs() -> None:
    case = _case("external_research_grounded")
    shared_ref = "https://fixture.invalid/fixture_source_lighthouse_archive"
    research = _node(
        node_id="research_fixture",
        capability_name="external_research",
        output={"conclusion": "密封来源结论"},
        source_refs=[shared_ref],
    )
    invocations = (
        _invocation(
            name="search_external_sources",
            call_id="call_search",
            parent_call_id="call_research",
            source_refs=(shared_ref,),
            sequence=0,
        ),
        _invocation(
            name="read_external_source",
            call_id="call_read",
            parent_call_id="call_research",
            source_refs=(shared_ref,),
            sequence=1,
        ),
        _invocation(
            name="external_research",
            call_id="call_research",
            parent_call_id=None,
            source_refs=(shared_ref,),
            sequence=2,
        ),
    )

    context = build_runtime_assertion_context(
        case=case,
        run=_run(case, nodes=[research]),
        invocations=invocations,
    )
    by_edge = {
        (item.producer, item.consumer): item for item in context.dataflow_identities
    }

    assert (
        by_edge[("search_external_sources", "read_external_source")].producer_identity
        == shared_ref
    )
    assert (
        by_edge[("read_external_source", "external_research")].consumer_identity
        == shared_ref
    )


def test_nested_source_flow_is_absent_when_consumer_did_not_retain_source() -> None:
    case = _case("external_research_grounded")
    shared_ref = "https://fixture.invalid/fixture_source_lighthouse_archive"
    invocations = (
        _invocation(
            name="search_external_sources",
            call_id="call_search",
            parent_call_id="call_research",
            source_refs=(shared_ref,),
            sequence=0,
        ),
        _invocation(
            name="read_external_source",
            call_id="call_read",
            parent_call_id="call_research",
            source_refs=("https://fixture.invalid/wrong",),
            sequence=1,
        ),
    )

    context = build_runtime_assertion_context(
        case=case,
        run=_run(case, nodes=[]),
        invocations=invocations,
    )

    assert not any(
        item.producer == "search_external_sources"
        and item.consumer == "read_external_source"
        for item in context.dataflow_identities
    )


def test_cross_run_create_update_uses_typed_payload_identity_projection() -> None:
    case = _case("structure_create_update")
    invocations = (
        _invocation(
            name="create_novel_structure_items",
            call_id="call_create",
            parent_call_id=None,
            source_refs=("manuscript:outline",),
            sequence=0,
        ),
        _invocation(
            name="update_novel_structure",
            call_id="call_update",
            parent_call_id=None,
            source_refs=("manuscript:outline",),
            sequence=1,
        ),
    )
    identities = (
        _identity(
            call_id="call_create",
            capability="create_novel_structure_items",
            direction="output",
            field="resource_id",
            value="volume-created",
            payload_sha256="2" * 64,
        ),
        _identity(
            call_id="call_update",
            capability="update_novel_structure",
            direction="input",
            field="resource_id",
            value="volume-created",
            payload_sha256="1" * 64,
        ),
        _identity(
            call_id="call_create",
            capability="create_novel_structure_items",
            direction="output",
            field="revision",
            value="revision-created",
            payload_sha256="2" * 64,
        ),
        _identity(
            call_id="call_update",
            capability="update_novel_structure",
            direction="input",
            field="revision",
            value="revision-created",
            payload_sha256="1" * 64,
        ),
    )

    context = build_runtime_assertion_context(
        case=case,
        run=_run(case, nodes=[]),
        invocations=invocations,
        invocation_identities=identities,
    )
    by_field = {
        item.identity_field: item
        for item in context.dataflow_identities
        if item.consumer == "update_novel_structure"
    }

    assert by_field["resource_id"].producer_identity == "volume-created"
    assert by_field["resource_id"].consumer_identity == "volume-created"
    assert by_field["revision"].producer_identity == "revision-created"


def _invocation(
    *,
    name: str,
    call_id: str,
    parent_call_id: str | None,
    source_refs: tuple[str, ...],
    sequence: int,
) -> ObservedInvocation:
    return ObservedInvocation(
        call_id=call_id,
        sequence=sequence,
        parent_call_id=parent_call_id,
        capability_kind=("subagent" if name == "external_research" else "tool"),
        capability_name=name,
        status="completed",
        input_sha256="1" * 64,
        output_sha256="2" * 64,
        source_refs=source_refs,
        started_at=f"2026-07-30T12:00:0{sequence}Z",
        finished_at=f"2026-07-30T12:00:0{sequence}Z",
    )


def _identity(
    *,
    call_id: str,
    capability: str,
    direction: str,
    field: str,
    value: str,
    payload_sha256: str,
) -> ObservedInvocationIdentity:
    return ObservedInvocationIdentity(
        call_id=call_id,
        capability_name=capability,
        direction=direction,
        identity_field=field,
        selector_path="fixture.path",
        identity=value,
        payload_sha256=payload_sha256,
    )
