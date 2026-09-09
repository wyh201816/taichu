"use client";

import { Dialog } from "@base-ui/react/dialog";
import { Activity, ArrowDownToLine, ArrowRight, Bot, BrainCircuit, Clock3, ListFilter, ListOrdered, Search, SlidersHorizontal, UserRound, Wrench, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { trajectoryDuration, trajectoryStatusLabels, type TrajectoryStatus } from "@/lib/general-agent-trajectory";
import { buildTrajectoryLedger, filterTrajectoryLedger, ledgerInputOutput, trajectoryPreview,
  type LedgerRow, type LedgerSource, type StripMode, type TrajectoryRecord } from "@/lib/general-agent-trajectory-ledger";
import { cn } from "@/lib/utils";
import { GeneralAgentTrajectoryInspector } from "./general-agent-trajectory-inspector";
import { GeneralAgentTrajectoryStrip } from "./general-agent-trajectory-strip";

const ROW_HEIGHT = 40;
const control = "h-7 rounded border border-[var(--tc-border-subtle)] bg-[var(--tc-surface-muted)] px-2 text-xs text-[var(--tc-text-primary)] outline-none focus-visible:outline-2 focus-visible:outline-white";
const statusStyle: Record<TrajectoryStatus, string> = { recorded: "text-[var(--tc-text-muted)]", completed: "text-emerald-300",
  running: "text-blue-300", failed: "text-red-300", waiting: "text-orange-300", skipped: "text-[var(--tc-text-muted)]", interrupted: "text-red-300" };

export function GeneralAgentTrajectoryLedger({ records }: { records: TrajectoryRecord[] }) {
  const [mode, setMode] = useState<StripMode>("duration");
  const [source, setSource] = useState<LedgerSource>("messages");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("全部");
  const [selectedId, setSelectedId] = useState("");
  const [detailOpen, setDetailOpen] = useState(false);
  const [follow, setFollow] = useState(true);
  const [scrollTop, setScrollTop] = useState(0);
  const [height, setHeight] = useState(600);
  const listRef = useRef<HTMLDivElement>(null);
  const pendingScroll = useRef<string | null>(null);
  const allRows = useMemo(() => buildTrajectoryLedger(records, "all"), [records]);
  const sourceRows = useMemo(() => buildTrajectoryLedger(records, source), [records, source]);
  const rows = useMemo(() => filterTrajectoryLedger(sourceRows, filter, query), [sourceRows, filter, query]);
  const selected = allRows.find(row => row.id === selectedId);
  const first = Math.min(Math.max(0, rows.length - 1), Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - 6));
  const last = Math.min(rows.length, first + Math.ceil(height / ROW_HEIGHT) + 12);
  const visible = rows.slice(first, last);
  const tailId = rows.at(-1)?.id;
  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    const observer = new ResizeObserver(() => setHeight(el.clientHeight));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (follow && listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [follow, tailId, rows.length, height]);
  useEffect(() => {
    if (!pendingScroll.current || !listRef.current) return;
    const index = rows.findIndex(row => row.id === pendingScroll.current);
    if (index >= 0) {
      listRef.current.scrollTop = Math.max(0, index * ROW_HEIGHT - listRef.current.clientHeight / 2 + ROW_HEIGHT / 2);
      pendingScroll.current = null;
    }
  }, [rows, selectedId]);
  function select(id: string, details = false) {
    setSelectedId(id); setFollow(false); setDetailOpen(details);
    const index = rows.findIndex(row => row.id === id);
    if (index >= 0 && listRef.current) {
      listRef.current.scrollTop = Math.max(0, index * ROW_HEIGHT - listRef.current.clientHeight / 2 + ROW_HEIGHT / 2);
    } else { pendingScroll.current = id; setSource("all"); setFilter("全部"); setQuery(""); }
  }
  function resetScroll() { setFollow(false); setScrollTop(0); listRef.current?.scrollTo({ top: 0 }); }
  const modes = [{ key: "duration", label: "时长", icon: Clock3 }, { key: "turns", label: "轮次", icon: ListOrdered }, { key: "calls", label: "调用", icon: SlidersHorizontal }] as const;
  return <div className="flex min-h-0 flex-1 flex-col" data-testid="trajectory-ledger">
    <div className="flex h-9 shrink-0 items-center gap-2 bg-[var(--tc-surface-card)] px-3">
      <div role="group" aria-label="时序横轴模式" className="flex gap-1">{modes.map(({ key, label, icon: Icon }) => <button key={key} type="button" aria-pressed={mode === key}
        onClick={() => setMode(key)} className={cn("flex h-7 items-center gap-1.5 rounded px-2.5 text-sm focus-visible:outline-2 focus-visible:outline-white", mode === key ? "bg-[var(--tc-surface-muted)] text-[var(--tc-text-primary)]" : "text-[var(--tc-text-muted)] hover:text-[var(--tc-text-primary)]")}><Icon className="size-3.5" />{label}</button>)}</div>
      <div className="ml-auto flex items-center gap-2"><select aria-label="轨迹证据视角" value={source} className={control} onChange={event => { setSource(event.target.value as LedgerSource); resetScroll(); }}>
        <option value="messages">消息回放</option><option value="calls">调用追踪</option><option value="all">全部证据</option>
      </select><ListFilter className="size-3.5 text-[var(--tc-text-muted)]" /><select aria-label="筛选轨迹事件" value={filter} className={control} onChange={event => { setFilter(event.target.value); resetScroll(); }}>
        <option>全部</option><option>异常</option><option value="user">作者输入</option><option value="request">助手与输出</option><option value="tool">工具</option><option value="subagent">专业智能体</option><option value="context">上下文</option><option value="answer">最终回复</option><option value="state">运行状态</option><option value="model">模型追踪</option><option value="human">人工介入</option>
      </select><label className="relative"><Search className="absolute left-2 top-2 size-3.5 text-[var(--tc-text-muted)]" /><input value={query} aria-label="搜索轨迹内容" placeholder="搜索轨迹" className={cn(control, "w-56 pl-7")}
        onChange={event => { setQuery(event.target.value); resetScroll(); }} /></label><Button variant="ghost" size="icon-sm" aria-label={follow ? "暂停跟随最新事件" : "跟随最新事件"} aria-pressed={follow} onClick={() => setFollow(value => !value)}><ArrowDownToLine className="size-4" /></Button></div>
    </div>
    <GeneralAgentTrajectoryStrip rows={allRows} mode={mode} selectedId={selectedId} onSelect={id => select(id)} />
    <div ref={listRef} aria-label="执行轨迹事件列表" className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden" tabIndex={0}
      onScroll={event => setScrollTop(event.currentTarget.scrollTop)} onWheel={event => { if (event.deltaY < 0) setFollow(false); }}
      onKeyDown={event => { if (["ArrowUp", "PageUp", "Home"].includes(event.key)) setFollow(false); }}
      onPointerDown={event => { if (event.target === event.currentTarget) setFollow(false); }}>
      {!rows.length ? <p className="px-6 py-10 text-sm text-[var(--tc-text-muted)]">没有匹配的事件，试试其他类型或关键词。</p> : <>
        <div aria-hidden="true" style={{ height: first * ROW_HEIGHT }} />
        {visible.map(row => <LedgerLine key={row.id} row={row} selected={row.id === selectedId} onClick={() => select(row.id, true)} />)}
        <div aria-hidden="true" style={{ height: Math.max(0, rows.length - last) * ROW_HEIGHT }} />
      </>}
    </div>
    <div className="flex h-7 shrink-0 items-center justify-between gap-3 bg-[var(--tc-surface-card)] px-4 text-[11px] text-[var(--tc-text-muted)]">
      <span>{records.length} 轮 · {rows.length} 条记录 · {follow ? "跟随最新" : "浏览历史"}</span>
      <span>{source === "messages" ? "消息回放与顶层动作；原生工具按调用编号配对" : source === "calls" ? "执行追踪；不猜测与回放的关联" : "全部证据；独立记录可能描述同一次调用"} · 搜索摘要 · 只读</span>
    </div>
    <Dialog.Root open={detailOpen && !!selected} onOpenChange={setDetailOpen}>
      <Dialog.Portal><Dialog.Backdrop className="fixed inset-0 z-50 bg-black/30" /><Dialog.Popup className="fixed bottom-4 right-4 top-4 z-50 flex w-[min(640px,48vw)] flex-col overflow-hidden rounded-xl border border-[var(--tc-border-subtle)] bg-[var(--tc-surface-muted)] text-[var(--tc-text-primary)]">
        <div className="flex shrink-0 items-center justify-between px-4 pt-3"><Dialog.Title className="text-sm">轨迹证据详情</Dialog.Title><Dialog.Close aria-label="关闭轨迹详情" className="rounded-full p-1"><X className="size-4" /></Dialog.Close></div>
        <Dialog.Description className="sr-only">查看原始请求、输入输出、来源和调用关系。</Dialog.Description>
        <GeneralAgentTrajectoryInspector key={selectedId} item={selected} parent={allRows.find(row => row.id === selected?.parentId)} childrenItems={allRows.filter(row => row.parentId === selectedId)} onSelect={id => select(id, true)} />
      </Dialog.Popup></Dialog.Portal>
    </Dialog.Root>
  </div>;
}

