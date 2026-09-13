"""大结果的可重复结构预览；完整结果先由基础设施保存。"""

import json
from typing import Any

from taichu.application.contracts.context_pipeline import ContextTokenCounter


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def result_preview(
    output: Any,
    *,
    reference: str,
    counter: ContextTokenCounter,
    limit: int,
    model_id: str | None = None,
) -> tuple[Any, int, int, bool]:
    original = counter.count_text(json_text(output), model_id)
    if original <= limit:
        return output, original, original, False
    base = {
        "已截断": True,
        "说明": "这里只展示部分结果，未展示不表示不存在；通过读取运行结果工具按引用、字段或范围回读全文。",
        "完整结果引用": reference,
        "原始Token估算": original,
    }
    # 优先保留少量完整条目，避免“很多空片段”挤掉正文信息。
    for text_limit in dict.fromkeys((limit, limit // 2, limit // 4, 500, 200, 80, 0)):
        for item_limit in (24, 12, 6, 3, 1, 0):
            candidate = {**base, "内容预览": _overview(output, item_limit, text_limit)}
            size = counter.count_text(json_text(candidate), model_id)
            if size <= limit:
                return candidate, original, size, True
    minimal = {
        "已截断": True,
        "完整结果引用": reference,
        "说明": "请按范围回读完整结果。",
    }
    size = counter.count_text(json_text(minimal), model_id)
    if size > limit:
        raise ValueError("单结果预览上限不足以容纳完整结果引用。")
    return minimal, original, size, True


def _overview(value: Any, item_limit: int, text_limit: int, depth: int = 0) -> Any:
    if isinstance(value, str):
        if len(value) <= text_limit:
            return value
        # 不截出半行或半段；超长单行只报告长度，全文可按字符范围读取。
        lines = value.splitlines(keepends=True)
        kept: list[str] = []
        used = 0
        for line in lines:
            if used + len(line) > text_limit:
                break
            kept.append(line)
            used += len(line)
        return {
            "原始字符数": len(value),
            "文本预览": "".join(kept),
            "已省略字符数": len(value) - used,
        }
    if isinstance(value, list):
        items = value[:item_limit] if depth < 3 else []
        return {
            "总条目数": len(value),
            "已省略条目数": len(value) - len(items),
            "条目": [
                _overview(item, item_limit, text_limit, depth + 1) for item in items
            ],
        }
    if isinstance(value, dict):
        keys = list(value)
        selected = keys[:24] if depth < 3 else []
        return {
            "总字段数": len(keys),
            "字段名": keys[:24],
            "字段": {
                key: _overview(value[key], item_limit, text_limit, depth + 1)
                for key in selected
            },
            "已省略字段数": len(keys) - len(selected),
        }
    return value
