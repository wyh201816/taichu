"""评测展示必须由真实可执行合同派生，不能维护夸大的展示数字。"""

from __future__ import annotations

import json
from pathlib import Path

from taichu.application.evaluations.general_agent_benchmark.portfolio import (
    build_benchmark_portfolio,
)
from taichu.application.evaluations.general_agent_benchmark.suite_loader import (
    AuthoredSuiteSpec,
    load_authored_suite,
)

_ROOT = Path("tests/fixtures/evaluations/general_writing_agent_benchmark")


def _suite() -> AuthoredSuiteSpec:
    payload = json.loads((_ROOT / "suite.json").read_text(encoding="utf-8"))
    return load_authored_suite(
        _ROOT / "suite.json",
        expected_capability_catalog_hash=payload["capability_catalog_hash"],
        fixture_manifest_path=(
            _ROOT / "fixtures" / "core_novel" / "fixture-manifest.json"
        ),
    )


def test_portfolio_uses_eighteen_multi_step_and_eight_recovery_cases() -> None:
    suite = _suite()
    multi_step, recovery = build_benchmark_portfolio(suite)
    cases = {case.case_id: case for case in suite.cases}

    assert multi_step.entry_id == "multi_step"
    assert multi_step.case_count == 18
    assert len(multi_step.categories) == 9
    assert {len(category.case_ids) for category in multi_step.categories} == {2}
    assert all(
        cases[case_id].setup.fault_plan_ref is None for case_id in multi_step.case_ids
    )

    assert recovery.entry_id == "recovery"
    assert recovery.case_count == 8
    assert len(recovery.categories) == 4
    assert {len(category.case_ids) for category in recovery.categories} == {2}
    assert all(
        cases[case_id].setup.fault_plan_ref is not None for case_id in recovery.case_ids
    )


def test_invalid_invocation_has_auditable_definition() -> None:
    multi_step, recovery = build_benchmark_portfolio(_suite())

    for entry in (multi_step, recovery):
        assert len(entry.invalid_invocation_rules) == 4
        assert any("未允许" in rule for rule in entry.invalid_invocation_rules)
        assert any("调用次数" in rule for rule in entry.invalid_invocation_rules)
        assert any("先后顺序" in rule for rule in entry.invalid_invocation_rules)
        assert any("实际消费" in rule for rule in entry.invalid_invocation_rules)
