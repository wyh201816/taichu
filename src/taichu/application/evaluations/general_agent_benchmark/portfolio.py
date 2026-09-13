"""面向评测工作台与 Opik Dataset 的可解释案例入口。"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from taichu.application.evaluations.general_agent_benchmark.models import (
    BenchmarkModel,
    StableId,
)
from taichu.application.evaluations.general_agent_benchmark.suite_loader import (
    AuthoredSuiteSpec,
)

BenchmarkEntryId = Literal["multi_step", "recovery"]


class BenchmarkScenarioCategory(BenchmarkModel):
    """一个入口下可向作者解释的稳定场景分类。"""

    category_id: StableId
    name: str = Field(min_length=1, max_length=100)
    purpose: str = Field(min_length=1, max_length=500)
    case_ids: tuple[StableId, ...] = Field(min_length=1)


class BenchmarkPortfolioEntry(BenchmarkModel):
    """同一权威 Suite 的一个聚焦展示与 Dataset 投影。"""

    entry_id: BenchmarkEntryId
    name: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=500)
    opik_dataset_name: str = Field(min_length=1, max_length=200)
    case_count: int = Field(gt=0)
    case_ids: tuple[StableId, ...] = Field(min_length=1)
    categories: tuple[BenchmarkScenarioCategory, ...] = Field(min_length=1)
    invalid_invocation_rules: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _categories_cover_entry_once(self) -> BenchmarkPortfolioEntry:
        declared = tuple(
            case_id for category in self.categories for case_id in category.case_ids
        )
        if len(declared) != len(set(declared)):
            raise ValueError("评测入口分类不得重复包含同一案例。")
        if declared != self.case_ids:
            raise ValueError("评测入口分类必须按声明顺序完整覆盖案例。")
        if self.case_count != len(self.case_ids):
            raise ValueError("评测入口 case_count 必须由案例列表派生。")
        return self


_INVALID_INVOCATION_RULES = (
    "调用了当前合同未允许的工具或专业智能体。",
    "同一能力的调用次数超过合同上限。",
    "调用的先后顺序、并行关系或父子关系不符合合同。",
    "工具结果没有被下游步骤或最终答案实际消费。",
)

_MULTI_STEP_CATEGORIES = (
    BenchmarkScenarioCategory(
        category_id="outline_decomposition",
        name="拆解小说大纲",
        purpose="验证结构读取、章节覆盖分析，以及结构创建后的精确更新。",
        case_ids=(
            "structure_coverage_read",
            "structure_create_update",
        ),
    ),
    BenchmarkScenarioCategory(
        category_id="character_and_world_design",
        name="生成人物与世界设定",
        purpose="验证摘要、世界观与人物分支协作，并把确认设定精确写入知识库。",
        case_ids=(
            "summary_world_character",
            "knowledge_create_update",
        ),
    ),
    BenchmarkScenarioCategory(
        category_id="chapter_continuation",
        name="分章节续写",
        purpose="验证剧情架构、场景规划、正文草稿与授权后落稿之间的数据交接。",
        case_ids=(
            "architecture_scene_draft",
            "manuscript_patch_authorized_resume",
        ),
    ),
    BenchmarkScenarioCategory(
        category_id="retroactive_setting_change",
        name="修改前文设定",
        purpose="验证先解析稳定身份再读取或变更目标，避免名称歧义和误删。",
        case_ids=(
            "knowledge_catalog_identity_read",
            "structure_delete_second_confirmation",
        ),
    ),
    BenchmarkScenarioCategory(
        category_id="foreshadow_retrieval",
        name="检索前文伏笔",
        purpose="验证正文伏笔召回，以及许可外部资料与小说证据的有据联合使用。",
        case_ids=(
            "single_manuscript_search",
            "external_research_grounded",
        ),
    ),
    BenchmarkScenarioCategory(
        category_id="chapter_rewrite",
        name="改写章节",
        purpose="验证审查意见进入修订，并在正式写入前只生成可核对预览。",
        case_ids=(
            "revision_from_reviews",
            "manuscript_preview_only",
        ),
    ),
    BenchmarkScenarioCategory(
        category_id="output_contract",
        name="输出与证据合同校验",
        purpose="验证最终结论保留来源依据，并只投影当前有效的运行状态。",
        case_ids=(
            "single_canon_evidence",
            "memory_active_projection",
        ),
    ),
    BenchmarkScenarioCategory(
        category_id="character_consistency",
        name="角色一致性校验",
        purpose="验证多类独立审查，以及被拒绝分支不会污染有效人物状态。",
        case_ids=(
            "parallel_review_triad",
            "memory_rejected_parallel_isolation",
        ),
    ),
    BenchmarkScenarioCategory(
        category_id="plot_logic_closure",
        name="剧情逻辑闭环",
        purpose="验证过期状态被排除、替代状态被修复，最终剧情依据形成闭环。",
        case_ids=(
            "memory_stale_dependency",
            "memory_superseded_repair",
        ),
    ),
)

_RECOVERY_CATEGORIES = (
    BenchmarkScenarioCategory(
        category_id="planning_and_tool_recovery",
        name="规划与工具结果恢复",
        purpose="验证规划完成后、执行开始前，以及工具结果尚未被消费时的恢复。",
        case_ids=(
            "recovery_after_plan_before_execution",
            "recovery_tool_result_before_consumption",
        ),
    ),
    BenchmarkScenarioCategory(
        category_id="subgraph_and_authorization_recovery",
        name="子图与授权等待恢复",
        purpose="验证专业智能体中断和人工授权等待都能保持同一逻辑运行。",
        case_ids=(
            "recovery_subagent_interrupted",
            "recovery_waiting_authorization",
        ),
    ),
    BenchmarkScenarioCategory(
        category_id="effect_and_verification_recovery",
        name="副作用与校验恢复",
        purpose="验证写入后先对账再继续，并能从最终校验阶段恢复。",
        case_ids=(
            "recovery_after_write_before_effect_success",
            "recovery_verification_interruption",
        ),
    ),
    BenchmarkScenarioCategory(
        category_id="checkpoint_resilience",
        name="多次中断与检查点韧性",
        purpose="验证连续故障仍保持幂等，并在检查点损坏时明确安全停止。",
        case_ids=(
            "recovery_multiple_interruptions",
            "recovery_checkpoint_unavailable",
        ),
    ),
)


def build_benchmark_portfolio(
    suite: AuthoredSuiteSpec,
) -> tuple[BenchmarkPortfolioEntry, ...]:
    """从权威 Suite 校验并投影两个具有可追溯证据的评测入口。"""

    entries = (
        _entry(
            entry_id="multi_step",
            name="多步骤组合任务",
            summary="18 条真实合同，按 9 类写作组合任务组织；每类保留一个基础场景和一个约束变化场景。",
            dataset_name="taichu-general-agent-multi-step",
            categories=_MULTI_STEP_CATEGORIES,
        ),
        _entry(
            entry_id="recovery",
            name="异常中断恢复",
            summary="8 条真实故障注入合同，覆盖 Runtime 当前存在的 8 个持久化与恢复窗口。",
            dataset_name="taichu-general-agent-recovery",
            categories=_RECOVERY_CATEGORIES,
        ),
    )
    cases_by_id = {case.case_id: case for case in suite.cases}
    for entry in entries:
        missing = tuple(
            case_id for case_id in entry.case_ids if case_id not in cases_by_id
        )
        if missing:
            raise ValueError(
                f"评测入口 {entry.entry_id} 引用了未知案例：" + "、".join(missing)
            )
        for case_id in entry.case_ids:
            case = cases_by_id[case_id]
            has_fault_plan = case.setup.fault_plan_ref is not None
            if entry.entry_id == "recovery" and not has_fault_plan:
                raise ValueError(f"恢复案例缺少故障计划：{case_id}")
            if entry.entry_id == "multi_step" and has_fault_plan:
                raise ValueError(f"多步骤案例不得混入故障计划：{case_id}")
    return entries


def _entry(
    *,
    entry_id: BenchmarkEntryId,
    name: str,
    summary: str,
    dataset_name: str,
    categories: tuple[BenchmarkScenarioCategory, ...],
) -> BenchmarkPortfolioEntry:
    case_ids = tuple(
        case_id for category in categories for case_id in category.case_ids
    )
    return BenchmarkPortfolioEntry(
        entry_id=entry_id,
        name=name,
        summary=summary,
        opik_dataset_name=dataset_name,
        case_count=len(case_ids),
        case_ids=case_ids,
        categories=categories,
        invalid_invocation_rules=_INVALID_INVOCATION_RULES,
    )


__all__ = [
    "BenchmarkEntryId",
    "BenchmarkPortfolioEntry",
    "BenchmarkScenarioCategory",
    "build_benchmark_portfolio",
]
