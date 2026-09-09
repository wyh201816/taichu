import { generalCapabilityLabel, generalRunStatusLabels, isGeneralAgentRunActive } from "./general-agent-display";
import type {
  GeneralAgentContextSnapshot, GeneralAgentInvocationTrace, GeneralAgentLLMReplay,
  GeneralAgentNodeRun, GeneralAgentRun,
} from "./types/general-agent";

export type TrajectoryKind = "user" | "state" | "context" | "request" | "model" | "tool" | "subagent" | "human" | "answer";
export type TrajectoryStatus = "recorded" | "running" | "completed" | "failed" | "waiting" | "skipped" | "interrupted";
export type TrajectoryEvidence = {
  traces: GeneralAgentInvocationTrace[];
  calls: GeneralAgentLLMReplay[];
  snapshots: GeneralAgentContextSnapshot[];
};
export type TrajectoryItem = {
  id: string;
  kind: TrajectoryKind;
  title: string;
  summary: string;
  startedAt: string;
  finishedAt?: string | null;
  durationMs: number | null;
  status: TrajectoryStatus;
  source: string;
  parentId?: string;
  inferred?: boolean;
  notice?: string;
  trace?: GeneralAgentInvocationTrace;
  replay?: GeneralAgentLLMReplay;
  node?: GeneralAgentNodeRun;
  snapshot?: GeneralAgentContextSnapshot;
};

export const trajectoryKindLabels: Record<TrajectoryKind, string> = {
  user: "作者输入", state: "运行状态", context: "上下文", request: "请求快照",
  model: "模型", tool: "工具", subagent: "专业智能体", human: "人工介入", answer: "助手回复",
};
export const trajectoryStatusLabels: Record<TrajectoryStatus, string> = {
  recorded: "已记录", running: "运行中", completed: "已完成", failed: "失败",
  waiting: "等待作者", skipped: "已跳过", interrupted: "已中断（推断）",
};

// 已注册的编排与子智能体输出契约；不能把任意 *Output 名称猜成结构化输出。
export const trajectoryOutputLabels: Record<string, string> = {
  GeneralAgentPlanDraft: "执行计划", GeneralAgentVerification: "结果校验",
  CanonEvidenceOutput: "小说事实取证", ExternalResearchOutput: "外部资料研究", NarrativeSummaryOutput: "叙事摘要",
  WorldbuildingOutput: "世界观设定", CharacterOutput: "人物分析", StoryArchitectureOutput: "剧情架构",
  ScenePlanningOutput: "场景规划", DraftingOutput: "正文草稿", RevisionOutput: "正文修订",
  ConsistencyReviewOutput: "一致性审查", NarrativeReviewOutput: "叙事审查", StyleReviewOutput: "文风审查",
};

export function trajectoryCapabilityLabel(name: string): string {
  const labels: Record<string, string> = {
    "general_writing_orchestrator.plan.materialize": "补齐节点参数",
    "general_writing_orchestrator.replan": "调整执行路径",
    "general_writing_orchestrator.replan.materialize": "补齐重规划节点参数",
    maintain_working_memory: "维护工作记忆",
  };
  return labels[name] ?? (trajectoryOutputLabels[name] ? `${trajectoryOutputLabels[name]}输出` : generalCapabilityLabel(name));
}

