"""Markdown 长期记忆按需召回测试。"""

import asyncio

from taichu.infrastructure.long_term_memory import MarkdownLongTermMemoryRetriever


def test_markdown_long_term_memory_retrieves_relevant_and_global_entries(
    tmp_path,
) -> None:
    path = tmp_path / "long_term_memory.md"
    path.write_text(
        """# 长期记忆

## 回答偏好
适用范围：全局

回答保持简洁。

## 战斗写作偏好
关键词：战斗、打斗

战斗场景使用短句并减少解释。

## 感情线偏好
关键词：感情、情绪

感情线保持克制。
""",
        encoding="utf-8",
    )

    result = asyncio.run(
        MarkdownLongTermMemoryRetriever(path).retrieve(
            "帮我写一段战斗场景",
        )
    )

    assert [item.content.splitlines()[0] for item in result] == [
        "战斗写作偏好",
        "回答偏好",
    ]
    assert all(item.kind == "user_preference" for item in result)
    assert all(item.source_refs for item in result)
