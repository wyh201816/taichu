"""Opik Dataset 必须与两个真实评测入口使用同一批权威合同。"""

from __future__ import annotations

from collections.abc import Sequence
import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from taichu.application.evaluations.general_agent_benchmark.suite_loader import (
    AuthoredSuiteSpec,
    load_authored_suite,
)
from taichu.infrastructure.evaluations.general_agent_benchmark.opik_integration import (
    OpikBenchmarkDatasetService,
    configure_opik_observability,
    opik,
)

_ROOT = Path("tests/fixtures/evaluations/general_writing_agent_benchmark")


class _FakeDataset:
    def __init__(self, name: str) -> None:
        self.name = name
        self.items: list[dict[str, Any]] = []

    def insert(self, items: Sequence[dict[str, Any]]) -> None:
        self.items.extend(items)


class _FakeClient:
    def __init__(self) -> None:
        self.datasets: dict[str, _FakeDataset] = {}
        self.descriptions: dict[str, str | None] = {}
        self.projects: dict[str, str | None] = {}
        self.flushed = False

    def get_or_create_dataset(
        self,
        name: str,
        description: str | None = None,
        project_name: str | None = None,
    ) -> _FakeDataset:
        self.descriptions[name] = description
        self.projects[name] = project_name
        return self.datasets.setdefault(name, _FakeDataset(name))

    def flush(self, timeout: int | None = None) -> bool:
        del timeout
        self.flushed = True
        return True


def _suite() -> AuthoredSuiteSpec:
    payload = json.loads((_ROOT / "suite.json").read_text(encoding="utf-8"))
    return load_authored_suite(
        _ROOT / "suite.json",
        expected_capability_catalog_hash=payload["capability_catalog_hash"],
        fixture_manifest_path=(
            _ROOT / "fixtures" / "core_novel" / "fixture-manifest.json"
        ),
    )


def test_sync_builds_two_reusable_datasets_from_authoritative_cases() -> None:
    client = _FakeClient()
    results = OpikBenchmarkDatasetService(
        client,
        project_name="太初评测",
    ).sync(_suite())

    assert [(item.entry_id, item.item_count) for item in results] == [
        ("multi_step", 18),
        ("recovery", 8),
    ]
    assert set(client.datasets) == {
        "taichu-general-agent-multi-step",
        "taichu-general-agent-recovery",
    }
    assert client.flushed is True
    assert set(client.projects.values()) == {"太初评测"}

    multi_step = client.datasets["taichu-general-agent-multi-step"].items
    recovery = client.datasets["taichu-general-agent-recovery"].items
    assert len({item["input"]["case_id"] for item in multi_step}) == 18
    assert len({item["metadata"]["category_id"] for item in multi_step}) == 9
    assert len({item["input"]["case_id"] for item in recovery}) == 8
    assert len({item["metadata"]["category_id"] for item in recovery}) == 4
    assert all(item["metadata"]["fault_plan_ref"] is None for item in multi_step)
    assert all(item["metadata"]["fault_plan_ref"] for item in recovery)


def test_dataset_item_carries_output_and_trajectory_contracts() -> None:
    client = _FakeClient()
    OpikBenchmarkDatasetService(client).sync(
        _suite(),
        entry_ids=("multi_step",),
    )

    item = client.datasets["taichu-general-agent-multi-step"].items[0]
    assert len(item["id"]) == 36
    assert UUID(item["id"]).version == 7
    assert (
        item["id"] == client.datasets["taichu-general-agent-multi-step"].items[0]["id"]
    )
    assert item["input"]["user_request"]
    assert item["expected_output"]["objective"]
    assert item["expected_output"]["terminal"]
    assert item["expected_output"]["behavior_assertions"]
    assert len(item["trajectory_contract"]["invalid_invocation_rules"]) == 4
    assert item["metadata"]["suite_content_hash"] == _suite().content_hash


def test_dataset_item_ids_are_stable_and_unique_per_entry_case() -> None:
    first_client = _FakeClient()
    second_client = _FakeClient()
    first = OpikBenchmarkDatasetService(first_client).sync(_suite())
    second = OpikBenchmarkDatasetService(second_client).sync(_suite())

    assert [item.dataset_item_ids for item in first] == [
        item.dataset_item_ids for item in second
    ]
    assert all(
        len(result.dataset_item_ids)
        == len(set(result.dataset_item_ids))
        == result.item_count
        for result in first
    )


def test_observability_is_explicitly_enabled_and_keeps_secrets_in_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active_states: list[bool] = []
    monkeypatch.setattr(opik, "set_tracing_active", active_states.append)

    configure_opik_observability(
        enabled=True,
        project_name="太初评测",
        url_override="http://127.0.0.1:5173/api",
        api_key="local-test-key",
        workspace="本地工作区",
    )

    assert active_states == [True]
    assert os.environ["OPIK_PROJECT_NAME"] == "太初评测"
    assert os.environ["OPIK_URL_OVERRIDE"] == "http://127.0.0.1:5173/api"
    assert os.environ["OPIK_API_KEY"] == "local-test-key"
    assert os.environ["OPIK_WORKSPACE"] == "本地工作区"
    assert os.environ["OPIK_TRACK_DISABLE"] == "false"

    configure_opik_observability(enabled=False)

    assert active_states[-1] is False
    assert os.environ["OPIK_TRACK_DISABLE"] == "true"
    assert "OPIK_API_KEY" not in os.environ
