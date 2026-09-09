import assert from "node:assert/strict";

import {
  buildGeneralAgentTrajectory,
} from "../../src/lib/general-agent-trajectory";
import type {
  GeneralAgentInvocationTrace,
  GeneralAgentContextSnapshot,
  GeneralAgentLLMReplay,
  GeneralAgentNodeRun,
  GeneralAgentRun,
} from "../../src/lib/types/general-agent";

const start = "2026-09-04T10:00:00Z";
const end = "2026-09-04T10:00:10Z";
const node = {
  node_id: "read", plan_revision: 1, kind: "tool", capability_name: "read_manuscript",
  objective: "查阅正文", trace_id: "trace-tool", status: "success", started_at: start,
  finished_at: end, duration_ms: 10000, resolved_input: { chapter: "第一章" },
  output: { content: "正文证据" }, source_refs: ["chapter:1"], artifact_refs: [],
  dependencies: [], authorization_approved: false, authorization_second_confirmation: false,
  authorization_resource_scopes: [],
} as GeneralAgentNodeRun;
const run = {
  run_id: "run", conversation_id: "conversation", request_index: 1,
  user_goal: "查阅第一章", status: "completed", created_at: start, started_at: start,
  updated_at: end, finished_at: end, final_answer: "查阅完成", errors: [],
  messages: [{ role: "user", content: "查阅第一章", created_at: start },
    { role: "assistant", content: "查阅完成", created_at: end }],
  node_runs: [node], lifecycle_events: [{ status: "completed", reason: "完成", created_at: end }],
} as unknown as GeneralAgentRun;
function trace(overrides: Partial<GeneralAgentInvocationTrace> = {}): GeneralAgentInvocationTrace {
  return {
    trace_id: "trace-tool", call_id: "tool-call", run_id: "run", capability_type: "tool",
    capability_name: "read_manuscript", status: "completed", started_at: start,
    finished_at: end, duration_ms: 10000, parent_call_id: null,
    ...overrides,
  } as GeneralAgentInvocationTrace;
}
const replay = {
  call_id: "replay-id", run_id: "run", task_name: "character", started_at: start,
  finished_at: end, duration_ms: 10000, status: "completed", messages: [], tools: [],
  response_tool_calls: [], response_text: "人物分析结果", redaction_count: 0,
} as unknown as GeneralAgentLLMReplay;
const evidence = { traces: [trace()], calls: [replay], snapshots: [] };
const items = buildGeneralAgentTrajectory(run, evidence);
assert.equal(items.filter(item => item.kind === "tool").length, 1, "节点与追踪只保留一个动作");
assert.equal(items.find(item => item.kind === "tool")?.node, node);
assert.equal(items.filter(item => item.kind === "answer").length, 1, "不重复最终回复");
assert.equal(items.find(item => item.kind === "user")?.summary, run.user_goal);
const cumulative = buildGeneralAgentTrajectory({ ...run, request_index: 2, current_request_message_id: "own-input", messages: [
  { role: "user", content: run.user_goal, created_at: start, turn_id: "old-run", request_index: 1, message_id: "old-input" },
  { role: "assistant", content: "历史回答", created_at: start, turn_id: "old-run", request_index: 1 },
  { role: "user", content: run.user_goal, created_at: start, turn_id: run.run_id, request_index: 2, message_id: "own-input" },
  { role: "user", content: run.user_goal, created_at: end, turn_id: run.run_id, request_index: 2, message_id: "human-repeated" },
] }, evidence);
assert.equal(cumulative.some(item => item.summary === "历史回答"), false, "累计历史不能冒充本轮事件");
assert.equal(cumulative.filter(item => item.kind === "user").length, 2, "相同文本的本轮人工回答必须保留");
assert.ok(cumulative.some(item => item.id.endsWith("human-repeated")), "消息使用真实稳定标识");
assert.ok(items.some(item => item.kind === "request" && item.replay === replay));

const separate = buildGeneralAgentTrajectory(run, {
  ...evidence,
  traces: [trace({ capability_type: "llm", capability_name: "character", call_id: "different-id" })],
});
assert.equal(separate.find(item => item.kind === "model")?.replay, undefined,
  "名称和时间一致仍不能伪造调用编号关联");
