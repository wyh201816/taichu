import { object, raw, type TrajectoryRow } from "./trajectory-records";

export type TrajectoryContentBlock = {
  kind: "thinking" | "text" | "tool-call" | "other";
  sourceType: string;
  text: string;
  toolName?: string;
  callId?: string;
};

function toolArguments(value: unknown): string {
  return typeof value === "string" ? value : raw(value);
}

function contentBlocks(value: unknown): TrajectoryContentBlock[] {
  if (typeof value === "string") return value ? [{ kind: "text", sourceType: "text", text: value }] : [];
  if (!Array.isArray(value)) return [];
  return value.flatMap(item => {
    const block = object(item);
    const sourceType = String(block.type ?? "unknown");
    if (sourceType === "message") return contentBlocks(block.content);
    if (sourceType === "text" || sourceType === "output_text") {
      return [{ kind: "text" as const, sourceType, text: String(block.text ?? "") }];
    }
    if (sourceType === "thinking") return [{ kind: "thinking" as const, sourceType, text: String(block.thinking ?? "") }];
    if (sourceType === "reasoning") {
      const parts = Array.isArray(block.content) && block.content.length ? block.content : block.summary;
      return [{ kind: "thinking" as const, sourceType, text: Array.isArray(parts) ? parts.map(part => String(object(part).text ?? "")).join("\n") : "" }];
    }
    if (sourceType === "tool_use" || sourceType === "function_call") {
      return [{ kind: "tool-call" as const, sourceType, text: toolArguments(sourceType === "tool_use" ? block.input : block.arguments), toolName: String(block.name ?? ""), callId: String(block.call_id ?? block.id ?? "") }];
    }
    return [{ kind: "other" as const, sourceType, text: raw(item) }];
  });
}

function responseBlocks(value: unknown): TrajectoryContentBlock[] {
  const response = object(value);
  if (Array.isArray(response.content)) return contentBlocks(response.content);
  if (Array.isArray(response.output)) return contentBlocks(response.output);
  if (typeof response.output_text === "string") return contentBlocks(response.output_text);
  return [];
}

/** 仅投影最后成功尝试或其实际流式返回；不将计划理由伪装成思考。 */
export function trajectoryContentBlocks(row: TrajectoryRow): TrajectoryContentBlock[] {
  if (row.type === "user") return contentBlocks(String(row.start.data.content ?? ""));
  if (row.type !== "assistant") return [];
  const received = row.attempts?.filter(event => event.kind === "attempt_received" && Number(event.data.status_code) < 400).at(-1);
  if (received) {
    const body = received.data.wire_response;
    const blocks = responseBlocks(body);
    return blocks.length ? blocks : [{ kind: "other", sourceType: "response", text: raw(body) }];
  }
  const attemptId = row.attempts?.filter(event => event.kind === "attempt_started").at(-1)?.data.attempt_id;
  const wire = row.fragments?.filter(event => event.kind === "wire_stream_chunk" && (!attemptId || event.data.attempt_id === attemptId)) ?? [];
  const payloads: Record<string, unknown>[] = wire.map(event => ({ ...object(event.data.payload), type: object(event.data.payload).type ?? event.data.event_name }));
  const completed = payloads.findLast(payload => payload.type === "response.completed");
  if (completed) {
    const blocks = responseBlocks(completed.response);
    if (blocks.length) return blocks;
  }
  // 消息接口的块索引保留思考、文本和工具请求的原始排列。
  const streamed = new Map<number, Record<string, unknown>>();
  for (const payload of payloads) {
    const index = Number(payload.index);
    if (!Number.isInteger(index)) continue;
    if (payload.type === "content_block_start") streamed.set(index, { ...object(payload.content_block) });
    if (payload.type !== "content_block_delta") continue;
    const delta = object(payload.delta);
    const block = streamed.get(index);
    if (!block) continue;
    if (delta.type === "text_delta") block.text = String(block.text ?? "") + String(delta.text ?? "");
    if (delta.type === "thinking_delta") block.thinking = String(block.thinking ?? "") + String(delta.thinking ?? "");
    if (delta.type === "input_json_delta") block.arguments_delta = String(block.arguments_delta ?? "") + String(delta.partial_json ?? "");
  }
  if (streamed.size) {
    return contentBlocks([...streamed.entries()].sort(([left], [right]) => left - right).map(([, block]) => {
      if (block.type !== "tool_use" || typeof block.arguments_delta !== "string") return block;
      // 参数尚未完整时保留实际片段，不补造对象。
      return { ...block, input: block.arguments_delta };
    }));
  }
  const responseItems = new Map<number, Record<string, unknown>>();
  for (const payload of payloads) {
    const index = Number(payload.output_index);
    if (!Number.isInteger(index)) continue;
    if (payload.type === "response.output_item.added" || payload.type === "response.output_item.done") responseItems.set(index, { ...object(payload.item) });
    const item = responseItems.get(index);
    if (!item) continue;
    if (payload.type === "response.function_call_arguments.delta") item.arguments = String(item.arguments ?? "") + String(payload.delta ?? "");
    if (payload.type === "response.output_text.delta") {
      const content = Array.isArray(item.content) ? item.content : [];
      const partIndex = Number(payload.content_index ?? 0);
      const part = object(content[partIndex]);
      content[partIndex] = { type: "output_text", text: String(part.text ?? "") + String(payload.delta ?? "") };
      item.content = content;
    }
    if (payload.type === "response.reasoning_summary_text.delta" || payload.type === "response.reasoning_text.delta") {
      const field = payload.type === "response.reasoning_text.delta" ? "content" : "summary";
      const parts = Array.isArray(item[field]) ? item[field] : [];
      const partIndex = Number(payload.summary_index ?? payload.content_index ?? 0);
      parts[partIndex] = { type: "text", text: String(object(parts[partIndex]).text ?? "") + String(payload.delta ?? "") };
      item[field] = parts;
    }
  }
  if (responseItems.size) return contentBlocks([...responseItems.entries()].sort(([left], [right]) => left - right).map(([, item]) => item));
  const response = object(row.end?.data.response);
  const blocks = contentBlocks(response.thinking_blocks);
  const partial = row.fragments?.filter(event => event.kind === "stream_chunk").map(event => object(event.data.event)) ?? [];
  blocks.push(...contentBlocks(String(response.text || partial.filter(event => event.event_type === "text_delta").map(event => String(event.delta ?? "")).join(""))));
  if (Array.isArray(response.tool_calls)) {
    blocks.push(...response.tool_calls.map(item => {
      const call = object(item);
      return { kind: "tool-call" as const, sourceType: "tool_call", text: toolArguments(call.arguments_json), toolName: String(call.name ?? ""), callId: String(call.call_id ?? "") };
    }));
  }
  if (!blocks.length && row.end?.data.error) blocks.push({ kind: "other", sourceType: "error", text: String(row.end.data.error) });
  return blocks;
}

export function trajectoryBlockLabel(block: TrajectoryContentBlock): string {
  const label = block.kind === "thinking" ? "思考" : block.kind === "text" ? "文本" : block.kind === "tool-call" ? "工具调用" : "内容";
  return `${label}（${block.sourceType}）`;
}

export function compactToolArguments(text: string): string {
  try { return JSON.stringify(JSON.parse(text)); } catch { return text.replace(/\s+/g, " "); }
}
