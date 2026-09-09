import { buildGeneralAgentTrajectory, parseTime, trajectoryCapabilityLabel, trajectoryOutputLabels, trajectoryStatusLabels, type TrajectoryEvidence, type TrajectoryItem } from "./general-agent-trajectory";
import type { GeneralAgentLLMReplayMessage, GeneralAgentRun } from "./types/general-agent";

export type TrajectoryRecord = { run: GeneralAgentRun; evidence: TrajectoryEvidence };
export type LedgerSource = "messages" | "calls" | "all";
export type StripMode = "duration" | "turns" | "calls";
export type LedgerRow = TrajectoryItem & {
  runId: string;
  requestIndex: number;
  positionAt: string;
  nativeCall?: { callId: string; name: string; input: string; output?: string; schema?: unknown; structured?: boolean; resultNotice?: string };
};
export type StripMark = { id: string; lane: "input" | "model" | "tools"; left: number; width: number; point: boolean; row: LedgerRow };

/** 消息回放与执行追踪是两种证据视角，不以相近时间猜测关联。 */
export function buildTrajectoryLedger(records: TrajectoryRecord[], source: LedgerSource): LedgerRow[] {
  const rows: LedgerRow[] = [];
  for (const { run, evidence } of [...records].sort((a, b) => a.run.request_index - b.run.request_index)) {
    const calls = evidence.calls.filter(call => call.run_id === run.run_id);
    const results = new Map<string, GeneralAgentLLMReplayMessage[]>();
    for (const call of calls) for (const message of call.messages) {
      if (message.role !== "tool" || !message.tool_call_id) continue;
      results.set(message.tool_call_id, [...(results.get(message.tool_call_id) ?? []), message]);
    }
    const includedNative = new Set<string>();
    const base = buildGeneralAgentTrajectory(run, evidence);
    for (const item of base) {
      const isReplay = Boolean(item.replay);
      if (source === "calls" && (item.kind === "request" || item.kind === "context")) continue;
      if (source === "messages" && calls.length && item.trace && !isReplay && !item.node) continue;
      const row: LedgerRow = { ...item, id: `${run.run_id}/${item.id}`,
        parentId: item.parentId ? `${run.run_id}/${item.parentId}` : undefined,
        runId: run.run_id, requestIndex: run.request_index, positionAt: item.startedAt };
      if (isReplay && source !== "calls") {
        row.title = `助手 · ${trajectoryCapabilityLabel(item.replay!.task_name)}`;
        const requests = item.replay!.response_tool_calls;
        row.summary = item.replay!.response_text || (requests.length
          ? `${requests.every(request => trajectoryOutputLabels[request.name]) ? "仅结构化输出" : "仅工具调用"} · ${requests.length} 项` : "未返回文本内容");
      }
      rows.push(row);
      if (!item.replay || source === "calls") continue;
      for (const request of item.replay.response_tool_calls) {
        if (includedNative.has(request.call_id)) continue;
        includedNative.add(request.call_id);
        const observations = results.get(request.call_id) ?? [];
        const variants = new Set(observations.map(result => JSON.stringify([result.content, result.is_error, result.tool_name])));
        const conflict = variants.size > 1;
        const result = !conflict ? observations[0] : undefined;
        const name = trajectoryCapabilityLabel(request.name);
        const schema = item.replay.tools.find(tool => tool.name === request.name);
        const structured = Boolean(schema && trajectoryOutputLabels[request.name]);
        const feedbackFailure = structured && !result?.is_error && result?.content.startsWith("Error: Failed to parse structured output");
        const resultNotice = conflict ? "结果存在冲突" : structured ? "未记录校验反馈" : "未记录配对结果";
        rows.push({
          id: `${run.run_id}/native:${request.call_id}`, kind: structured ? "request" : "tool", title: `${structured ? "输出" : "工具"} · ${name}`,
          summary: structured ? request.arguments_json : `${trajectoryPreview(request.arguments_json)} → ${result ? trajectoryPreview(result.content) : resultNotice}`,
          runId: run.run_id, requestIndex: run.request_index,
          startedAt: "", positionAt: item.replay.finished_at, durationMs: null,
          status: result ? (result.is_error || feedbackFailure ? "failed" : "completed") : "recorded",
          inferred: feedbackFailure || undefined,
          source: structured ? "模型原生结构化输出" : "模型原生工具请求与配对结果", parentId: row.id,
          nativeCall: { callId: request.call_id, name: request.name, input: request.arguments_json,
            output: result?.content, schema, structured, resultNotice },
          notice: conflict ? "同一原生调用编号出现相互冲突的结果，未擅自选择其中一份。"
            : feedbackFailure ? "原始错误标记为否，但配对反馈明确报告结构化校验失败；界面依据反馈标注失败，未修改原始记录。"
              : structured ? "这是通过原生工具协议返回的结构化内容，不代表调用了外部工具；输出已记录不等于业务校验通过。"
              : "按原生调用编号配对，跟随所属模型请求排列；请求回填时间不等于工具执行时间。",
        });
      }
    }
  }
  // 保留每份请求后紧随的原生工具行；不同轮次不按不可靠的时钟重新混排。
  return rows;
}