const matched = buildGeneralAgentTrajectory(run, {
  ...evidence, traces: [trace({ capability_type: "llm", call_id: replay.call_id })],
});
assert.equal(matched.find(item => item.kind === "model")?.replay, replay);
assert.equal(matched.filter(item => item.kind === "request").length, 0);

const nested = buildGeneralAgentTrajectory(run, {
  calls: [], snapshots: [], traces: [
    trace({ trace_id: "child", call_id: "child", parent_call_id: "parent" }),
    trace({ trace_id: "parent", call_id: "parent", capability_type: "subagent" }),
    trace({ trace_id: "orphan", call_id: "orphan", parent_call_id: "absent" }),
    trace({ trace_id: "cycle-a", call_id: "a", parent_call_id: "b" }),
    trace({ trace_id: "cycle-b", call_id: "b", parent_call_id: "a" }),
  ],
});
assert.equal(nested.find(item => item.trace?.call_id === "child")?.parentId, "call:parent");
assert.ok(nested.find(item => item.trace?.call_id === "orphan")?.notice);
assert.equal(nested.find(item => item.trace?.call_id === "a")?.parentId, undefined);

const pending = buildGeneralAgentTrajectory({
  ...run, node_runs: [{ ...node, trace_id: null, status: "running", finished_at: null }],
}, { traces: [], calls: [], snapshots: [] });
assert.equal(pending.find(item => item.kind === "tool")?.status, "interrupted");
assert.equal(pending.find(item => item.kind === "tool")?.durationMs, null);
assert.ok(pending.find(item => item.kind === "tool")?.inferred);
const active = buildGeneralAgentTrajectory({ ...run, status: "executing", finished_at: null,
  node_runs: [{ ...node, status: "running", trace_id: null, finished_at: null }],
}, { traces: [], calls: [], snapshots: [] });
assert.equal(active.find(item => item.kind === "tool")?.status, "running");
assert.equal(active.find(item => item.kind === "tool")?.durationMs, null);

const shuffled = buildGeneralAgentTrajectory(run, { ...evidence, traces: [...evidence.traces].reverse() });
assert.deepEqual(items.map(item => item.id), shuffled.map(item => item.id));
assert.equal(new Set(items.map(item => item.id)).size, items.length);

const context = { snapshot_id: "snapshot", run_id: "run", created_at: start, phase: "verify",
  envelope: { compressed: true, estimated_token_count: 42 },
} as GeneralAgentContextSnapshot;
const contextItems = buildGeneralAgentTrajectory(run, { ...evidence, snapshots: [context] });
assert.equal(contextItems.find(item => item.kind === "context")?.snapshot, context);
assert.match(contextItems.find(item => item.kind === "context")?.summary ?? "", /已压缩/);
assert.equal(contextItems.find(item => item.kind === "context")?.durationMs, null, "不估算压缩耗时");
const isolated = buildGeneralAgentTrajectory(run, { traces: [trace({ run_id: "other" })],
  calls: [{ ...replay, run_id: "other" }], snapshots: [{ ...context, run_id: "other" }] });
assert.equal(isolated.some(item => item.trace || item.replay || item.snapshot), false, "外部轮次证据不能串入当前请求");
const human = buildGeneralAgentTrajectory({ ...run, status: "waiting_human", messages: [...run.messages,
  { role: "user", content: "  同意\n保留原文  ", created_at: end }], pending_human_request: {
    request_id: "human", prompt: "是否写入？", created_at: end,
  } as GeneralAgentRun["pending_human_request"],
}, { traces: [], calls: [], snapshots: [] });
assert.equal(human.find(item => item.kind === "human")?.status, "waiting");
assert.ok(human.some(item => item.summary === "  同意\n保留原文  "), "人工答复保持原样");
assert.deepEqual(buildGeneralAgentTrajectory(run, evidence), buildGeneralAgentTrajectory(run, evidence), "重复投影不混入浏览器当前时间");

console.log("执行轨迹：关联、轮次归属、去重、排序与状态推断测试通过");
