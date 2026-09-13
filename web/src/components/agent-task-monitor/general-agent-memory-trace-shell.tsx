"use client";

import {
  Activity,
  Bot,
  BrainCircuit,
  Check,
  CheckCircle2,
  ChevronLeft,
  Clock3,
  Database,
  FileInput,
  FileOutput,
  GitBranch,
  History,
  Layers3,
  MessageSquareText,
  RefreshCw,
  Route,
  ShieldCheck,
  UserRound,
  Wrench,
} from "lucide-react";
import Link from "next/link";
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { GeneralAgentMonitorNav } from "@/components/agent-task-monitor/general-agent-monitor-nav";
import {
  GeneralAgentSubagentResult,
  hasGeneralAgentResult,
} from "@/components/agent-task-monitor/general-agent-subagent-result";
import { AppShell } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import {
  getGeneralAgentConversation,
  getGeneralAgentRecovery,
  listGeneralAgentContextSnapshots,
  listGeneralAgentConversations,
  listGeneralAgentLLMReplays,
  listGeneralAgentTraces,
} from "@/lib/api/general-agent";
import {
  buildNovelStructureDisplay,
  buildStableMemoryProjection,
  buildRuntimeTrace,
  checkpointSourceLabel,
  contextPhaseLabel,
  generalSubagentResultViewKind,
  generalToolResultViewKind,
  modelCallLabel,
  modelCallPurpose,
  readableEntries,
  readableFieldLabel,
  splitReadableContent,
  type MemoryTraceLayer,
  type RuntimeTraceItem,
  type ToolResultViewKind,
} from "@/lib/general-agent-memory-trace";
import {
  generalCapabilityLabel,
  generalRunStatusLabels,
  isGeneralAgentRunActive,
} from "@/lib/general-agent-display";
import type {
  GeneralAgentContextSnapshot,
  GeneralAgentConversationSummary,
  GeneralAgentLLMReplay,
  GeneralAgentRecoverySnapshot,
  GeneralAgentRun,
  GeneralAgentInvocationTrace,
} from "@/lib/types/general-agent";
import { cn } from "@/lib/utils";

type RunEvidence = {
  snapshots: GeneralAgentContextSnapshot[];
  calls: GeneralAgentLLMReplay[];
  traces: GeneralAgentInvocationTrace[];
  recovery: GeneralAgentRecoverySnapshot | null;
};

const emptyEvidence: RunEvidence = {
  snapshots: [],
  calls: [],
  traces: [],
  recovery: null,
};

const layers: Array<{
  value: MemoryTraceLayer;
  label: string;
  icon: typeof BrainCircuit;
}> = [
  {
    value: "model",
    label: "模型上下文",
    icon: BrainCircuit,
  },
  {
    value: "runtime",
    label: "运行链路",
    icon: Route,
  },
  {
    value: "recovery",
    label: "中断恢复",
    icon: History,
  },
];

