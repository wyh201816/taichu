"use client";

import { ChevronDown, ChevronRight, Clock3, RefreshCw, SquareMinus, SquarePlus } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { generalCapabilityLabel } from "@/lib/general-agent-display";
import { rowContent, rowLabel, rowPurpose, rowStatus, rowSubagentLabel, trajectoryLedgerEntries, type TrajectoryPage, type TrajectoryRow, type TrajectorySearch } from "@/lib/trajectory-records";
import { GeneralAgentTrajectoryInspector } from "./general-agent-trajectory-inspector";
import { GeneralAgentTrajectoryTimeline } from "./general-agent-trajectory-timeline";
import { GeneralAgentTrajectoryRole } from "./general-agent-trajectory-role";

type Props = {
  trace: TrajectoryPage; loadEarlier: () => Promise<void>; locate: (id: string) => Promise<void>;
  search: (query: string, offset?: number) => Promise<TrajectorySearch>;
  detail: (id: string, signal: AbortSignal) => Promise<TrajectoryRow>;
  onRefresh: () => void;
};
export function GeneralAgentTrajectoryLedger({ trace, loadEarlier, locate, search, detail, onRefresh }: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selected, setSelected] = useState<TrajectoryRow | null>(null);
  const [selectedRequest, setSelectedRequest] = useState(false);
  const [error, setError] = useState("");
  const [actualDuration, setActualDuration] = useState(false);
  const [collapsedTurns, setCollapsedTurns] = useState<Set<number>>(new Set());
  const [collapsedCalls, setCollapsedCalls] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<TrajectorySearch | null>(null);
  const [searchOffset, setSearchOffset] = useState(0);
  const [searching, setSearching] = useState(false);
  const [following, setFollowing] = useState(true);
  const [scrollTop, setScrollTop] = useState(0);
  const [height, setHeight] = useState(600);
  const viewport = useRef<HTMLDivElement>(null);
  const searchGeneration = useRef(0);
  const selectionGeneration = useRef(0);
  const focusedSelection = useRef<string | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const selectedChangedAt = trace.records.find(row => row.id === selectedId)?.changed_at;

  useEffect(() => {
    if (!selectedId) return;
    const controller = new AbortController();
    let disposed = false;
    detail(selectedId, controller.signal).then(row => {
      if (disposed) return;
      setSelected(row);
      if (row.parent_id) setCollapsedCalls(previous => previous.has(row.parent_id!) ? new Set([...previous].filter(id => id !== row.parent_id)) : previous);
    })
      .catch(cause => { if (!disposed && !controller.signal.aborted) setError(cause instanceof Error ? cause.message : "读取详情失败。"); });
    return () => { disposed = true; controller.abort(); };
  }, [selectedId, detail, selectedChangedAt]);
  useEffect(() => () => { searchGeneration.current++; selectionGeneration.current++; }, []);
  const select = async (id: string, modelRequest = false) => {
    const current = ++selectionGeneration.current;
    focusedSelection.current = null;
    setFollowing(false); setSelectedRequest(modelRequest); setSelectedId(id); if (id !== selectedId) setSelected(null); setError("");
    try {
      if (!trace.records.some(row => row.id === id)) await locate(id);
      if (current !== selectionGeneration.current) return;
      const row = trace.timeline.find(activity => activity.id === id);
      if (row && row.type !== "user") setCollapsedTurns(previous => new Set([...previous].filter(index => index !== row.request_index)));
      const parent = trace.records.find(item => item.id === id)?.parent_id;
      if (parent) setCollapsedCalls(previous => new Set([...previous].filter(value => value !== parent)));
    } catch (cause) { if (current === selectionGeneration.current) setError(cause instanceof Error ? cause.message : "定位失败。"); }
  };
  const submitSearch = async (offset = 0) => {
    if (!query.trim()) { setResults(null); return; }
    const current = ++searchGeneration.current;
    setSearching(true); setError("");
    try { const value = await search(query, offset); if (current === searchGeneration.current) { setResults(value); setSearchOffset(offset); } }
    catch (cause) { if (current === searchGeneration.current) setError(cause instanceof Error ? cause.message : "搜索失败。"); }
    finally { if (current === searchGeneration.current) setSearching(false); }
  };
  const entries = useMemo(() => trajectoryLedgerEntries(trace, collapsedTurns, collapsedCalls), [trace, collapsedTurns, collapsedCalls]);
  const collapsibleTurns = trace.turns.filter(turn => turn.models + turn.tools > 0).map(turn => turn.request_index);
  const collapsibleCalls = trace.records.filter(row => row.type === "assistant" && (row.tool_count ?? 0) > 0).map(row => row.id);
  const allTurnsCollapsed = collapsibleTurns.length > 0 && collapsibleTurns.every(index => collapsedTurns.has(index));
  const allCallsCollapsed = collapsibleCalls.length > 0 && collapsibleCalls.every(id => collapsedCalls.has(id));
  useEffect(() => {
    const element = viewport.current;
    if (!element) return;
    const observer = new ResizeObserver(() => setHeight(element.clientHeight));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (following && viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight;
  }, [following, entries.length, trace.cursor]);
  useEffect(() => {
    if (!selectedId || !viewport.current || focusedSelection.current === selectedId) return;
    const index = entries.findIndex(entry => entry.type === "record" && entry.row.id === selectedId);
    if (index >= 0) { viewport.current.scrollTop = Math.max(0, index * 40 - height / 3); focusedSelection.current = selectedId; }
  }, [selectedId, entries, height]);
  const first = Math.max(0, Math.floor(scrollTop / 40) - 8);
  const visible = entries.slice(first, first + Math.ceil(height / 40) + 16);
  const toggleTurn = (index: number) => setCollapsedTurns(previous => { const next = new Set(previous); if (next.has(index)) next.delete(index); else next.add(index); return next; });
  const toggleCall = (id: string) => setCollapsedCalls(previous => { const next = new Set(previous); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  return <>
    <div className="flex h-10 shrink-0 items-center gap-2 px-4 text-xs">
      <Button size="sm" variant="ghost" className="tc-trajectory-duration" aria-label={actualDuration ? "切换为等宽" : "切换为实际时长"} title={actualDuration ? "切换为等宽" : "切换为实际时长"} aria-pressed={actualDuration} onClick={() => setActualDuration(!actualDuration)}><Clock3 />时长</Button>
      <Button size="sm" variant="ghost" aria-label={allTurnsCollapsed ? "展开轮次" : "收起轮次"} title={allTurnsCollapsed ? "展开轮次" : "收起轮次"} aria-pressed={allTurnsCollapsed} onClick={() => setCollapsedTurns(allTurnsCollapsed ? new Set() : new Set(collapsibleTurns))}>{allTurnsCollapsed ? <SquarePlus /> : <SquareMinus />}轮次</Button>
      <Button size="sm" variant="ghost" aria-label={allCallsCollapsed ? "展开调用" : "收起调用"} title={allCallsCollapsed ? "展开调用" : "收起调用"} aria-pressed={allCallsCollapsed} onClick={() => setCollapsedCalls(allCallsCollapsed ? new Set() : new Set(collapsibleCalls))}>{allCallsCollapsed ? <SquarePlus /> : <SquareMinus />}调用</Button>
      {!following && scrollTop + height < entries.length * 40 - 24 && <Button size="sm" variant="ghost" onClick={() => setFollowing(true)}>返回尾部</Button>}
      <form className="ml-auto flex items-center gap-1" onSubmit={event => { event.preventDefault(); void submitSearch(); }}>
        <input aria-label="搜索完整会话轨迹" className="tc-input h-7 w-60 px-2 text-xs" value={query} onChange={event => { searchGeneration.current++; setSearching(false); setQuery(event.target.value); }} placeholder="搜索完整会话" />
        <Button size="sm" variant="ghost" disabled={searching} type="submit">{searching ? "搜索中" : "搜索"}</Button>
        {results && <Button size="sm" variant="ghost" onClick={() => { searchGeneration.current++; setResults(null); }}>关闭搜索</Button>}
      </form>
      <Button variant="ghost" size="icon-sm" aria-label="刷新执行轨迹" onClick={onRefresh}><RefreshCw className="size-3.5" /></Button>
    </div>
    <GeneralAgentTrajectoryTimeline activities={trace.timeline} actualDuration={actualDuration} selectedId={selectedId} onSelect={id => void select(id)} />
    {error && <p role="alert" className="px-4 text-xs">{error}</p>}
    {results && <div aria-label="轨迹搜索结果" className="max-h-36 shrink-0 overflow-y-auto px-4 py-1 text-xs">
      <div className="flex items-center gap-2"><span>{results.total} 条命中{!results.complete ? " · 记录不完整" : ""}</span>
        <Button size="sm" variant="ghost" disabled={!searchOffset} onClick={() => void submitSearch(Math.max(0, searchOffset - 50))}>上一页结果</Button>
        <Button size="sm" variant="ghost" disabled={searchOffset + 50 >= results.total} onClick={() => void submitSearch(searchOffset + 50)}>下一页结果</Button></div>
      {results.hits.map(hit => <button className="block h-7 w-full truncate rounded px-2 text-left hover:bg-[var(--tc-surface-muted)]" key={hit.record_id} onClick={() => void select(hit.record_id)}>第 {hit.request_index} 轮 · {hit.preview}</button>)}
    </div>}
    <div className="flex min-h-0 flex-1">
      <div className="flex min-w-0 flex-1 flex-col">
        {trace.has_earlier && <Button variant="ghost" size="sm" disabled={historyLoading} className="self-start" onClick={async () => {
          const element = viewport.current; const previous = element?.scrollHeight ?? 0; setHistoryLoading(true); setFollowing(false);
          try { await loadEarlier(); requestAnimationFrame(() => { if (element) element.scrollTop += element.scrollHeight - previous; }); }
          catch (cause) { setError(cause instanceof Error ? cause.message : "读取历史失败。"); } finally { setHistoryLoading(false); }
        }}>{historyLoading ? "正在读取…" : "加载更早记录"}</Button>}
        <div ref={viewport} aria-label="轨迹记录列表" className="min-h-0 flex-1 overflow-y-auto"
          onScroll={event => { const element = event.currentTarget; setScrollTop(element.scrollTop); setFollowing(element.scrollHeight - element.scrollTop - element.clientHeight < 24); }}>
          {!entries.length && <p className="py-3 text-sm text-[var(--tc-text-muted)]">尚无新轨迹记录</p>}
          <div style={{ height: entries.length * 40, position: "relative" }}>
            <div style={{ position: "absolute", top: first * 40, left: 0, right: 0 }}>
              {visible.map(entry => {
                if (entry.type === "turn-summary") {
                  return <button key={`turn-summary-${entry.index}`} className="tc-trajectory-row flex h-10 w-full items-center gap-2 pl-44 text-left text-sm text-[var(--tc-text-muted)]" aria-label={`展开第${entry.index}轮`} onClick={() => toggleTurn(entry.index)}>… {entry.models} 个步骤 · {entry.tools} 个工具调用</button>;
                }
                if (entry.type === "call-summary") {
                  return <button key={`call-summary-${entry.row.id}`} className="tc-trajectory-row flex h-10 w-full items-center gap-2 pl-44 text-left text-sm text-[var(--tc-text-muted)]" aria-label={`展开步骤${entry.row.step}的工具调用`} onClick={() => toggleCall(entry.row.id)}>… {entry.row.tool_count} 个工具调用 · {entry.row.tool_names?.map(generalCapabilityLabel).join("、")}</button>;
                }
                const row = entry.row;
                const toolCount = row.tool_count ?? 0;
                return <div key={row.id} data-model-request={entry.modelRequestId ?? undefined} data-step={row.step ?? undefined} className={`tc-trajectory-row relative flex h-10 items-center hover:bg-[var(--tc-surface-muted)] ${row.id === selectedId ? "tc-trajectory-row-selected" : ""}`}>
                  {row.type === "assistant" && <button type="button" className="absolute left-7 top-0 z-10 size-2 -translate-y-1/2 rounded-full" style={{ background: row.id === selectedId && selectedRequest ? "var(--tc-trajectory-selected)" : "var(--tc-text-muted)" }} aria-label={`查看模型请求 #${row.requestNumber}`} title={`模型请求 #${row.requestNumber} · 第 ${row.start.request_index} 轮 · 步骤 ${row.step}`} aria-pressed={row.id === selectedId && selectedRequest} onClick={() => void select(row.id, true)} />}
                  {entry.turnStart ? <button className="w-16 shrink-0 self-stretch text-left text-xs text-[var(--tc-text-muted)]" aria-label={`${collapsedTurns.has(row.start.request_index) ? "展开" : "收起"}第${row.start.request_index}轮`} onClick={() => toggleTurn(row.start.request_index)}>第 {row.start.request_index} 轮</button> : <span className="w-16 shrink-0" />}
                  <button type="button" aria-label={`查看${rowLabel(row)}记录${row.subagent_name ? ` · ${rowSubagentLabel(row)}` : ""}${row.type === "assistant" ? ` · 模型请求 #${row.requestNumber}` : row.type === "tool" ? ` · ${rowPurpose(row)}` : ""}`} aria-pressed={row.id === selectedId && !selectedRequest} onClick={() => void select(row.id)} className="flex h-full min-w-0 flex-1 items-center gap-4 px-4 text-left text-sm">
                    <span className="flex w-20 shrink-0 justify-end"><GeneralAgentTrajectoryRole row={row} /></span>
                    <span className="min-w-0 flex-1 truncate">{row.subagent_name && <span className="mr-3 text-xs text-[var(--tc-trajectory-model)]">{rowSubagentLabel(row)}</span>}{row.type === "tool" && <span className="mr-2 font-medium">{rowPurpose(row)}</span>}{rowContent(row)}</span>
                    <span className="shrink-0 text-xs text-[var(--tc-text-muted)]">{rowStatus(row)}</span>
                  </button>
                  {!!toolCount && <Button variant="ghost" size="sm" className="max-w-60 truncate" aria-label="切换该助手步骤的工具调用" onClick={() => toggleCall(row.id)}>{collapsedCalls.has(row.id) ? <ChevronRight /> : <ChevronDown />}{toolCount} 次 · {row.tool_names?.map(generalCapabilityLabel).join("、")}</Button>}
                </div>;
              })}
            </div>
          </div>
        </div>
      </div>
      {selectedId && (selected ? <GeneralAgentTrajectoryInspector key={`${selected.id}-${selectedRequest}`} row={selected} showModelRequest={selectedRequest} onClose={() => { selectionGeneration.current++; setSelectedId(null); setSelected(null); }} onSource={id => void select(id)} />
        : <aside className="w-[440px] rounded-xl bg-[var(--tc-surface-card)] p-4 text-xs">正在读取完整详情…<Button size="sm" variant="ghost" onClick={() => setSelectedId(null)}>关闭</Button></aside>)}
    </div>
  </>;
}
