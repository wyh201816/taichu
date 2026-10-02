import assert from "node:assert/strict";
import { rowContent, rowLabel, rowSubagentLabel, trajectoryLedgerEntries, trajectoryTimelineSpans, type TrajectoryActivity, type TrajectoryPage, type TrajectoryRow } from "../../src/lib/trajectory-records";

const time = (seconds: number) => new Date(Date.UTC(2026, 9, 1) + seconds * 1000).toISOString();
const activity = (id: string, start: number, end: number | null, type: TrajectoryActivity["type"] = "assistant"): TrajectoryActivity => ({ id, type, request_index: 1, step: null, start: time(start), end: end === null ? null : time(end) });
const activities = [activity("user", 0, null, "user"), activity("model", 10, 14), activity("tool-1", 15, 17, "tool"), activity("tool-2", 16, 18, "tool"), activity("running", 100, null)];
const equal = trajectoryTimelineSpans(activities, false);
assert.ok(equal.every(span => Math.abs(span.to - span.from - 0.2) < 1e-9), "等宽模式每个活动宽度一致");
assert.ok(equal.slice(1).every((span, index) => span.from === equal[index].to), "等宽模式不引入按条目比例放大的空隙");
const actual = trajectoryTimelineSpans(activities, true);
assert.equal(actual[0].to, actual[0].from, "输入只有真实时间点");
assert.ok(Math.abs(actual[1].to - 4 / 7) < 1e-9, "模型宽度按四秒实际活动计算");
assert.equal(actual[2].from, actual[1].to, "工具前空闲时间被压去");
assert.ok(actual[3].from < actual[2].to, "两个并行工具保留真实重叠");
assert.equal(actual[4].from, actual[4].to, "未结束的调用不补造运行时长");
assert.deepEqual(trajectoryTimelineSpans([], true), []);
assert.deepEqual(trajectoryTimelineSpans([activity("bad", 0, null)].map(item => ({ ...item, start: "invalid" })), true), []);

const record = (id: string, type: TrajectoryRow["type"], turn: number, parent_id?: string): TrajectoryRow => ({
  id, type, parent_id, step: type === "assistant" ? 1 : null,
  start: { schema_version: 1, event_id: id, sequence: 1, conversation_id: "synthetic", run_id: `run-${turn}`, request_index: turn, kind: "", recorded_at: time(0), call_id: null, parent_call_id: null, data: {} },
});
const model = { ...record("model-1", "assistant", 1), tool_count: 2, tool_names: ["tool-a", "tool-b"] };
assert.equal(rowLabel({ ...model, subagent_name: "drafting" }), "子助手");
assert.equal(rowSubagentLabel({ ...model, subagent_name: "drafting" }), "正文创作");
assert.equal(rowLabel({ ...record("tool", "tool", 1), subagent_name: "drafting" }), "工具", "子助手工具的真实类型不被身份覆盖");
assert.equal(rowLabel(model), "助手");
assert.equal(rowContent({ ...model, preview: "真实思考开头", start: { ...model.start, data: { request: { messages: [] } } } }), "真实思考开头", "完整详情与分页记录均使用同一真实摘要，不重新推断业务字段");
const trace: TrajectoryPage = {
  records: [record("user-1", "user", 1), model, record("tool-a", "tool", 1, model.id), record("tool-b", "tool", 1, model.id), record("user-2", "user", 2), record("model-2", "assistant", 2)],
  turns: [1, 2].map(request_index => ({ request_index, models: 1, tools: request_index === 1 ? 2 : 0, status: "completed", complete: true, input_tokens: null, output_tokens: null })),
  timeline: [], before: null, has_earlier: false, cursor: 1, complete: true, warnings: [],
};
const foldedTurn = trajectoryLedgerEntries(trace, new Set([1]), new Set());
assert.deepEqual(foldedTurn.map(item => item.type === "record" ? item.row.id : item.type), ["user-1", "turn-summary", "user-2", "model-2"], "折叠轮次保留用户与汇总，不隐藏另一轮");
assert.deepEqual(foldedTurn[1], { type: "turn-summary", index: 1, models: 1, tools: 2 });
const foldedCall = trajectoryLedgerEntries(trace, new Set(), new Set([model.id]));
assert.deepEqual(foldedCall.map(item => item.type === "record" ? item.row.id : item.type), ["user-1", model.id, "call-summary", "user-2", "model-2"], "调用折叠保留助手与工具汇总");
const partial = trajectoryLedgerEntries({ ...trace, records: trace.records.slice(0, 2) }, new Set([1]), new Set());
assert.deepEqual(partial[1], foldedTurn[1], "未加载工具不会缩小轮次完整汇总");
assert.equal(trajectoryLedgerEntries(trace, new Set(), new Set()).length, trace.records.length, "展开后恢复原始记录");
const grouped = trajectoryLedgerEntries(trace, new Set(), new Set()).filter(item => item.type === "record");
assert.deepEqual(grouped.map(item => item.modelRequestId), [null, model.id, model.id, model.id, null, "model-2"], "用户不属于模型请求，同一次请求的助手和工具共享身份");
const interleaved = trajectoryLedgerEntries({ ...trace, records: [model, trace.records[5], trace.records[2]] }, new Set(), new Set());
assert.deepEqual(interleaved.filter(item => item.type === "record").map(item => item.modelRequestId), [model.id, "model-2", model.id], "工具按真实父调用分组，不误归给最近出现的助手");
console.log("轨迹等宽、实际时长、并行重叠、轮次与调用折叠验证通过");