function LedgerLine({ row, selected, onClick }: { row: LedgerRow; selected: boolean; onClick: () => void }) {
  const assistant = row.kind === "request" || row.kind === "answer";
  const tool = row.kind === "tool";
  const Icon = assistant ? Bot : tool ? Wrench : row.kind === "user" ? UserRound : row.kind === "context" ? BrainCircuit : Activity;
  const label = row.nativeCall?.structured ? "输出" : assistant ? "助手" : ({ user: "输入", model: "模型", tool: "工具", subagent: "智能体", context: "上下文", state: "状态", human: "人工介入" } as Record<string, string>)[row.kind];
  const color = assistant || row.kind === "subagent" ? "text-violet-300" : tool ? "text-cyan-300" : row.kind === "model" ? "text-amber-300" : row.kind === "user" ? "text-emerald-300" : "text-[var(--tc-text-muted)]";
  const io = ledgerInputOutput(row);
  const name = row.title.split(" · ").slice(1).join(" · ");
  const summary = row.nativeCall?.structured && row.status === "failed" ? row.nativeCall.output ?? row.summary : row.summary;
  return <button type="button" aria-label={`查看${row.title}，${trajectoryStatusLabels[row.status]}`} aria-pressed={selected} data-row-kind={row.kind}
    data-testid="trajectory-row" onClick={onClick} className={cn("flex h-10 w-full items-center gap-3 px-4 text-left text-sm focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-white",
      selected ? "bg-[var(--tc-surface-muted)]" : "even:bg-[color-mix(in_srgb,var(--tc-surface-muted),transparent_78%)] hover:bg-[var(--tc-surface-muted)]/70")}>
    <span className="w-5 shrink-0 text-center font-mono text-[10px] text-[var(--tc-text-muted)]" title={`第 ${row.requestIndex} 轮`}>{row.kind === "user" ? row.requestIndex : <span className="inline-block size-1 rounded-full bg-current opacity-55" />}</span>
    <span className={cn("flex shrink-0 items-center gap-1.5", tool ? "w-[116px] justify-end pr-2" : "w-[84px]")}>
      <span className={cn("flex items-center gap-1.5 rounded px-1.5 py-0.5 text-xs font-medium", color, assistant && "bg-violet-400/10")}><Icon className="size-3" />{label}</span>
    </span>
    {io ? <span className="flex min-w-0 flex-1 items-center gap-3">
      <span className="max-w-[190px] shrink-0 truncate text-[var(--tc-text-primary)]">{name}</span>
      <span className="min-w-0 flex-1 truncate font-mono text-xs text-[var(--tc-text-secondary)]">{io.input}</span>
      <ArrowRight className="size-3.5 shrink-0 text-[var(--tc-text-muted)]" />
      <span className="min-w-0 flex-1 truncate font-mono text-xs text-[var(--tc-text-secondary)]">{io.output}</span>
    </span> : <span className="min-w-0 flex-1 truncate text-[var(--tc-text-secondary)]">{!["user", "answer", "state"].includes(row.kind) && <span className="mr-3 text-[var(--tc-text-muted)]">{name}</span>}{trajectoryPreview(summary, 600)}</span>}
    <span className="w-20 shrink-0 text-right text-[11px] text-[var(--tc-text-muted)]">{row.durationMs !== null ? trajectoryDuration(row.durationMs) : ""}</span>
    <span className={cn("w-[100px] shrink-0 text-right text-[11px]", statusStyle[row.status])}>{trajectoryStatusLabels[row.status]}</span>
  </button>;
}
