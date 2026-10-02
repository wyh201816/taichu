import { rowLabel, type TrajectoryRow } from "@/lib/trajectory-records";

export function GeneralAgentTrajectoryRole({ row }: { row: TrajectoryRow }) {
  const color = row.type === "user" ? "var(--tc-trajectory-user)" : row.type === "assistant" ? "var(--tc-trajectory-model)" : "var(--tc-trajectory-tool)";
  return <span className="shrink-0 rounded px-2 py-1 font-semibold" data-role={row.type} style={{ color, background: `color-mix(in srgb, ${color} 12%, transparent)` }}>{rowLabel(row)}</span>;
}
