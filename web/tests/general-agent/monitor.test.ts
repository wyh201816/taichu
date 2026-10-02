import assert from "node:assert/strict";

import {
  buildGeneralAgentExecutionFlow,
  buildGeneralAgentGraphLayout,
  GENERAL_AGENT_FLOW_ANSWER_ID,
  GENERAL_AGENT_FLOW_ORCHESTRATOR_ID,
  generalAgentPlanRevisions,
  orchestratorTraces,
  tracesForGeneralAgentNode,
} from "../../src/lib/general-agent-monitor";
import type {
  GeneralAgentInvocationTrace,
  GeneralAgentNodeRun,
} from "../../src/lib/types/general-agent";

const nodes = [
  node("evidence", [], 2, "trace_root"),
  node("draft", ["evidence"], 2),
  node("review", ["draft"], 2),
  node("old", [], 1),
];
assert.deepEqual(generalAgentPlanRevisions(nodes), [2, 1]);

const executionFlow = buildGeneralAgentExecutionFlow(nodes.slice(0, 3), "completed");
assert.deepEqual(executionFlow.map(item => item.node_id), [
  GENERAL_AGENT_FLOW_ORCHESTRATOR_ID,
  "evidence",
  "draft",
  "review",
  GENERAL_AGENT_FLOW_ANSWER_ID,
]);
assert.deepEqual(
  executionFlow.find(item => item.node_id === "evidence")?.dependencies,
  [GENERAL_AGENT_FLOW_ORCHESTRATOR_ID],
);
assert.deepEqual(
  executionFlow.find(item => item.node_id === GENERAL_AGENT_FLOW_ANSWER_ID)?.dependencies,
  ["review"],
);

const layout = buildGeneralAgentGraphLayout(executionFlow);
const levels = Object.fromEntries(layout.nodes.map(item => [item.node_id, item.level]));
assert.deepEqual(levels, {
  [GENERAL_AGENT_FLOW_ORCHESTRATOR_ID]: 0,
  evidence: 1,
  draft: 2,
  review: 3,
  [GENERAL_AGENT_FLOW_ANSWER_ID]: 4,
});
assert.ok(layout.width >= 520);
assert.ok(layout.height >= 220);
assert.ok(layout.nodes.every(item => item.x >= 0 && item.y >= 0 && item.x + item.width <= layout.width && item.y + item.height <= layout.height));
for (const target of layout.nodes) {
  for (const dependency of target.dependencies) {
    const source = layout.nodes.find(item => item.node_id === dependency)!;
    assert.ok(source.x + source.width < target.x);
  }
}

const singleNodeLayout = buildGeneralAgentGraphLayout(
  buildGeneralAgentExecutionFlow([], "completed"),
);
assert.equal(
  singleNodeLayout.nodes[0].level,
  0,
);
assert.equal(singleNodeLayout.nodes[1].level, 1);
assert.deepEqual(singleNodeLayout.nodes[1].dependencies, [GENERAL_AGENT_FLOW_ORCHESTRATOR_ID]);

const failedExecutionFlow = buildGeneralAgentExecutionFlow(nodes.slice(0, 1), "failed");
assert.equal(failedExecutionFlow[0].status, "success");
assert.equal(failedExecutionFlow.at(-1)?.status, "failed");

const traces = [
  trace("trace_root", "call_root", null, "subagent", "canon_evidence", "canon_evidence"),
  trace("trace_tool", "call_tool", "call_root", "tool", "read_manuscript", "canon_evidence"),
  trace("trace_llm", "call_llm", "call_root", "llm", "canon_evidence", "canon_evidence"),
  trace(
    "trace_plan",
    "call_plan",
    null,
    "llm",
    "general_writing_orchestrator.plan",
    "general_writing_orchestrator",
  ),
];
assert.deepEqual(
  tracesForGeneralAgentNode(nodes[0], traces).map(item => item.trace_id),
  ["trace_root", "trace_tool", "trace_llm"],
);
assert.deepEqual(orchestratorTraces(traces).map(item => item.trace_id), ["trace_plan"]);

console.log("通用写作助手节点监控逻辑测试通过。");

function node(
  nodeId: string,
  dependencies: string[],
  planRevision: number,
  traceId: string | null = null,
): GeneralAgentNodeRun {
  return {
    node_id: nodeId,
    plan_revision: planRevision,
    kind: "subagent",
    capability_name: "drafting",
    objective: nodeId,
    dependencies,
    status: "success",
    resolved_input: {},
    output: {},
    source_refs: [],
    artifact_refs: [],
    trace_id: traceId,
    authorization_approved: false,
    authorization_second_confirmation: false,
    authorization_resource_scopes: [],
    duration_ms: 1,
  };
}

function trace(
  traceId: string,
  callId: string,
  parentCallId: string | null,
  capabilityType: "tool" | "subagent" | "llm",
  capabilityName: string,
  callerName: string,
): GeneralAgentInvocationTrace {
  return {
    lifecycle: "confirmed",
    trace_id: traceId,
    capability_type: capabilityType,
    capability_name: capabilityName,
    task_id: "task",
    run_id: "run",
    call_id: callId,
    parent_call_id: parentCallId,
    caller_type: "orchestrator",
    caller_name: callerName,
    status: "completed",
    input_sha256: "a".repeat(64),
    input_char_count: 1,
    output_char_count: 1,
    source_count: 0,
    side_effect: "none",
    retry_count: 0,
    started_at: "2026-07-14T00:00:00Z",
    finished_at: "2026-07-14T00:00:00Z",
    duration_ms: 1,
  };
}
