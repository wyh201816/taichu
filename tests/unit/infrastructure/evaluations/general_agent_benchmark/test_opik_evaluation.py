"""Opik Experiment 必须执行当前入口并复用太初固定门禁评分。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from opik.evaluation.metrics import score_result

from taichu.application.evaluations.general_agent_benchmark.portfolio import (
    build_benchmark_portfolio,
)
from taichu.application.evaluations.general_agent_benchmark.suite_loader import (
    load_authored_suite,
)
from taichu.infrastructure.evaluations.general_agent_benchmark.opik_evaluation import (
    OpikBenchmarkExperimentService,
    score_opik_benchmark_case,
)

_ROOT = Path("tests/fixtures/evaluations/general_writing_agent_benchmark")


def _suite():
    payload = json.loads((_ROOT / "suite.json").read_text(encoding="utf-8"))
    return load_authored_suite(
        _ROOT / "suite.json",
        expected_capability_catalog_hash=payload["capability_catalog_hash"],
        fixture_manifest_path=(
            _ROOT / "fixtures" / "core_novel" / "fixture-manifest.json"
        ),
    )


class _FakeClient:
    def __init__(self) -> None:
        self.dataset = SimpleNamespace(name="taichu-general-agent-multi-step")
        self.requested: tuple[str, str | None] | None = None
        self.flushed = False

    def get_dataset(self, name: str, project_name: str | None = None):
        self.requested = (name, project_name)
        return self.dataset

    def flush(self, timeout: int | None = None) -> bool:
        del timeout
        self.flushed = True
        return True


def test_scorer_projects_total_invocation_and_six_gate_scores() -> None:
    dataset_item = {
        "input": {"case_id": "case-a"},
        "metadata": {"category_id": "outline", "category_name": "大纲拆解"},
    }
    task_outputs = {
        "case_id": "case-a",
        "passed": True,
        "invocation_contract_problem_count": 0,
        "gate_statuses": {
            "budget": "passed",
            "verifier": "passed",
            "artifact": "passed",
            "stop_reason": "passed",
            "security": "passed",
            "evidence": "passed",
        },
    }

    scores = score_opik_benchmark_case(dataset_item, task_outputs)

    assert len(scores) == 8
    assert {score.name for score in scores} == {
        "案例总合同",
        "能力调用合同",
        "门禁·资源预算",
        "门禁·行为校验",
        "门禁·结果产物",
        "门禁·结束状态",
        "门禁·安全边界",
        "门禁·证据完整性",
    }
    assert all(score.value == 1.0 for score in scores)


def test_scorer_rejects_cross_case_output() -> None:
    with pytest.raises(ValueError, match="案例身份不一致"):
        score_opik_benchmark_case(
            {
                "input": {"case_id": "case-a"},
                "metadata": {"category_id": "outline"},
            },
            {
                "case_id": "case-b",
                "passed": True,
                "invocation_contract_problem_count": 0,
                "gate_statuses": {},
            },
        )


def test_experiment_uses_exact_current_item_ids_and_single_worker() -> None:
    suite = _suite()
    entry = build_benchmark_portfolio(suite)[0]
    client = _FakeClient()
    captured: dict[str, Any] = {}

    def fake_evaluate(**kwargs: Any):
        captured.update(kwargs)
        results = [
            SimpleNamespace(
                test_case=SimpleNamespace(
                    task_output={"case_id": case_id, "passed": True}
                ),
                score_results=[score_result.ScoreResult(name="案例总合同", value=1.0)],
            )
            for case_id in entry.case_ids
        ]
        return SimpleNamespace(
            experiment_id="experiment-1",
            experiment_name=kwargs["experiment_name"],
            experiment_url="https://example.test/experiment-1",
            test_results=results,
        )

    item_ids = tuple(
        f"00000000-0000-0000-0000-{index:012x}" for index in range(entry.case_count)
    )
    result = OpikBenchmarkExperimentService(
        client,
        project_name="太初评测",
        evaluate=fake_evaluate,
    ).run(
        suite=suite,
        entry=entry,
        task=lambda item: item,
        dataset_item_ids=item_ids,
        batch_id="20260901T120000Z",
    )

    assert client.requested == (entry.opik_dataset_name, "太初评测")
    assert client.flushed is True
    assert captured["dataset_item_ids"] == list(item_ids)
    assert captured["task_threads"] == 1
    assert "project_name" not in captured
    assert captured["scoring_functions"] == [score_opik_benchmark_case]
    assert result.case_count == result.passed_count == entry.case_count
    assert result.failed_count == 0
    assert result.score_means == {"案例总合同": 1.0}
