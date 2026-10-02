"""从观测事实构建只读记录、汇总与定位；分页不改变身份和计数。"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from taichu.application.trajectory.models import TrajectoryEvent, TrajectoryRead
from taichu.application.trajectory.preview import assistant_preview


STARTS = {"user": "user", "model_started": "assistant", "tool_started": "tool"}
ENDS = {"model_completed", "model_failed", "model_stopped", "model_incomplete", "tool_completed", "tool_failed"}


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


class TrajectoryProjection:
    def __init__(self, trace: TrajectoryRead) -> None:
        self.trace = trace
        self.cursor = max((event.sequence for event in trace.events), default=0)
        self.records: list[dict[str, Any]] = []
        self.by_id: dict[str, dict[str, Any]] = {}
        self.by_call: dict[str, dict[str, Any]] = {}
        self.related: dict[str, list[TrajectoryEvent]] = defaultdict(list)
        self.turns: dict[int, dict[str, Any]] = {}
        steps: dict[int, int] = defaultdict(int)
        request_number = 0
        seen: set[str] = set()
        for event in trace.events:
            if event.event_id in seen:
                continue
            seen.add(event.event_id)
            turn = self.turns.setdefault(event.request_index, {
                "request_index": event.request_index, "models": 0, "tools": 0,
                "status": "未记录结束", "complete": trace.complete,
                "input_tokens": None, "output_tokens": None,
            })
            if event.kind in STARTS:
                if event.kind == "model_started":
                    steps[event.request_index] += 1
                    request_number += 1
                    turn["models"] += 1
                record: dict[str, Any] = {
                    "id": event.event_id, "type": STARTS[event.kind], "start": event,
                    "end": None, "requestNumber": request_number if event.kind == "model_started" else None,
                    "step": steps[event.request_index] if event.kind == "model_started" else None,
                }
                self.records.append(record)
                self.by_id[event.event_id] = record
                if event.call_id:
                    self.by_call[event.call_id] = record
            if event.call_id:
                self.related[event.call_id].append(event)
            if event.kind == "tool_executing":
                turn["tools"] += 1
            if event.kind in ENDS and event.call_id in self.by_call:
                self.by_call[event.call_id]["end"] = event
                if event.kind == "model_completed":
                    usage = event.data.get("response", {}).get("usage") or {}
                    for name in ("input_tokens", "output_tokens"):
                        if isinstance(usage.get(name), int):
                            turn[name] = (turn[name] or 0) + usage[name]
            if event.kind == "run_state":
                turn["status"] = event.data.get("status", "未记录结束")
            elif event.kind == "run_started":
                turn["status"] = "running"
            elif event.kind == "run_interrupted":
                turn["status"] = "未记录结束"
        for record in self.records:
            start = record["start"]
            parent = self.by_call.get(start.parent_call_id)
            if parent:
                record["step"] = parent["step"]
                record["parent_id"] = parent["id"]
            owner = record if record["type"] == "assistant" else parent
            request = owner["start"].data.get("request", {}) if owner else {}
            record["subagent_name"] = request.get("task_name") if request.get("task_type") == "general_writing_subagent" else None
            fragments = self.related.get(start.call_id, [])
            response = record["end"].data if record["end"] else {}
            if record["type"] == "user":
                preview = str(start.data.get("content", ""))
            elif record["type"] == "tool":
                preview = _text(start.data.get("arguments", {}))[:180] + " → " + _text(response.get("result", response.get("error", "尚无结果")))[:180]
            else:
                preview = assistant_preview(response, fragments)
            record["preview"] = preview[:360] + ("…" if len(preview) > 360 else "")
            record["changed_at"] = max((event.sequence for event in fragments), default=start.sequence)
        grouped_tools: dict[str, list[dict[str, Any]]] = defaultdict(list)
        incomplete_turns: set[int] = set()
        for row in self.records:
            if row["type"] == "tool" and row.get("parent_id"):
                grouped_tools[row["parent_id"]].append(row)
            if row["type"] != "user" and (row["end"] is None or row["end"].kind == "model_incomplete"):
                incomplete_turns.add(row["start"].request_index)
        for row in self.records:
            tools = grouped_tools.get(row["id"], [])
            row["tool_count"] = len(tools)
            row["tool_names"] = list(dict.fromkeys(tool["start"].data.get("name", "") for tool in tools))
            row["changed_at"] = max([row["changed_at"], *(tool["changed_at"] for tool in tools)])
        for turn in self.turns.values():
            turn["settled"] = turn["status"] in {"completed", "failed", "cancelled", "timeout"}
            if turn["settled"] and turn["request_index"] in incomplete_turns:
                turn["complete"] = False

    def _compact(self, record: dict[str, Any]) -> dict[str, Any]:
        start = record["start"]
        data = {key: start.data[key] for key in ("name", "redaction_count") if key in start.data}
        if record["type"] == "assistant":
            data["request"] = {"task_name": start.data.get("request", {}).get("task_name")}
        end = record["end"]
        end_data = {key: end.data[key] for key in ("duration_ms", "redaction_count", "execution") if key in end.data} if end else {}
        return {**record, "start": start.model_copy(update={"data": data}),
                "end": end.model_copy(update={"data": end_data}) if end else None}

    def page(self, *, limit: int = 100, before: int | None = None, after: int | None = None,
             around: str | None = None, request_index: int | None = None) -> dict[str, Any]:
        records = [row for row in self.records if request_index is None or row["start"].request_index == request_index]
        if after is not None:
            updates = sorted((row for row in records if row["changed_at"] > after), key=lambda row: row["changed_at"])
            selected = updates[:limit]
            if selected:
                # 父摘要和子活动可共享一次变更，不能在游标边界拆开同序号组。
                boundary = selected[-1]["changed_at"]
                selected.extend(row for row in updates[limit:] if row["changed_at"] == boundary)
        elif around is not None:
            index = next((index for index, row in enumerate(records) if row["id"] == around), None)
            if index is None:
                raise KeyError(around)
            begin = max(0, index - limit // 2)
            selected = records[begin:begin + limit]
        else:
            selected = [row for row in records if before is None or row["start"].sequence < before][-limit:]
        earliest = selected[0]["start"].sequence if selected else None
        return {
            "records": [self._compact(row) for row in sorted(selected, key=lambda row: row["start"].sequence)],
            "before": earliest, "has_earlier": earliest is not None and any(row["start"].sequence < earliest for row in records),
            "cursor": self.cursor if after is None or len(selected) < limit else max(row["changed_at"] for row in selected),
            "turns": list(self.turns.values()), "complete": self.trace.complete,
            "warnings": self.trace.warnings,
            "timeline": [{"id": row["id"], "type": row["type"], "request_index": row["start"].request_index,
                          "step": row["step"], "start": row["start"].recorded_at,
                          "end": row["end"].recorded_at if row["end"] else None} for row in self.records],
        }

    def detail(self, record_id: str) -> dict[str, Any]:
        record = self.by_id[record_id]
        events = self.related.get(record["start"].call_id, [])
        prepared = next((event for event in events if event.kind == "tool_prepared"), None)
        if prepared:
            record = {**record, "start": record["start"].model_copy(update={"data": {
                **record["start"].data, "dispatch_arguments": record["start"].data.get("arguments"),
                "arguments": prepared.data.get("arguments"),
            }})}
        return {**record, "attempts": [event for event in events if event.kind.startswith("attempt_")],
                "tool_records": [{"id": child["id"], "native_call_id": child["start"].data["native_call_id"]}
                                 for child in self.records if child.get("parent_id") == record_id
                                 and child["type"] == "tool" and child["start"].data.get("native_call_id")],
                "fragments": [event for event in events if event.kind in {"stream_chunk", "wire_stream_chunk"}]}

    def search(self, query: str, *, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        hits: dict[str, dict[str, Any]] = {}
        users = {row["start"].run_id: row for row in self.records if row["type"] == "user"}
        for event in self.trace.events:
            text = _text(event.data)
            location = text.casefold().find(query.casefold())
            if location < 0:
                continue
            row = self.by_call.get(event.call_id) if event.call_id else self.by_id.get(event.event_id)
            row = row or users.get(event.run_id)
            if row and row["id"] not in hits:
                hits[row["id"]] = {"record_id": row["id"], "request_index": event.request_index,
                                   "preview": text[max(0, location - 40):location + 120]}
        return {"hits": list(hits.values())[offset:offset + limit], "total": len(hits),
                "complete": self.trace.complete, "warnings": self.trace.warnings}
