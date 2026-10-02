"""列表只摘取模型实际内容，不把结构化工具参数冒充正文或思考。"""

from __future__ import annotations

import json
from typing import Any

from taichu.application.trajectory.models import TrajectoryEvent


def _blocks(value: Any) -> tuple[list[str], list[str], list[str]]:
    texts: list[str] = []
    thoughts: list[str] = []
    calls: list[str] = []
    for block in value if isinstance(value, (list, tuple)) else []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "message":
            nested = _blocks(block.get("content"))
            texts.extend(nested[0])
            thoughts.extend(nested[1])
            calls.extend(nested[2])
        elif kind in {"text", "output_text"}:
            texts.append(str(block.get("text", "")))
        elif kind == "thinking":
            thoughts.append(str(block.get("thinking", "")))
        elif kind == "reasoning":
            thoughts.extend(str(part.get("text", "")) for part in (block.get("content") or block.get("summary") or []) if isinstance(part, dict))
        elif kind in {"tool_use", "function_call", "tool_call"}:
            arguments = block.get("input") if kind == "tool_use" else block.get("arguments", block.get("arguments_json", ""))
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
            calls.append(f"工具调用（{block.get('name', '')}） {arguments}")
    return texts, thoughts, calls


def assistant_preview(data: dict[str, Any], events: list[TrajectoryEvent]) -> str:
    """正文优先，其次真实思考，最后工具名称和参数；重试只读当前尝试。"""
    response = data.get("response") or {}
    texts = [str(response.get("text") or "")]
    thoughts = _blocks(response.get("thinking_blocks"))[1]
    calls = _blocks([{**call, "type": "tool_call"} for call in response.get("tool_calls") or []])[2]
    started = next((event for event in reversed(events) if event.kind == "attempt_started"), None)
    attempt = started.data.get("attempt_id") if started else None
    current = [event for event in events if (not started or event.sequence >= started.sequence) and (not attempt or event.data.get("attempt_id") in {None, attempt})]
    received = next((event.data.get("wire_response") for event in reversed(current) if event.kind == "attempt_received" and int(event.data.get("status_code", 500)) < 400), None)
    if isinstance(received, dict):
        native = _blocks(received.get("content", received.get("output")))
        texts = native[0] or texts
        thoughts = native[1] or thoughts
        calls = native[2] or calls
    else:
        streamed: dict[int, dict[str, Any]] = {}
        for event in current:
            if event.kind != "wire_stream_chunk":
                continue
            payload = event.data.get("payload") or {}
            kind = payload.get("type", event.data.get("event_name"))
            if kind == "response.completed":
                final = payload.get("response") or {}
                native = _blocks(final.get("output"))
                texts, thoughts, calls = native
                break
            index = payload.get("index")
            if not isinstance(index, int):
                continue
            if kind == "content_block_start":
                streamed[index] = dict(payload.get("content_block") or {})
            elif kind == "content_block_delta" and index in streamed:
                delta = payload.get("delta") or {}
                field = {"text_delta": "text", "thinking_delta": "thinking", "input_json_delta": "input"}.get(delta.get("type"))
                if field:
                    block = streamed[index]
                    prior = block.get(field)
                    block[field] = (prior if isinstance(prior, str) else "") + str(delta.get("partial_json" if field == "input" else field, ""))
        if streamed:
            native = _blocks([streamed[index] for index in sorted(streamed)])
            texts = texts if any(text.strip() for text in texts) else native[0]
            thoughts = thoughts or native[1]
            calls = calls or native[2]
        if not any(text.strip() for text in texts):
            texts = ["".join(str(event.data.get("event", {}).get("delta", "")) for event in current if event.kind == "stream_chunk" and event.data.get("event", {}).get("event_type") == "text_delta")]
    for parts in (texts, thoughts, calls):
        content = "\n".join(parts)
        if content.strip():
            return content
    return str(data.get("error") or "尚无输出")
