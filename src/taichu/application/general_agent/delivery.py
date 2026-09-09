"""把本次真实候选与审查记录确定性地交付给作者，不让汇总模型重写。"""

from __future__ import annotations

from typing import Any


_REVIEW_LABELS = {
    "consistency_review": "一致性审查",
    "narrative_review": "叙事审查",
    "style_review": "文风审查",
}
_SEVERITY_LABELS = {
    "critical": "关键问题",
    "major": "严重问题",
    "minor": "轻微问题",
    "suggestion": "改进建议",
}
_VERDICT_LABELS = {
    "draft_with_revisions": "建议修改后采用",
    "pass": "通过",
    "passed": "通过",
    "fail": "未通过",
    "failed": "未通过",
    "needs_revision": "需要修改",
}


def compose_verified_delivery(summary: str, audit: list[dict[str, Any]]) -> str:
    """总结与逐字交付分离；不修改原始节点产物或其有效性。"""
    sections = [summary.strip()]
    for record in audit:
        output = record["output"]
        if record["deliver_candidate"]:
            text = output.get("text")
            if isinstance(text, str) and text.strip():
                sections.append("## 正文候选\n\n" + text)
        if "verdict" not in output or "issues" not in output:
            continue
        label = _REVIEW_LABELS.get(output.get("artifact_type"), "审查结果")
        verdict = str(output["verdict"])
        lines = [f"## {label}", _VERDICT_LABELS.get(verdict, verdict)]
        for issue in output["issues"]:
            severity = _SEVERITY_LABELS.get(issue.get("severity"), "问题")
            lines.append(f"### {severity}\n\n{issue['problem']}")
            for field, title in (("evidence", "依据"), ("suggestion", "建议")):
                if issue.get(field):
                    lines.append(f"{title}：{issue[field]}")
        sections.append("\n\n".join(lines))
    return "\n\n".join(sections)
