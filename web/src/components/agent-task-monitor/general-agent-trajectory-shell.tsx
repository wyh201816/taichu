"use client";

import { Dialog } from "@base-ui/react/dialog";
import { ChevronLeft, ChevronRight, MessageSquareText, RefreshCw, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { AppShell } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { generalRunStatusLabels, isGeneralAgentRunActive } from "@/lib/general-agent-display";
import { GeneralAgentMonitorNav } from "./general-agent-monitor-nav";
import { GeneralAgentTrajectoryLedger } from "./general-agent-trajectory-ledger";
import { useGeneralAgentTrajectory } from "./use-general-agent-trajectory";

export function GeneralAgentTrajectoryShell({ initialConversationId = "", initialRunId = "" }: { initialConversationId?: string; initialRunId?: string }) {
  const state = useGeneralAgentTrajectory(initialConversationId, initialRunId);
  const [pickerOpen, setPickerOpen] = useState(false);
  const { current } = state;
  const title = state.conversations.find(item => item.conversation_id === state.conversationId)?.title ?? current?.runs[0]?.user_goal ?? "选择对话";
  return <AppShell activePath="/task-monitor" viewportLocked headerActions={<div className="flex items-center gap-3">
    <Link href="/task-monitor" className="flex items-center gap-1 text-xs text-[var(--tc-text-muted)]"><ChevronLeft className="size-3" />任务监控</Link>
    <GeneralAgentMonitorNav active="trajectory" />
  </div>}>
    <section aria-label="执行轨迹工作区" className="flex h-full min-h-0 flex-col bg-[var(--tc-surface-page)] text-[var(--tc-text-primary)]">
      <div className="flex h-10 shrink-0 items-center gap-3 px-4">
        <h1 className="shrink-0 text-sm font-medium">执行轨迹</h1>
        <Button variant="ghost" size="sm" aria-label="选择轨迹对话" onClick={() => setPickerOpen(true)} className="min-w-0 max-w-[560px] justify-start">
          <MessageSquareText className="size-3.5 shrink-0" /><span className="truncate">{title}</span><ChevronRight className="size-3 shrink-0" />
        </Button>
        {current && <>
          <span className="ml-auto shrink-0 text-xs text-[var(--tc-text-muted)]">{state.runId === "all" ? "最近一轮 · " : ""}{generalRunStatusLabels[current.run.status]}{isGeneralAgentRunActive(current.run.status) ? " · 自动更新" : ""}</span>
          <select aria-label="选择轨迹轮次" className="h-7 rounded border border-[var(--tc-border-subtle)] bg-[var(--tc-surface-muted)] px-2 text-xs"
            value={state.runId === "all" ? "all" : current.run.run_id} onChange={event => state.selectRun(event.target.value)}>
            <option value="all">全部 {current.runs.length} 轮</option>
            {[...current.runs].reverse().map(run => <option key={run.run_id} value={run.run_id}>第 {run.request_index} 轮 · {generalRunStatusLabels[run.status]}</option>)}
          </select>
        </>}
        <Button variant="ghost" size="icon-sm" aria-label="刷新执行轨迹" onClick={state.reload}><RefreshCw className="size-3.5" /></Button>
      </div>
      {state.error && <p role="alert" className="px-4 py-2 text-xs text-red-300">{state.error}</p>}
      {!!current?.warnings.length && <details aria-label="证据加载提示" className="shrink-0 px-4 py-1 text-xs text-orange-300">
        <summary className="cursor-pointer">部分证据不可用 · {current.warnings.length} 项，其余轨迹可正常查看</summary>
        <p className="max-h-28 overflow-y-auto py-2 leading-5">{current.warnings.join("；")}。可点击刷新重试。</p>
      </details>}
      {current ? <GeneralAgentTrajectoryLedger key={`${current.conversationId}:${state.runId === "all" ? "all" : current.run.run_id}`} records={current.records} />
        : <p className="flex flex-1 items-center justify-center text-sm text-[var(--tc-text-muted)]">{state.detailLoading || state.listLoading ? "正在读取轨迹证据…" : "选择对话查看执行轨迹"}</p>}
    </section>
    <Dialog.Root open={pickerOpen} onOpenChange={setPickerOpen}>
      <Dialog.Portal><Dialog.Backdrop className="fixed inset-0 z-50 bg-black/45" /><Dialog.Popup className="fixed left-1/2 top-1/2 z-50 flex max-h-[76vh] w-[560px] -translate-x-1/2 -translate-y-1/2 flex-col rounded-xl border border-[var(--tc-border-subtle)] bg-[var(--tc-surface-card)] p-4 text-[var(--tc-text-primary)]">
        <div className="mb-3 flex items-center justify-between"><Dialog.Title className="text-sm font-medium">选择对话</Dialog.Title><Dialog.Close aria-label="关闭对话选择" className="rounded-full p-1"><X className="size-4" /></Dialog.Close></div>
        <Dialog.Description className="sr-only">按对话查看轨迹，可翻页选择较早的对话。</Dialog.Description>
        <div aria-label="轨迹对话列表" className="min-h-0 flex-1 space-y-1 overflow-y-auto">
          {!state.conversations.length && <p className="p-3 text-xs">{state.listLoading ? "正在读取对话…" : "暂无可查看的对话"}</p>}
          {state.conversations.map(conversation => <button key={conversation.conversation_id} type="button" onClick={() => { state.selectConversation(conversation.conversation_id); setPickerOpen(false); }}
            aria-pressed={state.conversationId === conversation.conversation_id} className="flex w-full items-center gap-4 rounded-lg px-3 py-2 text-left hover:bg-[var(--tc-surface-muted)] focus-visible:outline-2 focus-visible:outline-white">
            <span className="min-w-0 flex-1 truncate text-sm">{conversation.title}</span><span className="shrink-0 text-xs text-[var(--tc-text-muted)]">{conversation.request_count} 轮 · {new Date(conversation.created_at).toLocaleDateString("zh-CN")}</span>
          </button>)}
        </div>
        <div className="mt-3 flex items-center justify-between text-xs text-[var(--tc-text-muted)]"><span>共 {state.total} 个对话 · 第 {state.page} 页</span><div className="flex gap-1">
          <Button variant="ghost" size="sm" disabled={state.page <= 1 || state.listLoading} onClick={() => state.setPage(state.page - 1)}>上一页</Button>
          <Button variant="ghost" size="sm" disabled={state.page * 20 >= state.total || state.listLoading} onClick={() => state.setPage(state.page + 1)}>下一页</Button>
        </div></div>
      </Dialog.Popup></Dialog.Portal>
    </Dialog.Root>
  </AppShell>;
}
