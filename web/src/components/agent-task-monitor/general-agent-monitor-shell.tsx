"use client";

import { Dialog } from "@base-ui/react/dialog";
import { Popover } from "@base-ui/react/popover";
import {
  Activity,
  Bot,
  Check,
  ChevronLeft,
  ChevronsUpDown,
  Database,
  ListTree,
  PanelRightOpen,
  RefreshCw,
  ShieldCheck,
  Wrench,
  X,
} from "lucide-react";
import Link from "next/link";
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { GeneralAgentFlowGraph } from "@/components/agent-task-monitor/general-agent-flow-graph";
import { GeneralAgentMonitorNav } from "@/components/agent-task-monitor/general-agent-monitor-nav";
import {
  GeneralAgentSubagentResult,
  hasGeneralAgentResult,
} from "@/components/agent-task-monitor/general-agent-subagent-result";
import { Button } from "@/components/ui/button";
import {
  getGeneralAgentConversation,
  getGeneralAgentRecovery,
  listGeneralAgentConversations,
  listGeneralAgentTraces,
} from "@/lib/api/general-agent";
import {
  generalAgentContinuationRequestIndex,
  generalCapabilityLabel,
  generalNodeErrorMessage,
  generalNodeStatusLabel,
  generalRunProgressSummary,
  generalRunStatusLabels,
  isGeneralAgentRunActive,
} from "@/lib/general-agent-display";
import {
  generalAgentPlanRevisions,
  orchestratorTraces,
  tracesForGeneralAgentNode,
} from "@/lib/general-agent-monitor";
import type {
  GeneralAgentConversationSummary,
  GeneralAgentEffectStatus,
  GeneralAgentInvocationTrace,
  GeneralAgentNodeRun,
  GeneralAgentRecoverySnapshot,
  GeneralAgentRun,
  GeneralAgentRunStatus,
} from "@/lib/types/general-agent";
import { cn } from "@/lib/utils";

type StatusFilter = "all" | "active" | "waiting_human" | "completed" | "failed";
type DetailPanel = "node" | "run" | null;

const filters: Array<{ value: StatusFilter; label: string }> = [
  { value: "all", label: "全部" },
  { value: "active", label: "运行中" },
  { value: "waiting_human", label: "等作者" },
  { value: "completed", label: "已完成" },
  { value: "failed", label: "异常" },
];