/** 仅投影已有证据，不反向修改执行状态，不以时间邻近伪造跨仓储关联。 */
export function buildGeneralAgentTrajectory(run: GeneralAgentRun, evidence: TrajectoryEvidence): TrajectoryItem[] {
  const items: TrajectoryItem[] = [];
  const add = (item: Omit<TrajectoryItem, "durationMs" | "status"> & Partial<Pick<TrajectoryItem, "durationMs" | "status">>) => {
    items.push({ durationMs: null, status: "recorded", ...item });
  };
  add({ id: `user:${run.run_id}`, kind: "user", title: "作者 · 当前请求", summary: run.user_goal,
    startedAt: run.created_at, source: "运行记录中的用户原文" });
  let initialMessageSkipped = false;
  let finalMessageRecorded = false;
  run.messages.forEach((message, index) => {
    // 运行中保存的 messages 可能包含累计对话；只投影本轮，不按相同文本猜测归属。
    if (message.turn_id && message.turn_id !== run.run_id) return;
    if (!message.turn_id && message.request_index && message.request_index !== run.request_index) return;
    if (message.role === "user" && !initialMessageSkipped && (run.current_request_message_id
      ? message.message_id === run.current_request_message_id : message.content === run.user_goal)) {
      initialMessageSkipped = true;
      return;
    }
    if (message.role === "assistant" && message.content === run.final_answer) finalMessageRecorded = true;
    add({ id: `message:${run.run_id}:${message.message_id ?? index}`, kind: message.role === "user" ? "user" : "answer",
      title: message.role === "user" ? "作者 · 补充输入" : "助手 · 已展示回复",
      summary: message.content, startedAt: message.created_at, source: "已保存的原始对话消息" });
  });
  run.lifecycle_events.forEach((event, index) => {
    add({ id: `state:${run.run_id}:${index}`, kind: "state", title: `运行 · ${generalRunStatusLabels[event.status]}`,
      summary: event.reason, startedAt: event.created_at, source: "运行生命周期记录",
      status: event.status === "waiting_human" ? "waiting"
        : ["failed", "timeout"].includes(event.status) ? "failed"
          : event.status === "cancelled" ? "skipped" : "recorded" });
  });
  const snapshots = new Map(evidence.snapshots.filter(item => item.run_id === run.run_id).map(item => [item.snapshot_id, item]));
  for (const snapshot of snapshots.values()) {
    const phase = { plan: "规划前", replan: "重规划前", verify: "结果校验前" }[snapshot.phase];
    add({ id: `context:${snapshot.snapshot_id}`, kind: "context", title: `上下文 · ${phase}`,
      summary: `${snapshot.envelope.compressed ? "已压缩 · " : ""}五层记忆投影，约 ${snapshot.envelope.estimated_token_count.toLocaleString("zh-CN")} 个词元`,
      startedAt: snapshot.created_at, source: "已保存的上下文快照", snapshot,
      notice: snapshot.envelope.compressed ? "压缩标记来自该次上下文快照；未单独记录压缩起止时间。" : undefined });
  }
  const calls = new Map(evidence.calls.filter(call => call.run_id === run.run_id).map(call => [call.call_id, call]));
  const traces = new Map(evidence.traces.filter(trace => trace.run_id === run.run_id).map(trace => [trace.call_id, trace]));
  const nodesByTrace = new Map(run.node_runs.filter(node => node.trace_id).map(node => [node.trace_id, node]));
  const linkedReplays = new Set<string>();
  for (const trace of traces.values()) {
    const kind = trace.capability_type === "llm" ? "model" : trace.capability_type;
    const node = nodesByTrace.get(trace.trace_id);
    const replay = kind === "model" ? calls.get(trace.call_id) : undefined;
    if (replay) linkedReplays.add(replay.call_id);
    const parent = safeParent(trace, traces);
    add({ id: `call:${trace.call_id}`, kind, title: `${trajectoryKindLabels[kind]} · ${trajectoryCapabilityLabel(trace.capability_name)}`,
      summary: node?.objective || (trace.status === "completed" ? "调用已返回，点击查看证据" : "调用异常，点击查看详情"),
      startedAt: trace.started_at, finishedAt: trace.finished_at,
      durationMs: recordedDuration(trace.duration_ms, trace.finished_at),
      status: trace.status === "completed" ? "completed" : "failed", source: "能力调用追踪",
      parentId: parent.id, notice: parent.notice, trace, node, replay });
  }
  // 回放编号和调用追踪编号可能属于不同命名空间；无明确关联时保留独立请求证据，不计作第二次执行。
  for (const call of calls.values()) {
    if (linkedReplays.has(call.call_id)) continue;
    add({ id: `request:${call.call_id}`, kind: "request", title: `请求快照 · ${trajectoryCapabilityLabel(call.task_name)}`,
      summary: `${call.messages.length} 条消息 · ${call.tools.length} 项原生工具定义 · 含模型响应`,
      startedAt: call.started_at, finishedAt: call.finished_at, durationMs: recordedDuration(call.duration_ms, call.finished_at),
      status: call.status === "completed" ? "completed" : "failed", source: "脱敏模型回放", replay: call,
      snapshot: call.context_snapshot_id ? snapshots.get(call.context_snapshot_id) : undefined,
      notice: "请求快照与调用追踪未通过同一调用编号关联，独立展示；不重复计入时间概览的调用数量。" });
  }
  const knownTraceIds = new Set([...traces.values()].map(trace => trace.trace_id));
  const terminal = !isGeneralAgentRunActive(run.status) && run.status !== "waiting_human";
  for (const node of run.node_runs) {
    if (node.trace_id && knownTraceIds.has(node.trace_id)) continue;
    const interrupted = terminal && node.status === "running";
    const status: TrajectoryStatus = interrupted ? "interrupted" : ({
      pending: "recorded", running: "running", success: "completed", failed: "failed",
      skipped: "skipped", waiting_human: "waiting",
    } as const)[node.status];
    add({ id: `node:${run.run_id}:${node.plan_revision}:${node.node_id}`, kind: node.kind,
      title: `${trajectoryKindLabels[node.kind]} · ${trajectoryCapabilityLabel(node.capability_name)}`,
      summary: node.objective, startedAt: node.started_at ?? "", finishedAt: node.finished_at,
      durationMs: node.status === "running" ? null : recordedDuration(node.duration_ms, node.finished_at),
      status, source: "节点运行记录", node, inferred: interrupted,
      notice: interrupted ? "运行已结束，但该节点没有结束记录。中断状态由界面推断，未回写运行数据。"
        : "尚无关联调用追踪，仅展示节点记录；缺失的时间不作估算。" });
  }
  if (run.pending_human_request) {
    const request = run.pending_human_request;
    add({ id: `human:${request.request_id}`, kind: "human", title: "人工介入 · 等待作者决定",
      summary: request.prompt, startedAt: request.created_at, status: "waiting", source: "当前人工介入请求" });
  }
  if (run.final_answer && !finalMessageRecorded) {
    add({ id: `answer:${run.run_id}`, kind: "answer", title: "助手 · 本轮回答", summary: run.final_answer,
      startedAt: run.finished_at ?? run.updated_at, source: "运行最终回答" });
  }
  const order: Record<TrajectoryKind, number> = { user: 0, state: 1, context: 2, request: 3, subagent: 4, model: 5, tool: 6, human: 7, answer: 8 };
  return items.sort((left, right) => (parseTime(left.startedAt) ?? Number.MAX_SAFE_INTEGER) - (parseTime(right.startedAt) ?? Number.MAX_SAFE_INTEGER)
    || order[left.kind] - order[right.kind] || left.id.localeCompare(right.id));
}

