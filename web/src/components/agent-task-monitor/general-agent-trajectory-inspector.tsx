"use client";

import { X } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { object, raw, rowPurpose, rowStatus, rowSubagentLabel, type TrajectoryRow } from "@/lib/trajectory-records";
import { trajectoryContentBlocks } from "@/lib/trajectory-content";
import { GeneralAgentTrajectoryRole } from "./general-agent-trajectory-role";
import { GeneralAgentTrajectoryContent } from "./general-agent-trajectory-content";

function displayTime(value?: string): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", fractionalSecondDigits: 3, hourCycle: "h23" }).format(date);
}

export function GeneralAgentTrajectoryInspector({ row, showModelRequest = false, onClose, onSource }: { row: TrajectoryRow; showModelRequest?: boolean; onClose: () => void; onSource: (id: string) => void }) {
  const [tab, setTab] = useState("概述");
  const [showRequest, setShowRequest] = useState(showModelRequest);
  const tabs = row.type === "user" ? ["概述", "预览", "原始内容", "来源"]
    : row.type === "tool" ? ["概述", "参数", "结果", "接口约束", "计时"] : ["概述", "预览", "原始内容"];
  const response = object(row.end?.data.response);
  const usage = object(response.usage);
  const request = object(row.start.data.request);
  const blocks = trajectoryContentBlocks(row);
  const openToolCall = (callId: string) => {
    const target = row.tool_records?.find(tool => tool.native_call_id === callId);
    if (target) onSource(target.id);
    else setTab("原始内容");
  };
  const attempts = row.attempts?.filter(event => event.kind === "attempt_started") ?? [];
  const firstDelta = row.fragments?.find(event => event.kind === "stream_chunk" && object(event.data.event).event_type === "text_delta");
  const value = (label: string, data: unknown) => <div className="grid grid-cols-[104px_1fr] gap-3 py-1"><span className="text-[var(--tc-text-muted)]">{label}</span><span className="min-w-0 break-words">{data === null || data === undefined ? "不可用" : String(data)}</span></div>;
  return <aside aria-label="轨迹详情" className="tc-trajectory-inspector flex w-[38%] min-w-[440px] max-w-[800px] shrink-0 flex-col bg-[var(--tc-surface-card)]">
    <div className="flex h-12 shrink-0 items-center gap-3 px-4 text-sm">
      {showRequest ? <span>模型请求 #{row.requestNumber}</span> : <GeneralAgentTrajectoryRole row={row} />}{row.subagent_name && <span className="text-[var(--tc-trajectory-model)]">{rowSubagentLabel(row)}</span>}<span className="text-[var(--tc-text-muted)]">第 {row.start.request_index} 轮{row.step ? ` · 步骤 ${row.step}` : ""}</span>
      <Button variant="ghost" size="icon-sm" className="ml-auto" aria-label="关闭轨迹详情" onClick={onClose}><X className="size-4" /></Button>
    </div>
    {showRequest ? <div className="min-h-0 overflow-y-auto p-4 text-sm"><Button variant="ghost" size="sm" onClick={() => setShowRequest(false)}>返回助手记录</Button><p className="my-3 text-xs text-[var(--tc-text-muted)]">实际业务输入 · 不包含鉴权信息</p><pre className="whitespace-pre-wrap break-words text-xs leading-6">{raw(request)}</pre>
      {row.attempts?.filter(event => event.kind === "attempt_started").map(event => <details key={event.event_id} className="mt-3"><summary className="cursor-pointer">实际发送载荷{Number(event.data.redaction_count) > 0 ? " · 已脱敏" : ""}</summary><pre className="whitespace-pre-wrap break-words text-xs leading-6">{raw(event.data.wire_request)}</pre></details>)}
    </div> : <>
      <div role="tablist" aria-label="详情分类" className="tc-trajectory-tabs flex shrink-0 gap-3 px-4">{tabs.map(name => <Button role="tab" aria-selected={tab === name} key={name} variant="ghost" size="sm" className="h-10 rounded-none px-1" onClick={() => setTab(name)}>{name}</Button>)}</div>
      <div role="tabpanel" className="min-h-0 flex-1 overflow-y-auto p-4 text-sm leading-6">
        {[row.start, row.end, ...(row.attempts ?? []), ...(row.fragments ?? [])].some(event => Number(event?.data.redaction_count) > 0) && <p className="mb-3 text-xs text-[var(--tc-text-muted)]">此记录包含已脱敏内容。</p>}
        {tab === "概述" && <>
          {row.type === "assistant" && <div className="mb-3"><span className="mr-8 text-[var(--tc-text-muted)]">来源</span><Button variant="link" size="sm" onClick={() => setShowRequest(true)}>模型请求 #{row.requestNumber}</Button></div>}
          {row.type === "tool" && <div className="mb-3"><span className="mr-8 text-[var(--tc-text-muted)]">层级</span>{row.parent_id ? <Button variant="link" size="sm" onClick={() => onSource(row.parent_id!)}>助手步骤 {row.step}</Button> : "来源未记录"}</div>}
          {Number(row.start.data.redaction_count) > 0 && <p className="mb-2 text-xs">观测副本含脱敏内容</p>}
          {value("状态", rowStatus(row))}
          {row.subagent_name && value("所属子助手", rowSubagentLabel(row))}
          {row.type === "assistant" && !row.subagent_name && value("调用用途", rowPurpose(row))}
          {row.type === "tool" && value("工具名称", rowPurpose(row))}
          {row.type === "tool" && row.end?.data.execution === "not_started" && value("执行边界", "调用在执行前被拒绝")}
          {row.type === "assistant" && value("尝试次数", attempts.length || null)}
          {row.type === "assistant" && <>{value("输入用量", usage.input_tokens)}{value("输出用量", usage.output_tokens)}{value("推理用量", usage.reasoning_tokens)}</>}
          {value("开始时间", displayTime(row.start.recorded_at))}{value("结束时间", displayTime(row.end?.recorded_at))}{value("耗时（毫秒）", row.end?.data.duration_ms)}
          {row.type === "assistant" && value("首文本片段", displayTime(firstDelta?.recorded_at))}
          {!!row.attempts?.length && <details className="mt-3"><summary className="cursor-pointer">尝试与错误记录</summary><pre className="whitespace-pre-wrap break-words text-xs">{raw(row.attempts)}</pre></details>}
          {row.type !== "tool" && <details className="mt-4"><summary className="cursor-pointer">预览</summary><div className="mt-3"><GeneralAgentTrajectoryContent blocks={blocks} onToolCall={openToolCall} /></div></details>}
        </>}
        {tab === "预览" && <GeneralAgentTrajectoryContent blocks={blocks} onToolCall={openToolCall} />}
        {tab === "原始内容" && <><GeneralAgentTrajectoryContent blocks={blocks} original onToolCall={openToolCall} />{!!row.fragments?.length && <details className="mt-4"><summary className="cursor-pointer">流式片段与接收时间</summary><pre className="whitespace-pre-wrap break-words text-xs">{raw(row.fragments)}</pre></details>}</>}
        {tab === "来源" && <pre className="whitespace-pre-wrap break-words text-xs">{raw(row.start.data.source)}</pre>}
        {tab === "参数" && <pre className="whitespace-pre-wrap break-words text-xs">{raw(row.start.data.arguments)}</pre>}
        {tab === "结果" && <>{object(row.end?.data.result).truncated === true && <p className="mb-2 text-xs">工具本身返回了截断内容，轨迹保留其实际返回边界。</p>}<pre className="whitespace-pre-wrap break-words text-xs">{raw(row.end?.data.result ?? row.end?.data.error)}</pre>{Array.isArray(row.end?.data.warnings) && <pre className="mt-3 whitespace-pre-wrap break-words text-xs">{raw(row.end.data.warnings)}</pre>}</>}
        {tab === "接口约束" && <pre className="whitespace-pre-wrap break-words text-xs">{raw({ 输入约束: row.start.data.schema, 输出约束: row.start.data.output_schema })}</pre>}
        {tab === "计时" && <>{value("开始时间", displayTime(row.start.recorded_at))}{value("结束时间", displayTime(row.end?.recorded_at))}{value("耗时（毫秒）", row.end?.data.duration_ms)}</>}
      </div>
    </>}
  </aside>;
}