export function GeneralAgentMemoryTraceShell() {
  const [conversations, setConversations] = useState<GeneralAgentConversationSummary[]>([]);
  const [runs, setRuns] = useState<GeneralAgentRun[]>([]);
  const [currentRun, setCurrentRun] = useState<GeneralAgentRun | null>(null);
  const [evidence, setEvidence] = useState<RunEvidence>(emptyEvidence);
  const [selectedConversationId, setSelectedConversationId] = useState("");
  const [activeLayer, setActiveLayer] = useState<MemoryTraceLayer>("model");
  const [selectedSnapshotId, setSelectedSnapshotId] = useState("");
  const [selectedCallId, setSelectedCallId] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const selectedConversationRef = useRef("");
  const selectedRunRef = useRef("");
  const contentRef = useRef<HTMLElement>(null);

  const openRun = useCallback(async (run: GeneralAgentRun) => {
    const [snapshotResponse, replayResponse, traceResponse, recoveryResponse] =
      await Promise.all([
        listGeneralAgentContextSnapshots(run.run_id),
        listGeneralAgentLLMReplays(run.run_id),
        listGeneralAgentTraces(run.run_id),
        getGeneralAgentRecovery(run.run_id),
      ]);
    setCurrentRun(run);
    setEvidence({
      snapshots: snapshotResponse.snapshots,
      calls: replayResponse.calls,
      traces: traceResponse.traces,
      recovery: recoveryResponse.recovery,
    });
    selectedRunRef.current = run.run_id;
    setSelectedSnapshotId(current =>
      snapshotResponse.snapshots.some(item => item.snapshot_id === current)
        ? current
        : (snapshotResponse.snapshots.at(-1)?.snapshot_id ?? ""),
    );
    setSelectedCallId(current =>
      replayResponse.calls.some(item => item.call_id === current)
        ? current
        : (replayResponse.calls[0]?.call_id ?? ""),
    );
  }, []);

  const openConversation = useCallback(
    async (conversationId: string, preferredRunId = "") => {
      const response = await getGeneralAgentConversation(conversationId);
      setRuns(response.runs);
      setSelectedConversationId(conversationId);
      selectedConversationRef.current = conversationId;
      const run =
        response.runs.find(item => item.run_id === preferredRunId) ??
        response.runs.at(-1) ??
        null;
      if (run) {
        await openRun(run);
        return;
      }
      setCurrentRun(null);
      setEvidence(emptyEvidence);
      selectedRunRef.current = "";
    },
    [openRun],
  );

  const reload = useCallback(async () => {
    const response = await listGeneralAgentConversations({ pageSize: 100 });
    setConversations(response.conversations);
    const conversation =
      response.conversations.find(
        item => item.conversation_id === selectedConversationRef.current,
      ) ?? response.conversations[0];
    if (!conversation) {
      setRuns([]);
      setCurrentRun(null);
      setEvidence(emptyEvidence);
      setSelectedConversationId("");
      selectedConversationRef.current = "";
      selectedRunRef.current = "";
      return;
    }
    await openConversation(conversation.conversation_id, selectedRunRef.current);
  }, [openConversation]);

  useEffect(() => {
    let ignore = false;
    async function initialLoad() {
      try {
        await reload();
        if (!ignore) setError("");
      } catch (caught) {
        if (!ignore) setError(errorMessage(caught));
      } finally {
        if (!ignore) setLoading(false);
      }
    }
    void initialLoad();
    return () => {
      ignore = true;
    };
  }, [reload]);

  useEffect(() => {
    if (!currentRun || !isGeneralAgentRunActive(currentRun.status)) return;
    const timer = window.setInterval(() => {
      void reload().catch(caught => setError(errorMessage(caught)));
    }, 5_000);
    return () => window.clearInterval(timer);
  }, [currentRun, reload]);

  useEffect(() => {
    contentRef.current?.scrollTo({ top: 0 });
  }, [activeLayer, currentRun?.run_id]);

  const selectedConversation = conversations.find(
    item => item.conversation_id === selectedConversationId,
  );

  return (
    <AppShell activePath="/task-monitor" viewportLocked>
      <section className="mx-auto grid h-full min-h-0 max-w-[1640px] grid-cols-[300px_minmax(0,1fr)] grid-rows-[auto_minmax(0,1fr)] gap-4 px-4 py-4">
        <div className="col-span-2">
          <GeneralAgentMonitorNav active="memory" />
        </div>

        <aside className="flex min-h-0 flex-col overflow-hidden rounded-[var(--tc-radius-card)] border border-[var(--tc-border-subtle)] bg-[var(--tc-surface-card)] p-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h1 className="text-base font-semibold text-[var(--tc-text-primary)]">记忆追踪</h1>
            </div>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label="刷新记忆追踪"
              onClick={() => void reload()}
            >
              <RefreshCw className="size-4" />
            </Button>
          </div>
          <Link
            href="/task-monitor"
            className="mt-3 inline-flex items-center gap-1 text-xs text-[var(--tc-text-muted)] hover:text-[var(--tc-text-primary)]"
          >
            <ChevronLeft className="size-3" />
            返回任务入口
          </Link>
          <div className="mt-4 flex min-h-0 flex-1 flex-col">
            <div className="flex items-center justify-between px-2 pb-1.5 text-xs text-[var(--tc-text-muted)]">
              <span>通用写作助手对话</span>
              <span>{conversations.length} 个</span>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">
              {loading ? (
                <p className="px-2 py-3 text-xs text-[var(--tc-text-muted)]">正在读取运行记录</p>
              ) : conversations.length === 0 ? (
                <p className="px-2 py-3 text-xs text-[var(--tc-text-muted)]">暂无可追踪的对话</p>
              ) : (
                <div className="grid gap-1">
                  {conversations.map(conversation => {
                    const selected = conversation.conversation_id === selectedConversationId;
                    return (
                      <button
                        key={conversation.conversation_id}
                        type="button"
                        aria-pressed={selected}
                        className={cn(
                          "w-full min-w-0 overflow-hidden rounded-[var(--tc-radius-control)] px-3 py-2.5 text-left transition-colors duration-150 motion-reduce:transition-none",
                          selected
                            ? "bg-[var(--tc-surface-muted)] text-[var(--tc-text-primary)]"
                            : "text-[var(--tc-text-secondary)] hover:bg-[var(--tc-surface-muted)]",
                        )}
                        onClick={() => {
                          selectedRunRef.current = "";
                          void openConversation(conversation.conversation_id);
                        }}
                      >
                        <span className="flex items-center justify-between gap-2">
                          <span className={cn("truncate text-sm", selected && "font-medium")}>
                            {conversation.title}
                          </span>
                          {selected ? <Check className="size-3.5 shrink-0" /> : null}
                        </span>
                        <span className="mt-1 block text-xs text-[var(--tc-text-muted)]">
                          {conversation.request_count} 次请求 · {formatTime(conversation.updated_at)}
                        </span>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        </aside>

        <main className="flex min-h-0 flex-col gap-3 overflow-hidden">
          {error ? (
            <div className="shrink-0 rounded-[var(--tc-radius-control)] border border-red-700/70 bg-red-950/20 px-3 py-2 text-sm text-red-100">
              {error}
            </div>
          ) : null}
          {currentRun ? (
            <>
              <header className="shrink-0 rounded-[var(--tc-radius-card)] bg-[var(--tc-surface-card)] px-4 py-3">
                <div className="flex items-start justify-between gap-5">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 text-xs text-[var(--tc-text-muted)]">
                      <span>{selectedConversation?.title ?? "当前对话"}</span>
                      <span>·</span>
                      <span>{generalRunStatusLabels[currentRun.status]}</span>
                    </div>
                    <h2 className="mt-1 truncate text-base font-medium text-[var(--tc-text-primary)]">
                      {currentRun.user_goal}
                    </h2>
                  </div>
                  <label className="shrink-0 text-xs text-[var(--tc-text-muted)]">
                    <span className="mr-2">请求轮次</span>
                    <select
                      aria-label="选择记忆追踪请求轮次"
                      value={currentRun.run_id}
                      className="h-8 min-w-40 rounded-[var(--tc-radius-control)] border border-[var(--tc-border-subtle)] bg-[var(--tc-surface-muted)] px-2 text-xs text-[var(--tc-text-primary)] outline-none focus:border-[var(--tc-border-strong)]"
                      onChange={event => {
                        const run = runs.find(item => item.run_id === event.target.value);
                        if (run) void openRun(run);
                      }}
                    >
                      {[...runs].reverse().map(run => (
                        <option key={run.run_id} value={run.run_id}>
                          第 {run.request_index} 次 · {shortText(run.user_goal, 24)}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <LayerSwitcher active={activeLayer} onChange={setActiveLayer} />
              </header>

              <section ref={contentRef} className="min-h-0 flex-1 overflow-y-auto rounded-[var(--tc-radius-card)] bg-[var(--tc-surface-card)] px-4 py-4">
                {activeLayer === "model" ? (
                  <ModelMemoryLayer
                    snapshots={evidence.snapshots}
                    calls={evidence.calls}
                    selectedSnapshotId={selectedSnapshotId}
                    selectedCallId={selectedCallId}
                    onSelectSnapshot={setSelectedSnapshotId}
                    onSelectCall={setSelectedCallId}
                  />
                ) : activeLayer === "runtime" ? (
                  <RuntimeMemoryLayer
                    run={currentRun}
                    calls={evidence.calls}
                    traces={evidence.traces}
                  />
                ) : (
                  <RecoveryMemoryLayer run={currentRun} recovery={evidence.recovery} />
                )}
              </section>
            </>
          ) : (
            <div className="flex min-h-0 flex-1 items-center justify-center rounded-[var(--tc-radius-card)] bg-[var(--tc-surface-card)] text-sm text-[var(--tc-text-muted)]">
              {loading ? "正在读取记忆追踪" : "选择有运行记录的对话后查看"}
            </div>
          )}
        </main>
      </section>
    </AppShell>
  );
}

function LayerSwitcher({
  active,
  onChange,
}: {
  active: MemoryTraceLayer;
  onChange: (layer: MemoryTraceLayer) => void;
}) {
  return (
    <div className="mt-3 grid grid-cols-3 gap-1 rounded-[var(--tc-radius-control)] bg-[var(--tc-surface-muted)] p-1">
      {layers.map(layer => {
        const Icon = layer.icon;
        const selected = active === layer.value;
        return (
          <div
            key={layer.value}
            className={cn(
              "relative min-w-0 rounded-[var(--tc-radius-control)] transition-colors duration-150 motion-reduce:transition-none",
              selected
                ? "bg-[var(--tc-surface-card)] text-[var(--tc-text-primary)]"
                : "text-[var(--tc-text-muted)] hover:bg-white/[0.02] hover:text-[var(--tc-text-primary)]",
            )}
          >
            <button
              type="button"
              aria-pressed={selected}
              className="flex w-full min-w-0 items-center justify-center gap-2 rounded-[var(--tc-radius-control)] px-3 py-2 text-sm outline-none focus-visible:ring-1 focus-visible:ring-[var(--tc-border-strong)]"
              onClick={() => onChange(layer.value)}
            >
              <Icon className="size-4 shrink-0" />
              <span>{layer.label}</span>
            </button>
          </div>
        );
      })}
    </div>
  );
}

function ModelMemoryLayer({
  snapshots,
  calls,
  selectedSnapshotId,
  selectedCallId,
  onSelectSnapshot,
  onSelectCall,
}: {
  snapshots: GeneralAgentContextSnapshot[];
  calls: GeneralAgentLLMReplay[];
  selectedSnapshotId: string;
  selectedCallId: string;
  onSelectSnapshot: (id: string) => void;
  onSelectCall: (id: string) => void;
}) {
  const [activeView, setActiveView] = useState<"calls" | "snapshots">("calls");
  const call = calls.find(item => item.call_id === selectedCallId) ?? calls[0];
  const callSnapshots = calls.flatMap(item => {
    const matched = item.context_snapshot_id
      ? snapshots.find(snapshot => snapshot.snapshot_id === item.context_snapshot_id)
      : undefined;
    return matched ? [{ call: item, snapshot: matched }] : [];
  });
  const selectedCallSnapshot =
    callSnapshots.find(item => item.call.call_id === selectedCallId) ?? callSnapshots[0];
  const snapshot =
    selectedCallSnapshot?.snapshot ??
    snapshots.find(item => item.snapshot_id === selectedSnapshotId) ??
    snapshots.at(-1);
  return (
    <div>
      <div className="mb-5 grid grid-cols-2 gap-1 rounded-[var(--tc-radius-control)] bg-[var(--tc-surface-muted)] p-1">
        <button
          type="button"
          aria-pressed={activeView === "calls"}
          className={cn(
            "rounded-[var(--tc-radius-control)] px-3 py-2 text-xs transition-colors duration-150",
            activeView === "calls" ? "bg-[var(--tc-surface-card)] text-[var(--tc-text-primary)]" : "text-[var(--tc-text-muted)]",
          )}
          onClick={() => setActiveView("calls")}
        >
          模型 API 真实消息
        </button>
        <button
          type="button"
          aria-pressed={activeView === "snapshots"}
          className={cn(
            "rounded-[var(--tc-radius-control)] px-3 py-2 text-xs transition-colors duration-150",
            activeView === "snapshots" ? "bg-[var(--tc-surface-card)] text-[var(--tc-text-primary)]" : "text-[var(--tc-text-muted)]",
          )}
          onClick={() => setActiveView("snapshots")}
        >
          五层记忆
        </button>
      </div>

      {activeView === "calls" ? <section>
        <SectionHeading
          icon={<MessageSquareText className="size-4" />}
          title="模型 API 真实消息"
          summary={calls.length ? `${calls.length} 次调用` : "本轮无调用"}
        />
        {calls.length ? (
          <div className="mt-3 min-w-0 overflow-hidden">
            <div className="grid grid-cols-4 gap-1">
              {calls.map((item, index) => (
                <button
                  key={item.call_id}
                  type="button"
                  aria-pressed={item.call_id === call?.call_id}
                  className={cn(
                    "min-w-0 rounded-[var(--tc-radius-control)] px-3 py-2 text-left text-xs transition-colors duration-150 motion-reduce:transition-none",
                    item.call_id === call?.call_id
                      ? "bg-[var(--tc-surface-muted)] text-[var(--tc-text-primary)]"
                      : "text-[var(--tc-text-secondary)] hover:bg-[var(--tc-surface-muted)]",
                  )}
                  onClick={() => onSelectCall(item.call_id)}
                >
                  <span className="block truncate font-medium">第 {index + 1} 次 · {modelCallLabel(item)}</span>
                  <span className="mt-1 block text-[var(--tc-text-muted)]">{formatTime(item.started_at)}</span>
                </button>
              ))}
            </div>
            <div className="mt-6">{call ? <ModelCallDetail call={call} /> : null}</div>
          </div>
        ) : (
          <EmptyRecord text="该请求产生于模型回放接入前；技术调用计量可能仍存在，但没有当时的消息原文。" />
        )}
      </section> : null}

      {activeView === "snapshots" ? <section>
        <SectionHeading
          icon={<Layers3 className="size-4" />}
          title="五层记忆"
          summary={
            callSnapshots.length
              ? `${callSnapshots.length} 份调用快照`
              : snapshots.length
                ? `${snapshots.length} 份阶段快照`
                : "本轮无快照"
          }
        />
        {snapshots.length ? (
          <>
            <div className="mt-3 flex flex-wrap gap-1.5">
              {(callSnapshots.length
                ? callSnapshots
                : snapshots.map(item => ({ call: undefined, snapshot: item }))
              ).map((item, index) => (
                <button
                  key={item.call?.call_id ?? item.snapshot.snapshot_id}
                  type="button"
                  aria-pressed={
                    item.call
                      ? item.call.call_id === selectedCallSnapshot?.call.call_id
                      : item.snapshot.snapshot_id === snapshot?.snapshot_id
                  }
                  className={cn(
                    "rounded-[var(--tc-radius-pill)] border px-3 py-1.5 text-xs",
                    (item.call
                      ? item.call.call_id === selectedCallSnapshot?.call.call_id
                      : item.snapshot.snapshot_id === snapshot?.snapshot_id)
                      ? "border-[var(--tc-border-strong)] bg-[var(--tc-surface-muted)] text-[var(--tc-text-primary)]"
                      : "border-[var(--tc-border-subtle)] text-[var(--tc-text-muted)] hover:text-[var(--tc-text-primary)]",
                  )}
                  onClick={() => {
                    onSelectSnapshot(item.snapshot.snapshot_id);
                    if (item.call) onSelectCall(item.call.call_id);
                  }}
                >
                  第 {index + 1} 次 ·{" "}
                  {item.call
                    ? modelCallLabel(item.call)
                    : contextPhaseLabel(item.snapshot.phase)}
                </button>
              ))}
            </div>
            {snapshot ? (
              <ContextSnapshotDetail
                snapshot={snapshot}
                call={
                  selectedCallSnapshot?.call ??
                  calls.find(
                    item =>
                      item.task_name ===
                      `general_writing_orchestrator.${snapshot.phase}`,
                  )
                }
              />
            ) : null}
          </>
        ) : (
          <EmptyRecord text="该请求产生于阶段快照接入前，无法还原当时的五层组装结果。" />
        )}
      </section> : null}
    </div>
  );
}

function ContextSnapshotDetail({
  snapshot,
  call,
}: {
  snapshot: GeneralAgentContextSnapshot;
  call?: GeneralAgentLLMReplay;
}) {
  const envelope = snapshot.envelope;
  const contextLayers = [
    {
      title: "稳定记忆（系统提示词）",
      value: buildStableMemoryProjection(envelope.stable_memory, call),
      structured: true,
    },
    {
      title: `长期记忆（${envelope.long_term_memory.length} 条）`,
      value: envelope.long_term_memory.map(memory => memory.content),
      structured: false,
    },
    {
      title: `历史对话（${envelope.history_memory.messages.length} 条近期原文）`,
      value: historyMemoryContent(envelope.history_memory),
      structured: false,
    },
    {
      title: `工作记忆（${envelope.working_memory.memories.length} 条当前有效，${envelope.working_memory.invalidated_memories.length} 条仅供修复）`,
      value: workingMemoryContent(envelope.working_memory),
      structured: false,
    },
    {
      title: "当前请求",
      value: currentRequestContent(envelope.current_request),
      structured: false,
    },
  ];
  return (
    <div className="mt-4">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[var(--tc-text-muted)]">
        <span>{envelope.total_char_count.toLocaleString("zh-CN")} 字符</span>
        <span>约 {envelope.estimated_token_count.toLocaleString("zh-CN")} Token</span>
        <span>{envelope.compressed ? "已按预算压缩" : "未触发压缩"}</span>
      </div>
      <div className="mt-6 grid gap-7">
        {contextLayers.map((layer, index) => (
          <section key={layer.title} className="grid grid-cols-[32px_minmax(0,1fr)] gap-2">
            <span className="pt-0.5 font-mono text-xs text-[var(--tc-text-muted)]">{String(index + 1).padStart(2, "0")}</span>
            <div className="min-w-0">
              <h3 className="text-sm font-medium text-[var(--tc-text-primary)]">{layer.title}</h3>
              <div className="mt-2 min-w-0 rounded-[var(--tc-radius-control)] border border-[var(--tc-border-subtle)] bg-[var(--tc-surface-muted)] p-3">
                {layer.structured ? (
                  <ReadableModelPayload value={layer.value} />
                ) : (
                  <ReadableValue value={layer.value} empty="暂无" plainLists />
                )}
              </div>
            </div>
          </section>
        ))}
      </div>
      {envelope.category_stats.length ? (
        <p className="mt-7 text-xs leading-5 text-[var(--tc-text-muted)]">
          预算使用：{envelope.category_stats.map(stat =>
            `${contextCategoryLabel(stat.category)}装入 ${stat.selected_count} 项、${stat.selected_char_count.toLocaleString("zh-CN")} 字符${stat.omitted_count ? `，省略 ${stat.omitted_count} 项` : ""}`,
          ).join("；")}。
        </p>
      ) : null}
    </div>
  );
}

function workingMemoryContent(
  memory: GeneralAgentContextSnapshot["envelope"]["working_memory"],
): unknown {
  const content: Record<string, unknown> = {};
  if (memory.memories.length) {
    content["当前有效"] = memory.memories.map(item => item.content);
  }
  if (memory.invalidated_memories.length) {
    content["仅供修复，不作为当前事实"] = memory.invalidated_memories.map(item => ({
      内容: item.content,
      状态: memoryValidityLabel(item.validity),
      原因: item.invalidation_reason,
    }));
  }
  const runtimeState: unknown[] = [];
  const planSummary = compactContextValue(memory.plan_summary);
  if (planSummary !== null) runtimeState.push(planSummary);
  runtimeState.push(...memory.node_summaries
    .map(compactContextValue)
    .filter(item => item !== null));
  runtimeState.push(...memory.unresolved_issues);
  if (memory.replan_guidance) runtimeState.push(memory.replan_guidance);
  const sessionMemory = compactContextValue(memory.session_memory);
  if (sessionMemory !== null) runtimeState.push(sessionMemory);
  if (runtimeState.length) content["当前运行状态"] = runtimeState;
  return content;
}

function memoryValidityLabel(
  validity: GeneralAgentContextSnapshot["envelope"]["working_memory"]["memories"][number]["validity"],
): string {
  return {
    active: "当前有效",
    stale: "来源已变化",
    rejected: "审查未通过",
    superseded: "已被新版本替代",
  }[validity];
}

function historyMemoryContent(
  memory: GeneralAgentContextSnapshot["envelope"]["history_memory"],
): unknown {
  const content: string[] = [];
  if (memory.summary) content.push(memory.summary);
  content.push(...memory.messages.map(message =>
    `${message.role === "user" ? "用户" : "模型"}：${message.content}`,
  ));
  return content;
}

function currentRequestContent(
  request: GeneralAgentContextSnapshot["envelope"]["current_request"],
): unknown {
  const extras: unknown[] = [];
  if (request.user_constraints.length) {
    extras.push(`作者约束：${request.user_constraints.join("；")}`);
  }
  const scope = readableEntries(compactContextValue({
    ...request.scope,
    scope_type: request.scope.scope_type === "none"
      ? undefined
      : request.scope.scope_type,
  }));
  if (scope.length) {
    extras.push(Object.fromEntries(scope.map(entry => [entry.label, entry.value])));
  }
  return extras.length ? [request.content, ...extras] : request.content;
}

function compactContextValue(value: unknown): unknown | null {
  if (value === null || value === undefined || value === "") return null;
  if (Array.isArray(value)) {
    const items = value
      .map(compactContextValue)
      .filter(item => item !== null);
    return items.length ? items : null;
  }
  if (!isRecord(value)) return value;
  const entries = Object.entries(value)
    .map(([key, item]) => [key, compactContextValue(item)] as const)
    .filter((entry): entry is readonly [string, unknown] => entry[1] !== null);
  return entries.length ? Object.fromEntries(entries) : null;
}

function ModelCallDetail({ call }: { call: GeneralAgentLLMReplay }) {
  const requestBody = call.wire_request_body ?? {
    model_id: call.model_id,
    response_mode: call.response_mode,
    temperature: call.temperature,
    max_output_tokens: call.max_output_tokens,
    messages: call.messages,
    tools: call.tools,
    tool_choice: call.tool_choice,
  };
  const responseBody = {
    text: call.response_text,
    tool_calls: call.response_tool_calls,
    finish_reason: call.finish_reason,
    provider_request_id: call.provider_request_id,
    error_code: call.error_code,
    error_message: call.error_message,
  };
  return (
    <div className="min-w-0 overflow-hidden">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-sm font-medium text-[var(--tc-text-primary)]">{modelCallLabel(call)}</h3>
          <p className="mt-1 text-xs text-[var(--tc-text-muted)]">{modelCallPurpose(call)}</p>
        </div>
        <span className={cn("shrink-0 text-xs", call.status === "completed" ? "text-emerald-300" : "text-red-300")}>
          {call.status === "completed" ? "已返回" : "调用失败"}
        </span>
      </div>
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--tc-text-muted)]">
        <span>{wireProtocolLabel(call.wire_protocol)}</span>
        <span>{durationLabel(call.duration_ms)}</span>
        <span>{call.total_tokens?.toLocaleString("zh-CN") ?? "未统计"} Token</span>
        {call.redaction_count ? <span>已脱敏 {call.redaction_count} 处</span> : null}
      </div>
      <div className="mt-6 grid gap-6">
        <section className="min-w-0">
          <p className="flex items-center gap-1.5 text-xs font-medium text-[var(--tc-text-primary)]">
            <FileInput className="size-3.5" />
            {call.wire_request_body ? "发送给模型 API 的 JSON" : "后端统一请求 JSON"}
          </p>
          {!call.wire_request_body ? (
            <p className="mt-1 text-xs text-[var(--tc-text-muted)]">
              该历史调用产生于最终请求体留存前。
            </p>
          ) : null}
          <div className="mt-2 min-w-0">
            <RawModelContent content={JSON.stringify(requestBody, null, 2)} />
          </div>
        </section>
        <section className="min-w-0">
          <p className="flex items-center gap-1.5 text-xs font-medium text-[var(--tc-text-primary)]">
            <FileOutput className="size-3.5" />
            网关标准化返回 JSON
          </p>
          <div className="mt-2 min-w-0">
            <RawModelContent content={JSON.stringify(responseBody, null, 2)} />
          </div>
        </section>
      </div>
      {call.error_message ? (
        <p className="mt-3 text-xs text-red-200">{call.error_message}</p>
      ) : null}
    </div>
  );
}

function RawModelContent({ content }: { content: string }) {
  return (
    <pre className="max-h-[520px] overflow-auto whitespace-pre-wrap break-words rounded-[var(--tc-radius-control)] bg-[var(--tc-surface-muted)] px-3 py-2.5 font-mono text-[11px] leading-5 text-[var(--tc-text-secondary)]">
      {content || "（空内容）"}
    </pre>
  );
}

function ReadableModelPayload({ value }: { value: unknown }) {
  if (isOutputSchema(value)) return <OutputContract value={value} />;
  if (isStoryContext(value)) return <StoryContextResult value={value} />;
  const entries = readableEntries(value);
  if (!entries.length) return <ReadableValue value={value} empty="没有可读内容。" />;
  return (
    <div className="grid min-w-0 gap-4">
      {entries.map(entry => {
        if (isReadableScalar(entry.value)) {
          return (
            <dl key={entry.key} className="grid grid-cols-[140px_minmax(0,1fr)] gap-3 text-xs">
              <dt className="text-[var(--tc-text-muted)]">{entry.label}</dt>
              <dd className="min-w-0"><ReadableValue value={entry.value} empty="暂无" /></dd>
            </dl>
          );
        }
        return (
          <section key={entry.key} className="min-w-0 py-1">
            <div className="flex items-baseline justify-between gap-3">
              <h4 className="text-xs font-medium text-[var(--tc-text-primary)]">{entry.label}</h4>
              <span className="truncate text-[11px] text-[var(--tc-text-muted)]">{modelPayloadSummary(entry.key, entry.value)}</span>
            </div>
            <div className="mt-2 min-w-0">
              {entry.key === "阶段稳定契约" && isRecord(entry.value) ? (
                <ReadableModelPayload value={entry.value} />
              ) : entry.key === "完整轻量能力目录" ? (
                <CapabilityCatalog value={entry.value} />
              ) : entry.key === "已选能力精确契约" || entry.key === "已选能力完整契约" ? (
                <SelectedCapabilityContracts value={entry.value} />
              ) : entry.key === "输出Schema" ? (
                <OutputContract value={entry.value} />
              ) : (
                <ReadableValue value={entry.value} empty="暂无" />
              )}
            </div>
          </section>
        );
      })}
    </div>
  );
}

function isOutputSchema(value: unknown): value is Record<string, unknown> {
  return isRecord(value) && value.type === "object" && isRecord(value.properties);
}

function isStoryContext(value: unknown): value is Record<string, unknown> {
  return isRecord(value) && Array.isArray(value.evidences) && typeof value.query === "string";
}

function isReadableScalar(value: unknown): value is string | number | boolean {
  return typeof value === "string" || typeof value === "number" || typeof value === "boolean";
}

function ReadableTextContent({ content }: { content: string }) {
  return (
    <div className="grid min-w-0 gap-4">
      {splitReadableContent(content).map((part, index) =>
        part.kind === "text" ? (
          <p key={index} className="whitespace-pre-wrap break-words text-[13px] leading-6 text-[var(--tc-text-secondary)]">{part.text}</p>
        ) : (
          <ReadableModelPayload key={index} value={part.value} />
        ),
      )}
    </div>
  );
}

function CapabilityCatalog({ value }: { value: unknown }) {
  if (!isRecord(value)) return <ReadableValue value={value} empty="没有能力目录。" />;
  const capabilities = Array.isArray(value["能力索引"])
    ? value["能力索引"].filter(isRecord)
    : [];
  const tools = capabilities.filter(item => item.type === "tool");
  const agents = capabilities.filter(item => item.type === "subagent");
  return (
    <div className="grid gap-5">
      <p className="text-xs leading-5 text-[var(--tc-text-secondary)]">
        共 {capabilities.length} 项：{tools.length} 个工具、{agents.length} 个专业智能体。
      </p>
      <CapabilityGroup title={`工具（${tools.length} 个）`} capabilities={tools} />
      <CapabilityGroup title={`专业智能体（${agents.length} 个）`} capabilities={agents} />
    </div>
  );
}

function CapabilityGroup({ title, capabilities }: { title: string; capabilities: Record<string, unknown>[] }) {
  return (
    <section>
      <h5 className="text-xs font-medium text-[var(--tc-text-primary)]">{title}</h5>
      <ul className="mt-2 grid gap-1.5">
        {capabilities.map((capability, index) => {
          const name = typeof capability.name === "string" ? capability.name : "";
          const description = typeof capability.description === "string" ? capability.description : "职责说明未记录。";
          return (
            <li key={`${name}-${index}`} className="grid grid-cols-[150px_minmax(0,1fr)] gap-3 rounded-[var(--tc-radius-control)] px-2 py-1.5 odd:bg-[var(--tc-surface-muted)]">
              <span className="text-xs text-[var(--tc-text-primary)]">{generalCapabilityLabel(name)}</span>
              <div className="text-xs leading-5 text-[var(--tc-text-muted)]"><ReadableTextContent content={description} /></div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function SelectedCapabilityContracts({ value }: { value: unknown }) {
  if (!isRecord(value)) return <ReadableValue value={value} empty="没有已选能力契约。" />;
  const capabilities = Object.values(value)
    .flatMap(item => Array.isArray(item) ? item : [])
    .filter(isRecord);
  if (!capabilities.length) return <ReadableValue value={value} empty="没有已选能力契约。" />;
  return (
    <ul className="grid gap-3">
      {capabilities.map((capability, index) => {
        const name = typeof capability.name === "string" ? capability.name : "";
        const inputFields = schemaPropertyLabels(capability.input_schema);
        const outputFields = schemaPropertyLabels(capability.output_schema);
        return (
          <li key={`${name}-${index}`} className="rounded-[var(--tc-radius-control)] px-2 py-1.5 odd:bg-[var(--tc-surface-muted)]">
            <p className="text-xs font-medium text-[var(--tc-text-primary)]">{generalCapabilityLabel(name)}</p>
            {typeof capability.description === "string" ? <div className="mt-1 text-xs leading-5 text-[var(--tc-text-muted)]"><ReadableTextContent content={capability.description} /></div> : null}
            <p className="mt-1 text-[11px] leading-5 text-[var(--tc-text-muted)]">
              输入要求：{inputFields.length ? inputFields.join("、") : "无额外字段"}；返回内容：{outputFields.length ? outputFields.join("、") : "按能力结果约定"}。
            </p>
          </li>
        );
      })}
    </ul>
  );
}

function OutputContract({ value }: { value: unknown }) {
  const fields = schemaPropertyLabels(value);
  if (!fields.length) return <p className="text-xs text-[var(--tc-text-muted)]">模型返回仍会经过结构校验。</p>;
  return <p className="text-xs leading-5 text-[var(--tc-text-secondary)]">模型必须返回：{fields.join("、")}。返回后由运行时校验，不把技术格式交给作者维护。</p>;
}

function schemaPropertyLabels(value: unknown): string[] {
  if (!isRecord(value) || !isRecord(value.properties)) return [];
  return Object.keys(value.properties).map(readableFieldLabel);
}

function RuntimeMemoryLayer({
  run,
  calls,
  traces,
}: {
  run: GeneralAgentRun;
  calls: GeneralAgentLLMReplay[];
  traces: GeneralAgentInvocationTrace[];
}) {
  const timeline = useMemo(
    () => buildRuntimeTrace(run, calls, traces),
    [run, calls, traces],
  );
  const [selectedItemId, setSelectedItemId] = useState("");
  const selectedItem = timeline.find(item => item.id === selectedItemId) ?? timeline[0];
  return (
    <section>
      <SectionHeading
        icon={<GitBranch className="size-4" />}
        title="实际运行走向"
        summary={`${timeline.length} 条记录`}
      />
      <div className="mt-4 flex flex-wrap gap-x-6 gap-y-1 text-xs text-[var(--tc-text-muted)]">
        <span>计划修订 {Math.max(run.plan_revision, 0)} 版</span>
        <span>能力节点 {run.node_runs.length} 个</span>
        <span>模型调用 {calls.length} 次</span>
        <span>最终状态 {generalRunStatusLabels[run.status]}</span>
      </div>
      <div className="mt-5 grid min-w-0 grid-cols-[minmax(360px,0.9fr)_minmax(0,1.1fr)] gap-6">
        <div className="grid content-start gap-1">
          {timeline.map((item, index) => (
            <RuntimeTraceRow
              key={item.id}
              item={item}
              index={index + 1}
              selected={item.id === selectedItem?.id}
              onSelect={() => setSelectedItemId(item.id)}
            />
          ))}
        </div>
        {selectedItem ? <RuntimeTraceDetail item={selectedItem} /> : null}
      </div>
    </section>
  );
}

function RuntimeTraceRow({
  item,
  index,
  selected,
  onSelect,
}: {
  item: RuntimeTraceItem;
  index: number;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      className={cn(
        "flex min-w-0 items-start gap-3 rounded-[var(--tc-radius-control)] px-2.5 py-2 text-left transition-colors duration-150 motion-reduce:transition-none",
        selected ? "bg-[var(--tc-surface-muted)]" : "hover:bg-[var(--tc-surface-muted)]",
      )}
      onClick={onSelect}
    >
      <span className={cn("mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full", traceIconClass(item.kind))}>
        {traceIcon(item.kind)}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2">
          <span className="font-mono text-[11px] text-[var(--tc-text-muted)]">{String(index).padStart(2, "0")}</span>
          <span className="truncate text-sm font-medium text-[var(--tc-text-primary)]">{item.title}</span>
        </span>
        <span className="mt-1 block text-xs leading-5 text-[var(--tc-text-secondary)]">{shortText(item.summary, 100)}</span>
        <span className="mt-1 block text-[11px] text-[var(--tc-text-muted)]">{formatTime(item.occurredAt)}</span>
      </span>
      <span className={cn("shrink-0 text-xs", traceStatusClass(item.status))}>{item.status}</span>
    </button>
  );
}

function RuntimeTraceDetail({ item }: { item: RuntimeTraceItem }) {
  const hasInput = hasGeneralAgentResult(item.input);
  const hasOutput = hasGeneralAgentResult(item.output);
  return (
    <section className="min-w-0 px-2 py-1">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-sm font-medium text-[var(--tc-text-primary)]">{item.title}</h3>
          <p className="mt-1 text-xs leading-5 text-[var(--tc-text-secondary)]">{item.summary}</p>
        </div>
        <span className={cn("shrink-0 text-xs", traceStatusClass(item.status))}>{item.status}</span>
      </div>
      {item.details.length ? (
        <p className="mt-2 text-[11px] leading-5 text-[var(--tc-text-muted)]">
          {item.details.map(detail => detail.replace(/[。；]+$/u, "")).join("；")}。
        </p>
      ) : null}
      {hasInput || hasOutput ? (
        <div className="mt-6 grid gap-7">
          {hasInput ? <TracePayload icon={<FileInput className="size-3.5" />} title="接收的信息" value={item.input} /> : null}
          {hasOutput ? (
            <TracePayload
              icon={<FileOutput className="size-3.5" />}
              title="产生的信息"
              value={item.output}
              capabilityName={item.capabilityName}
            />
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function RecoveryMemoryLayer({
  run,
  recovery,
}: {
  run: GeneralAgentRun;
  recovery: GeneralAgentRecoverySnapshot | null;
}) {
  if (!recovery) return <EmptyRecord text="没有读取到该请求的恢复记录。" />;
  const pendingNodes = run.node_runs.filter(node => ["pending", "running", "waiting_human"].includes(node.status));
  return (
    <div className="grid gap-8">
      <section>
        <SectionHeading
          icon={<ShieldCheck className="size-4" />}
          title="当前恢复边界"
          summary={run.resumable ? "可以续跑" : "不再续跑"}
        />
        <div className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-xs text-[var(--tc-text-muted)]">
          <span>官方检查点 {checkpointStatusLabel(recovery.checkpoint.status)}</span>
          <span>持久状态 {recovery.checkpoint.checkpoint_count} 个</span>
          <span>业务状态记录 {run.checkpoint_revision} 次</span>
          <span>{run.resumable ? "可以续跑" : "不再续跑"}</span>
        </div>
        <div className="mt-5 text-xs leading-5 text-[var(--tc-text-secondary)]">
          <p className="font-medium text-[var(--tc-text-primary)]">恢复后从哪里继续</p>
          <p className="mt-1">{resumeDescription(run, pendingNodes.length)}</p>
        </div>
        {run.context_resume_differences.length ? (
          <div className="mt-5 text-xs">
            <p className="font-medium text-[var(--tc-text-primary)]">恢复时重新取证产生的上下文差异</p>
            <ul className="mt-2 grid gap-1 text-[var(--tc-text-secondary)]">
              {run.context_resume_differences.map(item => <li key={item}>· {item}</li>)}
            </ul>
          </div>
        ) : null}
      </section>

      <section>
        <SectionHeading
          icon={<History className="size-4" />}
          title="检查点写入时间线"
          summary={`${recovery.checkpoints.length} 个状态`}
        />
        {recovery.checkpoints.length ? (
          <div className="mt-3 grid max-h-[420px] gap-1 overflow-y-auto pr-1">
            {recovery.checkpoints.map((checkpoint, index) => (
              <div
                key={checkpoint.checkpoint_id}
                className="flex items-center gap-3 rounded-[var(--tc-radius-control)] px-2.5 py-2 text-xs odd:bg-[var(--tc-surface-muted)]"
              >
                <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-cyan-400/10 text-cyan-300">
                  {index === 0 ? <CheckCircle2 className="size-3.5" /> : <Clock3 className="size-3.5" />}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-[var(--tc-text-primary)]">{checkpointSourceLabel(checkpoint.source)}</span>
                  <span className="mt-0.5 block text-[var(--tc-text-muted)]">{checkpoint.created_at ? formatTime(checkpoint.created_at) : "时间未记录"}</span>
                </span>
                <span className="font-mono text-[var(--tc-text-muted)]">图步骤 {checkpoint.step}</span>
                {index === 0 ? <span className="text-emerald-300">当前恢复点</span> : null}
              </div>
            ))}
          </div>
        ) : (
          <EmptyRecord text="该请求还没有持久化的 LangGraph 检查点。" />
        )}
      </section>

      <section>
        <SectionHeading
          icon={<Database className="size-4" />}
          title="写入副作用保护"
          summary={recovery.effects.length ? `${recovery.effects.length} 个真实写入需要在恢复时对账` : "本轮没有真实写入副作用"}
        />
        {recovery.effects.length ? (
          <div className="mt-3 grid gap-1">
            {recovery.effects.map(effect => (
              <div key={effect.effect_id} className="flex items-start gap-3 rounded-[var(--tc-radius-control)] px-2.5 py-2 text-xs odd:bg-[var(--tc-surface-muted)]">
                <Wrench className="mt-0.5 size-4 shrink-0 text-cyan-300" />
                <span className="min-w-0 flex-1">
                  <span className="block text-[var(--tc-text-primary)]">工具 · {generalCapabilityLabel(effect.tool_name)}</span>
                  <span className="mt-1 block leading-5 text-[var(--tc-text-muted)]">{effect.reason || "恢复前会核对真实资源是否已经写入。"}</span>
                </span>
                <span className={effectStatusClass(effect.status)}>{effectStatusLabel(effect.status)}</span>
              </div>
            ))}
          </div>
        ) : (
          <EmptyRecord text="没有需要防止重复执行的写入节点。" compact />
        )}
      </section>
    </div>
  );
}

function SectionHeading({ icon, title, summary }: { icon: ReactNode; title: string; summary: string }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <h2 className="flex items-center gap-2 text-sm font-medium text-[var(--tc-text-primary)]">{icon}{title}</h2>
      <p className="text-xs text-[var(--tc-text-muted)]">{summary}</p>
    </div>
  );
}

function TracePayload({
  icon,
  title,
  value,
  capabilityName,
}: {
  icon: ReactNode;
  title: string;
  value: unknown;
  capabilityName?: string;
}) {
  const resultViewKind = capabilityName
    ? generalToolResultViewKind(capabilityName)
    : undefined;
  const subagentViewKind = capabilityName
    ? generalSubagentResultViewKind(capabilityName)
    : undefined;
  const hasResult = hasGeneralAgentResult(value);
  return (
    <div className="min-w-0">
      <p className="flex items-center gap-1.5 text-xs font-medium text-[var(--tc-text-primary)]">{icon}{title}</p>
      <div className="mt-2 min-w-0">
        {resultViewKind && hasResult ? (
          <ToolResultValue kind={resultViewKind} value={value} />
        ) : subagentViewKind && capabilityName && hasResult ? (
          <GeneralAgentSubagentResult
            capabilityName={capabilityName}
            value={value}
          />
        ) : (
          <ReadableValue value={value} empty="没有记录内容。" />
        )}
      </div>
    </div>
  );
}

function ToolResultValue({
  kind,
  value,
}: {
  kind: ToolResultViewKind;
  value: unknown;
}) {
  if (kind === "novel_structure") return <NovelStructureResult value={value} />;
  if (kind === "manuscript_content") return <ManuscriptContentResult value={value} />;
  if (kind === "story_context") return <StoryContextResult value={value} />;
  if (kind === "knowledge_resolution") return <KnowledgeResolutionResult value={value} />;
  if (kind === "knowledge_catalog") return <KnowledgeCatalogResult value={value} />;
  if (kind === "knowledge_cards") return <KnowledgeCardsResult value={value} />;
  if (kind === "external_search") return <ExternalSearchResult value={value} />;
  if (kind === "external_content") return <ExternalContentResult value={value} />;
  if (kind === "manuscript_preview") return <ManuscriptDiffResult value={value} preview />;
  if (kind === "manuscript_write") return <ManuscriptDiffResult value={value} />;
  if (kind === "structure_write") return <StructureWriteResult value={value} />;
  return <KnowledgeWriteResult value={value} />;
}

function NovelStructureResult({ value }: { value: unknown }) {
  const structure = buildNovelStructureDisplay(value);
  if (!structure) {
    return <ReadableValue value={value} empty="没有读取到卷章结构。" />;
  }
  return (
    <div className="min-w-0">
      <p className="text-xs text-[var(--tc-text-secondary)]">
        返回 {structure.volumes.length} 卷、{structure.returnedChapters} 章
        {structure.totalChapters !== structure.returnedChapters
          ? `，全书共 ${structure.totalChapters} 章`
          : ""}
        {structure.truncated ? "，结果已截断" : "，结果完整"}
      </p>
      <div className="tc-editor-scrollbar mt-3 max-h-[440px] overflow-y-auto pr-2">
        {structure.volumes.map(volume => (
          <section key={`${volume.order}-${volume.title}`} className="mb-4 last:mb-0">
            <div className="flex items-center justify-between gap-3 border-b border-[var(--tc-border-subtle)] pb-1.5">
              <h4 className="text-xs font-medium text-[var(--tc-text-primary)]">
                {volume.title}
              </h4>
              <span className="text-[11px] text-[var(--tc-text-muted)]">
                {volume.chapters.length} 章
              </span>
            </div>
            <ol className="mt-1">
              {volume.chapters.map(chapter => (
                <li
                  key={`${chapter.order}-${chapter.title}`}
                  className="grid grid-cols-[36px_minmax(0,1fr)_auto_auto] items-center gap-2 px-1.5 py-1.5 text-xs odd:bg-[var(--tc-surface-muted)]"
                >
                  <span className="font-mono text-[11px] text-[var(--tc-text-muted)]">
                    {chapter.order}
                  </span>
                  <span className="min-w-0 text-[var(--tc-text-secondary)]">
                    {chapter.title}
                  </span>
                  <span className="text-[11px] text-[var(--tc-text-muted)]">
                    {chapter.wordCount === null
                      ? "字数未记录"
                      : `${chapter.wordCount.toLocaleString("zh-CN")} 字`}
                  </span>
                  <span className="text-[11px] text-[var(--tc-text-muted)]">
                    {chapter.status === "active" ? "使用中" : chapter.status || "状态未知"}
                  </span>
                </li>
              ))}
            </ol>
          </section>
        ))}
      </div>
    </div>
  );
}

function ManuscriptContentResult({ value }: { value: unknown }) {
  const output = isRecord(value) ? value : {};
  const chunks = recordList(output.chunks);
  return (
    <ToolResultLayout
      summary={`返回 ${chunks.length} 段正文，共 ${numberField(output, "total_content_chars").toLocaleString("zh-CN")} 字符${output.truncated === true ? "，结果已截断" : "，结果完整"}`}
    >
      {chunks.map((chunk, index) => (
        <section key={`${stringField(chunk, "chapter_id")}-${index}`} className="mb-5 last:mb-0">
          <ResultHeading
            title={stringField(chunk, "title") || `第 ${index + 1} 段正文`}
            meta={`第 ${numberField(chunk, "order")} 章 · 字符 ${numberField(chunk, "start_char")}-${numberField(chunk, "end_char")}${chunk.truncated === true ? " · 已截断" : ""}`}
          />
          <p className="mt-2 whitespace-pre-wrap text-xs leading-6 text-[var(--tc-text-secondary)]">
            {stringField(chunk, "content") || "没有正文内容。"}
          </p>
        </section>
      ))}
    </ToolResultLayout>
  );
}

function StoryContextResult({ value }: { value: unknown }) {
  const output = isRecord(value) ? value : {};
  const evidences = recordList(output.evidences);
  return (
    <ToolResultLayout
      summary={`“${stringField(output, "query")}”返回 ${evidences.length} 条权威证据 · 关系召回 ${stringList(output.retrieved_relations).length} 条 · 多跳扩展 ${stringList(output.expanded_relations).length} 条`}
    >
      {evidences.map((evidence, index) => (
        <section key={`${stringField(evidence, "source_ref")}-${index}`} className="mb-4 last:mb-0">
          <ResultHeading
            title={stringField(evidence, "title") || `证据 ${index + 1}`}
            meta={`${readableEnum("source_type", stringField(evidence, "source_type"))} · 排名 ${numberField(evidence, "rank")} · ${evidence.authority_verified === true ? "已核对事实源" : "尚未核对事实源"}`}
          />
          <p className="mt-2 whitespace-pre-wrap text-xs leading-6 text-[var(--tc-text-secondary)]">
            {stringField(evidence, "content") || "没有证据正文。"}
          </p>
          {stringField(evidence, "context_content") ? (
            <p className="mt-2 whitespace-pre-wrap border-l border-[var(--tc-border-subtle)] pl-3 text-[11px] leading-5 text-[var(--tc-text-muted)]">
              {stringField(evidence, "context_content")}
            </p>
          ) : null}
          <ResultTags values={[stringField(evidence, "source_ref")].filter(Boolean)} />
        </section>
      ))}
    </ToolResultLayout>
  );
}

function KnowledgeResolutionResult({ value }: { value: unknown }) {
  const output = isRecord(value) ? value : {};
  const matches = recordList(output.matches);
  const cards = matches.map(match => ({
    ...match,
    name: stringField(match, "canonical_name"),
    type: stringField(match, "knowledge_type"),
    aliases: stringList(match.matched_aliases),
  }));
  const resolution = stringField(output, "resolution");
  const resolutionLabel = {
    unique: "唯一匹配",
    ambiguous: "存在多个候选",
    not_found: "未找到",
  }[resolution] ?? "状态未知";
  return (
    <ToolResultLayout summary={`${resolutionLabel} · ${stringField(output, "reason") || "没有补充说明"}`}>
      <KnowledgeCardList cards={cards} empty="没有匹配的知识卡。" />
    </ToolResultLayout>
  );
}

function KnowledgeCatalogResult({ value }: { value: unknown }) {
  const output = isRecord(value) ? value : {};
  const items = recordList(output.items);
  return (
    <ToolResultLayout
      summary={`返回 ${items.length} 张知识卡，目录共 ${numberField(output, "total")} 张${output.truncated === true ? "，结果已截断" : ""}`}
    >
      <KnowledgeCardList cards={items} empty="知识目录没有内容。" />
    </ToolResultLayout>
  );
}

function KnowledgeCardsResult({ value }: { value: unknown }) {
  const output = isRecord(value) ? value : {};
  const cards = recordList(output.cards);
  const missing = stringList(output.missing_card_ids).length;
  const rejected = stringList(output.rejected_card_ids).length;
  return (
    <ToolResultLayout
      summary={`读取 ${cards.length} 张知识卡${missing ? `，${missing} 张不存在` : ""}${rejected ? `，${rejected} 张不可用` : ""}`}
    >
      <KnowledgeCardList cards={cards} empty="没有读取到知识卡。" />
    </ToolResultLayout>
  );
}

function ExternalSearchResult({ value }: { value: unknown }) {
  const output = isRecord(value) ? value : {};
  const items = recordList(output.items);
  return (
    <ToolResultLayout summary={`“${stringField(output, "query")}”返回 ${items.length} 条外部资料`}>
      {items.map((item, index) => {
        const url = stringField(item, "url");
        return (
          <section key={`${url}-${index}`} className="mb-4 last:mb-0">
            <ResultHeading
              title={stringField(item, "title") || `资料 ${index + 1}`}
              meta={[stringField(item, "domain"), stringField(item, "published_at")].filter(Boolean).join(" · ")}
            />
            <p className="mt-1.5 text-xs leading-5 text-[var(--tc-text-secondary)]">
              {stringField(item, "snippet") || "没有内容摘要。"}
            </p>
            {isHttpUrl(url) ? (
              <a className="mt-1.5 block break-all text-[11px] text-cyan-300 hover:underline" href={url} target="_blank" rel="noreferrer">
                {url}
              </a>
            ) : null}
          </section>
        );
      })}
    </ToolResultLayout>
  );
}

function ExternalContentResult({ value }: { value: unknown }) {
  const output = isRecord(value) ? value : {};
  const url = stringField(output, "final_url") || stringField(output, "url");
  return (
    <ToolResultLayout summary={`${stringField(output, "title") || "外部资料"}${output.truncated === true ? " · 内容已截断" : " · 内容完整"}`}>
      {isHttpUrl(url) ? (
        <a className="block break-all text-[11px] text-cyan-300 hover:underline" href={url} target="_blank" rel="noreferrer">
          {url}
        </a>
      ) : null}
      <p className="mt-3 whitespace-pre-wrap text-xs leading-6 text-[var(--tc-text-secondary)]">
        {stringField(output, "content") || "没有读取到正文。"}
      </p>
    </ToolResultLayout>
  );
}

function ManuscriptDiffResult({
  value,
  preview = false,
}: {
  value: unknown;
  preview?: boolean;
}) {
  const output = isRecord(value) ? value : {};
  const oldCount = optionalNumberField(output, "old_char_count");
  const newCount = optionalNumberField(output, "new_char_count");
  const wordCount = optionalNumberField(output, "word_count");
  const summary = preview
    ? `修改预览${oldCount === null || newCount === null ? "" : ` · ${oldCount.toLocaleString("zh-CN")} → ${newCount.toLocaleString("zh-CN")} 字符`}`
    : `正文已写入${wordCount === null ? "" : ` · ${wordCount.toLocaleString("zh-CN")} 字`}`;
  return (
    <ToolResultLayout summary={summary}>
      <pre className="overflow-x-auto whitespace-pre-wrap rounded-[var(--tc-radius-control)] bg-[var(--tc-surface-muted)] p-3 font-mono text-[11px] leading-5 text-[var(--tc-text-secondary)]">
        {stringField(output, "unified_diff") || "没有差异内容。"}
      </pre>
    </ToolResultLayout>
  );
}

function StructureWriteResult({ value }: { value: unknown }) {
  const output = isRecord(value) ? value : {};
  const changes = recordList(output.changes);
  return (
    <ToolResultLayout summary={`完成 ${changes.length} 项卷章结构变更`}>
      <ol>
        {changes.map((change, index) => (
          <li key={`${stringField(change, "item_id")}-${index}`} className="grid grid-cols-[28px_80px_minmax(0,1fr)] gap-2 px-1.5 py-2 text-xs odd:bg-[var(--tc-surface-muted)]">
            <span className="font-mono text-[11px] text-[var(--tc-text-muted)]">{index + 1}</span>
            <span className="text-[var(--tc-text-muted)]">{structureActionLabel(stringField(change, "action"))}</span>
            <span className="text-[var(--tc-text-secondary)]">{stringField(change, "title") || "未命名项目"}</span>
          </li>
        ))}
      </ol>
    </ToolResultLayout>
  );
}

function KnowledgeWriteResult({ value }: { value: unknown }) {
  const output = isRecord(value) ? value : {};
  const card = isRecord(output.card) ? output.card : null;
  const changedFields = stringList(output.changed_fields);
  return (
    <ToolResultLayout summary={changedFields.length ? `知识卡已更新 · 修改 ${changedFields.length} 个字段` : "知识卡已创建并确认"}>
      <KnowledgeCardList cards={card ? [card] : []} empty="没有返回知识卡内容。" />
      {changedFields.length ? <ResultTags values={changedFields.map(readableFieldLabel).filter(Boolean)} /> : null}
    </ToolResultLayout>
  );
}

function KnowledgeCardList({
  cards,
  empty,
}: {
  cards: Record<string, unknown>[];
  empty: string;
}) {
  if (!cards.length) return <p className="text-xs text-[var(--tc-text-muted)]">{empty}</p>;
  return (
    <div>
      {cards.map((card, index) => {
        const name = stringField(card, "name") || stringField(card, "display_name") || `知识卡 ${index + 1}`;
        const summary = stringField(card, "summary");
        const type = stringField(card, "type") || stringField(card, "knowledge_type");
        const score = optionalNumberField(card, "score");
        return (
          <section key={`${name}-${index}`} className="mb-4 last:mb-0">
            <ResultHeading
              title={name}
              meta={[readableEnum("type", type), score === null ? "" : `相关度 ${formatScore(score)}`].filter(Boolean).join(" · ")}
            />
            {summary ? <p className="mt-1.5 text-xs leading-5 text-[var(--tc-text-secondary)]">{summary}</p> : null}
            <ResultTags values={[...stringList(card.aliases), ...stringList(card.match_reasons)]} />
          </section>
        );
      })}
    </div>
  );
}

function ToolResultLayout({
  summary,
  children,
}: {
  summary: string;
  children: ReactNode;
}) {
  return (
    <div className="min-w-0">
      <p className="text-xs text-[var(--tc-text-secondary)]">{summary}</p>
      <div className="tc-editor-scrollbar mt-3 max-h-[440px] overflow-y-auto pr-2">
        {children}
      </div>
    </div>
  );
}

function ResultHeading({ title, meta }: { title: string; meta: string }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <h4 className="min-w-0 text-xs font-medium text-[var(--tc-text-primary)]">{title}</h4>
      {meta ? <span className="shrink-0 text-[11px] text-[var(--tc-text-muted)]">{meta}</span> : null}
    </div>
  );
}

function ResultTags({ values }: { values: string[] }) {
  if (!values.length) return null;
  return (
    <p className="mt-1.5 text-[11px] leading-5 text-[var(--tc-text-muted)]">
      {values.join(" · ")}
    </p>
  );
}

function ReadableValue({
  value,
  empty,
  plainLists = false,
}: {
  value: unknown;
  empty: string;
  plainLists?: boolean;
}) {
  if (value === null || value === undefined || value === "") {
    return <p className="text-xs text-[var(--tc-text-muted)]">{empty}</p>;
  }
  if (typeof value === "string") {
    return <ReadableTextContent content={value} />;
  }
  if (typeof value === "number") {
    return <p className="text-xs leading-5 text-[var(--tc-text-secondary)]">{value.toLocaleString("zh-CN")}</p>;
  }
  if (typeof value === "boolean") {
    return <p className="text-xs text-[var(--tc-text-secondary)]">{value ? "是" : "否"}</p>;
  }
  if (Array.isArray(value)) {
    if (!value.length) return <p className="text-xs text-[var(--tc-text-muted)]">{empty}</p>;
    return (
      <ol className="grid min-w-0 gap-1.5 overflow-hidden">
        {value.map((item, index) => (
          <li
            key={index}
            className={cn(
              "grid min-w-0 grid-cols-[20px_minmax(0,1fr)] gap-1 rounded-[var(--tc-radius-control)] px-1.5 py-1",
              plainLists ? "" : "odd:bg-[var(--tc-surface-muted)]",
            )}
          >
            <span className="font-mono text-[11px] text-[var(--tc-text-muted)]">{index + 1}</span>
            <ReadableValue value={item} empty={empty} plainLists={plainLists} />
          </li>
        ))}
      </ol>
    );
  }
  const entries = readableEntries(value);
  if (!entries.length) return <p className="text-xs text-[var(--tc-text-muted)]">{empty}</p>;
  return (
    <dl className="grid gap-2 text-xs">
      {entries.map(entry => (
        <div key={entry.key} className="grid grid-cols-[120px_minmax(0,1fr)] gap-3">
          <dt className="text-[var(--tc-text-muted)]">{entry.label}</dt>
          <dd className="min-w-0 text-[var(--tc-text-secondary)]">
            <ReadableValue value={entry.value} empty="暂无" plainLists={plainLists} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

function EmptyRecord({ text, compact = false }: { text: string; compact?: boolean }) {
  return (
    <p className={cn("rounded-[var(--tc-radius-control)] bg-[var(--tc-surface-muted)] px-3 text-xs text-[var(--tc-text-muted)]", compact ? "mt-2 py-2.5" : "mt-3 py-5 text-center")}>
      {text}
    </p>
  );
}

function traceIcon(kind: RuntimeTraceItem["kind"]): ReactNode {
  if (kind === "request") return <UserRound className="size-3.5" />;
  if (kind === "state") return <Activity className="size-3.5" />;
  if (kind === "context") return <Layers3 className="size-3.5" />;
  if (kind === "model") return <Bot className="size-3.5" />;
  if (kind === "capability") return <Wrench className="size-3.5" />;
  if (kind === "human") return <MessageSquareText className="size-3.5" />;
  return <FileOutput className="size-3.5" />;
}

function traceIconClass(kind: RuntimeTraceItem["kind"]): string {
  if (kind === "model") return "bg-amber-400/10 text-amber-300";
  if (kind === "capability") return "bg-violet-400/10 text-violet-300";
  if (kind === "context" || kind === "request") return "bg-cyan-400/10 text-cyan-300";
  return "bg-white/5 text-[var(--tc-text-secondary)]";
}

function traceStatusClass(status: string): string {
  if (["已返回", "已完成", "已输出", "完整装入", "完成"].includes(status)) return "text-emerald-300";
  if (["失败", "调用失败", "已超时"].includes(status)) return "text-red-300";
  if (status.includes("等待") || status === "已压缩") return "text-amber-300";
  return "text-[var(--tc-text-muted)]";
}

function resumeDescription(run: GeneralAgentRun, pendingNodeCount: number): string {
  if (run.status === "completed") return "本轮已经完成；检查点继续保留作审计依据，不会重复执行。";
  if (run.status === "waiting_human") return "恢复后仍停在作者确认位置，收到答复后才继续后续节点。";
  if (!run.resumable) return "本轮已关闭续跑能力，只保留现有记录供查看。";
  if (pendingNodeCount) return `恢复后从最新有效图状态继续处理 ${pendingNodeCount} 个未完成节点，已成功节点不会重新执行。`;
  return "恢复后从最新有效图状态继续编排或校验，不从头重放整轮任务。";
}

function contextCategoryLabel(category: string): string {
  return {
    stable_memory: "稳定记忆（系统提示词）",
    long_term_memory: "长期记忆",
    history_memory: "历史对话",
    working_memory: "工作记忆",
    current_request: "当前请求",
  }[category] ?? "其他上下文";
}

function wireProtocolLabel(protocol: string): string {
  return {
    openai_responses: "OpenAI Responses 请求",
    anthropic_messages: "Anthropic Messages 请求",
  }[protocol] ?? "模型 API 请求";
}

function checkpointStatusLabel(status: GeneralAgentRecoverySnapshot["checkpoint"]["status"]): string {
  return {
    available: "可恢复",
    missing: "尚未生成",
  }[status];
}

function effectStatusLabel(status: GeneralAgentRecoverySnapshot["effects"][number]["status"]): string {
  return {
    prepared: "已准备",
    started: "写入中",
    succeeded: "已生效",
    failed: "未生效",
    unknown: "结果待核对",
    reconciled: "已对账",
    requires_human: "需作者核对",
  }[status];
}

function effectStatusClass(status: GeneralAgentRecoverySnapshot["effects"][number]["status"]): string {
  if (["failed"].includes(status)) return "shrink-0 text-red-300";
  if (["unknown", "requires_human"].includes(status)) return "shrink-0 text-amber-300";
  return "shrink-0 text-emerald-300";
}

function durationLabel(durationMs: number): string {
  return durationMs < 1_000 ? `${durationMs} 毫秒` : `${(durationMs / 1_000).toFixed(1)} 秒`;
}

function modelPayloadSummary(key: string, value: unknown): string {
  if (key === "稳定记忆" || key === "稳定记忆（System Prompt）") {
    return "模型身份、基本行为、准则与静态能力索引";
  }
  if (key === "工作记忆") return "当前任务的资料和运行状态";
  if (key === "完整轻量能力目录") {
    const total = isRecord(value) && typeof value["能力总数"] === "number" ? value["能力总数"] : null;
    return total === null ? "本轮可选能力目录" : `${total} 项可选能力`;
  }
  if (key === "已选能力完整契约") return "本轮节点使用的输入输出约束";
  if (key === "输出Schema") return "用于校验模型返回结构";
  if (key === "source_request") return "资料收集范围和约束";
  if (key === "rationale") return "模型已记录选择这条路径的原因";
  if (key === "final_response_guidance") return "模型已记录最终回答的组织要求";
  if (typeof value === "string") return shortText(value, 44);
  if (Array.isArray(value)) return `${value.length} 项`;
  if (isRecord(value)) return `${Object.keys(value).length} 组信息`;
  if (typeof value === "boolean") return value ? "是" : "否";
  return String(value ?? "暂无");
}

function recordList(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter(isRecord) : [];
}

function stringField(value: Record<string, unknown>, key: string): string {
  return typeof value[key] === "string" ? value[key] : "";
}

function numberField(value: Record<string, unknown>, key: string): number {
  return optionalNumberField(value, key) ?? 0;
}

function optionalNumberField(
  value: Record<string, unknown>,
  key: string,
): number | null {
  return typeof value[key] === "number" && Number.isFinite(value[key])
    ? value[key]
    : null;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string" && Boolean(item))
    : [];
}

function formatScore(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toFixed(3)
    : "未记录";
}

function readableEnum(key: string, value: string): string {
  const labels: Record<string, Record<string, string>> = {
    strategy: {
      milvus_hybrid_vector_graph: "Milvus 混合向量图谱召回",
    },
    source_type: {
      manuscript_chunk: "正文片段",
      knowledge_card: "知识卡",
    },
    type: {
      character: "人物",
      event: "事件",
      faction: "势力",
      item: "物品",
      location: "地点",
      realm: "境界",
      rule: "规则",
      technique: "功法",
    },
  };
  return labels[key]?.[value] ?? value;
}

function structureActionLabel(value: string): string {
  const label = {
    created: "创建",
    updated: "更新",
    deleted: "归档",
    create: "创建",
    update: "更新",
    delete: "归档",
  }[value];
  return (label ?? value) || "已变更";
}

function isHttpUrl(value: string): boolean {
  return value.startsWith("https://") || value.startsWith("http://");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function shortText(value: string, limit: number): string {
  const text = value.replace(/\s+/g, " ").trim();
  return text.length <= limit ? text : `${text.slice(0, limit)}…`;
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime()) || date.getUTCFullYear() >= 9999) return "尚未开始";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "记忆追踪加载失败";
}
