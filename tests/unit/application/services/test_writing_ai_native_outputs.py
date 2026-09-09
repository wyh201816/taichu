"""写作页原生结构化输出协议回归测试。"""

from taichu.application.services.writing_ai_outputs import (
    ChapterSummaryOutput,
    ChatAnswerOutput,
    EvidenceAnswerOutput,
    InspirationOutput,
    PendingFactCandidatesOutput,
    PolishedTextOutput,
    SettingSuggestionOutput,
    TextCandidateOutput,
    WritingSuggestionOutput,
)
from taichu.application.services.writing_ai_prompts import (
    TAICHU_COMMON_SYSTEM_V1,
    WritingAIPromptRegistry,
)
from taichu.application.services.writing_ai_service import _stream_preview_text
from taichu.domain.models import WritingAIButtonType, WritingAIOutputType


_OUTPUT_SCHEMAS = (
    ChatAnswerOutput,
    TextCandidateOutput,
    PolishedTextOutput,
    SettingSuggestionOutput,
    WritingSuggestionOutput,
    EvidenceAnswerOutput,
    ChapterSummaryOutput,
    InspirationOutput,
    PendingFactCandidatesOutput,
)


def test_all_writing_output_objects_are_strict_and_fully_required() -> None:
    for output_schema in _OUTPUT_SCHEMAS:
        _assert_strict_objects(output_schema.model_json_schema())


def test_writing_prompts_do_not_embed_output_schema_or_json_examples() -> None:
    registry = WritingAIPromptRegistry()
    variables = {
        "button_label": "入口",
        "reference_scope_label": "本章",
        "chapter_id": "chapter_001",
        "chapter_title": "第一章",
        "user_input": "继续",
        "selected_text": "选区",
        "before_selection": "前文",
        "after_selection": "后文",
        "chapter_excerpt": "正文",
        "knowledge_context": "知识",
        "evidence_context": "证据",
        "target_words": "500",
    }
    assert "JSON" not in TAICHU_COMMON_SYSTEM_V1
    for button_type in WritingAIButtonType:
        rendered = registry.render_user_prompt(button_type, variables)
        assert "JSON" not in rendered
        assert "output_type" not in rendered
        assert "请严格输出" not in rendered


def test_stream_preview_uses_each_output_contracts_primary_content() -> None:
    cases = {
        WritingAIOutputType.CHAT_ANSWER: ({"answer": "回答"}, "回答"),
        WritingAIOutputType.TEXT_CANDIDATE: ({"text": "续写"}, "续写"),
        WritingAIOutputType.POLISHED_TEXT: (
            {"polished_text": "润色正文"},
            "润色正文",
        ),
        WritingAIOutputType.SETTING_SUGGESTION: (
            {"setting_supplements": [{"content": "设定"}]},
            "设定",
        ),
        WritingAIOutputType.WRITING_SUGGESTION: (
            {"suggestions": [{"action": "修改动作"}]},
            "修改动作",
        ),
        WritingAIOutputType.EVIDENCE_ANSWER: ({"conclusion": "结论"}, "结论"),
        WritingAIOutputType.CHAPTER_SUMMARY: ({"summary": "摘要"}, "摘要"),
        WritingAIOutputType.INSPIRATION: (
            {"ideas": [{"content": "灵感"}]},
            "灵感",
        ),
        WritingAIOutputType.PENDING_FACT_CANDIDATES: (
            {"candidates": [{"content": "事实候选"}]},
            "事实候选",
        ),
    }
    for output_type, (arguments, expected) in cases.items():
        assert _stream_preview_text({"args": arguments}, output_type) == expected


def _assert_strict_objects(value: object) -> None:
    if isinstance(value, dict):
        if value.get("type") == "object":
            assert value.get("additionalProperties") is False
            properties = value.get("properties", {})
            assert set(value.get("required", [])) == set(properties)
        for nested in value.values():
            _assert_strict_objects(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_strict_objects(nested)
