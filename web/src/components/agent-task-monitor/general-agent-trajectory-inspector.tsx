"use client";

import { type ReactNode, useState } from "react";
import { ArrowUpLeft, FileSearch } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  trajectoryDuration, trajectoryKindLabels, trajectoryStatusLabels,
  trajectoryCapabilityLabel, type TrajectoryItem,
} from "@/lib/general-agent-trajectory";
import type { GeneralAgentContextSnapshot, GeneralAgentLLMReplay } from "@/lib/types/general-agent";
import type { LedgerRow } from "@/lib/general-agent-trajectory-ledger";

export function GeneralAgentTrajectoryInspector({ item, parent, childrenItems, onSelect }: {
  item?: TrajectoryItem & Partial<Pick<LedgerRow, "nativeCall">>;
  parent?: TrajectoryItem;
  childrenItems: TrajectoryItem[];
  onSelect: (id: string) => void;
}) {
  if (!item) return <aside className="flex items-center justify-center rounded-xl bg-[var(--tc-surface-muted)] p-6 text-sm text-[var(--tc-text-muted)]">
    <span className="flex items-center gap-2"><FileSearch className="size-4" />选择事件查看证据</span>
  </aside>;
  return <aside aria-label="轨迹事件详情" className="min-h-0 overflow-y-auto rounded-xl bg-[var(--tc-surface-muted)] p-4 text-sm text-[var(--tc-text-primary)]">
    <p className="text-xs text-[var(--tc-text-muted)]">{trajectoryKindLabels[item.kind]} / 事件详情</p>
    <h2 className="mt-2 text-base font-medium">{item.title}</h2>
    <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[var(--tc-text-secondary)]">
      <span>{trajectoryStatusLabels[item.status]}</span>
      {item.durationMs !== null && <span>{trajectoryDuration(item.durationMs)}</span>}
    </div>
    <p className="mt-2 text-xs leading-5 text-[var(--tc-text-muted)]">来源：{item.source}</p>
    {item.notice && <p className="mt-3 rounded-lg bg-[var(--tc-surface-page)] p-3 text-xs leading-5 text-[var(--tc-text-secondary)]">{item.notice}</p>}
    {parent && <Button className="mt-3 max-w-full" variant="ghost" size="sm" onClick={() => onSelect(parent.id)}>
      <ArrowUpLeft className="size-3.5" /><span className="truncate">父调用：{parent.title}</span>
    </Button>}
    <div className="mt-5 space-y-4">
      <Section title={item.kind === "user" || item.kind === "answer" ? "原始内容" : "事件摘要"} open>
        <TextValue value={item.summary || "暂无摘要"} />
      </Section>
      {item.replay && <ReplayDetails call={item.replay} />}
      {item.nativeCall && <>
        <Section title={item.nativeCall.structured ? "原生输出内容" : "原生工具输入"} open><TextValue value={item.nativeCall.input} /></Section>
        <Section title={item.nativeCall.structured ? "配对校验反馈" : "配对工具结果"} open><TextValue value={item.nativeCall.output ?? item.nativeCall.resultNotice ?? "未记录配对结果"} /></Section>
        <Section title="调用时工具定义"><JsonValue value={item.nativeCall.schema ?? "未记录"} /></Section>
        <Section title="原生调用编号"><TextValue value={item.nativeCall.callId} /></Section>
      </>}
      {item.snapshot && <ContextDetails snapshot={item.snapshot} />}
      {item.node && <>
        <Section title="节点输入"><JsonValue value={item.node.resolved_input} /></Section>
        <Section title="节点输出"><JsonValue value={item.node.output} /></Section>
        <Section title={`来源与产物 · ${item.node.source_refs.length + item.node.artifact_refs.length} 项`}>
          <JsonValue value={{ 来源引用: item.node.source_refs, 产物引用: item.node.artifact_refs }} />
        </Section>
        <Section title="授权与写入证据">
          <JsonValue value={{ 已经作者批准: item.node.authorization_approved, 已经二次确认: item.node.authorization_second_confirmation,
            授权资源范围: item.node.authorization_resource_scopes, 授权记录编号: item.node.authorization_grant_id ?? "未记录",
            写入效果编号: item.node.effect_id ?? "未记录", 对账说明: item.node.reconciliation_reason || "暂无" }} />
        </Section>
        {item.node.error_message && <Section title="节点错误"><TextValue value={item.node.error_message} /></Section>}
      </>}
      {item.trace && <>
        <Section title="调用统计与重试">
          <JsonValue value={{ 输入字符: item.trace.input_char_count, 输出字符: item.trace.output_char_count,
            输入词元: item.trace.input_tokens ?? "未记录", 输出词元: item.trace.output_tokens ?? "未记录",
            重试次数: item.trace.retry_count, 来源数量: item.trace.source_count }} />
        </Section>
        {!item.node && item.kind !== "model" && <p className="text-xs leading-5 text-[var(--tc-text-muted)]">此调用只保存了追踪摘要，未关联独立输入、输出正文。</p>}
        {item.trace.error_message && <Section title="调用错误"><TextValue value={item.trace.error_message} /></Section>}
      </>}
      {childrenItems.length > 0 && <Section title={`子调用 · ${childrenItems.length} 项`} open>
        <div className="space-y-1">{childrenItems.map(child => <button key={child.id} type="button"
          onClick={() => onSelect(child.id)} className="w-full rounded-lg p-2 text-left text-xs hover:bg-[var(--tc-surface-page)] focus-visible:outline-2 focus-visible:outline-white">
          <span className="block">{child.title}</span><span className="text-[var(--tc-text-muted)]">{trajectoryStatusLabels[child.status]} · {trajectoryDuration(child.durationMs)}</span>
        </button>)}</div>
      </Section>}
      <Section title="原始标识与时间">
        <JsonValue value={{ 事件标识: item.id, 开始时间: item.startedAt || "未记录", 结束时间: item.finishedAt ?? "未记录",
          调用编号: item.trace?.call_id ?? item.replay?.call_id ?? "未记录", 父调用编号: item.trace?.parent_call_id ?? "无",
          快照编号: item.snapshot?.snapshot_id ?? "未记录", 节点编号: item.node?.node_id ?? "未记录",
          计划修订号: item.node?.plan_revision ?? "未记录", 状态是否推断: item.inferred ? "是" : "否" }} />
      </Section>
    </div>
  </aside>;
}

