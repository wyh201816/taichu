"""需求 9.1—9.10：上下文压力计划与案例 30—37。"""

from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from taichu.application.evaluations.general_agent_benchmark.pressure import (
    PressureFixtureBlob,
    PressureKind,
    PressurePlan,
    PressureSeed,
    PressureSeedGenerator,
)


def _fixture_blob() -> PressureFixtureBlob:
    return PressureFixtureBlob.seal(
        blob_ref="pressure/context-anchor.txt",
        content="灯塔火焰必须始终保持青白色。",
    )


def _plan(
    kind: PressureKind,
    *,
    repetition_count: int = 24,
    unit_size: int = 240,
) -> PressurePlan:
    plan_ids = {
        PressureKind.HISTORY: "pressure_long_history",
        PressureKind.WORKING_MEMORY: "pressure_long_working_memory",
        PressureKind.NODE_OUTPUT: "pressure_large_node_output",
        PressureKind.MULTI_SOURCE: "pressure_multi_source",
        PressureKind.EQUIVALENCE_PAIR: "pressure_equivalence",
        PressureKind.INVALID_MEMORY: "pressure_invalid_memory",
        PressureKind.CURRENT_REQUEST: "pressure_long_current_request",
        PressureKind.UNSAFE_TOTAL: "pressure_unsafe_compression",
    }
    protected_refs = (
        "current_request",
        "fact_lighthouse_flame",
        "stable_rules",
    )
    invalid_refs = (
        ("sentinel_rejected", "sentinel_stale", "sentinel_superseded")
        if kind is PressureKind.INVALID_MEMORY
        else ()
    )
    return PressurePlan.seal(
        plan_id=plan_ids.get(kind, f"pressure_{kind.value}"),
        kind=kind,
        fixture_blob_ref="pressure/context-anchor.txt",
        repetition_count=repetition_count,
        unit_size=unit_size,
        protected_fact_refs=protected_refs,
        invalid_sentinel_refs=invalid_refs,
        paired_case_ref=(
            "context_baseline_pair" if kind is PressureKind.EQUIVALENCE_PAIR else None
        ),
    )


def _generate(
    kind: PressureKind,
    *,
    repetition_count: int = 24,
    unit_size: int = 240,
) -> PressureSeed:
    return PressureSeedGenerator().generate(
        _plan(
            kind,
            repetition_count=repetition_count,
            unit_size=unit_size,
        ),
        _fixture_blob(),
    )


def test_pressure_plan_and_generated_seed_are_content_addressed_and_fixed() -> None:
    plan = _plan(PressureKind.MULTI_SOURCE)
    blob = _fixture_blob()
    generator = PressureSeedGenerator()

    first = generator.generate(plan, blob)
    second = generator.generate(plan, blob)
    changed = generator.generate(
        plan,
        PressureFixtureBlob.seal(
            blob_ref=blob.blob_ref,
            content=blob.content + "不得改成赤红色。",
        ),
    )

    assert first == second
    assert first.content_hash == second.content_hash
    assert first.content_hash != changed.content_hash
    assert first.generation_seed == second.generation_seed
    assert first.fixture_blob_sha256 == blob.content_sha256
    assert first.long_term_memories == ()
    assert "case_id" not in inspect.signature(generator.generate).parameters

    payload = first.model_dump(mode="json", by_alias=True)
    payload["current_request"] = "篡改后的请求"
    with pytest.raises(ValidationError, match="content_hash"):
        PressureSeed.model_validate(payload)


def test_pressure_plan_rejects_unknown_kind_bad_identity_and_invalid_pairing() -> None:
    payload = _plan(PressureKind.HISTORY).model_dump(mode="json", by_alias=True)

    with pytest.raises(ValidationError):
        PressurePlan.model_validate({**payload, "kind": "case_30_only"})
    with pytest.raises(ValidationError, match="规范化内容"):
        PressurePlan.model_validate(
            {
                **payload,
                "protected_fact_refs": [
                    "current_request",
                    "current_request",
                ],
            }
        )
    with pytest.raises(ValidationError, match="paired_case_ref"):
        PressurePlan.seal(
            plan_id="pressure_bad_pair",
            kind=PressureKind.HISTORY,
            fixture_blob_ref="pressure/context-anchor.txt",
            repetition_count=2,
            unit_size=100,
            protected_fact_refs=("current_request",),
            invalid_sentinel_refs=(),
            paired_case_ref="context_baseline_pair",
        )
