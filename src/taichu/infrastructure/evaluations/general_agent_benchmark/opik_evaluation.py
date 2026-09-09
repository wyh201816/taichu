"""通过 Opik Dataset 正式执行固定基准并生成 Experiment。"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

import opik
from opik.evaluation.metrics import score_result
from pydantic import Field

from taichu.application.evaluations.general_agent_benchmark.canonical import (
    canonical_sha256,
)
from taichu.application.evaluations.general_agent_benchmark.models import (
    BenchmarkModel,
    CapabilityCatalogSnapshot,
    CaseConclusion,
    GateKind,
    GateStatus,
)
from taichu.application.evaluations.general_agent_benchmark.oracles import (
    TypedOracle,
)
from taichu.application.evaluations.general_agent_benchmark.portfolio import (
    BenchmarkPortfolioEntry,
)
from taichu.application.evaluations.general_agent_benchmark.suite_loader import (
    AuthoredCaseSpec,
    AuthoredSuiteSpec,
)
from taichu.application.evaluations.general_agent_benchmark.synthetic_suite import (
    SyntheticCaseBaselineResult,
    SyntheticSuiteBaselineResult,
    SyntheticSuiteRunner,
)
from taichu.infrastructure.evaluations.general_agent_benchmark.interactive_synthetic import (
    load_synthetic_oracle_catalog,
)
from taichu.infrastructure.evaluations.general_agent_benchmark.synthetic_environment import (
    SyntheticFixtureRuntime,
)

_GATE_SCORE_NAMES = {
    GateKind.BUDGET.value: "门禁·资源预算",
    GateKind.VERIFIER.value: "门禁·行为校验",
    GateKind.ARTIFACT.value: "门禁·结果产物",
    GateKind.STOP_REASON.value: "门禁·结束状态",
    GateKind.SECURITY.value: "门禁·安全边界",
    GateKind.EVIDENCE.value: "门禁·证据完整性",
}


class OpikExperimentDatasetPort(Protocol):
    name: str


class OpikExperimentClientPort(Protocol):
    def get_dataset(
        self,
        name: str,
        project_name: str | None = None,
    ) -> OpikExperimentDatasetPort: ...

    def flush(self, timeout: int | None = None) -> bool: ...


class OpikExperimentRunResult(BenchmarkModel):
    """一次 Opik Experiment 的本地最小审计投影。"""

    batch_id: str = Field(min_length=1, max_length=100)
    project_name: str = Field(min_length=1, max_length=200)
    entry_id: str = Field(min_length=1, max_length=100)
    dataset_name: str = Field(min_length=1, max_length=200)
    dataset_item_ids: tuple[str, ...] = Field(min_length=1)
    experiment_id: str = Field(min_length=1, max_length=200)
    experiment_name: str = Field(min_length=1, max_length=300)
    experiment_url: str | None = Field(default=None, max_length=2_000)
    suite_content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    case_count: int = Field(gt=0)
    passed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    score_means: dict[str, float]


class OpikCaseTaskOutput(BenchmarkModel):
    """上传给 Experiment 的脱敏案例结论，不复制正文或完整运行轨迹。"""

    case_id: str = Field(min_length=1, max_length=200)
    conclusion: str = Field(min_length=1, max_length=50)
    passed: bool
    gate_statuses: dict[str, str]
    invocation_count: int = Field(ge=0)
    invocation_contract_problem_count: int = Field(ge=0)
    evidence_reference_count: int = Field(ge=0)
    evidence_complete: bool
    case_execution_id: str | None = Field(default=None, max_length=200)
    runtime_run_id: str | None = Field(default=None, max_length=200)
    observation_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    final_answer_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    artifact_reference_count: int = Field(ge=0)
    problems: tuple[str, ...]


class SyntheticOpikBenchmarkTask:
    """Opik 每取出一个 Dataset item，就执行对应的真实 Synthetic 案例。"""

    def __init__(
        self,
        *,
        suite: AuthoredSuiteSpec,
        entry: BenchmarkPortfolioEntry,
        fixture_root: Path,
        claim_catalog_path: Path,
        workspaces_root: Path,
        mongodb_uri: str,
        capability_catalog: CapabilityCatalogSnapshot,
        batch_id: str,
    ) -> None:
        self._suite = suite
        self._entry = entry
        self._cases = {
            case.case_id: case
            for case in suite.cases
            if case.case_id in set(entry.case_ids)
        }
        if set(self._cases) != set(entry.case_ids):
            raise ValueError("Opik 入口案例与权威套件不一致。")
        oracle = TypedOracle(
            catalog=load_synthetic_oracle_catalog(
                suite=suite,
                fixture_root=fixture_root,
                claim_catalog_path=claim_catalog_path,
            )
        )
        runtime_identity = canonical_sha256(
            {
                "mode": "opik_evaluate",
                "batch_id": batch_id,
                "entry_id": entry.entry_id,
                "suite_content_hash": suite.content_hash,
                "capability_catalog_hash": capability_catalog.canonical_hash,
            }
        )
        self._runner = SyntheticSuiteRunner(
            runtime=SyntheticFixtureRuntime(
                sealed_fixture_root=fixture_root,
                workspaces_root=workspaces_root,
                mongodb_uri=mongodb_uri,
            ),
            runtime_config_identity=runtime_identity,
            capability_catalog=capability_catalog,
            oracle=oracle,
        )

    def __call__(self, dataset_item: dict[str, Any]) -> dict[str, Any]:
        case = self._validate_dataset_item(dataset_item)
        return asyncio.run(self._execute(case)).model_dump(mode="json")

    async def _execute(self, case: AuthoredCaseSpec) -> OpikCaseTaskOutput:
        result = await self._runner.run(
            self._suite,
            requested_case_ids=(case.case_id,),
        )
        if not isinstance(result, SyntheticSuiteBaselineResult):
            raise ValueError(result.message)
        if len(result.cases) != 1 or result.cases[0].case_id != case.case_id:
            raise RuntimeError("Opik 单案例执行返回了错误的案例集合。")
        return build_opik_case_task_output(result.cases[0])

    def _validate_dataset_item(
        self,
        dataset_item: Mapping[str, Any],
    ) -> AuthoredCaseSpec:
        input_data = _required_mapping(dataset_item, "input")
        metadata = _required_mapping(dataset_item, "metadata")
        case_id = input_data.get("case_id")
        if not isinstance(case_id, str) or case_id not in self._cases:
            raise ValueError("Opik Dataset item 不属于当前评测入口。")
        if metadata.get("suite_content_hash") != self._suite.content_hash:
            raise ValueError("Opik Dataset item 的套件身份已过期。")
        if metadata.get("entry_id") != self._entry.entry_id:
            raise ValueError("Opik Dataset item 的入口身份不一致。")
        case = self._cases[case_id]
        if input_data.get("user_request") != case.user_request_raw:
            raise ValueError("Opik Dataset item 与本地权威请求原文不一致。")
        return case


class OpikBenchmarkExperimentService:
    """选择本次套件身份对应的 Dataset items 并创建正式 Experiment。"""

    def __init__(
        self,
        client: OpikExperimentClientPort,
        *,
        project_name: str,
        evaluate: Callable[..., Any] = opik.evaluate,
    ) -> None:
        self._client = client
        self._project_name = project_name
        self._evaluate = evaluate

    def run(
        self,
        *,
        suite: AuthoredSuiteSpec,
        entry: BenchmarkPortfolioEntry,
        task: Callable[[dict[str, Any]], dict[str, Any]],
        dataset_item_ids: Sequence[str],
        batch_id: str,
    ) -> OpikExperimentRunResult:
        selected_ids = tuple(dict.fromkeys(dataset_item_ids))
        if len(selected_ids) != entry.case_count:
            raise ValueError("Opik Dataset 当前套件的 item 数量与入口合同不一致。")
        dataset = self._client.get_dataset(
            entry.opik_dataset_name,
            project_name=self._project_name,
        )
        experiment_name = f"太初·{entry.name}·{batch_id}"
        evaluation = self._evaluate(
            dataset=dataset,
            task=task,
            scoring_functions=[score_opik_benchmark_case],
            experiment_name=experiment_name,
            experiment_config={
                "suite_id": suite.suite_id,
                "suite_content_hash": suite.content_hash,
                "entry_id": entry.entry_id,
                "entry_name": entry.name,
                "case_ids": list(entry.case_ids),
                "runtime": "太初确定性合成运行时",
                "evaluation_source": "太初固定合同门禁",
            },
            verbose=1,
            task_threads=1,
            dataset_item_ids=list(selected_ids),
            experiment_tags=["taichu", entry.entry_id, "synthetic"],
        )
        self._client.flush()
        outputs = [item.test_case.task_output for item in evaluation.test_results]
        observed_case_ids = tuple(output.get("case_id") for output in outputs)
        if (
            len(outputs) != len(selected_ids)
            or len(set(observed_case_ids)) != len(observed_case_ids)
            or set(observed_case_ids) != set(entry.case_ids)
        ):
            raise RuntimeError("Opik Experiment 没有返回完整且唯一的入口案例集。")
        passed_count = sum(output.get("passed") is True for output in outputs)
        return OpikExperimentRunResult(
            batch_id=batch_id,
            project_name=self._project_name,
            entry_id=entry.entry_id,
            dataset_name=entry.opik_dataset_name,
            dataset_item_ids=selected_ids,
            experiment_id=evaluation.experiment_id,
            experiment_name=evaluation.experiment_name or experiment_name,
            experiment_url=evaluation.experiment_url,
            suite_content_hash=suite.content_hash,
            case_count=len(outputs),
            passed_count=passed_count,
            failed_count=len(outputs) - passed_count,
            score_means=_score_means(evaluation.test_results),
        )


def build_opik_case_task_output(
    result: SyntheticCaseBaselineResult,
) -> OpikCaseTaskOutput:
    observation = result.case_observation
    gate_statuses = {gate.gate_kind.value: gate.status.value for gate in result.gates}
    evidence_complete = (
        observation is not None
        and result.normalization_artifact is not None
        and bool(result.evidence_ids)
        and gate_statuses.get(GateKind.EVIDENCE.value) == GateStatus.PASSED.value
    )
    final_answer_sha256 = None
    artifact_reference_count = 0
    runtime_run_id = None
    case_execution_id = None
    if observation is not None:
        final_answer_sha256 = (
            observation.final_answer.content_sha256
            if observation.final_answer is not None
            else None
        )
        artifact_reference_count = len(observation.artifacts)
        runtime_run_id = observation.owner.run_id
        case_execution_id = observation.owner.case_execution_id
    return OpikCaseTaskOutput(
        case_id=result.case_id,
        conclusion=result.conclusion.value,
        passed=result.conclusion is CaseConclusion.PASSED,
        gate_statuses=gate_statuses,
        invocation_count=len(result.invocations),
        invocation_contract_problem_count=len(result.problems),
        evidence_reference_count=len(result.evidence_ids),
        evidence_complete=evidence_complete,
        case_execution_id=case_execution_id,
        runtime_run_id=runtime_run_id,
        observation_sha256=result.observation_sha256,
        final_answer_sha256=final_answer_sha256,
        artifact_reference_count=artifact_reference_count,
        problems=result.problems,
    )


def score_opik_benchmark_case(
    dataset_item: dict[str, Any],
    task_outputs: dict[str, Any],
) -> list[score_result.ScoreResult]:
    """把太初既有确定性门禁投影为 Opik 评分，不重复调用 LLM 裁判。"""

    expected_case_id = _required_mapping(dataset_item, "input").get("case_id")
    actual_case_id = task_outputs.get("case_id")
    if expected_case_id != actual_case_id:
        raise ValueError("Opik 评分输入与任务输出的案例身份不一致。")
    metadata = _required_mapping(dataset_item, "metadata")
    score_metadata = {
        "case_id": actual_case_id,
        "category_id": metadata.get("category_id"),
        "category_name": metadata.get("category_name"),
    }
    passed = task_outputs.get("passed") is True
    invocation_contract_passed = (
        task_outputs.get("invocation_contract_problem_count") == 0
    )
    scores = [
        score_result.ScoreResult(
            name="案例总合同",
            value=1.0 if passed else 0.0,
            reason="全部固定门禁通过。" if passed else "至少一项固定门禁未通过。",
            metadata=score_metadata,
        ),
        score_result.ScoreResult(
            name="能力调用合同",
            value=1.0 if invocation_contract_passed else 0.0,
            reason=(
                "未发现越权、超次、乱序或未消费结果的能力调用。"
                if invocation_contract_passed
                else "能力调用轨迹违反固定合同。"
            ),
            metadata=score_metadata,
        ),
    ]
    gate_statuses = task_outputs.get("gate_statuses")
    if not isinstance(gate_statuses, Mapping):
        raise ValueError("Opik 任务输出缺少门禁状态。")
    for gate_kind, score_name in _GATE_SCORE_NAMES.items():
        gate_passed = gate_statuses.get(gate_kind) == GateStatus.PASSED.value
        scores.append(
            score_result.ScoreResult(
                name=score_name,
                value=1.0 if gate_passed else 0.0,
                reason="该门禁通过。" if gate_passed else "该门禁未通过。",
                metadata=score_metadata,
            )
        )
    return scores


def _required_mapping(
    source: Mapping[str, Any],
    key: str,
) -> Mapping[str, Any]:
    value = source.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"Opik Dataset item 缺少 {key} 对象。")
    return value


def _score_means(test_results: Sequence[Any]) -> dict[str, float]:
    values: defaultdict[str, list[float]] = defaultdict(list)
    for test_result in test_results:
        for score in test_result.score_results:
            values[score.name].append(float(score.value))
    return {
        name: sum(items) / len(items) for name, items in sorted(values.items()) if items
    }


__all__ = [
    "OpikBenchmarkExperimentService",
    "OpikCaseTaskOutput",
    "OpikExperimentRunResult",
    "SyntheticOpikBenchmarkTask",
    "build_opik_case_task_output",
    "score_opik_benchmark_case",
]