export function GeneralAgentMonitorShell() {
  const [conversations, setConversations] = useState<GeneralAgentConversationSummary[]>([]);
  const [conversationRuns, setConversationRuns] = useState<GeneralAgentRun[]>([]);
  const [currentRun, setCurrentRun] = useState<GeneralAgentRun | null>(null);
  const [traces, setTraces] = useState<GeneralAgentInvocationTrace[]>([]);
  const [traceTotal, setTraceTotal] = useState(0);
  const [recovery, setRecovery] = useState<GeneralAgentRecoverySnapshot | null>(null);
  const [selectedConversationId, setSelectedConversationId] = useState("");
  const [selectedNodeId, setSelectedNodeId] = useState("");
  const [selectedRevision, setSelectedRevision] = useState(0);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [detailPanel, setDetailPanel] = useState<DetailPanel>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const selectedConversationRef = useRef("");
  const selectedRunRef = useRef("");

  const openRun = useCallback(async (run: GeneralAgentRun) => {
    const [traceResponse, recoveryResponse] = await Promise.all([
      listGeneralAgentTraces(run.run_id),
      getGeneralAgentRecovery(run.run_id),
    ]);
    setCurrentRun(run);
    setTraces(traceResponse.traces);
    setTraceTotal(traceResponse.total);
    setRecovery(recoveryResponse.recovery);
    selectedRunRef.current = run.run_id;
    setSelectedRevision(current =>
      generalAgentPlanRevisions(run.node_runs).includes(current)
        ? current
        : run.plan_revision,
    );
    setSelectedNodeId(current => {
      const currentPlanNodes = run.node_runs.filter(
        node => node.plan_revision === run.plan_revision,
      );
      return run.node_runs.some(node => node.node_id === current)
        ? current
        : (currentPlanNodes[0]?.node_id ?? "");
    });
  }, []);

  const openConversation = useCallback(async (
    conversationId: string,
    preferredRunId = "",
  ) => {
    const response = await getGeneralAgentConversation(conversationId);
    setConversationRuns(response.runs);
    setSelectedConversationId(conversationId);
    selectedConversationRef.current = conversationId;
    const target =
      response.runs.find(run => run.run_id === preferredRunId) ??
      response.runs.at(-1) ??
      null;
    if (target) {
      await openRun(target);
      return;
    }
    setCurrentRun(null);
    setTraces([]);
    setTraceTotal(0);
    setRecovery(null);
    selectedRunRef.current = "";
  }, [openRun]);

  const reload = useCallback(async () => {
    const response = await listGeneralAgentConversations({ pageSize: 100 });
    setConversations(response.conversations);
    const candidates = response.conversations.filter(conversation =>
      matchesFilter(conversation.status, statusFilter),
    );
    const preferredConversation = selectedConversationRef.current;
    const conversation =
      candidates.find(
        item => item.conversation_id === preferredConversation,
      ) ?? candidates[0];
    if (conversation) {
      await openConversation(conversation.conversation_id, selectedRunRef.current);
    } else {
      setConversationRuns([]);
      setCurrentRun(null);
      setTraces([]);
      setTraceTotal(0);
      setRecovery(null);
      setSelectedConversationId("");
      selectedConversationRef.current = "";
      selectedRunRef.current = "";
    }
  }, [openConversation, statusFilter]);

  useEffect(() => {
    let ignore = false;
    async function initialLoad() {
      try {
        await reload();
        if (!ignore) {
          setError("");
        }
      } catch (caught) {
        if (!ignore) {
          setError(errorMessage(caught));
        }
      } finally {
        if (!ignore) {
          setLoading(false);
        }
      }
    }
    void initialLoad();
    return () => {
      ignore = true;
    };
  }, [reload]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void reload().catch(caught => setError(errorMessage(caught)));
    }, 3000);
    return () => window.clearInterval(timer);
  }, [reload]);

  const visibleConversations = useMemo(
    () => conversations.filter(item => matchesFilter(item.status, statusFilter)),
    [conversations, statusFilter],
  );
  const displayedRuns = useMemo(() => [...conversationRuns].reverse(), [conversationRuns]);
  const childRoundByParent = useMemo(
    () =>
      new Map(
        conversationRuns
          .map(run => [
            run.run_id,
            generalAgentContinuationRequestIndex(run, conversationRuns),
          ] as const)
          .filter((entry): entry is readonly [string, number] => entry[1] !== undefined),
      ),
    [conversationRuns],
  );
  const revisions = useMemo(
    () => (currentRun ? generalAgentPlanRevisions(currentRun.node_runs) : []),
    [currentRun],
  );
  const visibleNodes = useMemo(
    () =>
      currentRun?.node_runs.filter(node => node.plan_revision === selectedRevision) ?? [],
    [currentRun, selectedRevision],
  );
  const selectedNode =
    visibleNodes.find(node => node.node_id === selectedNodeId) ?? visibleNodes[0] ?? null;
  const nodeTraces = selectedNode ? tracesForGeneralAgentNode(selectedNode, traces) : [];
  const plannerTraces = orchestratorTraces(traces);

  return (
    <AppShell activePath="/task-monitor" viewportLocked>
      <section className="mx-auto grid h-full min-h-0 max-w-[1640px] grid-rows-[auto_minmax(0,1fr)] gap-4 px-4 py-4 xl:grid-cols-[340px_minmax(0,1fr)]">
        <div className="xl:col-span-2">
          <GeneralAgentMonitorNav active="monitor" />
        </div>
        <aside className="flex min-h-0 flex-col overflow-hidden rounded-[var(--tc-radius-card)] border border-[var(--tc-border-subtle)] bg-[var(--tc-surface-card)] p-3">
          <div className="flex items-center justify-between gap-3">
            <h1 className="text-base font-semibold text-[var(--tc-text-primary)]">
              通用写作助手
            </h1>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label="刷新通用写作助手任务"
              onClick={() => void reload()}
            >
              <RefreshCw className="size-4" />
            </Button>
          </div>
          <Link
            href="/task-monitor"
            className="mt-2 inline-flex items-center gap-1 text-xs text-[var(--tc-text-muted)] hover:text-[var(--tc-text-primary)]"
          >
            <ChevronLeft className="size-3" />
            返回任务入口
          </Link>
          <div className="mt-3 flex flex-wrap gap-1">
            {filters.map(filter => (
              <button
                key={filter.value}
                type="button"
                className={cn(
                  "rounded-[var(--tc-radius-control)] px-2 py-1.5 text-xs transition-colors duration-150 motion-reduce:transition-none",
                  statusFilter === filter.value
                    ? "bg-[var(--tc-surface-muted)] text-[var(--tc-text-primary)]"
                    : "text-[var(--tc-text-muted)] hover:bg-[var(--tc-surface-muted)] hover:text-[var(--tc-text-primary)]",
                )}
                  onClick={() => {
                    setStatusFilter(filter.value);
                  }}
              >
                {filter.label}
              </button>
            ))}
          </div>
          <div className="mt-4 flex min-h-0 flex-1 flex-col">
            <div className="flex items-center justify-between px-2 pb-1.5 text-xs text-[var(--tc-text-muted)]">
              <span>对话</span>
              <span>{visibleConversations.length} 个</span>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">
              {loading ? (
                <p className="px-2 py-3 text-xs text-[var(--tc-text-muted)]">正在读取对话</p>
              ) : visibleConversations.length === 0 ? (
                <p className="px-2 py-3 text-xs text-[var(--tc-text-muted)]">暂无符合条件的对话</p>
              ) : (
                <div className="grid gap-1">
                  {visibleConversations.map(conversation => {
                    const selected = selectedConversationId === conversation.conversation_id;
                    return (
                      <button
                        key={conversation.conversation_id}
                        type="button"
                        aria-pressed={selected}
                        className={cn(
                          "rounded-[var(--tc-radius-control)] px-3 py-2.5 text-left transition-colors duration-150 motion-reduce:transition-none",
                          selected
                            ? "bg-[var(--tc-surface-muted)] text-[var(--tc-text-primary)]"
                            : "text-[var(--tc-text-secondary)] hover:bg-[var(--tc-surface-muted)]",
                        )}
                        onClick={() => {
                          setDetailPanel(null);
                          selectedRunRef.current = "";
                          void openConversation(conversation.conversation_id);
                        }}
                      >
                        <span className="flex items-center justify-between gap-2">
                          <span className={cn("truncate text-sm", selected && "font-medium")}>
                            {conversation.title}
                          </span>
                          {selected ? (
                            <span className="inline-flex shrink-0 items-center gap-1 text-[11px] text-[var(--tc-text-secondary)]">
                              <Check className="size-3" />
                              当前
                            </span>
                          ) : null}
                        </span>
                        <span className="mt-1 flex items-center gap-2 text-xs text-[var(--tc-text-muted)]">
                          <span>{generalRunStatusLabels[conversation.status]}</span>
                          <span>·</span>
                          <span>{conversation.request_count} 次</span>
                          <span>·</span>
                          <span>{formatTime(conversation.updated_at)}</span>
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
            <div className="shrink-0 rounded-[var(--tc-radius-control)] border border-red-700/70 bg-red-950/20 px-3 py-2 text-sm text-[var(--tc-text-primary)]">
              {error}
            </div>
          ) : null}
          {currentRun ? (
            <>
              <RoundToolbar
                run={currentRun}
                runs={displayedRuns}
                childRoundByParent={childRoundByParent}
                nodeDetailDisabled={!selectedNode}
                onSelectRun={run => {
                  setDetailPanel(null);
                  void openRun(run);
                }}
                onOpenNodeDetail={() => setDetailPanel("node")}
                onOpenRunDetail={() => setDetailPanel("run")}
              />
              <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-[var(--tc-radius-card)] bg-[var(--tc-surface-card)] p-3">
                  <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                    <div className="flex flex-wrap gap-1.5">
                      {revisions.length === 0 ? (
                        <span className="text-xs text-[var(--tc-text-muted)]">直接回答</span>
                      ) : (
                        revisions.map(revision => (
                          <button
                            key={revision}
                            type="button"
                            className={cn(
                              "rounded-[var(--tc-radius-pill)] border px-2.5 py-1 text-xs",
                              selectedRevision === revision
                                ? "border-[var(--tc-border-strong)] bg-[var(--tc-surface-muted)] text-[var(--tc-text-primary)]"
                                : "border-[var(--tc-border-subtle)] text-[var(--tc-text-muted)]",
                            )}
                            onClick={() => setSelectedRevision(revision)}
                          >
                            {revision === currentRun.plan_revision ? "当前计划" : "历史计划"} · 修订 {revision}
                          </button>
                        ))
                      )}
                    </div>
                    <span className="text-xs text-[var(--tc-text-muted)]">
                      重规划 {currentRun.replan_count} 次
                    </span>
                  </div>
                  <GeneralAgentFlowGraph
                    nodes={visibleNodes}
                    runStatus={currentRun.status}
                    selectedNodeId={selectedNode?.node_id ?? ""}
                    onSelectNode={(nodeId, role) => {
                      if (role === "capability") {
                        setSelectedNodeId(nodeId);
                        setDetailPanel("node");
                        return;
                      }
                      setDetailPanel("run");
                    }}
                  />
              </section>
              <MonitorDetailDialog
                open={detailPanel === "node" && selectedNode !== null}
                title={selectedNode ? generalCapabilityLabel(selectedNode.capability_name) : "节点详情"}
                onOpenChange={open => setDetailPanel(open ? "node" : null)}
              >
                {selectedNode ? (
                  <NodeDetail
                    node={selectedNode}
                    nodes={currentRun.node_runs}
                    runStatus={currentRun.status}
                    traces={nodeTraces}
                  />
                ) : null}
              </MonitorDetailDialog>
              <MonitorDetailDialog
                open={detailPanel === "run"}
                title="本次请求详情"
                onOpenChange={open => setDetailPanel(open ? "run" : null)}
              >
                <RunDetail
                  run={currentRun}
                  traceTotal={traceTotal}
                  plannerTraces={plannerTraces}
                  recovery={recovery}
                />
              </MonitorDetailDialog>
            </>
          ) : (
            <div className="flex flex-1 items-center justify-center rounded-[var(--tc-radius-card)] border border-[var(--tc-border-subtle)] bg-[var(--tc-surface-card)] text-sm text-[var(--tc-text-muted)]">
              暂无通用写作助手运行记录。
            </div>
          )}
        </main>
      </section>
    </AppShell>
  );
}

function RoundToolbar({
  run,
  runs,
  childRoundByParent,
  nodeDetailDisabled,
  onSelectRun,
  onOpenNodeDetail,
  onOpenRunDetail,
}: {
  run: GeneralAgentRun;
  runs: GeneralAgentRun[];
  childRoundByParent: Map<string, number>;
  nodeDetailDisabled: boolean;
  onSelectRun: (run: GeneralAgentRun) => void;
  onOpenNodeDetail: () => void;
  onOpenRunDetail: () => void;
}) {
  const [roundsOpen, setRoundsOpen] = useState(false);
  return (
    <section className="flex shrink-0 flex-wrap items-center justify-between gap-3 rounded-[var(--tc-radius-card)] bg-[var(--tc-surface-card)] px-3 py-2.5">
      <Popover.Root open={roundsOpen} onOpenChange={setRoundsOpen}>
        <Popover.Trigger
          aria-label="选择监控请求"
          className="flex h-9 min-w-0 max-w-[720px] flex-1 items-center gap-2 rounded-[var(--tc-radius-pill)] border border-[var(--tc-border-subtle)] bg-[var(--tc-surface-muted)] px-3 text-left text-sm text-[var(--tc-text-primary)] outline-none hover:border-[var(--tc-border-strong)] focus-visible:border-[var(--tc-border-strong)]"
        >
          <span className="shrink-0 font-medium">第 {run.request_index} 次</span>
          <span aria-hidden="true" className="text-[var(--tc-text-muted)]">·</span>
          <span className="truncate text-[var(--tc-text-secondary)]">{run.user_goal}</span>
          <ChevronsUpDown className="ml-auto size-4 shrink-0 text-[var(--tc-text-muted)]" />
        </Popover.Trigger>
        <Popover.Portal>
          <Popover.Positioner side="bottom" align="start" sideOffset={8} className="z-50">
            <Popover.Popup className="w-[min(620px,calc(100vw-32px))] overflow-hidden rounded-[var(--tc-radius-card)] border border-[var(--tc-border-strong)] bg-[var(--tc-surface-card)] p-2 text-[var(--tc-text-primary)] outline-none data-[starting-style]:translate-y-1 data-[starting-style]:opacity-0 data-[ending-style]:translate-y-1 data-[ending-style]:opacity-0 motion-safe:transition-[opacity,transform] motion-safe:duration-150 motion-reduce:transition-none">
              <Popover.Title className="px-2 pt-1 pb-2 text-sm font-medium">
                请求（{runs.length}）
              </Popover.Title>
              <div className="max-h-[420px] overflow-y-auto">
                {runs.map(item => {
                  const selected = item.run_id === run.run_id;
                  const continuedByRound = childRoundByParent.get(item.run_id);
                  return (
                    <button
                      key={item.run_id}
                      type="button"
                      aria-pressed={selected}
                      className={cn(
                        "grid w-full grid-cols-[minmax(0,1fr)_auto] gap-x-3 rounded-[var(--tc-radius-control)] px-3 py-2.5 text-left",
                        selected
                          ? "bg-[var(--tc-surface-muted)]"
                          : "hover:bg-[var(--tc-surface-muted)]",
                      )}
                      onClick={() => {
                        setRoundsOpen(false);
                        onSelectRun(item);
                      }}
                    >
                      <span className="min-w-0">
                        <span className="flex items-center gap-2 text-xs text-[var(--tc-text-muted)]">
                          <strong className="font-medium text-[var(--tc-text-primary)]">
                            第 {item.request_index} 次
                          </strong>
                          <span>
                            {continuedByRound
                              ? `已由第 ${continuedByRound} 次请求接续`
                              : generalRunStatusLabels[item.status]}
                          </span>
                        </span>
                        <span className="mt-1 block truncate text-sm text-[var(--tc-text-secondary)]">
                          {item.user_goal}
                        </span>
                        <span className="mt-1 block text-xs text-[var(--tc-text-muted)]">
                          {generalRunProgressSummary(item)} · {formatTime(item.updated_at)}
                        </span>
                      </span>
                      {selected ? (
                        <Check className="mt-1 size-4 text-[var(--tc-text-primary)]" />
                      ) : null}
                    </button>
                  );
                })}
              </div>
            </Popover.Popup>
          </Popover.Positioner>
        </Popover.Portal>
      </Popover.Root>
      <div className="flex shrink-0 flex-wrap justify-end gap-2">
        <Button type="button" variant="outline" onClick={onOpenRunDetail}>
          <PanelRightOpen className="size-4" />
          本次请求详情
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={nodeDetailDisabled}
          onClick={onOpenNodeDetail}
        >
          <ListTree className="size-4" />
          节点详情
        </Button>
        <Link
          href="/agent-workbench"
          className="inline-flex h-8 items-center gap-1.5 rounded-[var(--tc-radius-pill)] border border-[var(--tc-border-subtle)] bg-[var(--tc-surface-muted)] px-3 text-sm font-medium text-[var(--tc-text-primary)]"
        >
          <Bot className="size-4" />
          返回工作台
        </Link>
      </div>
    </section>
  );
}

function NodeDetail({
  node,
  nodes,
  runStatus,
  traces,
}: {
  node: GeneralAgentNodeRun;
  nodes: GeneralAgentNodeRun[];
  runStatus: GeneralAgentRunStatus;
  traces: GeneralAgentInvocationTrace[];
}) {
  const visibleErrorMessage = generalNodeErrorMessage(
    node.error_message,
    runStatus,
  );
  const actionTraces = traces.filter(trace => trace.trace_id !== node.trace_id);
  return (
    <div>
      <div className="grid grid-cols-2 gap-2 text-xs">
        <Metric label="节点类型" value={node.kind === "tool" ? "工具" : "专业智能体"} />
        <Metric
          label="节点状态"
          value={generalNodeStatusLabel(node.status, runStatus)}
        />
        <Metric label="执行耗时" value={durationLabel(node.duration_ms)} />
        <Metric label="来源数量" value={`${node.source_refs.length} 个`} />
      </div>
      <DetailBlock title="任务目标" text={node.objective} />
      {node.kind === "subagent" && hasGeneralAgentResult(node.output) ? (
        <div className="mt-5">
          <h4 className="mb-2 text-sm font-medium text-[var(--tc-text-primary)]">
            执行结果
          </h4>
          <GeneralAgentSubagentResult
            capabilityName={node.capability_name}
            value={node.output}
          />
        </div>
      ) : null}
      <DetailBlock
        title="依赖节点"
        text={
          node.dependencies.length > 0
            ? node.dependencies
                .map(dependencyId => dependencyLabel(dependencyId, nodes))
                .join("、")
            : "无上游依赖"
        }
      />
      {node.authorization_approved || node.authorization_resource_scopes.length > 0 ? (
        <div className="mt-3 rounded-[var(--tc-radius-control)] border border-amber-700/50 bg-amber-950/15 p-3 text-xs text-[var(--tc-text-secondary)]">
          <p className="flex items-center gap-1.5 font-medium text-amber-200">
            <ShieldCheck className="size-4" />
            写入授权
          </p>
          <p className="mt-1">{node.authorization_approved ? "作者已授权该节点的确定输入。" : "该节点正在等待作者授权。"}</p>
        </div>
      ) : null}
      {node.effect_status ? (
        <div className="mt-3 rounded-[var(--tc-radius-control)] bg-[var(--tc-surface-muted)] p-3 text-xs text-[var(--tc-text-secondary)]">
          <p className="flex items-center gap-1.5 font-medium text-[var(--tc-text-primary)]">
            <ShieldCheck className="size-4" />
            重复写入保护
          </p>
          <p className="mt-1">
            副作用状态：{effectStatusLabel(node.effect_status)}；
            {node.duplicate_execution_protected ? "已启用对账保护。" : "未记录对账保护。"}
          </p>
          {node.reconciliation_reason ? (
            <p className="mt-1 text-[var(--tc-text-muted)]">{node.reconciliation_reason}</p>
          ) : null}
        </div>
      ) : null}
      {visibleErrorMessage ? (
        <div className="mt-3 rounded-[var(--tc-radius-control)] border border-red-700/60 bg-red-950/20 p-3 text-xs text-red-100">
          <p className="font-medium">节点异常</p>
          <p className="mt-1 whitespace-pre-wrap">{visibleErrorMessage}</p>
          {node.error_type ? <p className="mt-1 text-red-200/65">技术错误类型：{node.error_type}</p> : null}
        </div>
      ) : null}
      <div className="mt-5">
        <h4 className="text-sm font-medium text-[var(--tc-text-primary)]">实际动作</h4>
        <NodeActionList traces={actionTraces} />
      </div>
      <details className="mt-4 text-xs text-[var(--tc-text-secondary)]">
        <summary className="cursor-pointer text-[var(--tc-text-muted)] hover:text-[var(--tc-text-primary)]">
          技术调用记录（{traces.length}）
        </summary>
        <TraceList traces={traces} />
      </details>
    </div>
  );
}

function NodeActionList({ traces }: { traces: GeneralAgentInvocationTrace[] }) {
  if (traces.length === 0) {
    return (
      <p className="mt-2 rounded-[var(--tc-radius-control)] bg-[var(--tc-surface-muted)] px-3 py-2.5 text-xs text-[var(--tc-text-muted)]">
        该节点没有继续调用其他工具、智能体或模型。
      </p>
    );
  }
  return (
    <div className="mt-2 grid gap-1.5">
      {traces.map(trace => (
        <div
          key={trace.trace_id}
          className="flex items-center gap-2.5 rounded-[var(--tc-radius-control)] bg-[var(--tc-surface-muted)] px-3 py-2.5 text-xs"
        >
          <span className={cn(
            "flex size-7 shrink-0 items-center justify-center rounded-full",
            trace.capability_type === "tool"
              ? "bg-cyan-400/10 text-cyan-300"
              : trace.capability_type === "subagent"
                ? "bg-violet-400/10 text-violet-300"
                : "bg-amber-400/10 text-amber-300",
          )}>
            {trace.capability_type === "tool" ? (
              <Wrench className="size-3.5" />
            ) : trace.capability_type === "subagent" ? (
              <Bot className="size-3.5" />
            ) : (
              <Activity className="size-3.5" />
            )}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[var(--tc-text-primary)]">
              {invocationTypeLabel(trace.capability_type)} · {generalCapabilityLabel(trace.capability_name)}
            </span>
            <span className="mt-0.5 block text-[var(--tc-text-muted)]">
              {durationLabel(trace.duration_ms)}
            </span>
          </span>
          <span className={trace.status === "completed" ? "text-emerald-300" : "text-red-300"}>
            {invocationStatusLabel(trace.status)}
          </span>
        </div>
      ))}
    </div>
  );
}

function TraceList({ traces }: { traces: GeneralAgentInvocationTrace[] }) {
  if (traces.length === 0) {
    return <p className="mt-2 text-xs text-[var(--tc-text-muted)]">暂无可展示的调用记录。</p>;
  }
  return (
    <div className="mt-2 grid gap-1.5">
      {traces.map(trace => (
        <details
          key={trace.trace_id}
          className="rounded-[var(--tc-radius-control)] border border-[var(--tc-border-subtle)] bg-[var(--tc-surface-muted)] px-2.5 py-2 text-xs"
        >
          <summary className="cursor-pointer list-none text-[var(--tc-text-primary)]">
            <span className="flex items-center justify-between gap-2">
              <span className="truncate">
                {invocationTypeLabel(trace.capability_type)} · {generalCapabilityLabel(trace.capability_name)}
              </span>
              <span className={trace.status === "completed" ? "text-emerald-300" : "text-red-300"}>
                {invocationStatusLabel(trace.status)}
              </span>
            </span>
          </summary>
          <dl className="mt-2 grid gap-1 rounded-[var(--tc-radius-control)] bg-[var(--tc-surface-card)] p-2 text-[var(--tc-text-muted)]">
            <div className="flex justify-between gap-2"><dt>耗时</dt><dd>{durationLabel(trace.duration_ms)}</dd></div>
            <div className="flex justify-between gap-2"><dt>输入/输出字符</dt><dd>{trace.input_char_count}/{trace.output_char_count}</dd></div>
            {trace.input_tokens != null || trace.output_tokens != null ? (
              <div className="flex justify-between gap-2"><dt>输入/输出 Token</dt><dd>{trace.input_tokens ?? 0}/{trace.output_tokens ?? 0}</dd></div>
            ) : null}
            {trace.model_role ? <div className="flex justify-between gap-2"><dt>模型角色</dt><dd>{trace.model_role}</dd></div> : null}
            {trace.model_id ? <div className="flex justify-between gap-2"><dt>模型标识</dt><dd>{trace.model_id}</dd></div> : null}
            {trace.authorization_reference ? <div className="flex justify-between gap-2"><dt>授权记录</dt><dd>已绑定</dd></div> : null}
            {trace.error_message ? <div><dt className="text-red-300">错误说明</dt><dd className="mt-0.5 whitespace-pre-wrap text-red-100">{trace.error_message}</dd></div> : null}
          </dl>
        </details>
      ))}
      </div>
  );
}

function RunDetail({
  run,
  traceTotal,
  plannerTraces,
  recovery,
}: {
  run: GeneralAgentRun;
  traceTotal: number;
  plannerTraces: GeneralAgentInvocationTrace[];
  recovery: GeneralAgentRecoverySnapshot | null;
}) {
  return (
    <div>
      <div className="grid grid-cols-3 gap-2 text-xs">
        <Metric label="本次状态" value={generalRunStatusLabels[run.status]} />
        <Metric label="能力进度" value={generalRunProgressSummary(run)} />
        <Metric label="调用记录" value={`${traceTotal} 条`} />
      </div>

      {recovery ? (
        <div className="mt-4 rounded-[var(--tc-radius-control)] bg-[var(--tc-surface-muted)] p-3 text-xs text-[var(--tc-text-secondary)]">
          <p className="flex items-center gap-1.5 font-medium text-[var(--tc-text-primary)]">
            <ShieldCheck className="size-4" />
            恢复与重复写入保护
          </p>
          <div className="mt-2 grid grid-cols-2 gap-2">
            <Metric
              label="官方检查点"
              value={checkpointStatusLabel(recovery.checkpoint.status)}
            />
            <Metric
              label="持久状态"
              value={`${recovery.checkpoint.checkpoint_count} 个`}
            />
          </div>
          {recovery.effects.length ? (
            <div className="mt-3 grid gap-1.5">
              {recovery.effects.map(effect => (
                <div
                  key={effect.effect_id}
                  className="flex items-start gap-2 rounded-[var(--tc-radius-control)] bg-[var(--tc-surface-card)] px-2.5 py-2"
                >
                  <ShieldCheck className="mt-0.5 size-3.5 shrink-0 text-cyan-300" />
                  <span className="min-w-0 flex-1">
                    <span className="block text-[var(--tc-text-primary)]">
                      工具 · {generalCapabilityLabel(effect.tool_name)}
                    </span>
                    <span className="mt-0.5 block text-[var(--tc-text-muted)]">
                      {effect.reason || "已记录真实资源对账状态。"}
                    </span>
                  </span>
                  <span className={effect.status === "requires_human" || effect.status === "unknown" ? "text-amber-300" : effect.status === "failed" ? "text-red-300" : "text-emerald-300"}>
                    {effectStatusLabel(effect.status)}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <p className="mt-2 text-[var(--tc-text-muted)]">本次请求没有真实写入副作用。</p>
          )}
        </div>
      ) : null}

      <div className="mt-4 rounded-[var(--tc-radius-control)] bg-[var(--tc-surface-muted)] p-3 text-xs text-[var(--tc-text-secondary)]">
        <p className="flex items-center gap-1.5 font-medium text-[var(--tc-text-primary)]">
          <Database className="size-4" />
          上下文与记忆
        </p>
        <p className="mt-2">
          上下文{run.compression_stats.compressed ? "已压缩" : "未压缩"}，估算约
          {" "}{run.compression_stats.estimated_token_count.toLocaleString("zh-CN")} Token。
        </p>
        <p className="mt-1 text-[var(--tc-text-muted)]">
          省略 {run.compression_stats.omitted_message_count} 条消息、
          {run.compression_stats.omitted_node_count} 个节点输出。
        </p>
        {run.context_resume_differences.length ? (
          <div className="mt-3">
            <p className="font-medium text-[var(--tc-text-primary)]">恢复时的上下文差异</p>
            <ul className="mt-1 grid gap-1 text-[var(--tc-text-muted)]">
              {run.context_resume_differences.map(item => (
                <li key={item}>· {item}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>

      <DetailBlock title="计划依据" text={run.plan?.rationale || "未记录计划依据"} />
      {run.final_answer ? <DetailBlock title="最终结果" text={run.final_answer} /> : null}

      <div className="mt-5">
        <h4 className="text-sm font-medium text-[var(--tc-text-primary)]">高层编排调用</h4>
        <TraceList traces={plannerTraces} />
      </div>
    </div>
  );
}

function MonitorDetailDialog({
  open,
  title,
  onOpenChange,
  children,
}: {
  open: boolean;
  title: string;
  onOpenChange: (open: boolean) => void;
  children: ReactNode;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-40 bg-black/45 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0 motion-safe:transition-opacity motion-safe:duration-150 motion-reduce:transition-none" />
        <Dialog.Viewport className="fixed inset-0 z-50 flex justify-end p-3">
          <Dialog.Popup className="flex h-full w-full max-w-[560px] flex-col overflow-hidden rounded-[var(--tc-radius-card)] border border-[var(--tc-border-strong)] bg-[var(--tc-surface-card)] text-[var(--tc-text-primary)] outline-none data-[starting-style]:translate-x-3 data-[starting-style]:opacity-0 data-[ending-style]:translate-x-3 data-[ending-style]:opacity-0 motion-safe:transition-[opacity,transform] motion-safe:duration-150 motion-reduce:transition-none">
            <header className="flex shrink-0 items-start justify-between gap-3 bg-[var(--tc-surface-muted)] px-4 py-3">
              <div className="min-w-0">
                <Dialog.Title className="truncate text-base font-semibold">{title}</Dialog.Title>
                <Dialog.Description className="sr-only">
                  {title}
                </Dialog.Description>
              </div>
              <Dialog.Close
                type="button"
                aria-label="关闭详情"
                className="flex size-7 shrink-0 items-center justify-center rounded-[var(--tc-radius-control)] border border-[var(--tc-border-subtle)] text-[var(--tc-text-muted)] hover:text-[var(--tc-text-primary)]"
              >
                <X className="size-4" />
              </Dialog.Close>
            </header>
            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">{children}</div>
          </Dialog.Popup>
        </Dialog.Viewport>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-[var(--tc-radius-control)] bg-[var(--tc-surface-muted)] px-2.5 py-2">
      <p className="text-[var(--tc-text-muted)]">{label}</p>
      <p className="mt-0.5 text-[var(--tc-text-primary)]">{value}</p>
    </div>
  );
}

function DetailBlock({
  title,
  text,
}: {
  title: string;
  text: string;
}) {
  return (
    <div className="mt-3">
      <p className="text-xs font-medium text-[var(--tc-text-primary)]">{title}</p>
      <p className="mt-1 whitespace-pre-wrap text-xs leading-5 text-[var(--tc-text-secondary)]">
        {text}
      </p>
    </div>
  );
}

function matchesFilter(status: GeneralAgentRunStatus, filter: StatusFilter): boolean {
  if (filter === "all") return true;
  if (filter === "active") return isGeneralAgentRunActive(status);
  if (filter === "failed") return ["failed", "cancelled", "timeout"].includes(status);
  return status === filter;
}

function invocationTypeLabel(type: GeneralAgentInvocationTrace["capability_type"]): string {
  return { tool: "工具", subagent: "专业智能体", llm: "模型" }[type];
}

function dependencyLabel(
  dependencyId: string,
  nodes: GeneralAgentNodeRun[],
): string {
  const dependency = nodes.find(node => node.node_id === dependencyId);
  if (!dependency) return dependencyId;
  return `${dependency.kind === "tool" ? "工具" : "专业智能体"} · ${generalCapabilityLabel(dependency.capability_name)}`;
}

function effectStatusLabel(status: GeneralAgentEffectStatus): string {
  return {
    prepared: "已准备",
    started: "写入中",
    succeeded: "已生效",
    failed: "未生效",
    unknown: "结果待核对",
    reconciled: "对账确认生效",
    requires_human: "需作者核对",
  }[status];
}

function checkpointStatusLabel(status: GeneralAgentRecoverySnapshot["checkpoint"]["status"]): string {
  return {
    available: "可恢复",
    missing: "尚未生成",
  }[status];
}

function invocationStatusLabel(status: GeneralAgentInvocationTrace["status"]): string {
  return { completed: "成功", failed: "失败", timed_out: "超时" }[status];
}

function durationLabel(durationMs: number): string {
  return durationMs < 1_000 ? `${durationMs} 毫秒` : `${(durationMs / 1_000).toFixed(1)} 秒`;
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "通用写作助手任务监控加载失败";
}