function ReplayDetails({ call }: { call: GeneralAgentLLMReplay }) {
  const system = call.messages.filter(message => message.role === "system");
  const roles = { system: "系统", developer: "应用约束", user: "用户", assistant: "助手", tool: "工具结果" };
  return <>
    <Section title="当时的系统提示词">
      {system.length ? system.map((message, index) => <TextValue key={index} value={message.content} />) : <TextValue value="本次回放未记录系统消息。" />}
    </Section>
    <Section title={`当时的原生工具定义 · ${call.tools.length} 项`}>
      {call.tools.length ? call.tools.map((tool, index) => <Section key={index} title={trajectoryCapabilityLabel(tool.name)}>
        <p className="mb-2 text-xs">{tool.description}</p><JsonValue value={tool} />
      </Section>) : <TextValue value="本次请求未携带原生工具定义。" />}
    </Section>
    <Section title={`实际请求消息 · ${call.messages.length} 条`}>
      <p className="mb-3 text-xs text-[var(--tc-text-muted)]">以下按模型接口顺序展示。接口角色不等于记忆层；内部调用不是用户历史。</p>
      <div className="space-y-3">{call.messages.map((message, index) => <Section key={index} title={`${index + 1}. ${roles[message.role]}`}>
        <TextValue value={message.content || "无文本内容"} />
        {!!message.tool_calls.length && <JsonValue value={{ 原生工具请求: message.tool_calls }} />}
        {message.tool_call_id && <JsonValue value={{ 配对调用编号: message.tool_call_id }} />}
      </Section>)}</div>
    </Section>
    <Section title="模型实际响应">
      <TextValue value={call.response_text || "未返回文本内容"} />
      {!!call.response_tool_calls.length && <JsonValue value={{ 原生工具调用: call.response_tool_calls }} />}
    </Section>
    <Section title="模型用量与脱敏信息">
      <JsonValue value={{ 模型标识: call.model_id, 上游模型标识: call.upstream_model,
        输入词元: call.input_tokens ?? "未记录", 缓存命中词元: call.cached_input_tokens ?? "未记录",
        输出词元: call.output_tokens ?? "未记录", 推理词元: call.reasoning_tokens ?? "未记录",
        总词元: call.total_tokens ?? "未记录", 首字延迟: "未采集", 解码耗时: "未采集",
        脱敏字段数量: call.redaction_count, 请求内容哈希: call.request_sha256, 响应内容哈希: call.response_sha256 }} />
    </Section>
    {call.error_message && <Section title="模型错误"><TextValue value={call.error_message} /></Section>}
  </>;
}

function ContextDetails({ snapshot }: { snapshot: GeneralAgentContextSnapshot }) {
  const envelope = snapshot.envelope;
  return <Section title="五层记忆投影">
    <div className="space-y-3">
      <Section title="稳定记忆"><JsonValue value={envelope.stable_memory} /></Section>
      <Section title="长期记忆"><JsonValue value={envelope.long_term_memory} /></Section>
      <Section title="历史对话"><JsonValue value={envelope.history_memory} /></Section>
      <Section title="工作记忆"><JsonValue value={envelope.working_memory} /></Section>
      <Section title="当前请求"><JsonValue value={envelope.current_request} /></Section>
      <Section title="压缩与投影统计"><JsonValue value={{ 已压缩: envelope.compressed, 使用回退: envelope.fallback_used,
        总字符数: envelope.total_char_count, 估算词元: envelope.estimated_token_count, 分类统计: envelope.category_stats }} /></Section>
    </div>
  </Section>;
}

function Section({ title, children, open = false }: { title: string; children: ReactNode; open?: boolean }) {
  const [expanded, setExpanded] = useState(open);
  return <details open={expanded} onToggle={event => setExpanded(event.currentTarget.open)} className="group text-sm">
    <summary className="cursor-pointer rounded py-1 text-[var(--tc-text-secondary)] focus-visible:outline-2 focus-visible:outline-white">{title}</summary>
    {expanded && <div className="mt-2 min-w-0">{children}</div>}
  </details>;
}
function TextValue({ value }: { value: string }) {
  return <p className="max-h-[420px] overflow-y-auto whitespace-pre-wrap break-words text-xs leading-6 [overflow-wrap:anywhere]">{value}</p>;
}
function JsonValue({ value }: { value: unknown }) {
  return <pre className="max-h-[420px] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-[var(--tc-surface-page)] p-3 font-mono text-xs leading-5 [overflow-wrap:anywhere]">{JSON.stringify(value, null, 2)}</pre>;
}
