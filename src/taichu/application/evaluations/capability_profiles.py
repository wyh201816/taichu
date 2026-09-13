"""第一版 Tool 与专业子 Agent 的独立评测口径注册表。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MetricDirection = Literal["higher_is_better", "lower_is_better", "guardrail"]
MetricDefinition = tuple[str, str, MetricDirection]


class CapabilityEvaluationMetric(BaseModel):
    """一个能力独立维护的质量指标。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1)
    direction: MetricDirection
    dimension: str | None = None


class CapabilityEvaluationProfile(BaseModel):
    """不统一三类业务评测，只定义单个能力自身的效果口径。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    capability_type: Literal["tool", "subagent"]
    capability_name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    metrics: tuple[CapabilityEvaluationMetric, ...]


def all_capability_evaluation_profiles() -> list[CapabilityEvaluationProfile]:
    """返回与第一版能力目录一一对应的独立评测 Profile。"""
    return [
        *(_profile("tool", name, metrics) for name, metrics in _TOOL_METRICS.items()),
        *(
            _profile("subagent", name, metrics)
            for name, metrics in _SUBAGENT_METRICS.items()
        ),
    ]


def capability_evaluation_profile(
    capability_type: Literal["tool", "subagent"],
    capability_name: str,
) -> CapabilityEvaluationProfile:
    for profile in all_capability_evaluation_profiles():
        if (
            profile.capability_type == capability_type
            and profile.capability_name == capability_name
        ):
            return profile
    raise LookupError(f"能力“{capability_name}”没有注册评测口径。")


def _profile(
    capability_type: Literal["tool", "subagent"],
    name: str,
    metrics: tuple[MetricDefinition, ...],
) -> CapabilityEvaluationProfile:
    return CapabilityEvaluationProfile(
        capability_type=capability_type,
        capability_name=name,
        metrics=tuple(
            CapabilityEvaluationMetric(
                key=key,
                label=label,
                direction=direction,
                dimension=(key.split("__", 1)[0] if "__" in key else None),
            )
            for key, label, direction in metrics
        ),
    )


_TOOL_METRICS: dict[str, tuple[MetricDefinition, ...]] = {
    "read_runtime_result": (
        ("range_accuracy", "结果回读范围准确率", "higher_is_better"),
        ("source_integrity", "结果来源完整性", "guardrail"),
    ),
    "get_novel_structure": (
        ("field_accuracy", "结构字段准确率", "higher_is_better"),
        ("stable_order", "稳定排序", "guardrail"),
    ),
    "get_knowledge_chapter_coverage": (
        ("coverage_accuracy", "章节覆盖统计准确率", "higher_is_better"),
        ("confirmed_only", "仅统计已确认知识", "guardrail"),
    ),
    "read_manuscript": (
        ("range_accuracy", "正文范围准确率", "higher_is_better"),
        ("budget_violation", "字符预算违规率", "lower_is_better"),
    ),
    "retrieve_story_context": (
        ("precision_at_k", "统一证据准确率", "higher_is_better"),
        ("recall_at_k", "统一证据召回率", "higher_is_better"),
        ("multi_hop_recall", "多跳证据召回率", "higher_is_better"),
        ("relation_precision", "关系证据准确率", "higher_is_better"),
        ("unsupported_relation", "无依据关系率", "guardrail"),
    ),
    "resolve_knowledge_identity": (
        ("identity_accuracy", "唯一身份准确率", "higher_is_better"),
        ("ambiguity_detection", "歧义检出率", "higher_is_better"),
    ),
    "list_knowledge_catalog": (
        ("pagination_accuracy", "分页完整性", "higher_is_better"),
        ("lifecycle_isolation", "生命周期隔离", "guardrail"),
    ),
    "read_knowledge_cards": (
        ("id_accuracy", "定向读取准确率", "higher_is_better"),
        ("lifecycle_isolation", "生命周期隔离", "guardrail"),
    ),
    "search_external_sources": (
        ("source_access_rate", "来源可访问率", "higher_is_better"),
        ("unauthorized_access", "越权访问率", "guardrail"),
    ),
    "read_external_source": (
        ("content_extraction", "正文抽取准确率", "higher_is_better"),
        ("unsafe_url_access", "不安全地址访问率", "guardrail"),
    ),
    "preview_manuscript_patch": (
        ("diff_accuracy", "差异预览准确率", "higher_is_better"),
        ("side_effect_count", "预览副作用次数", "guardrail"),
    ),
    "apply_manuscript_patch": (
        ("write_accuracy", "正文写入准确率", "higher_is_better"),
        ("conflict_overwrite", "并发覆盖次数", "guardrail"),
    ),
    "create_novel_structure_items": (
        ("structure_integrity", "结构完整性", "higher_is_better"),
        ("duplicate_on_retry", "重试重复创建次数", "guardrail"),
    ),
    "update_novel_structure": (
        ("update_accuracy", "结构更新准确率", "higher_is_better"),
        ("stale_version_write", "过期版本写入次数", "guardrail"),
    ),
    "delete_novel_structure_items": (
        ("archive_accuracy", "归档准确率", "higher_is_better"),
        ("unconfirmed_delete", "未二次确认删除次数", "guardrail"),
    ),
    "create_confirmed_knowledge": (
        ("schema_acceptance", "合法知识写入率", "higher_is_better"),
        ("identity_conflict_write", "身份冲突写入次数", "guardrail"),
    ),
    "update_confirmed_knowledge": (
        ("field_accuracy", "字段更新准确率", "higher_is_better"),
        ("stale_version_write", "过期知识版本写入次数", "guardrail"),
    ),
}


_SUBAGENT_METRICS: dict[str, tuple[MetricDefinition, ...]] = {
    "canon_evidence": (
        ("evidence_accuracy", "证据正确率", "higher_is_better"),
        ("unsupported_claims", "无依据断言率", "lower_is_better"),
    ),
    "external_research": (
        ("citation_accuracy", "引用准确率", "higher_is_better"),
        ("unauthorized_access", "越权率", "guardrail"),
    ),
    "narrative_summary": (
        ("fact_fidelity", "事实忠实度", "higher_is_better"),
        ("key_omission", "关键信息遗漏率", "lower_is_better"),
    ),
    "worldbuilding": (
        ("internal_coherence", "设定内部自洽", "higher_is_better"),
        ("canon_conflict", "既有事实冲突率", "lower_is_better"),
    ),
    "character": (
        ("motivation_coherence", "动机连贯性", "higher_is_better"),
        ("character_conflict", "人设冲突率", "lower_is_better"),
    ),
    "story_architecture": (
        ("long_causality", "长线因果完整度", "higher_is_better"),
        ("foreshadowing_recovery", "伏笔可回收性", "higher_is_better"),
    ),
    "scene_planning": (
        ("beat_executability", "场景节拍可执行性", "higher_is_better"),
        ("continuity_coverage", "承接约束覆盖率", "higher_is_better"),
    ),
    "drafting": (
        ("new_text_quality", "新内容质量", "higher_is_better"),
        ("constraint_coverage", "写作约束覆盖率", "higher_is_better"),
    ),
    "revision": (
        ("intent_preservation", "原意保留率", "higher_is_better"),
        ("non_target_change", "非目标改动率", "lower_is_better"),
    ),
    "consistency_reviewer": tuple(
        (
            f"{dimension}__detection",
            f"{label}问题检出率",
            "higher_is_better",
        )
        for dimension, label in (
            ("world_rules", "世界规则"),
            ("character", "人物"),
            ("timeline", "时间线"),
            ("causality", "因果"),
            ("state_continuity", "状态连续性"),
            ("foreshadowing", "伏笔"),
        )
    ),
    "narrative_reviewer": (
        ("issue_detection", "叙事问题检出率", "higher_is_better"),
        ("false_positive", "误报率", "lower_is_better"),
    ),
    "style_reviewer": (
        ("style_issue_detection", "文风问题检出率", "higher_is_better"),
        ("false_positive", "误报率", "lower_is_better"),
    ),
}
