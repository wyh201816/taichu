"""Opik 链路追踪开关与固定 Benchmark Dataset 同步。"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import UUID

import opik
from pydantic import Field

from taichu.config import Settings
from taichu.application.evaluations.general_agent_benchmark.canonical import (
    canonical_sha256,
)
from taichu.application.evaluations.general_agent_benchmark.models import (
    BenchmarkModel,
)
from taichu.application.evaluations.general_agent_benchmark.portfolio import (
    BenchmarkEntryId,
    BenchmarkPortfolioEntry,
    build_benchmark_portfolio,
)
from taichu.application.evaluations.general_agent_benchmark.suite_loader import (
    AuthoredSuiteSpec,
    load_authored_suite,
)

DEFAULT_OPIK_PROJECT_NAME = "taichu-general-agent-benchmark"
_DEFAULT_SUITE_PATH = (
    Path(__file__).resolve().parents[5]
    / "tests"
    / "fixtures"
    / "evaluations"
    / "general_writing_agent_benchmark"
    / "suite.json"
)
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_OPIK_DATASET_ITEM_EPOCH_MS = 1_788_220_800_000


def _environment_enables_tracing() -> bool:
    enabled = os.getenv("OPIK_ENABLED", "").strip().lower()
    if enabled:
        return enabled in _TRUE_VALUES
    disabled = os.getenv("OPIK_TRACK_DISABLE", "").strip().lower()
    return disabled in {"0", "false", "no", "off"}


# Benchmark 追踪默认关闭。应用组合根或显式环境变量会在首个案例运行前开启；
# Dataset 同步不依赖该开关。
opik.set_tracing_active(_environment_enables_tracing())


def configure_opik_observability(
    *,
    enabled: bool,
    project_name: str = DEFAULT_OPIK_PROJECT_NAME,
    url_override: str = "",
    api_key: str = "",
    workspace: str = "",
) -> None:
    """在应用启动时配置 Opik，但不把凭据写入 SDK 配置文件。"""

    _set_environment("OPIK_PROJECT_NAME", project_name)
    _set_environment("OPIK_URL_OVERRIDE", url_override)
    _set_environment("OPIK_API_KEY", api_key)
    _set_environment("OPIK_WORKSPACE", workspace)
    _set_environment("OPIK_ENVIRONMENT", "benchmark")
    _set_environment("OPIK_TRACK_DISABLE", "false" if enabled else "true")
    opik.set_tracing_active(enabled)


def update_current_span(
    *,
    name: str,
    metadata: dict[str, Any],
    output: dict[str, Any] | None = None,
    model: str | None = None,
    usage: dict[str, int] | None = None,
) -> None:
    """只更新当前 ``@opik.track`` Span；关闭追踪时是安全空操作。"""

    if opik.opik_context.get_current_span_data() is None:
        return
    opik.opik_context.update_current_span(
        name=name,
        metadata=metadata,
        output=output,
        model=model,
        usage=usage,
    )


class OpikDatasetPort(Protocol):
    name: str

    def insert(self, items: Sequence[dict[str, Any]]) -> None: ...


class OpikClientPort(Protocol):
    def get_or_create_dataset(
        self,
        name: str,
        description: str | None = None,
        project_name: str | None = None,
    ) -> OpikDatasetPort: ...

    def flush(self, timeout: int | None = None) -> bool: ...


class OpikDatasetSyncResult(BenchmarkModel):
    project_name: str = Field(min_length=1)
    dataset_name: str = Field(min_length=1)
    entry_id: BenchmarkEntryId
    item_count: int = Field(gt=0)
    dataset_item_ids: tuple[str, ...] = Field(min_length=1)
    suite_content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class OpikBenchmarkDatasetService:
    """把两个展示入口投影为可重复同步、内容去重的 Opik Dataset。"""

    def __init__(
        self,
        client: OpikClientPort,
        *,
        project_name: str = DEFAULT_OPIK_PROJECT_NAME,
    ) -> None:
        self._client = client
        self._project_name = project_name

    def sync(
        self,
        suite: AuthoredSuiteSpec,
        *,
        entry_ids: Iterable[BenchmarkEntryId] = ("multi_step", "recovery"),
    ) -> tuple[OpikDatasetSyncResult, ...]:
        requested = tuple(dict.fromkeys(entry_ids))
        entries = {entry.entry_id: entry for entry in build_benchmark_portfolio(suite)}
        unknown = tuple(entry_id for entry_id in requested if entry_id not in entries)
        if unknown:
            raise ValueError("未知的 Opik 评测入口：" + "、".join(unknown))

        results: list[OpikDatasetSyncResult] = []
        for entry_id in requested:
            entry = entries[entry_id]
            items = build_opik_dataset_items(suite, entry)
            dataset = self._client.get_or_create_dataset(
                entry.opik_dataset_name,
                description=(
                    f"{entry.summary} 数据来自 Suite {suite.suite_id}，"
                    "相同内容重复同步由 Opik 内容哈希去重。"
                ),
                project_name=self._project_name,
            )
            dataset.insert(items)
            results.append(
                OpikDatasetSyncResult(
                    project_name=self._project_name,
                    dataset_name=dataset.name,
                    entry_id=entry.entry_id,
                    item_count=len(items),
                    dataset_item_ids=tuple(str(item["id"]) for item in items),
                    suite_content_hash=suite.content_hash,
                )
            )
        self._client.flush()
        return tuple(results)


def build_opik_dataset_items(
    suite: AuthoredSuiteSpec,
    entry: BenchmarkPortfolioEntry,
) -> list[dict[str, Any]]:
    """构建 Opik 可直接用于实验的输入、期望与轨迹合同。"""

    cases = {case.case_id: case for case in suite.cases}
    category_by_case = {
        case_id: category
        for category in entry.categories
        for case_id in category.case_ids
    }
    items: list[dict[str, Any]] = []
    for case_id in entry.case_ids:
        case = cases[case_id]
        category = category_by_case[case_id]
        items.append(
            {
                "id": _dataset_item_id(entry, case.case_id),
                "input": {
                    "user_request": case.user_request_raw,
                    "case_id": case.case_id,
                },
                "expected_output": {
                    "objective": case.scenario.objective,
                    "target_final_artifact": case.scenario.target_final_artifact,
                    "terminal": case.expected_terminal.model_dump(mode="json"),
                    "behavior_assertions": [
                        assertion.description for assertion in case.behavior_assertions
                    ],
                },
                "trajectory_contract": {
                    "required_invocations": [
                        invocation.model_dump(mode="json")
                        for invocation in case.required_invocations
                    ],
                    "invalid_invocation_rules": list(entry.invalid_invocation_rules),
                },
                "metadata": {
                    "suite_id": suite.suite_id,
                    "suite_content_hash": suite.content_hash,
                    "entry_id": entry.entry_id,
                    "entry_name": entry.name,
                    "category_id": category.category_id,
                    "category_name": category.name,
                    "case_name": case.name,
                    "tracks": [track.value for track in case.applicable_tracks],
                    "fault_plan_ref": case.setup.fault_plan_ref,
                },
            }
        )
    return items


def main() -> None:
    settings = Settings()
    parser = argparse.ArgumentParser(
        description="同步太初固定合同，并可通过 Opik 正式运行 Experiment。"
    )
    parser.add_argument(
        "--entry",
        choices=("all", "multi_step", "recovery"),
        default="all",
        help="只同步多步骤任务、异常恢复，或同步全部入口。",
    )
    parser.add_argument(
        "--project",
        default=None,
        help="Opik 项目名称。",
    )
    parser.add_argument(
        "--suite",
        type=Path,
        default=_DEFAULT_SUITE_PATH,
        help="固定 Suite JSON 路径。",
    )
    parser.add_argument(
        "--run-evaluations",
        action="store_true",
        help="同步后执行两个入口并生成 Opik Experiment。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只校验和展示同步内容，不连接 Opik。",
    )
    arguments = parser.parse_args()
    project_name = arguments.project or settings.opik_project_name
    suite = _load_suite(arguments.suite)
    selected: tuple[BenchmarkEntryId, ...] = (
        ("multi_step", "recovery") if arguments.entry == "all" else (arguments.entry,)
    )
    entries = {entry.entry_id: entry for entry in build_benchmark_portfolio(suite)}
    if arguments.dry_run:
        for entry_id in selected:
            entry = entries[entry_id]
            print(
                f"已校验：{entry.name}，{entry.case_count} 条，"
                f"{len(entry.categories)} 类，Dataset={entry.opik_dataset_name}。"
            )
        if arguments.run_evaluations:
            print("已校验：将从 Opik Dataset 逐案执行并生成正式 Experiment。")
        return

    configure_opik_observability(
        enabled=True,
        project_name=project_name,
        url_override=settings.opik_url_override,
        api_key=settings.opik_api_key.get_secret_value(),
        workspace=settings.opik_workspace,
    )
    client = cast(
        OpikClientPort,
        opik.Opik(
            project_name=project_name,
            workspace=settings.opik_workspace or None,
            host=settings.opik_url_override or None,
            api_key=settings.opik_api_key.get_secret_value() or None,
        ),
    )
    results = OpikBenchmarkDatasetService(
        client,
        project_name=project_name,
    ).sync(suite, entry_ids=selected)
    for result in results:
        print(
            f"同步完成：{result.dataset_name}，{result.item_count} 条，"
            f"项目={result.project_name}。"
        )
    if not arguments.run_evaluations:
        return

    from taichu.infrastructure.evaluations.general_agent_benchmark.opik_evaluation import (
        OpikBenchmarkExperimentService,
        SyntheticOpikBenchmarkTask,
    )
    from taichu.infrastructure.evaluations.general_agent_benchmark.runtime_factory import (
        production_capability_catalog_snapshot,
    )

    batch_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    capability_catalog = production_capability_catalog_snapshot()
    entries = {entry.entry_id: entry for entry in build_benchmark_portfolio(suite)}
    experiment_service = OpikBenchmarkExperimentService(
        cast(Any, client),
        project_name=project_name,
    )
    for sync_result in results:
        entry = entries[sync_result.entry_id]
        task = SyntheticOpikBenchmarkTask(
            suite=suite,
            entry=entry,
            fixture_root=arguments.suite.parent / "fixtures" / "core_novel",
            claim_catalog_path=arguments.suite.parent / "claim-catalog.json",
            workspaces_root=(
                settings.project_assets_dir
                / "derived"
                / "general_agent_benchmarks"
                / "opik-workspaces"
                / batch_id
                / entry.entry_id
            ),
            mongodb_uri=settings.mongodb_uri,
            capability_catalog=capability_catalog,
            batch_id=batch_id,
        )
        experiment = experiment_service.run(
            suite=suite,
            entry=entry,
            task=task,
            dataset_item_ids=sync_result.dataset_item_ids,
            batch_id=batch_id,
        )
        print(
            f"评测完成：{entry.name}，{experiment.passed_count}/"
            f"{experiment.case_count} 通过，Experiment={experiment.experiment_url}。"
        )


def _load_suite(path: Path) -> AuthoredSuiteSpec:
    payload = json.loads(path.read_text(encoding="utf-8"))
    catalog_hash = payload.get("capability_catalog_hash")
    if not isinstance(catalog_hash, str):
        raise ValueError("Suite 缺少能力目录内容身份。")
    return load_authored_suite(
        path,
        expected_capability_catalog_hash=catalog_hash,
        fixture_manifest_path=(
            path.parent / "fixtures" / "core_novel" / "fixture-manifest.json"
        ),
    )


def _set_environment(name: str, value: str) -> None:
    if value:
        os.environ[name] = value
    else:
        os.environ.pop(name, None)


def _dataset_item_id(
    entry: BenchmarkPortfolioEntry,
    case_id: str,
) -> str:
    """同一入口与案例始终覆盖同一 Opik item，避免旧套件残留参与评测。"""

    digest = bytes.fromhex(
        canonical_sha256(
            {
                "namespace": "taichu.opik.dataset_item",
                "dataset_name": entry.opik_dataset_name,
                "case_id": case_id,
            }
        )
    )
    raw = bytearray(_OPIK_DATASET_ITEM_EPOCH_MS.to_bytes(6, "big") + digest[:10])
    raw[6] = (raw[6] & 0x0F) | 0x70
    raw[8] = (raw[8] & 0x3F) | 0x80
    return str(UUID(bytes=bytes(raw)))


__all__ = [
    "DEFAULT_OPIK_PROJECT_NAME",
    "OpikBenchmarkDatasetService",
    "OpikDatasetSyncResult",
    "build_opik_dataset_items",
    "configure_opik_observability",
    "main",
    "opik",
    "update_current_span",
]
