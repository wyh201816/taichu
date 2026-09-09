"""Opik 只读投影只能展示当前 Suite 的完整实验。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from taichu.application.evaluations.general_agent_benchmark.portfolio import (
    BenchmarkPortfolioEntry,
    build_benchmark_portfolio,
)
from taichu.application.evaluations.general_agent_benchmark.suite_loader import (
    load_authored_suite,
)
from taichu.infrastructure.evaluations.general_agent_benchmark.opik_query import (
    OpikBenchmarkObservabilityQuery,
)

_ROOT = Path("tests/fixtures/evaluations/general_writing_agent_benchmark")
_SCORE_NAMES = (
    "案例总合同",
    "能力调用合同",
    "门禁·资源预算",
    "门禁·行为校验",
    "门禁·结果产物",
    "门禁·结束状态",
    "门禁·安全边界",
    "门禁·证据完整性",
)


def _suite():
    payload = json.loads((_ROOT / "suite.json").read_text(encoding="utf-8"))
    return load_authored_suite(
        _ROOT / "suite.json",
        expected_capability_catalog_hash=payload["capability_catalog_hash"],
        fixture_manifest_path=(
            _ROOT / "fixtures" / "core_novel" / "fixture-manifest.json"
        ),
    )


class _FakeDataset:
    def __init__(self, entry: BenchmarkPortfolioEntry) -> None:
        self.id = f"dataset-{entry.entry_id}"
        self.name = entry.opik_dataset_name
        self.dataset_items_count = entry.case_count
        self.version = SimpleNamespace(
            id=f"version-{entry.entry_id}",
            items_total=entry.case_count,
        )

    def get_current_version_name(self) -> str:
        return "v1"

    def get_version_info(self) -> Any:
        return self.version


class _FakeExperiment:
    def __init__(self, suite: Any, entry: BenchmarkPortfolioEntry) -> None:
        self.entry = entry
        self.data = SimpleNamespace(
            id=f"experiment-{entry.entry_id}",
            name=f"太初·{entry.name}·测试批次",
            dataset_id=f"dataset-{entry.entry_id}",
            dataset_version_id=f"version-{entry.entry_id}",
            project_id="project-1",
            metadata={
                "suite_content_hash": suite.content_hash,
                "entry_id": entry.entry_id,
                "case_ids": list(entry.case_ids),
            },
            status="completed",
            created_at=datetime(2026, 9, 1, 12, tzinfo=UTC),
            trace_count=entry.case_count,
            duration=SimpleNamespace(p50=480.5, p90=650.25),
            total_estimated_cost=None,
        )
        self.items = tuple(
            SimpleNamespace(
                dataset_item_data={
                    "input": {"case_id": case_id},
                    "metadata": {
                        "suite_content_hash": suite.content_hash,
                        "entry_id": entry.entry_id,
                    },
                },
                trace_id=f"trace-{case_id}",
                feedback_scores=[{"name": name, "value": 1.0} for name in _SCORE_NAMES],
            )
            for case_id in entry.case_ids
        )

    def get_experiment_data(self) -> Any:
        return self.data

    def get_items(self, max_results: int = 100) -> tuple[Any, ...]:
        return self.items[:max_results]


class _FakeClient:
    def __init__(
        self,
        suite: Any,
        entries: tuple[BenchmarkPortfolioEntry, ...],
    ) -> None:
        self.datasets = {
            entry.opik_dataset_name: _FakeDataset(entry) for entry in entries
        }
        self.experiments = {
            entry.opik_dataset_name: _FakeExperiment(suite, entry) for entry in entries
        }
        self.dataset_reads = 0

    def get_dataset(
        self,
        name: str,
        project_name: str | None = None,
    ) -> _FakeDataset:
        assert project_name == "太初评测"
        self.dataset_reads += 1
        return self.datasets[name]

    def get_dataset_experiments(
        self,
        dataset_name: str,
        *,
        max_results: int = 100,
        project_name: str | None = None,
    ) -> tuple[_FakeExperiment, ...]:
        del max_results
        assert project_name == "太初评测"
        return (self.experiments[dataset_name],)


def test_query_projects_current_datasets_experiments_scores_and_traces() -> None:
    suite = _suite()
    entries = build_benchmark_portfolio(suite)
    client = _FakeClient(suite, entries)
    query = OpikBenchmarkObservabilityQuery(
        client,
        suite=suite,
        entries=entries,
        project_name="太初评测",
        workspace="太初工作区",
        url_override="https://www.comet.com/opik/api",
    )

    first = asyncio.run(query.get_snapshot())
    second = asyncio.run(query.get_snapshot())

    assert first == second
    assert first.status == "available"
    assert first.project_url == (
        "https://www.comet.com/opik/%E5%A4%AA%E5%88%9D%E5%B7%A5%E4%BD%9C%E5%8C%BA/"
        "projects/project-1"
    )
    assert [
        (item.entry_id, item.passed_count, item.trace_count) for item in first.entries
    ] == [
        ("multi_step", 18, 18),
        ("recovery", 8, 8),
    ]
    assert all(len(item.scores) == 8 for item in first.entries)
    assert all(score.value == 1 for item in first.entries for score in item.scores)
    assert client.dataset_reads == 2


def test_query_rejects_experiment_from_stale_dataset_version() -> None:
    suite = _suite()
    entries = build_benchmark_portfolio(suite)
    client = _FakeClient(suite, entries)
    client.datasets[entries[0].opik_dataset_name].version.id = "new-version"
    query = OpikBenchmarkObservabilityQuery(
        client,
        suite=suite,
        entries=entries,
        project_name="太初评测",
        workspace="太初工作区",
        url_override="https://www.comet.com/opik/api",
    )

    snapshot = asyncio.run(query.get_snapshot())

    assert snapshot.status == "unavailable"
    assert snapshot.entries == ()
    assert "本地合同结果仍可正常查看" in snapshot.message
