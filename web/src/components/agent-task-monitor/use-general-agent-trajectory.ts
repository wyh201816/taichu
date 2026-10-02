"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { apiRequest } from "@/lib/api-client";
import type { TrajectoryPage, TrajectoryRow, TrajectorySearch } from "@/lib/trajectory-records";

export function useGeneralAgentTrajectory(conversationId: string, active: boolean) {
  const [trace, setTrace] = useState<TrajectoryPage | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [revision, setRevision] = useState(0);
  const generation = useRef(0);
  const cursor = useRef(0);
  const reload = useCallback(() => setRevision(value => value + 1), []);
  const base = `/api/agent-workbench/general-assistant/conversations/${encodeURIComponent(conversationId)}/trajectory`;

  useEffect(() => {
    if (!conversationId || !active) return;
    const current = ++generation.current;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const update = (result: TrajectoryPage) => {
      if (generation.current !== current) return;
      cursor.current = result.cursor;
      setError("");
      setTrace(previous => {
        if (!previous) return result;
        const records = new Map(previous.records.map(row => [row.id, row]));
        for (const row of result.records) records.set(row.id, row);
        return { ...result, before: previous.before, has_earlier: previous.has_earlier,
          records: [...records.values()].sort((a, b) => a.start.sequence - b.start.sequence) };
      });
    };
    const poll = async (initial: boolean) => {
      try {
        const result = await apiRequest<TrajectoryPage>(`${base}/records?limit=200${initial ? "" : `&after=${cursor.current}`}`, { signal: controller.signal });
        update(result);
      } catch (cause) {
        if (generation.current === current && !controller.signal.aborted) setError(cause instanceof Error ? cause.message : "读取轨迹失败。");
      } finally {
        if (generation.current === current && !controller.signal.aborted) {
          setLoading(false); timer = setTimeout(() => void poll(false), 1500);
        }
      }
    };
    Promise.resolve().then(() => { if (generation.current === current) { setLoading(true); setError(""); void poll(true); } });
    return () => { generation.current = current + 1; controller.abort(); clearTimeout(timer); };
  }, [conversationId, base, revision, active]);

  const load = useCallback(async (parameters: string) => {
    const current = generation.current;
    const result = await apiRequest<TrajectoryPage>(`${base}/records?limit=200&${parameters}`);
    if (current !== generation.current) return;
    setTrace(previous => {
      const records = new Map(previous?.records.map(row => [row.id, row]) ?? []);
      for (const row of result.records) records.set(row.id, row);
      const ordered = [...records.values()].sort((a, b) => a.start.sequence - b.start.sequence);
      return { ...result, cursor: Math.max(previous?.cursor ?? 0, result.cursor), records: ordered,
        before: ordered[0]?.start.sequence ?? null,
        has_earlier: result.timeline.length > 0 && result.timeline[0].id !== ordered[0]?.id };
    });
  }, [base]);
  const loadEarlier = useCallback(() => load(`before=${trace?.before ?? 0}`), [load, trace?.before]);
  const locate = useCallback((id: string) => load(`around=${encodeURIComponent(id)}`), [load]);
  const search = useCallback((query: string, offset = 0) => apiRequest<TrajectorySearch>(`${base}/search?q=${encodeURIComponent(query)}&offset=${offset}`), [base]);
  const detail = useCallback((id: string, signal: AbortSignal) => apiRequest<TrajectoryRow>(`${base}/records/${encodeURIComponent(id)}`, { signal }), [base]);
  return { trace, error, loading, reload,
    loadEarlier, locate, search, detail };
}
