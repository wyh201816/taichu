"""写作页各入口通过模型原生工具返回的严格结构化契约。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _WritingOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class UsedEvidence(_WritingOutput):
    display_name: str
    excerpt: str
    usage: str


class ChatAnswerOutput(_WritingOutput):
    output_type: Literal["chat_answer"]
    answer: str
    evidence: list[UsedEvidence]
    inference: list[str]
    uncertainties: list[str]
    actionable_suggestions: list[str]


class TextCandidateOutput(_WritingOutput):
    output_type: Literal["text_candidate"]
    text: str = Field(description="可直接插入正文的续写文本。")
    risk_notes: list[str]
    used_evidence: list[UsedEvidence]


class PolishedTextOutput(_WritingOutput):
    output_type: Literal["polished_text"]
    polished_text: str = Field(description="可直接替换选区的润色正文。")
    change_summary: list[str] = Field(max_length=5)
    risk_notes: list[str]
    used_evidence: list[UsedEvidence]


class SettingSupplement(_WritingOutput):
    title: str
    content: str
    scope: str
    conflict_risk: str


class SettingSuggestionOutput(_WritingOutput):
    output_type: Literal["setting_suggestion"]
    setting_supplements: list[SettingSupplement]
    usage_advice: list[str]
    possible_impacts: list[str]
    used_evidence: list[UsedEvidence]


class WritingDiagnosis(_WritingOutput):
    problem: str
    why_it_matters: str
    severity: Literal["轻微", "中等", "严重"]
    evidence_excerpt: str


class WritingSuggestionItem(_WritingOutput):
    title: str
    action: str
    expected_effect: str


class WritingSuggestionOutput(_WritingOutput):
    output_type: Literal["writing_suggestion"]
    diagnosis: list[WritingDiagnosis]
    suggestions: list[WritingSuggestionItem]
    do_not_change: list[str]
    uncertainties: list[str]


class SupportingEvidence(_WritingOutput):
    display_name: str
    excerpt: str
    supports: str


class EvidenceAnswerOutput(_WritingOutput):
    output_type: Literal["evidence_answer"]
    conclusion: str
    evidence: list[SupportingEvidence]
    inference: list[str]
    unconfirmed_points: list[str]
    conflict_warnings: list[str]


class ChapterCharacterChange(_WritingOutput):
    character_name: str
    change: str
    evidence_excerpt: str


class ChapterSettingCandidate(_WritingOutput):
    title: str
    content: str
    needs_confirmation: Literal[True]


class ChapterSummaryOutput(_WritingOutput):
    output_type: Literal["chapter_summary"]
    summary: str
    key_events: list[str]
    character_changes: list[ChapterCharacterChange]
    setting_candidates: list[ChapterSettingCandidate]
    foreshadow_or_hooks: list[str]
    unconfirmed_points: list[str]


class InspirationIdea(_WritingOutput):
    title: str
    content: str
    use_scene: str
    priority: Literal["低", "中", "高"]
    risk: str


class InspirationOutput(_WritingOutput):
    output_type: Literal["inspiration"]
    ideas: list[InspirationIdea]
    recommended_next_action: list[str]


class PendingFactCandidate(_WritingOutput):
    title: str
    content: str
    type_hint_text: str
    source_origin: Literal["manual", "agent_extract"]
    source_note: str
    needs_author_confirmation: Literal[True]
    conflict_risk: str


class PendingFactCandidatesOutput(_WritingOutput):
    output_type: Literal["pending_fact_candidates"]
    candidates: list[PendingFactCandidate]
    ignored_items: list[str]


WritingAIOutputSchema = type[_WritingOutput]
