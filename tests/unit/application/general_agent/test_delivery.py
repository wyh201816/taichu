"""真实产物交付不能依赖汇总模型重新生成。"""

from copy import deepcopy

from taichu.application.general_agent.delivery import compose_verified_delivery


def test_delivery_preserves_text_and_all_review_problems_without_mutation() -> None:
    text = "秦浩轩停住。\n\n“小金！”\n"
    audit = [
        {"deliver_candidate": True, "output": {"text": text}},
        {"deliver_candidate": False, "output": {
            "artifact_type": "style_review", "verdict": "draft_with_revisions",
            "issues": [
                {"severity": "major", "problem": "重复了前章结尾。", "evidence": "前章已写明。", "suggestion": "从新动作开始。"},
                {"severity": "minor", "problem": "人物口吻不符。"},
            ],
        }},
    ]
    original = deepcopy(audit)
    answer = compose_verified_delivery("规划摘要。", audit)
    assert text in answer
    for expected in ("文风审查", "建议修改后采用", "严重问题", "重复了前章结尾。", "前章已写明。", "从新动作开始。", "人物口吻不符。"):
        assert expected in answer
    assert "draft_with_revisions" not in answer
    assert audit == original


def test_delivery_does_not_append_superseded_draft() -> None:
    answer = compose_verified_delivery("已按要求修改。", [
        {"deliver_candidate": False, "output": {"text": "旧稿不应交付。"}},
        {"deliver_candidate": True, "output": {"text": "修改后的原文。"}},
    ])
    assert "旧稿不应交付。" not in answer
    assert "修改后的原文。" in answer


def test_delivery_without_artifacts_remains_a_direct_answer() -> None:
    assert compose_verified_delivery("一句简单回答。", []) == "一句简单回答。"