function safeParent(trace: GeneralAgentInvocationTrace, traces: Map<string, GeneralAgentInvocationTrace>): { id?: string; notice?: string } {
  if (!trace.parent_call_id) return {};
  if (!traces.has(trace.parent_call_id)) return { notice: "父调用不在当前追踪窗口中，未补造父节点。" };
  const seen = new Set([trace.call_id]);
  let cursor: string | null | undefined = trace.parent_call_id;
  while (cursor) {
    if (seen.has(cursor) || seen.size > 256) return { notice: "调用关系存在循环或超出层级限制，已停止展开。" };
    seen.add(cursor);
    cursor = traces.get(cursor)?.parent_call_id;
  }
  return { id: `call:${trace.parent_call_id}` };
}

function recordedDuration(value: number, finishedAt?: string | null): number | null {
  return finishedAt && Number.isFinite(value) && value >= 0 ? value : null;
}
export function parseTime(value?: string | null): number | null {
  const time = value ? Date.parse(value) : NaN;
  return Number.isFinite(time) ? time : null;
}
export function trajectoryDuration(value: number | null): string {
  if (value === null) return "未记录耗时";
  if (value < 1000) return `${Math.round(value)} 毫秒`;
  if (value < 60000) return `${(value / 1000).toFixed(1)} 秒`;
  return `${Math.floor(value / 60000)} 分 ${Math.round(value % 60000 / 1000)} 秒`;
}
