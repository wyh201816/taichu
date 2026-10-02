"use client";

import { useMemo } from "react";
import { trajectoryTimelineSpans, type TrajectoryActivity } from "@/lib/trajectory-records";

export function GeneralAgentTrajectoryTimeline({ activities, actualDuration, selectedId, onSelect }: {
  activities: TrajectoryActivity[]; actualDuration: boolean; selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const points = useMemo(() => trajectoryTimelineSpans(activities, actualDuration), [activities, actualDuration]);
  return <div aria-label="轨迹时间概览" className="tc-trajectory-timeline shrink-0 px-4 py-2">
    <div className="flex gap-2">
      <div className="flex w-8 shrink-0 flex-col justify-around text-xs text-[var(--tc-text-muted)]"><span>输入</span><span>模型</span><span>工具</span></div>
      <div className="relative h-[72px] flex-1 overflow-hidden">
        {points.map(item => <button key={item.id}
          aria-label={`查看第${item.request_index}轮${item.type === "user" ? "输入" : item.type === "assistant" ? "模型" : "工具"}活动`}
          aria-pressed={item.id === selectedId} onClick={() => onSelect(item.id)}
          title={`${item.start}${item.end ? ` → ${item.end}` : item.type === "user" ? " · 输入时间" : " · 未记录结束"}`}
          className={`absolute h-3 ${item.id === selectedId ? "outline-2 outline-[var(--tc-trajectory-selected)] outline-offset-2" : ""} ${!item.end && item.type !== "user" ? "opacity-40" : ""}`}
          style={{ borderRadius: 2, left: `min(${item.from * 100}%, calc(100% - 3px))`,
            width: `max(3px, calc(${(item.to - item.from) * 100}% - min(${(item.to - item.from) * 8}%, 1px)))`,
            top: item.type === "user" ? 6 : item.type === "assistant" ? 30 : 54,
            background: item.type === "user" ? "var(--tc-trajectory-user)" : item.type === "assistant" ? "var(--tc-trajectory-model)" : "var(--tc-trajectory-tool)" }} />)}
      </div>
    </div>
  </div>;
}
