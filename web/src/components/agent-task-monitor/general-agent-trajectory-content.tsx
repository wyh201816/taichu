"use client";

import { Wrench } from "lucide-react";
import { MarkdownContent } from "@/components/ui/markdown-content";
import { compactToolArguments, trajectoryBlockLabel, type TrajectoryContentBlock } from "@/lib/trajectory-content";
import { generalCapabilityLabel } from "@/lib/general-agent-display";

function toolLabel(name: string): string {
  const outputs: Record<string, string> = { GeneralAgentPlanDraft: "计划结果提交", WorkingMemoryPatch: "工作记忆更新", GeneralAgentVerification: "执行结果校验" };
  const label = outputs[name] ?? generalCapabilityLabel(name);
  return `${label === name ? "工具调用" : label}（${name}）`;
}

export function GeneralAgentTrajectoryContent({ blocks, original = false, onToolCall }: {
  blocks: TrajectoryContentBlock[];
  original?: boolean;
  onToolCall: (callId: string) => void;
}) {
  if (!blocks.length) return <p className="text-[var(--tc-text-muted)]">尚无输出</p>;
  return <div className="space-y-4">{blocks.map((block, index) => {
    if (original) return <section key={index}>
      <div className="mb-1 font-mono text-xs text-[var(--tc-text-muted)]">块 #{index + 1} · {trajectoryBlockLabel(block)}{block.toolName && <span className="ml-2">{block.toolName}</span>}</div>
      <pre className={`whitespace-pre-wrap break-words ${block.kind === "tool-call" || block.kind === "other" ? "font-mono text-xs leading-6" : "font-sans text-[15px] leading-7"}`}>{block.text}</pre>
    </section>;
    if (block.kind === "thinking") return <details key={index} className="pl-2 text-[var(--tc-text-secondary)]">
      <summary className="cursor-pointer text-sm font-medium text-[var(--tc-text-muted)]">思考</summary>
      <MarkdownContent className="mt-2" content={block.text} />
    </details>;
    if (block.kind === "text") return <MarkdownContent key={index} content={block.text} />;
    if (block.kind === "tool-call") return <button key={index} type="button" className="flex w-full min-w-0 items-center gap-2 text-left text-xs text-[var(--tc-text-muted)] hover:text-[var(--tc-text-secondary)]" title="查看工具调用详情" onClick={() => onToolCall(block.callId ?? "")}>
      <Wrench className="size-3.5 shrink-0" aria-hidden="true" /><span className="min-w-0 truncate font-mono"><span className="mr-2">{toolLabel(block.toolName ?? "")}</span>{compactToolArguments(block.text)}</span>
    </button>;
    return <pre key={index} className="whitespace-pre-wrap break-words text-xs text-[var(--tc-text-secondary)]">{block.text}</pre>;
  })}</div>;
}
