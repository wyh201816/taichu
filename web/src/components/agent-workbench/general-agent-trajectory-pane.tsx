"use client";

import { GeneralAgentTrajectoryLedger } from "@/components/agent-task-monitor/general-agent-trajectory-ledger";
import { useGeneralAgentTrajectory } from "@/components/agent-task-monitor/use-general-agent-trajectory";

export function GeneralAgentTrajectoryPane({ conversationId, active }: {
  conversationId: string; active: boolean;
}) {
  const state = useGeneralAgentTrajectory(conversationId, active);
  return <section aria-label="执行轨迹工作区" className="tc-trajectory-workspace flex h-full min-h-0 flex-col bg-[var(--tc-surface-page)] text-[var(--tc-text-primary)]">
    {state.error && <p role="alert" className="px-4 py-2 text-xs">{state.error}</p>}
    {state.trace && !state.trace.complete && <p role="status" className="px-4 py-1 text-xs">记录不完整 · {state.trace.warnings.join("；")}</p>}
    {state.trace && <GeneralAgentTrajectoryLedger trace={state.trace} loadEarlier={state.loadEarlier} locate={state.locate} search={state.search} detail={state.detail} onRefresh={state.reload} />}
    {!state.trace && <p className="p-4 text-sm text-[var(--tc-text-muted)]">{!conversationId ? "开始对话后可查看轨迹" : state.loading ? "正在读取轨迹…" : "尚无新轨迹记录"}</p>}
  </section>;
}
