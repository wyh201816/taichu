import assert from "node:assert/strict";
import { buildTrajectoryLedger, buildTrajectoryStrip, filterTrajectoryLedger, ledgerInputOutput, trajectoryPreview } from "../../src/lib/general-agent-trajectory-ledger";
import type { GeneralAgentLLMReplay, GeneralAgentRun } from "../../src/lib/types/general-agent";
const start = "2026-09-04T00:00:00Z";
const run = { run_id: "run", request_index: 1, user_goal: "作者原文", created_at: start, started_at: start,
  status: "completed", messages: [], lifecycle_events: [], node_runs: [], final_answer: "", updated_at: start,
} as unknown as GeneralAgentRun;
const first = { call_id: "request-a", run_id: "run", task_name: "character", started_at: start,
  finished_at: "2026-09-04T00:00:03Z", duration_ms: 3000, status: "completed", response_text: "",
  response_tool_calls: [{ call_id: "native-a", name: "read_manuscript", arguments_json: '{"chapter_ids":["第一章"]}' }],
  messages: [], tools: [],
} as unknown as GeneralAgentLLMReplay;
const second = { ...first, call_id: "request-b", started_at: "2026-09-04T00:00:05Z", finished_at: "2026-09-04T00:00:06Z",
  response_text: "已查到人物经历", response_tool_calls: [], messages: [
    { role: "tool", tool_call_id: "native-a", content: "真正的正文结果", tool_calls: [], is_error: false },
  ],
} as GeneralAgentLLMReplay;
const evidence = { traces: [], snapshots: [], calls: [second, first] };
const rows = buildTrajectoryLedger([{ run, evidence }], "messages");
const tool = rows.find(item => item.nativeCall);
assert.equal(tool?.nativeCall?.output, "真正的正文结果");
assert.equal(tool?.nativeCall?.input, first.response_tool_calls[0].arguments_json);
assert.equal(tool?.startedAt, "", "回填观察时间不能冒充工具执行时间");
assert.equal(tool?.parentId, "run/request:request-a");
assert.equal(rows.filter(item => item.nativeCall).length, 1);
assert.ok(rows.find(item => item.id.endsWith("request:request-a"))?.summary.includes("仅工具调用"));
const noResult = buildTrajectoryLedger([{ run, evidence: { ...evidence, calls: [first] } }], "messages");
assert.equal(noResult.find(item => item.nativeCall)?.status, "recorded", "未回填结果不等于仍在运行");
const conflict = buildTrajectoryLedger([{ run, evidence: { ...evidence, calls: [first, second,
  { ...second, call_id: "request-c", messages: [{ ...second.messages[0], content: "不同结果" }] },
] } }], "messages");
assert.match(conflict.find(item => item.nativeCall)?.notice ?? "", /冲突/);
assert.equal(conflict.find(item => item.nativeCall)?.nativeCall?.output, undefined);
assert.equal(ledgerInputOutput(conflict.find(item => item.nativeCall)!)?.output, "结果存在冲突");
assert.equal(filterTrajectoryLedger(rows, "全部", "真正的正文结果").length, 1);
assert.ok(trajectoryPreview({ content: "正文段落", other: "其他" }).includes("正文段落"));
assert.equal(trajectoryPreview(JSON.stringify(JSON.stringify({ chunks: [{ title: "第九十九章", content: "真实正文" }] }))), "1 段正文 · 第九十九章");
assert.equal(trajectoryPreview({ query: "秦浩轩", evidences: [{ text: "真实召回片段" }] }), "1 条证据 · 真实召回片段");
assert.equal(trajectoryPreview({ chapter_ids: ["chapter-abc", "chapter-def"] }), "2 章正文（编号见详情）");
assert.match(trajectoryPreview("I have enough context now to analyze the manuscript."), /非中文原文/);
const structuredCall = { ...first, response_tool_calls: [{ call_id: "native-output", name: "CharacterOutput", arguments_json: '{"analysis":"人物动机分析"}' }],
  tools: [{ name: "CharacterOutput", description: "", input_schema: {} }] } as unknown as GeneralAgentLLMReplay;
const structuredRows = buildTrajectoryLedger([{ run, evidence: { ...evidence, calls: [structuredCall] } }], "messages");
const structured = structuredRows.find(row => row.nativeCall)!;
assert.equal(structured.kind, "request");
assert.equal(structured.nativeCall?.structured, true);
assert.equal(structured.status, "recorded", "已记录结构化输出不等于业务校验通过");
assert.equal(ledgerInputOutput(structured), null, "结构化输出不能伪装成外部工具输入输出");
assert.match(structured.title, /人物分析输出/);
assert.equal(trajectoryPreview(structured.summary), "人物动机分析");
const badFeedback = { ...second, messages: [{ ...second.messages[0], tool_call_id: "native-output", is_error: false,
  content: "Error: Failed to parse structured output for tool 'CharacterOutput': missing analysis" }] };
const invalidOutput = buildTrajectoryLedger([{ run, evidence: { ...evidence, calls: [structuredCall, badFeedback] } }], "messages").find(row => row.nativeCall)!;
assert.equal(invalidOutput.status, "failed");
assert.equal(invalidOutput.inferred, true);
assert.match(invalidOutput.notice ?? "", /原始错误标记为否/);
assert.equal(badFeedback.messages[0].is_error, false, "界面推断不得修改原始错误标记");
assert.equal(filterTrajectoryLedger([invalidOutput], "异常", "").length, 1);
assert.equal(filterTrajectoryLedger([structured], "tool", "").length, 0);
assert.deepEqual(buildTrajectoryStrip([], "duration"), []);
assert.deepEqual(buildTrajectoryStrip([{ ...rows[0], startedAt: "invalid" }], "duration"), []);
const unknownRows = buildTrajectoryLedger([{ run, evidence: { ...evidence, calls: [{ ...structuredCall,
  response_tool_calls: [{ ...structuredCall.response_tool_calls[0], name: "UnknownOutput" }],
}] } }], "messages");
assert.equal(unknownRows.find(row => row.nativeCall)?.kind, "tool", "不能按名称后缀猜测输出契约");
const secondRun = { ...run, run_id: "run-2", request_index: 2 };
const both = buildTrajectoryLedger([{ run, evidence }, { run: secondRun, evidence: {
  ...evidence, calls: [{ ...first, run_id: "run-2" }],
} }], "messages");
assert.equal(new Set(both.map(item => item.id)).size, both.length, "跨轮次同名调用的界面身份必须隔离");
assert.equal(both.filter(item => item.nativeCall)[1]?.nativeCall?.output, undefined, "不能跨轮次回填结果");
for (const mode of ["duration", "turns", "calls"] as const) {
  const strip = buildTrajectoryStrip(rows, mode);
  assert.ok(strip.every(mark => mark.left >= 0 && mark.left <= 100 && mark.width > 0 && mark.width <= 100));
  assert.ok(strip.some(mark => mark.lane === "input"));
  assert.ok(strip.some(mark => mark.lane === "model"));
}
console.log("轨迹账本：真实工具配对、冲突保真、搜索与时序坐标测试通过");
