import assert from "node:assert/strict";
import { compactToolArguments, trajectoryContentBlocks } from "../../src/lib/trajectory-content";
import type { TrajectoryEvent, TrajectoryRow } from "../../src/lib/trajectory-records";

const event = (kind: string, data: Record<string, unknown>): TrajectoryEvent => ({
  schema_version: 1, event_id: kind, sequence: 1, conversation_id: "test", run_id: "run", request_index: 1,
  kind, recorded_at: "2026-10-01T00:00:00Z", call_id: "model", parent_call_id: null, data,
});
const row = (response: Record<string, unknown>): TrajectoryRow => ({
  id: "model", type: "assistant", start: event("model_started", {}),
  attempts: [event("attempt_received", { status_code: 200, wire_response: response })],
});

const native = row({ content: [
  { type: "thinking", thinking: "真实思考\n保留换行", signature: "signature" },
  { type: "text", text: "**正文**" },
  { type: "tool_use", id: "call-a", name: "read_manuscript", input: { start_order: 95, end_order: 98 } },
  { type: "tool_use", id: "call-b", name: "GeneralAgentPlanDraft", input: { rationale: "这是计划理由，不是思考" } },
] });
const before = JSON.stringify(native);
const blocks = trajectoryContentBlocks(native);
assert.deepEqual(blocks.map(block => block.kind), ["thinking", "text", "tool-call", "tool-call"]);
assert.equal(blocks[0].text, "真实思考\n保留换行");
assert.equal(blocks[1].text, "**正文**");
assert.equal(blocks[2].callId, "call-a");
assert.deepEqual(JSON.parse(blocks[2].text), { start_order: 95, end_order: 98 });
assert.ok(!blocks[2].text.includes("arguments_json"), "工具参数不再包在序列化传输对象里");
assert.equal(JSON.stringify(native), before, "阅读投影不修改真实记录");
assert.equal(trajectoryContentBlocks(row({ content: [{ type: "tool_use", input: { rationale: "计划" } }] })).filter(block => block.kind === "thinking").length, 0);
const responses = trajectoryContentBlocks(row({ output: [
  { type: "reasoning", content: [], summary: [{ type: "summary_text", text: "供应商摘要" }] },
  { type: "message", content: [{ type: "output_text", text: "回答" }] },
  { type: "function_call", call_id: "function-a", name: "read_manuscript", arguments: '{"chapter_ids":[]}' },
] }));
assert.deepEqual(responses.map(block => block.kind), ["thinking", "text", "tool-call"]);
assert.equal(responses[0].text, "供应商摘要", "空思考正文不遮蔽供应商实际返回的摘要");
assert.equal(responses[2].text, '{"chapter_ids":[]}');
assert.equal(responses[2].callId, "function-a");

const fallback: TrajectoryRow = { id: "snapshot", type: "assistant", start: event("model_started", {}), end: event("model_completed", { response: {
  thinking_blocks: [{ type: "thinking", thinking: "保存的思考" }], text: "保存的文本",
  tool_calls: [{ call_id: "native", name: "WorkingMemoryPatch", arguments_json: '{"task_goal":{}}' }],
} }) };
assert.deepEqual(trajectoryContentBlocks(fallback).map(block => block.kind), ["thinking", "text", "tool-call"]);

const stream = (event_name: string, payload: Record<string, unknown>, attempt_id = "latest") => event("wire_stream_chunk", { event_name, payload, attempt_id });
const running: TrajectoryRow = { id: "stream", type: "assistant", start: event("model_started", {}),
  attempts: [event("attempt_started", { attempt_id: "latest" })], fragments: [
    stream("content_block_start", { index: 0, content_block: { type: "thinking", thinking: "旧尝试" } }, "earlier"),
    stream("content_block_start", { index: 0, content_block: { type: "thinking", thinking: "" } }),
    stream("content_block_delta", { index: 0, delta: { type: "thinking_delta", thinking: "实际片段" } }),
    stream("content_block_start", { index: 1, content_block: { type: "tool_use", id: "partial", name: "read_manuscript", input: {} } }),
    stream("content_block_delta", { index: 1, delta: { type: "input_json_delta", partial_json: '{"start_order":' } }),
  ] };
const partial = trajectoryContentBlocks(running);
assert.equal(partial[0].text, "实际片段", "重试不拼入旧尝试的思考");
assert.equal(partial[1].text, '{"start_order":', "中断参数保留实际片段，不补造空对象");
const finalStream = { ...running, fragments: [...running.fragments!, stream("response.completed", { response: { output: [
  { type: "reasoning", summary: [{ text: "完整思考" }] }, { type: "message", content: [{ type: "output_text", text: "完整回答" }] },
] } })] };
assert.deepEqual(trajectoryContentBlocks(finalStream).map(block => block.text), ["完整思考", "完整回答"], "完整返回不重复拼接增量");
assert.equal(compactToolArguments('{\n  "text": "有空 格"\n}'), '{"text":"有空 格"}');
assert.equal(trajectoryContentBlocks(row({ content: [{ type: "unknown_extension", payload: "原始内容" }] }))[0].kind, "other");
console.log("轨迹预览、原始内容分块及流式来源边界验证通过");