/** 仅生成一行中文内容预览；全文和机器字段仍保存在详情。 */
export function trajectoryPreview(value: unknown, limit = 180, depth = 0): string {
  if (depth > 6) return "嵌套内容（详情查看）";
  if (value === null || value === undefined) return "未记录";
  if (typeof value === "string") {
    const text = value.trim();
    if (depth < 4 && /^[{"\[]/.test(text)) {
      try { return trajectoryPreview(JSON.parse(text), limit, depth + 1); } catch { /* 原样显示非 JSON 文本 */ }
    }
    const compact = text.replace(/\s+/g, " ");
    const enumLabels: Record<string, string> = { draft: "待确认", confirmed: "已确认", rejected: "已拒绝", satisfied: "满足目标", unique: "唯一匹配", none: "无匹配", ambiguous: "多个匹配" };
    if (enumLabels[compact]) return enumLabels[compact];
    if (/^Error: Failed to parse structured output/.test(compact)) return "结构化输出校验失败，原文见详情";
    const head = compact.slice(0, 100);
    if (head.length > 24 && (head.match(/[a-z]/gi)?.length ?? 0) > (head.match(/[\u4e00-\u9fff]/g)?.length ?? 0) * 2) return `非中文原文 · ${compact.length} 字符（详情查看）`;
    return compact.length > limit ? `${compact.slice(0, limit)}…` : compact || "无文本";
  }
  if (Array.isArray(value)) return value.length
    ? `${value.slice(0, 2).map(item => trajectoryPreview(item, Math.floor(limit / 2), depth + 1)).join("；")}${value.length > 2 ? ` … 共 ${value.length} 项` : ""}` : "空列表";
  if (typeof value === "object") {
    const object = value as Record<string, unknown>;
    for (const [field, label] of [["chunks", "段正文"], ["evidences", "条证据"], ["cards", "张知识卡"], ["items", "项记录"], ["matches", "项匹配"]]) {
      const entries = object[field];
      if (Array.isArray(entries)) {
        const names = entries.slice(0, 2).map(entry => trajectoryPreview(entry, Math.floor(limit / 2), depth + 1)).join("；");
        return `${entries.length} ${label}${names ? ` · ${names}` : ""}`;
      }
    }
    if (typeof object.total_chapters === "number") return `共 ${object.total_chapters} 章 · 返回 ${object.returned_chapters ?? "未记录"} 章`;
    if (Array.isArray(object.chapter_ids) && object.chapter_ids.length) return `${object.chapter_ids.length} 章正文（编号见详情）`;
    if (Array.isArray(object.card_ids)) return `${object.card_ids.length} 张知识卡（编号见详情）`;
    if (typeof object.start_order === "number" || typeof object.end_order === "number") return `第 ${object.start_order ?? "起始"}—${object.end_order ?? "末尾"} 章`;
    const preferred = ["title", "canonical_name", "name", "summary", "text", "content", "answer", "final_answer", "message", "verdict", "overview", "analysis", "proposal", "rationale", "objective", "reason", "problem", "query"];
    const key = preferred.find(field => typeof object[field] === "string" && object[field]);
    if (key) return trajectoryPreview(object[key], limit, depth + 1);
    const labels: Record<string, string> = { nodes: "步骤", results: "结果", limit: "数量", offset: "起点", lifecycle: "生命周期", warnings: "提示", risks: "风险", issues: "问题", findings: "发现", constraints: "约束" };
    const entries = Object.entries(object).filter(([name]) => labels[name]).slice(0, 2);
    return entries.length ? entries.map(([name, content]) => `${labels[name]}：${trajectoryPreview(content, Math.floor(limit / 2), depth + 1)}`).join("；") : Object.keys(object).length ? `结构化内容 · ${Object.keys(object).length} 个字段（详情查看）` : "空对象";
  }
  return typeof value === "boolean" ? (value ? "是" : "否") : String(value);
}

export function ledgerInputOutput(row: LedgerRow): { input: string; output: string } | null {
  if (row.nativeCall?.structured) return null;
  if (row.nativeCall) return { input: trajectoryPreview(row.nativeCall.input),
    output: row.nativeCall.output !== undefined ? trajectoryPreview(row.nativeCall.output) : row.nativeCall.resultNotice ?? "未记录配对结果" };
  if (row.node) return { input: trajectoryPreview(row.node.resolved_input), output: trajectoryPreview(row.node.output) };
  return null;
}

export function filterTrajectoryLedger(rows: LedgerRow[], filter: string, query: string): LedgerRow[] {
  const normalized = query.trim().toLocaleLowerCase("zh-CN");
  return rows.filter(row => (filter === "全部" || (filter === "异常" ? ["failed", "interrupted"].includes(row.status) : row.kind === filter))
    && (!normalized || `${row.title} ${row.summary.slice(0, 1000)} ${JSON.stringify(ledgerInputOutput(row))} ${trajectoryStatusLabels[row.status]}`.toLocaleLowerCase("zh-CN").includes(normalized)));
}

/** 三种横坐标：真实时长、用户轮次等宽、调用次序等距。未知执行时间不画时长。 */
export function buildTrajectoryStrip(rows: LedgerRow[], mode: StripMode): StripMark[] {
  const timed = rows.filter(row => !row.nativeCall && parseTime(row.startedAt) !== null
    && ["user", "human", "context", "model", "request", "tool", "subagent"].includes(row.kind));
  // 有调用追踪时用追踪绘制模型，避免独立回放产生第二套时长条；缺失追踪时才用回放。
  const tracedRuns = new Set(timed.filter(row => row.kind === "model").map(row => row.runId));
  const canonical = timed.filter(row => row.kind !== "request" || !tracedRuns.has(row.runId));
  const start = Math.min(...canonical.map(row => parseTime(row.startedAt)!));
  const end = Math.max(...canonical.map(row => Math.max(parseTime(row.finishedAt) ?? 0, parseTime(row.startedAt)!)));
  const span = Math.max(1, end - start);
  const runs = [...new Set(canonical.map(row => row.runId))];
  const runBounds = new Map(runs.map(id => {
    const entries = canonical.filter(row => row.runId === id);
    const first = Math.min(...entries.map(row => parseTime(row.startedAt)!));
    const last = Math.max(...entries.map(row => Math.max(parseTime(row.finishedAt) ?? 0, parseTime(row.startedAt)!)));
    return [id, { first, span: Math.max(1, last - first) }];
  }));
  return [...canonical].sort((a, b) => (parseTime(a.startedAt) ?? 0) - (parseTime(b.startedAt) ?? 0) || a.id.localeCompare(b.id)).map((row, index) => {
    const time = parseTime(row.startedAt)!;
    const finish = parseTime(row.finishedAt);
    const duration = finish !== null && finish >= time ? finish - time : 0;
    const bound = runBounds.get(row.runId)!;
    const left = mode === "calls" ? index / canonical.length * 100
      : mode === "turns" ? (runs.indexOf(row.runId) + (time - bound.first) / bound.span) / runs.length * 100
        : (time - start) / span * 100;
    const width = mode === "calls" ? 70 / canonical.length
      : mode === "turns" ? duration / bound.span / runs.length * 100 : duration / span * 100;
    return { id: row.id, row, lane: ["user", "human", "context"].includes(row.kind) ? "input"
      : ["model", "request"].includes(row.kind) ? "model" : "tools",
    left: Math.min(99.7, Math.max(0, left)), width: Math.max(0.18, Math.min(100 - left, width)), point: duration === 0 };
  });
}
