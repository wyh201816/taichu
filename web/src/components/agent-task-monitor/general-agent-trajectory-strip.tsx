"use client";

import { useMemo } from "react";
import { buildTrajectoryStrip, type LedgerRow, type StripMode } from "@/lib/general-agent-trajectory-ledger";
import { cn } from "@/lib/utils";

export function GeneralAgentTrajectoryStrip({ rows, mode, selectedId, onSelect }: {
  rows: LedgerRow[]; mode: StripMode; selectedId: string; onSelect: (id: string) => void;
}) {
  const marks = useMemo(() => buildTrajectoryStrip(rows, mode), [rows, mode]);
  const lanes = [{ key: "input", label: "输入", color: "bg-emerald-400/65" },
    { key: "model", label: "模型", color: "bg-amber-300/55" },
    { key: "tools", label: "工具", color: "bg-cyan-400/65" }] as const;
  return <section aria-label="密集调用时间线" className="shrink-0 bg-[var(--tc-surface-muted)]/40 px-3 py-2" data-axis={mode}>
    {lanes.map(lane => <div key={lane.key} className="flex h-[18px] items-center gap-2">
      <span className="w-10 shrink-0 text-right text-[11px] text-[var(--tc-text-muted)]">{lane.label}</span>
      <div className="relative h-full min-w-0 flex-1" aria-label={`${lane.label}时序标记`}>
        {marks.filter(mark => mark.lane === lane.key).map(mark => <button type="button" key={mark.id} aria-label={`定位${mark.row.title}`} title={`第 ${mark.row.requestIndex} 轮 · ${mark.row.title}${mark.point ? " · 起点" : ""}`}
          onClick={() => onSelect(mark.id)} className="absolute top-0 h-[18px] min-w-[4px] rounded-sm focus-visible:z-10 focus-visible:outline-2 focus-visible:outline-white"
          style={{ left: `${mark.left}%`, width: `${mark.point ? 0.18 : mark.width}%` }}>
          <span className={cn("pointer-events-none absolute inset-x-px top-[3px] h-3 min-w-[2px] rounded-[1px]", mark.row.kind === "subagent" ? "bg-violet-300/65" : lane.color,
            selectedId === mark.id && "ring-1 ring-white brightness-150")} />
        </button>)}
      </div>
    </div>)}
  </section>;
}
