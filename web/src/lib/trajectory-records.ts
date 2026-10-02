import { generalCapabilityLabel } from "./general-agent-display";

export type TrajectoryEvent = {
  schema_version: number; event_id: string; sequence: number;
  conversation_id: string; run_id: string; request_index: number;
  kind: string; recorded_at: string; call_id: string | null;
  parent_call_id: string | null; data: Record<string, unknown>;
};
export type TrajectoryRow = {
  id: string; type: "user" | "assistant" | "tool";
  start: TrajectoryEvent; end?: TrajectoryEvent; requestNumber?: number;
  attempts?: TrajectoryEvent[];
  fragments?: TrajectoryEvent[];
  step?: number | null;
  parent_id?: string;
  subagent_name?: string | null;
  preview?: string;
  tool_count?: number;
  tool_names?: string[];
  tool_records?: { id: string; native_call_id: string }[];
  changed_at?: number;
};
export type TrajectoryTurn = { request_index: number; models: number; tools: number; status: string; complete: boolean; input_tokens: number | null; output_tokens: number | null };
export type TrajectoryActivity = { id: string; type: TrajectoryRow["type"]; request_index: number; step: number | null; start: string; end: string | null };
export type TrajectoryPage = { records: TrajectoryRow[]; turns: TrajectoryTurn[]; timeline: TrajectoryActivity[]; before: number | null; has_earlier: boolean; cursor: number; complete: boolean; warnings: string[] };
export type TrajectorySearch = { hits: { record_id: string; request_index: number; preview: string }[]; total: number; complete: boolean; warnings: string[] };
export type TrajectoryLedgerEntry =
  | { type: "record"; row: TrajectoryRow; turnStart: boolean; modelRequestId: string | null }
  | { type: "turn-summary"; index: number; models: number; tools: number }
  | { type: "call-summary"; row: TrajectoryRow };

/** 只改变阅读投影；折叠保留首条记录及完整汇总，不修改事实记录。 */
export function trajectoryLedgerEntries(trace: TrajectoryPage, collapsedTurns: ReadonlySet<number>, collapsedCalls: ReadonlySet<string>): TrajectoryLedgerEntry[] {
  const result: TrajectoryLedgerEntry[] = [];
  const seen = new Set<number>();
  const turns = new Map(trace.turns.map(turn => [turn.request_index, turn]));
  for (const row of trace.records) {
    const index = row.start.request_index;
    const turnStart = !seen.has(index);
    seen.add(index);
    if (collapsedTurns.has(index)) {
      if (!turnStart) continue;
      result.push({ type: "record", row, turnStart, modelRequestId: row.type === "assistant" ? row.id : row.parent_id ?? null });
      const turn = turns.get(index);
      if (turn && turn.models + turn.tools > 0) result.push({ type: "turn-summary", index, models: turn.models, tools: turn.tools });
      continue;
    }
    if (row.parent_id && collapsedCalls.has(row.parent_id)) continue;
    result.push({ type: "record", row, turnStart, modelRequestId: row.type === "assistant" ? row.id : row.parent_id ?? null });
    if (row.type === "assistant" && row.tool_count && collapsedCalls.has(row.id)) result.push({ type: "call-summary", row });
  }
  return result;
}

/** 等宽排列，或按实际活动时长排列并压去空闲间隔，保留并行重叠。 */
export function trajectoryTimelineSpans(activities: TrajectoryActivity[], actualDuration: boolean) {
  if (!actualDuration) return activities.map((item, index) => ({ ...item, from: index / activities.length, to: (index + 1) / activities.length }));
  const timed = activities.flatMap(item => {
    const start = Date.parse(item.start);
    if (!Number.isFinite(start)) return [];
    const end = Date.parse(item.end ?? item.start);
    return [{ ...item, from: start, to: Number.isFinite(end) ? Math.max(start, end) : start }];
  });
  const offsets = new Map<string, number>();
  let idle = 0;
  let covered: number | null = null;
  for (const item of [...timed].sort((a, b) => a.from - b.from || a.to - b.to)) {
    if (covered !== null && item.from > covered) idle += item.from - covered;
    offsets.set(item.id, idle);
    covered = Math.max(covered ?? item.to, item.to);
  }
  const compressed = timed.map(item => ({ ...item, from: item.from - (offsets.get(item.id) ?? 0), to: item.to - (offsets.get(item.id) ?? 0) }));
  const start = Math.min(...compressed.map(item => item.from));
  const span = Math.max(1, Math.max(...compressed.map(item => item.to)) - start);
  return compressed.map(item => ({ ...item, from: (item.from - start) / span, to: (item.to - start) / span }));
}
export function object(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
}
export function raw(value: unknown): string {
  return JSON.stringify(value, null, 2) ?? "暂无记录";
}
export function rowStatus(row: TrajectoryRow): string {
  if (row.type === "user") return "已记录";
  if (!row.end) return "未记录结束";
  if (row.end.kind === "model_stopped") return "已停止";
  if (row.end.kind === "model_incomplete") return "结束状态不可用";
  if (row.type === "tool" && row.end.data.execution === "reused") return "已复用结果";
  return row.end.kind.endsWith("failed") ? "失败" : "已完成";
}
export function rowContent(row: TrajectoryRow): string {
  if (row.preview !== undefined) return row.preview;
  if (row.type === "user") return String(row.start.data.content ?? "");
  if (row.type === "tool") return row.end ? raw(row.end.data.result ?? row.end.data.error) : "尚无结果";
  return "尚无输出";
}

export function rowLabel(row: TrajectoryRow): string {
  if (row.type === "user") return "用户";
  return row.type === "tool" ? "工具" : row.subagent_name ? "子助手" : "助手";
}

export function rowSubagentLabel(row: TrajectoryRow): string | null {
  return row.subagent_name ? generalCapabilityLabel(row.subagent_name) : null;
}

export function rowPurpose(row: TrajectoryRow): string {
  return generalCapabilityLabel(String(row.type === "tool" ? row.start.data.name ?? "" : object(row.start.data.request).task_name ?? ""));
}
