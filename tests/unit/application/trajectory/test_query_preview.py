"""列表摘要必须遵守正文、实际思考、原生工具请求的来源优先级。"""

from taichu.application.trajectory.models import TrajectoryEvent, TrajectoryRead
from taichu.application.trajectory.query import TrajectoryProjection


def _event(kind, data, sequence):
    return TrajectoryEvent(conversation_id="test", run_id="run", request_index=1,
                           call_id="model", kind=kind, data=data, sequence=sequence)


def _preview(response=None, fragments=()):
    events = [_event("model_started", {}, 1), *fragments]
    if response is not None:
        events.append(_event("model_completed", {"response": response}, 100))
    projection = TrajectoryProjection(TrajectoryRead(events=events))
    return projection.page()["records"][0]["preview"]


def test_text_then_thinking_then_tool_arguments():
    call = {"name": "GeneralAgentPlanDraft", "arguments_json": '{"rationale":"计划理由","direct_response":"业务回答"}'}
    thought = {"type": "thinking", "thinking": "实际思考"}
    assert _preview({"text": "真正正文", "thinking_blocks": [thought], "tool_calls": [call]}) == "真正正文"
    assert _preview({"text": "  ", "thinking_blocks": [thought], "tool_calls": [call]}) == "实际思考"
    assert _preview({"tool_calls": [call]}) == '工具调用（GeneralAgentPlanDraft） {"rationale":"计划理由","direct_response":"业务回答"}'
    assert _preview({"tool_calls": [{"name": "tool", "arguments_json": '{"incomplete":'}]}) == '工具调用（tool） {"incomplete":'


def test_actual_wire_thinking_and_text_are_read_without_inference():
    def received(content):
        return _event("attempt_received", {"status_code": 200, "wire_response": {"content": content}}, 2)
    assert _preview({}, [received([
        {"type": "thinking", "thinking": "原生思考"},
        {"type": "text", "text": "原生正文"},
    ])]) == "原生正文"
    assert _preview({}, [received([{ "type": "thinking", "thinking": "原生思考"}])]) == "原生思考"
    assert _preview({}, [received([{ "type": "redacted_thinking", "data": "不可见思考"}])]) == "尚无输出"


def test_current_stream_updates_from_thinking_to_text_and_excludes_prior_attempt():
    def wire(kind, payload, sequence):
        return _event("wire_stream_chunk", {"event_name": kind, "payload": payload, "attempt_id": "new"}, sequence)
    events = [
        _event("stream_chunk", {"event": {"event_type": "text_delta", "delta": "失败尝试的正文"}}, 2),
        _event("attempt_started", {"attempt_id": "new"}, 3),
        wire("content_block_start", {"index": 0, "content_block": {"type": "thinking", "thinking": ""}}, 4),
        wire("content_block_delta", {"index": 0, "delta": {"type": "thinking_delta", "thinking": "当前思考"}}, 5),
    ]
    assert _preview(fragments=events) == "当前思考"
    events.extend([
        wire("content_block_start", {"index": 1, "content_block": {"type": "text", "text": ""}}, 6),
        wire("content_block_delta", {"index": 1, "delta": {"type": "text_delta", "text": "当前正文"}}, 7),
    ])
    assert _preview(fragments=events) == "当前正文"


def test_prefix_limit_leaves_full_content_in_detail():
    text = "原文" * 300
    start = _event("model_started", {}, 1)
    end = _event("model_completed", {"response": {"text": text}}, 2)
    projection = TrajectoryProjection(TrajectoryRead(events=[start, end]))
    assert projection.page()["records"][0]["preview"] == text[:360] + "…"
    assert projection.detail(start.event_id)["end"].data["response"]["text"] == text


def test_subagent_identity_uses_explicit_request_origin_and_tool_parent():
    child = _event("model_started", {"request": {"task_type": "general_writing_subagent", "task_name": "drafting"}}, 1)
    tool = _event("tool_started", {"name": "read_manuscript"}, 2).model_copy(update={"call_id": "tool", "parent_call_id": "model"})
    ordinary = _event("model_started", {"request": {"task_type": "other", "task_name": "drafting"}}, 3).model_copy(update={"call_id": "ordinary"})
    projection = TrajectoryProjection(TrajectoryRead(events=[child, tool, ordinary]))
    rows = projection.page()["records"]
    assert [row["subagent_name"] for row in rows] == ["drafting", "drafting", None]
    assert [row["type"] for row in rows] == ["assistant", "tool", "assistant"]
    assert [row["step"] for row in rows] == [1, 1, 2]
    assert projection.detail(child.event_id)["subagent_name"] == "drafting"
