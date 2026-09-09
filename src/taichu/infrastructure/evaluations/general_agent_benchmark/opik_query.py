"""把 Opik Dataset、Experiment、评分与 Trace 投影为前端只读快照。"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
import logging
from time import monotonic
from typing import Any, Protocol, cast
from urllib.parse import quote, urlencode

import opik

from taichu.application.evaluations.general_agent_benchmark.observability import (
    BenchmarkObservabilityEntry,
    BenchmarkObservabilityScore,
    BenchmarkObservabilitySnapshot,
    BenchmarkObservabilityStatus,
)
from taichu.application.evaluations.general_agent_benchmark.portfolio import (
    BenchmarkPortfolioEntry,
)
from taichu.application.evaluations.general_agent_benchmark.suite_loader import (
    AuthoredSuiteSpec,
)

logger = logging.getLogger(__name__)

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


class OpikExperimentPort(Protocol):
    def get_experiment_data(self) -> Any: ...

    def get_items(self, max_results: int = 100) -> Sequence[Any]: ...


class OpikDatasetPort(Protocol):
    id: str
    name: str
    dataset_items_count: int

    def get_current_version_name(self) -> str | None: ...

    def get_version_info(self) -> Any: ...


class OpikQueryClientPort(Protocol):
    def get_dataset(
        self,
        name: str,
        project_name: str | None = None,
    ) -> OpikDatasetPort: ...

    def get_dataset_experiments(
        self,
        dataset_name: str,
        *,
        max_results: int = 100,
        project_name: str | None = None,
    ) -> Sequence[OpikExperimentPort]: ...


class OpikBenchmarkObservabilityQuery:
    """按当前 Suite 身份读取最新完整 Experiment，并短时缓存结果。"""

    def __init__(
        self,
        client: OpikQueryClientPort,
        *,
        suite: AuthoredSuiteSpec,
        entries: tuple[BenchmarkPortfolioEntry, ...],
        project_name: str,
        workspace: str,
        url_override: str = "",
        ttl_seconds: float = 60,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("Opik 查询缓存时间必须大于零。")
        self._client = client
        self._suite = suite
        self._entries = entries
        self._project_name = project_name
        self._workspace = workspace
        self._url_override = url_override
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = asyncio.Lock()
        self._cached_at: float | None = None
        self._cached_snapshot: BenchmarkObservabilitySnapshot | None = None

    @classmethod
    def create(
        cls,
        *,
        suite: AuthoredSuiteSpec,
        entries: tuple[BenchmarkPortfolioEntry, ...],
        project_name: str,
        workspace: str,
        api_key: str,
        url_override: str = "",
        ttl_seconds: float = 60,
    ) -> OpikBenchmarkObservabilityQuery:
        client = cast(
            OpikQueryClientPort,
            opik.Opik(
                project_name=project_name,
                workspace=workspace or None,
                host=url_override or None,
                api_key=api_key or None,
                batching=False,
            ),
        )
        return cls(
            client,
            suite=suite,
            entries=entries,
            project_name=project_name,
            workspace=workspace,
            url_override=url_override,
            ttl_seconds=ttl_seconds,
        )

    async def get_snapshot(self) -> BenchmarkObservabilitySnapshot:
        cached = self._fresh_cache()
        if cached is not None:
            return cached
        async with self._lock:
            cached = self._fresh_cache()
            if cached is not None:
                return cached
            try:
                snapshot = await asyncio.to_thread(self._load_snapshot)
            except Exception:
                logger.warning("读取 Opik 云端评测结果失败。", exc_info=True)
                snapshot = BenchmarkObservabilitySnapshot(
                    status=BenchmarkObservabilityStatus.UNAVAILABLE,
                    project_name=self._project_name,
                    suite_content_hash=self._suite.content_hash,
                    refreshed_at=datetime.now(UTC),
                    message=("Opik 云端结果暂时不可读取；本地合同结果仍可正常查看。"),
                )
            self._cached_snapshot = snapshot
            self._cached_at = self._clock()
            return snapshot

    def _fresh_cache(self) -> BenchmarkObservabilitySnapshot | None:
        if self._cached_at is None or self._cached_snapshot is None:
            return None
        if self._clock() - self._cached_at >= self._ttl_seconds:
            return None
        return self._cached_snapshot

    def _load_snapshot(self) -> BenchmarkObservabilitySnapshot:
        projected = tuple(self._load_entry(entry) for entry in self._entries)
        project_ids = {item[1] for item in projected}
        if len(project_ids) != 1:
            raise ValueError("Opik 两个评测入口不属于同一个项目。")
        project_id = project_ids.pop()
        root = _opik_ui_root(
            workspace=self._workspace,
            url_override=self._url_override,
        )
        return BenchmarkObservabilitySnapshot(
            status=BenchmarkObservabilityStatus.AVAILABLE,
            project_name=self._project_name,
            project_url=f"{root}/projects/{quote(project_id, safe='')}",
            suite_content_hash=self._suite.content_hash,
            refreshed_at=datetime.now(UTC),
            message="已校验当前套件对应的 Opik Dataset 与最新完整 Experiment。",
            entries=tuple(item[0] for item in projected),
        )

    def _load_entry(
        self,
        entry: BenchmarkPortfolioEntry,
    ) -> tuple[BenchmarkObservabilityEntry, str]:
        dataset = self._client.get_dataset(
            entry.opik_dataset_name,
            project_name=self._project_name,
        )
        version = dataset.get_version_info()
        version_name = dataset.get_current_version_name()
        if version is None or not version_name:
            raise ValueError("Opik Dataset 缺少当前版本。")
        if (
            dataset.dataset_items_count != entry.case_count
            or getattr(version, "items_total", None) != entry.case_count
        ):
            raise ValueError("Opik Dataset 案例数量与当前入口不一致。")

        experiment, experiment_data = self._select_experiment(entry)
        if getattr(experiment_data, "dataset_id", None) != dataset.id:
            raise ValueError("Opik Experiment 引用了其他 Dataset。")
        if getattr(experiment_data, "dataset_version_id", None) != getattr(
            version, "id", None
        ):
            raise ValueError("Opik Experiment 不是基于 Dataset 当前版本运行。")

        items = tuple(experiment.get_items(max_results=entry.case_count + 1))
        if len(items) != entry.case_count:
            raise ValueError("Opik Experiment 案例结果数量不完整。")
        scores, passed_count, trace_count = _validate_experiment_items(
            items,
            entry=entry,
            suite_content_hash=self._suite.content_hash,
        )
        declared_trace_count = getattr(experiment_data, "trace_count", None)
        if declared_trace_count != trace_count:
            raise ValueError("Opik Experiment 的 Trace 数量与案例结果不一致。")

        project_id = _required_string(experiment_data, "project_id")
        experiment_id = _required_string(experiment_data, "id")
        experiment_name = _required_string(experiment_data, "name")
        created_at = getattr(experiment_data, "created_at", None)
        if not isinstance(created_at, datetime):
            raise ValueError("Opik Experiment 缺少创建时间。")
        duration = getattr(experiment_data, "duration", None)
        root = _opik_ui_root(
            workspace=self._workspace,
            url_override=self._url_override,
        )
        experiment_query = urlencode({"experiments": f'["{experiment_id}"]'})
        return (
            BenchmarkObservabilityEntry(
                entry_id=entry.entry_id,
                dataset_name=dataset.name,
                dataset_id=dataset.id,
                dataset_url=(
                    f"{root}/projects/{quote(project_id, safe='')}/datasets/"
                    f"{quote(dataset.id, safe='')}/items"
                ),
                dataset_version=version_name,
                dataset_item_count=dataset.dataset_items_count,
                experiment_id=experiment_id,
                experiment_name=experiment_name,
                experiment_url=(
                    f"{root}/experiments/{quote(dataset.id, safe='')}/compare?"
                    f"{experiment_query}"
                ),
                traces_url=(f"{root}/projects/{quote(project_id, safe='')}/traces"),
                experiment_status=_required_string(experiment_data, "status"),
                created_at=created_at,
                case_count=entry.case_count,
                passed_count=passed_count,
                trace_count=trace_count,
                duration_p50_ms=_optional_nonnegative_float(duration, "p50"),
                duration_p90_ms=_optional_nonnegative_float(duration, "p90"),
                total_estimated_cost=_optional_nonnegative_float(
                    experiment_data, "total_estimated_cost"
                ),
                scores=scores,
            ),
            project_id,
        )

    def _select_experiment(
        self,
        entry: BenchmarkPortfolioEntry,
    ) -> tuple[OpikExperimentPort, Any]:
        candidates: list[tuple[datetime, OpikExperimentPort, Any]] = []
        for experiment in self._client.get_dataset_experiments(
            entry.opik_dataset_name,
            max_results=50,
            project_name=self._project_name,
        ):
            data = experiment.get_experiment_data()
            metadata = getattr(data, "metadata", None)
            created_at = getattr(data, "created_at", None)
            if not isinstance(metadata, Mapping) or not isinstance(
                created_at, datetime
            ):
                continue
            if (
                getattr(data, "status", None) == "completed"
                and metadata.get("suite_content_hash") == self._suite.content_hash
                and metadata.get("entry_id") == entry.entry_id
                and tuple(metadata.get("case_ids", ())) == entry.case_ids
            ):
                candidates.append((created_at, experiment, data))
        if not candidates:
            raise ValueError("Opik 中没有当前套件的完整已完成 Experiment。")
        _, experiment, data = max(candidates, key=lambda item: item[0])
        return experiment, data


def _validate_experiment_items(
    items: Sequence[Any],
    *,
    entry: BenchmarkPortfolioEntry,
    suite_content_hash: str,
) -> tuple[tuple[BenchmarkObservabilityScore, ...], int, int]:
    case_ids: list[str] = []
    trace_ids: set[str] = set()
    score_values: defaultdict[str, list[float]] = defaultdict(list)
    passed_count = 0
    for item in items:
        data = getattr(item, "dataset_item_data", None)
        if not isinstance(data, Mapping):
            raise ValueError("Opik Experiment item 缺少 Dataset 快照。")
        input_data = data.get("input")
        metadata = data.get("metadata")
        if not isinstance(input_data, Mapping) or not isinstance(metadata, Mapping):
            raise ValueError("Opik Experiment item 缺少输入或元数据。")
        case_id = input_data.get("case_id")
        if not isinstance(case_id, str):
            raise ValueError("Opik Experiment item 缺少案例身份。")
        if (
            metadata.get("suite_content_hash") != suite_content_hash
            or metadata.get("entry_id") != entry.entry_id
        ):
            raise ValueError("Opik Experiment item 的套件身份已过期。")
        case_ids.append(case_id)
        trace_id = getattr(item, "trace_id", None)
        if not isinstance(trace_id, str) or not trace_id:
            raise ValueError("Opik Experiment item 缺少 Trace。")
        trace_ids.add(trace_id)

        feedback_scores = getattr(item, "feedback_scores", None)
        if not isinstance(feedback_scores, Sequence):
            raise ValueError("Opik Experiment item 缺少评分。")
        item_scores: dict[str, float] = {}
        fixed_score_count = 0
        for score in feedback_scores:
            if not isinstance(score, Mapping):
                raise ValueError("Opik Experiment item 评分格式无效。")
            name = score.get("name")
            value = score.get("value")
            if name in _SCORE_NAMES:
                if (
                    not isinstance(value, int | float)
                    or isinstance(value, bool)
                    or not 0 <= value <= 1
                ):
                    raise ValueError("Opik Experiment item 固定评分值无效。")
                fixed_score_count += 1
                item_scores[str(name)] = float(value)
        if tuple(
            name for name in _SCORE_NAMES if name in item_scores
        ) != _SCORE_NAMES or fixed_score_count != len(_SCORE_NAMES):
            raise ValueError("Opik Experiment item 缺少固定评分维度。")
        for name in _SCORE_NAMES:
            score_values[name].append(item_scores[name])
        if item_scores["案例总合同"] == 1:
            passed_count += 1

    if tuple(case_ids) != entry.case_ids and set(case_ids) != set(entry.case_ids):
        raise ValueError("Opik Experiment 案例集合与当前入口不一致。")
    if len(case_ids) != len(set(case_ids)) or len(trace_ids) != len(items):
        raise ValueError("Opik Experiment 出现重复案例或重复 Trace。")
    scores = tuple(
        BenchmarkObservabilityScore(
            name=name,
            value=sum(score_values[name]) / len(score_values[name]),
        )
        for name in _SCORE_NAMES
    )
    return scores, passed_count, len(trace_ids)


def _required_string(source: Any, field: str) -> str:
    value = getattr(source, field, None)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Opik 结果缺少 {field}。")
    return value


def _optional_nonnegative_float(source: Any, field: str) -> float | None:
    value = getattr(source, field, None)
    if value is None:
        return None
    if not isinstance(value, int | float) or value < 0:
        raise ValueError(f"Opik 结果中的 {field} 无效。")
    return float(value)


def _opik_ui_root(*, workspace: str, url_override: str) -> str:
    normalized = url_override.rstrip("/")
    if not normalized or normalized == "https://www.comet.com/opik/api":
        if not workspace:
            raise ValueError("Opik Cloud 缺少 Workspace。")
        return f"https://www.comet.com/opik/{quote(workspace, safe='')}"
    if normalized.endswith("/api"):
        normalized = normalized[:-4]
    if not normalized:
        raise ValueError("Opik 页面地址无效。")
    return normalized


__all__ = ["OpikBenchmarkObservabilityQuery"]
