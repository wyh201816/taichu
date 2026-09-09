"use client";

import { useEffect, useState } from "react";

import {
  getGeneralAgentConversation, listGeneralAgentContextSnapshots, listGeneralAgentConversations,
  listGeneralAgentLLMReplays, listGeneralAgentTraces,
} from "@/lib/api/general-agent";
import { isGeneralAgentRunActive } from "@/lib/general-agent-display";
import type { TrajectoryRecord } from "@/lib/general-agent-trajectory-ledger";
import type { GeneralAgentConversationSummary, GeneralAgentRun } from "@/lib/types/general-agent";

type RunEvidence = {
  conversationId: string;
  selection: string;
  run: GeneralAgentRun;
  runs: GeneralAgentRun[];
  records: TrajectoryRecord[];
  warnings: string[];
};

export function useGeneralAgentTrajectory(initialConversationId: string, initialRunId: string) {
  const [conversations, setConversations] = useState<GeneralAgentConversationSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [conversationId, setConversationId] = useState(initialConversationId);
  const [runId, setRunId] = useState(initialRunId);
  const [refresh, setRefresh] = useState(0);
  const [listLoading, setListLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [listError, setListError] = useState("");
  const [detailError, setDetailError] = useState("");
  const [data, setData] = useState<RunEvidence | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      setListLoading(true);
      try {
        const response = await listGeneralAgentConversations({ page, pageSize: 20 });
        if (cancelled) return;
        setConversations(response.conversations);
        setTotal(response.total);
        setListError("");
        setConversationId(current => current || response.conversations[0]?.conversation_id || "");
      } catch {
        if (!cancelled) setListError("对话列表加载失败，请刷新重试。");
      } finally {
        if (!cancelled) setListLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [page, refresh]);

  useEffect(() => {
    if (!conversationId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failedEvidenceAttempts = 0;
    async function load(quiet = false) {
      if (!quiet) setDetailLoading(true);
      try {
        const response = await getGeneralAgentConversation(conversationId);
        if (cancelled) return;
        const run = runId && runId !== "all" ? response.runs.find(item => item.run_id === runId) : response.runs.at(-1);
        if (!run) {
          setData(null);
          setDetailError("当前对话没有可查看的运行记录。");
          return;
        }
        const warnings: string[] = [];
        let hasLoadFailure = false;
        const records: TrajectoryRecord[] = [];
        const targets = runId === "all" ? response.runs : [run];
        for (let index = 0; index < targets.length; index += 3) {
          if (cancelled) return;
          records.push(...await Promise.all(targets.slice(index, index + 3).map(async target => {
            const [traces, calls, snapshots] = await Promise.allSettled([
              listGeneralAgentTraces(target.run_id, 2000), listGeneralAgentLLMReplays(target.run_id), listGeneralAgentContextSnapshots(target.run_id),
            ]);
            const prefix = `第 ${target.request_index} 轮`;
            if ([traces, calls, snapshots].some(result => result.status === "rejected")) hasLoadFailure = true;
            if (traces.status === "rejected") warnings.push(`${prefix}调用追踪加载失败`);
            else if (traces.value.total > traces.value.traces.length) warnings.push(`${prefix}追踪仅载入 ${traces.value.traces.length}/${traces.value.total} 条`);
            if (calls.status === "rejected") warnings.push(`${prefix}模型回放加载失败`);
            if (snapshots.status === "rejected") warnings.push(`${prefix}上下文快照加载失败`);
            return { run: target, evidence: {
              traces: traces.status === "fulfilled" ? traces.value.traces : [], calls: calls.status === "fulfilled" ? calls.value.calls : [],
              snapshots: snapshots.status === "fulfilled" ? snapshots.value.snapshots : [],
            } };
          })));
        }
        if (cancelled) return;
        setData({ conversationId, selection: runId, run, runs: response.runs, warnings, records });
        setDetailError("");
        failedEvidenceAttempts = hasLoadFailure ? failedEvidenceAttempts + 1 : 0;
        if (targets.some(target => isGeneralAgentRunActive(target.status) || target.status === "waiting_human") || (hasLoadFailure && failedEvidenceAttempts < 3)) {
          timer = setTimeout(() => void load(true), 5000);
        }
      } catch {
        if (!cancelled) {
          setDetailError("运行记录加载失败或已删除，请刷新或选择其他对话。");
          setData(null);
        }
      } finally {
        if (!cancelled) setDetailLoading(false);
      }
    }
    void load();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [conversationId, runId, refresh]);

  // 选择切换后立即隐藏旧证据；迟到响应由每次 effect 的取消标记隔离。
  const current = data?.conversationId === conversationId && data.selection === runId ? data : null;
  const currentConversation = current?.run.conversation_id;
  const currentRun = current?.run.run_id;
  useEffect(() => {
    if (!currentConversation || !currentRun) return;
    const url = new URL(window.location.href);
    url.searchParams.set("conversation", currentConversation);
    url.searchParams.set("run", runId === "all" ? "all" : currentRun);
    window.history.replaceState(null, "", url);
  }, [currentConversation, currentRun, runId]);
  return {
    conversations, total, page, setPage, conversationId, runId, current,
    listLoading, detailLoading, error: listError || detailError,
    reload: () => setRefresh(value => value + 1),
    selectConversation: (id: string) => { setConversationId(id); setRunId(""); setDetailError(""); },
    selectRun: setRunId,
  };
}
