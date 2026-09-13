"use client";

import {
  AlertTriangle,
  ArrowUp,
  Ban,
  Check,
  ChevronDown,
  ChevronRight,
  CirclePause,
  Clipboard,
  Database,
  Globe,
  Layers3,
  LoaderCircle,
  Plus,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  SlidersHorizontal,
  Square,
  Trash2,
} from "lucide-react";
import {
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import { AppShell } from "@/components/app-shell";
import {
  AgentWorkbenchSwitcher,
  type WorkbenchAgent,
} from "@/components/agent-workbench/agent-workbench-switcher";
import { ModelSelector } from "@/components/llm/model-selector";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { MarkdownContent } from "@/components/ui/markdown-content";
import { useModelSelection } from "@/hooks/use-model-selection";
import {
  requestContextCompaction,
  getContextCompactionStatus,
  cancelGeneralAgentRun,
  deleteGeneralAgentConversation,
  getGeneralAgentConversation,
  listGeneralAgentConversations,
  listGeneralAgentContextSnapshots,
  listGeneralAgentMemories,
  resumeGeneralAgentRun,
  startGeneralAgentRun,
  streamGeneralAgentEvents,
} from "@/lib/api/general-agent";
import { listChapters } from "@/lib/api/chapters";
import {
  currentGeneralAgentNodes,
  generalAgentContinuationRequestIndex,
  generalCapabilityLabel,
  generalNodeErrorMessage,
  generalNodeStatusLabel,
  generalRunProgressSummary,
  generalRunStatusLabels,
  isGeneralAgentRunActive,
} from "@/lib/general-agent-display";
import { shouldSubmitGeneralAgentComposer } from "@/lib/general-agent-composer";
import { readableEntries, splitReadableContent } from "@/lib/general-agent-memory-trace";
import type { ChapterInfo } from "@/lib/types/chapters";
import type {
  AgentMemoryEntry,
  SessionWorkingMemory,
  ContextCompactionOperation,
  GeneralAgentContextSnapshot,
  GeneralAgentConversationSummary,
  GeneralAgentNodeRun,
  GeneralAgentRun,
  GeneralAgentRunStatus,
  GeneralAgentScopeType,
} from "@/lib/types/general-agent";
import { cn } from "@/lib/utils";

const scopeOptions: Array<{
  value: GeneralAgentScopeType;
  label: string;
}> = [
  { value: "none", label: "默认范围" },
  { value: "selection", label: "选区" },
  { value: "chapter", label: "单章" },
  { value: "range", label: "多章" },
  { value: "novel", label: "全文" },
];

export function GeneralAgentWorkbench({
  onAgentChange,
}: {
  onAgentChange: (agent: WorkbenchAgent) => void;
}) {
  const [compaction, setCompaction] = useState<ContextCompactionOperation | null>(null);
  const [compactionSubmitting, setCompactionSubmitting] = useState(false);
  const [chapters, setChapters] = useState<ChapterInfo[]>([]);
  const [conversations, setConversations] = useState<
    GeneralAgentConversationSummary[]
  >([]);
  const [conversationRuns, setConversationRuns] = useState<GeneralAgentRun[]>([]);
  const [selectedConversationId, setSelectedConversationId] = useState("");
  const [goal, setGoal] = useState("");
  const [scopeType, setScopeType] = useState<GeneralAgentScopeType>("none");
  const [selectedChapterIds, setSelectedChapterIds] = useState<string[]>([]);
  const [selectionText, setSelectionText] = useState("");
  const [authorConstraintsText, setAuthorConstraintsText] = useState("");
  const [externalAccessAllowed, setExternalAccessAllowed] = useState(false);
  const [clarificationAnswer, setClarificationAnswer] = useState("");
  const [secondConfirmation, setSecondConfirmation] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [copiedRunId, setCopiedRunId] = useState("");
  const [memories, setMemories] = useState<AgentMemoryEntry[]>([]);
  const [memoryPanelOpen, setMemoryPanelOpen] = useState(false);
  const [contextPanelOpen, setContextPanelOpen] = useState(false);
  const [selectedContextRunId, setSelectedContextRunId] = useState("");
  const [contextSnapshots, setContextSnapshots] = useState<
    GeneralAgentContextSnapshot[]
  >([]);
  const [contextSnapshotsRunId, setContextSnapshotsRunId] = useState("");
  const conversationEndRef = useRef<HTMLDivElement>(null);
  const goalInputRef = useRef<HTMLTextAreaElement>(null);
  const modelSelection = useModelSelection();

  useEffect(() => {
    let cancelled = false;
    if (selectedConversationId) {
      void getContextCompactionStatus(selectedConversationId).then(response => {
        if (!cancelled) setCompaction(response.operation);
      }).catch(() => { if (!cancelled) setCompaction(null); });
    }
    return () => { cancelled = true; };
  }, [selectedConversationId]);

  const reloadMemories = useCallback(async (conversationId: string) => {
    if (!conversationId) {
      setMemories([]);
      return;
    }
    const response = await listGeneralAgentMemories(conversationId);
    setMemories(response.memories);
  }, []);

  const reloadConversations = useCallback(async (preferredConversationId = "") => {
    const [listResponse, detailResponse] = await Promise.all([
      listGeneralAgentConversations(),
      preferredConversationId
        ? getGeneralAgentConversation(preferredConversationId)
        : Promise.resolve(null),
    ]);
    setConversations(listResponse.conversations);
    if (detailResponse) {
      setSelectedConversationId(detailResponse.conversation_id);
      setConversationRuns(detailResponse.runs);
      await reloadMemories(detailResponse.conversation_id);
      return;
    }
    const latest = listResponse.conversations[0];
    if (latest) {
      const latestDetail = await getGeneralAgentConversation(latest.conversation_id);
      setSelectedConversationId(latestDetail.conversation_id);
      setConversationRuns(latestDetail.runs);
      await reloadMemories(latestDetail.conversation_id);
      return;
    }
    setMemories([]);
  }, [reloadMemories]);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      try {
        const [chapterResponse] = await Promise.all([
          listChapters(),
          reloadConversations(),
        ]);
        if (!cancelled) {
          setChapters(chapterResponse.chapters);
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(errorMessage(loadError));
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [reloadConversations]);

  useEffect(() => {
    if (!selectedConversationId || !compaction || !["queued", "running"].includes(compaction.status)) return;
    let cancelled = false;
    const timer = window.setInterval(() => {
      void getContextCompactionStatus(selectedConversationId).then(async response => {
        if (cancelled) return;
        setCompaction(response.operation);
        if (response.operation?.status === "completed") await reloadConversations(selectedConversationId);
      }).catch(error => { if (!cancelled) setError(error instanceof Error ? error.message : "读取压缩状态失败。"); });
    }, 1200);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [selectedConversationId, compaction, reloadConversations]);

  async function handleCompact() {
    setCompactionSubmitting(true);
    try { setCompaction((await requestContextCompaction(selectedConversationId)).operation); }
    catch (error) { setError(error instanceof Error ? error.message : "提交压缩请求失败。"); }
    finally { setCompactionSubmitting(false); }
  }

  const currentRun = conversationRuns.at(-1) ?? null;
  const selectedContextRun =
    conversationRuns.find(run => run.run_id === selectedContextRunId) ?? currentRun;
  const activeRunId = currentRun?.run_id ?? "";
  const contextRunId = selectedContextRun?.run_id ?? "";
  const latestContextSnapshotId = selectedContextRun?.context_snapshot_id ?? "";

  useEffect(() => {
    if (!contextPanelOpen || !contextRunId) {
      return;
    }
    let cancelled = false;
    void listGeneralAgentContextSnapshots(contextRunId)
      .then(response => {
        if (!cancelled) {
          setContextSnapshots(response.snapshots);
          setContextSnapshotsRunId(response.run_id);
        }
      })
      .catch(contextError => {
        if (!cancelled) {
          setContextSnapshots([]);
          setContextSnapshotsRunId(contextRunId);
          setError(errorMessage(contextError));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [contextPanelOpen, contextRunId, latestContextSnapshotId]);

  useEffect(() => {
    if (!selectedConversationId) {
      return;
    }
    const controller = new AbortController();
    void streamGeneralAgentEvents(event => {
      if (event.run.conversation_id !== selectedConversationId) {
        return;
      }
      setConversationRuns(current =>
        current.map(run =>
          run.run_id === event.run.run_id ? event.run : run,
        ),
      );
      if (!isGeneralAgentRunActive(event.run.status)) {
        void reloadConversations(event.run.conversation_id);
        if (memoryPanelOpen) {
          void reloadMemories(event.run.conversation_id);
        }
      }
    }, controller.signal).catch(streamError => {
      if (!controller.signal.aborted) {
        setError(errorMessage(streamError));
      }
    });
    return () => controller.abort();
  }, [
    memoryPanelOpen,
    reloadConversations,
    reloadMemories,
    selectedConversationId,
  ]);
  const scopeSummary = (() => {
    const label =
      scopeOptions.find(option => option.value === scopeType)?.label ??
      "默认范围";
    if (
      (scopeType === "chapter" || scopeType === "range") &&
      selectedChapterIds.length
    ) {
      return `${label} · ${selectedChapterIds.length} 章`;
    }
    return label;
  })();
  const hasFinalAnswer = Boolean(currentRun?.final_answer);
  const pendingRequestCreatedAt =
    currentRun?.pending_human_request?.created_at ?? "";
  const composerLocked =
    busy ||
    loading ||
    Boolean(
      currentRun &&
        (isGeneralAgentRunActive(currentRun.status) ||
          currentRun.status === "waiting_human"),
    );
  const canSendMessage =
    Boolean(goal.trim()) && Boolean(modelSelection.modelId) && !composerLocked;

  useEffect(() => {
    if (!activeRunId && conversationRuns.length === 0) {
      return;
    }
    conversationEndRef.current?.scrollIntoView({ block: "end" });
  }, [conversationRuns.length, hasFinalAnswer, pendingRequestCreatedAt, activeRunId]);

  useEffect(() => {
    const input = goalInputRef.current;
    if (!input) {
      return;
    }
    input.style.height = "auto";
    const nextHeight = Math.min(Math.max(input.scrollHeight, 64), 160);
    input.style.height = `${nextHeight}px`;
    input.style.overflowY = input.scrollHeight > 160 ? "auto" : "hidden";
  }, [goal]);

  async function handleStart() {
    const trimmedGoal = goal.trim();
    if (!trimmedGoal) {
      setError("请先输入你希望通用写作助手完成的任务。");
      return;
    }
    if (!modelSelection.modelId) {
      setError(modelSelection.error || "模型列表尚未加载完成。");
      return;
    }
    if (scopeType === "selection" && !selectionText.trim()) {
      setError("选择“选区”范围时，请粘贴需要处理的正文。");
      return;
    }
    if (
      (scopeType === "chapter" || scopeType === "range") &&
      selectedChapterIds.length === 0
    ) {
      setError("请选择至少一个章节。");
      return;
    }
    if (
      currentRun &&
      (isGeneralAgentRunActive(currentRun.status) ||
        currentRun.status === "waiting_human")
    ) {
      setError("当前对话仍在处理中，请等待完成或先处理待确认内容。");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const response = await startGeneralAgentRun({
        user_goal: goal,
        model_id: modelSelection.modelId,
        conversation_id: selectedConversationId || undefined,
        start_new_conversation: !selectedConversationId,
        scope: {
          scope_type: scopeType,
          current_chapter_id: selectedChapterIds[0] ?? null,
          chapter_ids:
            scopeType === "novel"
              ? chapters.map(chapter => chapter.id)
              : selectedChapterIds,
          selection_text: scopeType === "selection" ? selectionText : "",
          direct_context: "",
        },
        author_constraints: authorConstraintsText
          .split("\n")
          .map(item => item.trim())
          .filter(Boolean),
        external_access_allowed: externalAccessAllowed,
      });
      setSelectedConversationId(response.run.conversation_id);
      setConversationRuns(current =>
        selectedConversationId === response.run.conversation_id
          ? [
              ...current.filter(run => run.run_id !== response.run.run_id),
              response.run,
            ]
          : [response.run],
      );
      setGoal("");
      setAuthorConstraintsText("");
      setClarificationAnswer("");
      setSecondConfirmation(false);
      await reloadConversations(response.run.conversation_id);
      if (memoryPanelOpen) {
        await reloadMemories(response.run.conversation_id);
      }
    } catch (startError) {
      setError(errorMessage(startError));
    } finally {
      setBusy(false);
    }
  }

  async function handleOpenConversation(conversationId: string) {
    setSelectedConversationId(conversationId);
    setError("");
    try {
      const response = await getGeneralAgentConversation(conversationId);
      setConversationRuns(response.runs);
      if (memoryPanelOpen) {
        await reloadMemories(conversationId);
      }
    } catch (openError) {
      setError(errorMessage(openError));
    }
  }

  function handleNewConversation() {
    setSelectedConversationId("");
    setConversationRuns([]);
    setGoal("");
    setScopeType("none");
    setSelectedChapterIds([]);
    setSelectionText("");
    setAuthorConstraintsText("");
    setExternalAccessAllowed(false);
    setClarificationAnswer("");
    setSecondConfirmation(false);
    setSettingsOpen(false);
    setCopiedRunId("");
    setMemories([]);
    setMemoryPanelOpen(false);
    setError("");
  }

  async function handleDeleteConversation(
    conversation: GeneralAgentConversationSummary,
  ) {
    if (
      !window.confirm(
        `确认删除对话“${shortText(conversation.title, 28)}”及其全部消息吗？`,
      )
    ) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      await deleteGeneralAgentConversation(conversation.conversation_id);
      if (selectedConversationId === conversation.conversation_id) {
        handleNewConversation();
      }
      await reloadConversations();
    } catch (deleteError) {
      setError(errorMessage(deleteError));
    } finally {
      setBusy(false);
    }
  }

  async function handleResume(request: {
    answer?: string;
    approve?: boolean;
    second_confirmation?: boolean;
    effect_resolution?: "recheck" | "confirm_not_applied" | "cancel";
  }) {
    if (!currentRun) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      const response = await resumeGeneralAgentRun(currentRun.run_id, request);
      setConversationRuns(current => {
        const existing = current.some(run => run.run_id === response.run.run_id);
        return existing
          ? current.map(run =>
              run.run_id === response.run.run_id ? response.run : run,
            )
          : [...current, response.run];
      });
      setClarificationAnswer("");
      setSecondConfirmation(false);
      await reloadConversations(response.run.conversation_id);
      if (memoryPanelOpen) {
        await reloadMemories(response.run.conversation_id);
      }
    } catch (resumeError) {
      setError(errorMessage(resumeError));
    } finally {
      setBusy(false);
    }
  }

  async function handleCancel() {
    if (!currentRun) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      const response = await cancelGeneralAgentRun(currentRun.run_id);
      setConversationRuns(current =>
        current.map(run =>
          run.run_id === response.run.run_id ? response.run : run,
        ),
      );
      await reloadConversations(response.run.conversation_id);
      if (memoryPanelOpen) {
        await reloadMemories(response.run.conversation_id);
      }
    } catch (cancelError) {
      setError(errorMessage(cancelError));
    } finally {
      setBusy(false);
    }
  }

  async function handleCopy(run: GeneralAgentRun) {
    if (!run.final_answer) {
      return;
    }
    try {
      await navigator.clipboard.writeText(run.final_answer);
      setCopiedRunId(run.run_id);
      window.setTimeout(
        () => setCopiedRunId(current => (current === run.run_id ? "" : current)),
        1600,
      );
    } catch {
      setError("复制失败，请手动选择结果文本。");
    }
  }

  async function handleToggleMemories() {
    const nextOpen = !memoryPanelOpen;
    setMemoryPanelOpen(nextOpen);
    if (nextOpen) {
      setContextPanelOpen(false);
    }
    if (!nextOpen || !selectedConversationId) {
      return;
    }
    setError("");
    try {
      await reloadMemories(selectedConversationId);
    } catch (memoryError) {
      setError(errorMessage(memoryError));
    }
  }

  function handleToggleContext() {
    const nextOpen = !contextPanelOpen;
    setContextPanelOpen(nextOpen);
    if (nextOpen) {
      setMemoryPanelOpen(false);
      setSelectedContextRunId(currentRun?.run_id ?? "");
    }
  }

  return (
    <AppShell
      activePath="/agent-workbench"
      viewportLocked
      workspaceStyle={{ backgroundImage: "none" }}
    >
      <section className="grid h-full min-h-0 grid-cols-[280px_minmax(0,1fr)]">
        <aside className="flex min-h-0 min-w-0 flex-col bg-[var(--tc-surface-card)]">
          <div className="shrink-0 px-3 py-4">
            <AgentWorkbenchSwitcher
              activeAgent="general"
              onAgentChange={onAgentChange}
            />
          </div>

          <div className="flex min-h-0 flex-1 flex-col px-3 pb-4 pt-2">
            <div className="flex shrink-0 items-center justify-between gap-2 px-2">
              <h2 className="text-xs font-medium text-[var(--tc-text-secondary)]">
                最近对话
              </h2>
              <div className="flex items-center gap-1">
                <Button
                  type="button"
                  variant="ghost"
                  size="xs"
                  onClick={handleNewConversation}
                  title="新对话"
                >
                  <Plus className="size-3.5" />
                  新对话
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-xs"
                  aria-label="刷新最近对话"
                  title="刷新最近对话"
                  onClick={() =>
                    void reloadConversations(selectedConversationId)
                  }
                >
                  <RefreshCw className="size-3.5" />
                </Button>
              </div>
            </div>
            <div className="tc-editor-scrollbar mt-2 min-h-0 flex-1 overflow-y-auto pr-1">
              <GeneralConversationList
                conversations={conversations}
                selectedConversationId={selectedConversationId}
                busy={busy}
                onOpen={conversationId =>
                  void handleOpenConversation(conversationId)
                }
                onDelete={conversation =>
                  void handleDeleteConversation(conversation)
                }
              />
            </div>
          </div>
        </aside>

        <section className="flex min-h-0 min-w-0 flex-col bg-[var(--tc-surface-muted)]">
          <header className="shrink-0 px-8 pb-2 pt-5">
            <div className="mx-auto w-full max-w-[900px]">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h2 className="text-sm font-semibold text-[var(--tc-text-primary)]">
                    长程创作智能体
                  </h2>
                  <p className="mt-0.5 text-xs text-[var(--tc-text-muted)]">
                    规划、取证、执行与核验
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    disabled={!selectedConversationId}
                    aria-expanded={contextPanelOpen}
                    onClick={handleToggleContext}
                    className="rounded-full bg-[var(--tc-surface-card)] text-[var(--tc-text-secondary)]"
                  >
                    <Layers3 className="size-3.5" />
                    上下文快照
                    <ChevronDown
                      className={cn(
                        "size-3.5 transition-transform duration-150 motion-reduce:transition-none",
                        contextPanelOpen && "rotate-180",
                      )}
                    />
                  </Button>
                  <Button type="button" variant="ghost" size="sm"
                    disabled={!selectedConversationId || compactionSubmitting || !!compaction && ["queued", "running"].includes(compaction.status)}
                    onClick={() => void handleCompact()}
                    className="rounded-full bg-[var(--tc-surface-card)] text-[var(--tc-text-secondary)]">
                    {compactionSubmitting || compaction?.status === "running" ? <LoaderCircle className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}
                    {compaction?.status === "queued" ? "压缩已排队" : compaction?.status === "running" ? "正在压缩" : "压缩上下文"}
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    disabled={!selectedConversationId}
                    aria-expanded={memoryPanelOpen}
                    onClick={() => void handleToggleMemories()}
                    className="rounded-full bg-[var(--tc-surface-card)] text-[var(--tc-text-secondary)]"
                  >
                    <Database className="size-3.5" />
                    工作记忆 {Object.values(currentRun?.context_pipeline?.working_memory ?? {}).reduce((count, items) => count + Object.keys(items).length, 0)}
                    <ChevronDown
                      className={cn(
                        "size-3.5 transition-transform duration-150 motion-reduce:transition-none",
                        memoryPanelOpen && "rotate-180",
                      )}
                    />
                  </Button>
                </div>
              </div>
              {compaction && compaction.conversation_id === selectedConversationId ? (
                <p role="status" className="mt-2 px-1 text-xs text-[var(--tc-text-muted)]">{compaction.message}</p>
              ) : null}
              {memoryPanelOpen && selectedConversationId ? (
                <div className="mt-3 rounded-2xl bg-[var(--tc-surface-card)] p-3">
                  <SessionMemoryPanel memory={currentRun?.context_pipeline?.working_memory} />
                  <details className="mt-3 text-xs text-[var(--tc-text-muted)]"><summary className="cursor-pointer">执行有效性与历史审计</summary><GeneralMemoryPanel memories={memories} /></details>
                </div>
              ) : null}
              {contextPanelOpen && selectedConversationId ? (
                <GeneralContextPanel
                  runs={conversationRuns}
                  selectedRun={selectedContextRun}
                  snapshots={
                    contextSnapshotsRunId === contextRunId
                      ? contextSnapshots
                      : []
                  }
                  loading={contextSnapshotsRunId !== contextRunId}
                  onSelectRun={setSelectedContextRunId}
                />
              ) : null}
            </div>
          </header>

          <div className="tc-editor-scrollbar min-h-0 flex-1 overflow-y-auto">
            <div className="mx-auto flex min-h-full w-full max-w-[900px] flex-col px-8 py-6">
              {conversationRuns.length ? (
                conversationRuns.map(run => (
                  <GeneralRunPanel
                    key={run.run_id}
                    run={run}
                    nodes={currentGeneralAgentNodes(run)}
                    continuedByRequestIndex={
                      generalAgentContinuationRequestIndex(run, conversationRuns)
                    }
                    busy={busy}
                    clarificationAnswer={clarificationAnswer}
                    secondConfirmation={secondConfirmation}
                    copied={copiedRunId === run.run_id}
                    onClarificationAnswerChange={setClarificationAnswer}
                    onSecondConfirmationChange={setSecondConfirmation}
                    onResume={request => void handleResume(request)}
                    onCopy={() => void handleCopy(run)}
                  />
                ))
              ) : (
                <div className="flex flex-1 items-center justify-center pb-12 text-center">
                  <div>
                    <h3 className="text-lg font-semibold text-[var(--tc-text-primary)]">
                      把复杂创作变成可控的长程任务
                    </h3>
                    <p className="mt-2 text-sm text-[var(--tc-text-muted)]">
                      从一个问题、一条设定或一个创作目标开始。
                    </p>
                  </div>
                </div>
              )}
              <div ref={conversationEndRef} aria-hidden="true" />
            </div>
          </div>

          <div className="shrink-0 bg-[var(--tc-surface-muted)] px-8 pb-5 pt-2">
            <div className="mx-auto w-full max-w-[900px]">
              {error ? (
                <div
                  role="alert"
                  className="mb-2 flex items-start gap-2 rounded-2xl bg-[var(--tc-surface-card)] px-3 py-2 text-sm text-[var(--tc-text-primary)]"
                >
                  <AlertTriangle className="mt-0.5 size-4 shrink-0" />
                  <span>{error}</span>
                </div>
              ) : null}

              <section className="rounded-[20px] border border-[var(--tc-border-subtle)] bg-[var(--tc-surface-page)] p-2 shadow-[0_8px_24px_rgba(0,0,0,0.12)] transition-colors duration-150 focus-within:border-[var(--tc-border-strong)]">
                <label htmlFor="general-agent-goal" className="sr-only">
                  任务内容
                </label>
                <textarea
                  ref={goalInputRef}
                  id="general-agent-goal"
                  value={goal}
                  onChange={event => {
                    setGoal(event.target.value);
                    if (error) {
                      setError("");
                    }
                  }}
                  onKeyDown={event => {
                    if (
                      !shouldSubmitGeneralAgentComposer({
                        key: event.key,
                        shiftKey: event.shiftKey,
                        isComposing: event.nativeEvent.isComposing,
                      })
                    ) {
                      return;
                    }
                    event.preventDefault();
                    if (canSendMessage) {
                      void handleStart();
                    }
                  }}
                  rows={2}
                  placeholder="描述你的创作目标、约束与需要核验的事实……"
                  className="min-h-16 max-h-40 w-full resize-none bg-transparent px-3 py-2 text-[15px] leading-6 text-[var(--tc-text-primary)] outline-none placeholder:text-[var(--tc-text-muted)]"
                />

                {settingsOpen ? (
                  <div
                    id="general-agent-settings"
                    className="tc-editor-scrollbar mx-1 mb-2 grid max-h-[42vh] gap-3 overflow-y-auto rounded-2xl bg-[var(--tc-surface-card)] p-3"
                  >
                    <div>
                      <p className="text-xs text-[var(--tc-text-muted)]">正文范围</p>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {scopeOptions.map(option => (
                          <Button
                            key={option.value}
                            type="button"
                            variant="ghost"
                            size="xs"
                            aria-pressed={scopeType === option.value}
                            className={cn(
                              "rounded-full border-transparent",
                              scopeType === option.value
                                ? "bg-[var(--tc-action-primary-bg)] text-[var(--tc-action-primary-text)] hover:bg-[var(--tc-action-primary-bg)] hover:text-[var(--tc-action-primary-text)]"
                                : "bg-[var(--tc-surface-muted)] text-[var(--tc-text-secondary)]",
                            )}
                            onClick={() => {
                              setScopeType(option.value);
                              if (option.value === "none" || option.value === "novel") {
                                setSelectedChapterIds([]);
                              }
                            }}
                          >
                            {option.label}
                          </Button>
                        ))}
                      </div>
                    </div>

                    {scopeType === "selection" ? (
                      <textarea
                        value={selectionText}
                        onChange={event => setSelectionText(event.target.value)}
                        rows={5}
                        placeholder="粘贴需要处理的正文选区"
                        className="w-full resize-y rounded-xl border border-[var(--tc-border-subtle)] bg-[var(--tc-surface-page)] px-3 py-2 text-sm leading-6 text-[var(--tc-text-primary)] outline-none placeholder:text-[var(--tc-text-muted)] focus:border-[var(--tc-border-strong)]"
                      />
                    ) : null}

                    {scopeType === "chapter" ? (
                      <label className="grid gap-1 text-xs text-[var(--tc-text-muted)]">
                        当前章节
                        <select
                          value={selectedChapterIds[0] ?? ""}
                          onChange={event =>
                            setSelectedChapterIds(
                              event.target.value ? [event.target.value] : [],
                            )
                          }
                          className="h-9 rounded-xl border border-[var(--tc-border-subtle)] bg-[var(--tc-surface-page)] px-2 text-sm text-[var(--tc-text-primary)] outline-none focus:border-[var(--tc-border-strong)]"
                        >
                          <option value="">请选择章节</option>
                          {chapters.map(chapter => (
                            <option key={chapter.id} value={chapter.id}>
                              {chapter.title}
                            </option>
                          ))}
                        </select>
                      </label>
                    ) : null}

                    {scopeType === "range" ? (
                      <div className="grid max-h-44 gap-1 overflow-y-auto">
                        {chapters.map(chapter => {
                          const checked = selectedChapterIds.includes(chapter.id);
                          return (
                            <label
                              key={chapter.id}
                              className="flex cursor-pointer items-center gap-2 rounded-xl bg-[var(--tc-surface-muted)] px-3 py-2 text-sm text-[var(--tc-text-secondary)]"
                            >
                              <Checkbox
                                checked={checked}
                                onCheckedChange={value =>
                                  setSelectedChapterIds(current =>
                                    value
                                      ? [...current, chapter.id]
                                      : current.filter(item => item !== chapter.id),
                                  )
                                }
                              />
                              <span>{chapter.title}</span>
                              <span className="ml-auto text-xs text-[var(--tc-text-muted)]">
                                {chapter.word_count.toLocaleString("zh-CN")} 字
                              </span>
                            </label>
                          );
                        })}
                      </div>
                    ) : null}

                    <label className="grid gap-1 text-xs text-[var(--tc-text-muted)]">
                      作者硬约束（每行一条）
                      <textarea
                        value={authorConstraintsText}
                        onChange={event => setAuthorConstraintsText(event.target.value)}
                        rows={3}
                        placeholder="例如：不得改变主角姓名"
                        className="w-full resize-y rounded-xl border border-[var(--tc-border-subtle)] bg-[var(--tc-surface-page)] px-3 py-2 text-sm leading-6 text-[var(--tc-text-primary)] outline-none placeholder:text-[var(--tc-text-muted)] focus:border-[var(--tc-border-strong)]"
                      />
                      <span className="leading-5">
                        这些约束会由运行时自动加入当前对话的工作记忆，但不会写入小说知识库。
                      </span>
                    </label>

                  </div>
                ) : null}

                <div className="flex items-center justify-between gap-3 px-1 pb-1 pt-1">
                  <div className="flex items-center gap-1.5">
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      aria-label={
                        externalAccessAllowed
                          ? "已启用外部资料"
                          : "启用外部资料"
                      }
                      aria-pressed={externalAccessAllowed}
                      title={
                        externalAccessAllowed
                          ? "已启用外部资料"
                          : "启用外部资料"
                      }
                      onClick={() =>
                        setExternalAccessAllowed(current => !current)
                      }
                      className={cn(
                        "rounded-full border transition-colors duration-150 motion-reduce:transition-none",
                        externalAccessAllowed
                          ? "border-blue-400/60 bg-blue-500/20 text-blue-300 hover:bg-blue-500/25 hover:text-blue-200"
                          : "border-transparent bg-[var(--tc-surface-card)] text-[var(--tc-text-secondary)] hover:text-[var(--tc-text-primary)]",
                      )}
                    >
                      <Globe className="size-4" />
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      aria-expanded={settingsOpen}
                      aria-controls="general-agent-settings"
                      onClick={() => setSettingsOpen(current => !current)}
                      className="rounded-full bg-[var(--tc-surface-card)] px-3 text-[var(--tc-text-secondary)]"
                    >
                      <SlidersHorizontal className="size-3.5" />
                      {scopeSummary}
                      <ChevronDown
                        className={cn(
                          "size-3.5 transition-transform duration-150 motion-reduce:transition-none",
                          settingsOpen && "rotate-180",
                        )}
                      />
                    </Button>
                  </div>
                  <div className="flex items-center gap-2">
                    {currentRun && isGeneralAgentRunActive(currentRun.status) ? (
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        disabled={busy}
                        onClick={() => void handleCancel()}
                      >
                        <Square className="size-3.5" />
                        停止当前任务
                      </Button>
                    ) : null}
                    <ModelSelector selection={modelSelection} compact />
                    <Button
                      type="button"
                      size="icon-lg"
                      className="rounded-full"
                      aria-label={busy ? "正在发送消息" : "发送消息"}
                      title="发送消息"
                      disabled={!canSendMessage}
                      onClick={() => void handleStart()}
                    >
                      {busy ? (
                        <LoaderCircle className="size-4 animate-spin motion-reduce:animate-none" />
                      ) : (
                        <ArrowUp className="size-4" />
                      )}
                    </Button>
                  </div>
                </div>
              </section>
            </div>
          </div>
        </section>
      </section>
    </AppShell>
  );
}

const sessionFieldLabels: Record<keyof SessionWorkingMemory, string> = {
  task_goal: "任务目标", file_list: "正文、资料与产物", workflow_state: "进展与待办", error_knowledge: "错误与处理经验", constraints: "用户约束",
};
function SessionMemoryPanel({ memory }: { memory?: SessionWorkingMemory }) {
  return <div className="grid gap-3">{Object.entries(sessionFieldLabels).map(([field, label]) => {
    const items = Object.entries(memory?.[field as keyof SessionWorkingMemory] ?? {});
    return <div key={field}><p className="text-xs font-medium text-[var(--tc-text-primary)]">{label}</p>
      {items.length ? items.map(([id, item]) => <div key={id} className="mt-1 text-xs leading-5 text-[var(--tc-text-secondary)]">
        <MemoryReadableContent content={item.content} />
        <details className="text-[var(--tc-text-muted)]"><summary className="cursor-pointer">查看来源</summary><p className="break-all">{item.source_ids.join("、")}</p></details>
      </div>) : <p className="mt-1 text-xs text-[var(--tc-text-muted)]">暂无记录</p>}</div>;
  })}</div>;
}

function GeneralMemoryPanel({
  memories,
}: {
  memories: AgentMemoryEntry[];
}) {
  return (
    <section className="mt-3 rounded-2xl bg-[var(--tc-surface-card)] p-3">
      <div className="flex items-start gap-2 rounded-xl bg-[var(--tc-surface-muted)] px-3 py-2">
        <Database className="mt-0.5 size-4 shrink-0 text-[var(--tc-text-secondary)]" />
        <div>
          <p className="text-xs font-medium text-[var(--tc-text-primary)]">
            当前对话运行记忆
          </p>
          <p className="mt-0.5 text-xs leading-5 text-[var(--tc-text-muted)]">
            系统自动维护的只读运行状态，不是小说知识库事实；涉及人物、设定和情节事实时仍会重新取证。
          </p>
        </div>
      </div>
      <div className="tc-editor-scrollbar mt-2 grid max-h-72 gap-1 overflow-y-auto pr-1">
        {memories.length ? (
          memories.map(memory => (
            <article
              key={memory.memory_id}
              className={cn(
                "rounded-[var(--tc-radius-control)] border px-2.5 py-2.5",
                memory.validity === "active"
                  ? "border-transparent odd:bg-[var(--tc-surface-muted)]"
                  : "border-[var(--tc-border-subtle)] bg-[var(--tc-surface-page)]",
              )}
            >
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="font-medium text-[var(--tc-text-secondary)]">
                    {memoryKindLabels[memory.kind]}
                  </span>
                  <span className="text-[var(--tc-text-muted)]">
                    {memoryValidityLabels[memory.validity]}
                  </span>
                  <span className="text-[var(--tc-text-muted)]">
                    运行时在第 {memory.created_request_index} 次请求后生成
                  </span>
                  {memory.expires_after_request_index ? (
                    <span className="text-[var(--tc-text-muted)]">
                      第 {memory.expires_after_request_index} 次请求后由运行时自动退出
                    </span>
                  ) : null}
                </div>
                <div className="mt-2 text-sm leading-6 text-[var(--tc-text-primary)]">
                  <MemoryReadableContent content={memory.content} />
                </div>
                {memory.validity !== "active" && memory.invalidation_reason ? (
                  <p className="mt-2 border-l border-[var(--tc-border-strong)] pl-2 text-xs leading-5 text-[var(--tc-text-secondary)]">
                    {memory.invalidation_reason}
                  </p>
                ) : null}
                {memory.source_refs.length || memory.artifact_refs.length ? (
                  <p className="mt-1 text-xs leading-5 text-[var(--tc-text-muted)]">
                    引用 {memory.source_refs.length} 个来源标识{memory.artifact_refs.length ? `，关联 ${memory.artifact_refs.length} 份运行产物` : ""}。
                  </p>
                ) : null}
              </div>
            </article>
          ))
        ) : (
          <p className="rounded-xl bg-[var(--tc-surface-muted)] px-3 py-4 text-center text-xs text-[var(--tc-text-muted)]">
            当前对话还没有运行记忆。
          </p>
        )}
      </div>
    </section>
  );
}

function MemoryReadableContent({ content }: { content: string }) {
  return (
    <div className="grid gap-2">
      {splitReadableContent(content).map((part, index) =>
        part.kind === "text" ? (
          <p key={index} className="whitespace-pre-wrap break-words">{part.text}</p>
        ) : (
          <MemoryReadableValue key={index} value={part.value} />
        ),
      )}
    </div>
  );
}

function MemoryReadableValue({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "string") return <MemoryReadableContent content={value} />;
  if (typeof value === "number") return <span>{value.toLocaleString("zh-CN")}</span>;
  if (typeof value === "boolean") return <span>{value ? "是" : "否"}</span>;
  if (Array.isArray(value)) {
    return (
      <ul className="grid gap-1">
        {value.map((item, index) => <li key={index}>· <MemoryReadableValue value={item} /></li>)}
      </ul>
    );
  }
  const entries = readableEntries(value);
  return (
    <dl className="grid gap-1.5 text-xs leading-5">
      {entries.map(entry => (
        <div key={entry.key} className="grid grid-cols-[96px_minmax(0,1fr)] gap-2">
          <dt className="text-[var(--tc-text-muted)]">{entry.label}</dt>
          <dd className="min-w-0 text-[var(--tc-text-secondary)]"><MemoryReadableValue value={entry.value} /></dd>
        </div>
      ))}
    </dl>
  );
}

function GeneralContextPanel({
  runs,
  selectedRun,
  snapshots,
  loading,
  onSelectRun,
}: {
  runs: GeneralAgentRun[];
  selectedRun: GeneralAgentRun | null;
  snapshots: GeneralAgentContextSnapshot[];
  loading: boolean;
  onSelectRun: (runId: string) => void;
}) {
  const [selectedSnapshotId, setSelectedSnapshotId] = useState("");
  const snapshot =
    snapshots.find(item => item.snapshot_id === selectedSnapshotId) ??
    snapshots.at(-1) ??
    selectedRun?.context_snapshot ??
    null;
  return (
    <section className="mt-3 rounded-2xl bg-[var(--tc-surface-card)] p-3">
      <div className="flex items-start justify-between gap-3 rounded-xl bg-[var(--tc-surface-muted)] px-3 py-2">
        <div className="flex min-w-0 items-start gap-2">
          <Layers3 className="mt-0.5 size-4 shrink-0 text-[var(--tc-text-secondary)]" />
          <div className="min-w-0">
            <p className="text-xs font-medium text-[var(--tc-text-primary)]">
              编排模型五层上下文
            </p>
            <p className="mt-0.5 text-xs leading-5 text-[var(--tc-text-muted)]">
              只读展示该次请求各编排阶段实际组装的快照，不代表子智能体输入或完整调用原文。
            </p>
          </div>
        </div>
        <label className="shrink-0 text-xs text-[var(--tc-text-muted)]">
          <span className="sr-only">选择请求</span>
          <select
            value={selectedRun?.run_id ?? ""}
            onChange={event => onSelectRun(event.target.value)}
            className="h-8 rounded-[var(--tc-radius-control)] bg-[var(--tc-surface-page)] px-2 text-xs text-[var(--tc-text-primary)] outline-none focus:ring-1 focus:ring-[var(--tc-text-primary)]"
          >
            {[...runs].reverse().map(run => (
              <option key={run.run_id} value={run.run_id}>
                第 {run.request_index} 次请求 · {formatTime(run.created_at)}
              </option>
            ))}
          </select>
        </label>
      </div>

      {loading ? (
        <p className="mt-2 rounded-xl bg-[var(--tc-surface-muted)] px-3 py-4 text-center text-xs text-[var(--tc-text-muted)]">
          正在读取上下文快照…
        </p>
      ) : snapshot ? (
        <div className="mt-2">
          <div className="flex flex-wrap items-center gap-2 px-1 py-1 text-xs text-[var(--tc-text-muted)]">
            {snapshots.length > 1 ? (
              <label>
                <span className="sr-only">选择编排阶段</span>
                <select
                  value={snapshot.snapshot_id}
                  onChange={event => setSelectedSnapshotId(event.target.value)}
                  className="h-7 rounded-[var(--tc-radius-control)] bg-[var(--tc-surface-page)] px-2 text-xs text-[var(--tc-text-primary)] outline-none focus:ring-1 focus:ring-[var(--tc-text-primary)]"
                >
                  {snapshots.map((item, index) => (
                    <option key={item.snapshot_id} value={item.snapshot_id}>
                      第 {index + 1} 份 · {contextPhaseLabels[item.phase]} · {formatTime(item.created_at)}
                    </option>
                  ))}
                </select>
              </label>
            ) : (
              <span>{contextPhaseLabels[snapshot.phase]}</span>
            )}
            <span>约 {snapshot.envelope.estimated_token_count.toLocaleString("zh-CN")} Token</span>
            <span>{snapshot.envelope.compressed ? "已压缩" : "未压缩"}</span>
            <span>{formatTime(snapshot.created_at)}</span>
          </div>
          <details className="px-1 py-2 text-xs text-[var(--tc-text-muted)]">
            <summary className="cursor-pointer">处理记录与计量详情</summary>
            <p className="mt-2">窗口：{snapshot.envelope.context_window_tokens?.toLocaleString("zh-CN") ?? "未配置"} Token · {snapshot.envelope.token_count_method ?? "旧快照"}</p>
            {(snapshot.envelope.pipeline_events ?? []).map(item => <div key={item.event_id} className="mt-2">
              <p>{({ truncate: "结果截断", clear: "陈旧结果清理", extract: "工作记忆抽取", fold: "局部折叠", full: "全量压缩" })[item.stage]} · {item.reason} · {item.status === "failed" ? "失败" : item.status === "skipped" ? "跳过" : "完成"}</p>
              <p>{item.before_tokens.toLocaleString("zh-CN")} → {item.after_tokens.toLocaleString("zh-CN")} Token</p>
              {item.detail ? <p>{item.detail}</p> : null}
              <details><summary className="cursor-pointer">来源范围</summary><p className="break-all">{item.source_ids.join("、") || "无新增来源"}</p></details>
            </div>)}
          </details>
          <div className="tc-editor-scrollbar mt-1 grid max-h-[420px] gap-2 overflow-y-auto pr-1">
            <ContextLayer
              title="稳定记忆（系统提示词）"
              summary={`${snapshot.envelope.stable_memory.length} 条规则`}
            >
              <ContextTextList items={snapshot.envelope.stable_memory} />
            </ContextLayer>
            <ContextLayer
              title="长期记忆"
              summary={`${snapshot.envelope.long_term_memory.length} 条偏好记忆`}
            >
              <ContextMemoryList
                memories={snapshot.envelope.long_term_memory}
                emptyText="当前尚未沉淀可用的用户偏好长期记忆。"
              />
            </ContextLayer>
            <ContextLayer
              title="历史对话"
              summary={`${snapshot.envelope.history_memory.messages.length} 条近期原文 · ${snapshot.envelope.history_memory.omitted_message_count} 条已摘要`}
            >
              {snapshot.envelope.history_memory.summary ? (
                <p className="mb-2 whitespace-pre-wrap text-xs leading-5 text-[var(--tc-text-secondary)]">
                  {snapshot.envelope.history_memory.summary}
                </p>
              ) : null}
              {snapshot.envelope.history_memory.messages.length ? (
                <div className="grid gap-2">
                  {snapshot.envelope.history_memory.messages.map((message, index) => (
                    <div
                      key={`${message.created_at}-${index}`}
                      className="rounded-[var(--tc-radius-control)] bg-[var(--tc-surface-page)] px-3 py-2"
                    >
                      <p className="text-xs text-[var(--tc-text-muted)]">
                        {messageRoleLabels[message.role]} · {formatTime(message.created_at)}
                      </p>
                      <p className="mt-1 whitespace-pre-wrap text-xs leading-5 text-[var(--tc-text-secondary)]">
                        {message.content}
                      </p>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-[var(--tc-text-muted)]">本次没有选入历史对话。</p>
              )}
            </ContextLayer>
            <ContextLayer
              title="工作记忆"
              summary={`${Object.values(snapshot.envelope.working_memory.session_memory ?? {}).reduce((count, items) => count + Object.keys(items).length, 0)} 条运行记忆 · ${snapshot.envelope.working_memory.node_summaries.length} 个节点概览`}
            >
              <ContextMemoryList
                memories={snapshot.envelope.working_memory.memories}
                emptyText="没有选入工作记忆。"
              />
              {snapshot.envelope.working_memory.plan_summary ? (
                <ContextJson
                  label="当前计划摘要"
                  value={snapshot.envelope.working_memory.plan_summary}
                />
              ) : null}
              {snapshot.envelope.working_memory.node_summaries.length ? (
                <ContextJson
                  label="节点摘要"
                  value={snapshot.envelope.working_memory.node_summaries}
                />
              ) : null}
              <ContextTextList
                label="未解决问题"
                items={snapshot.envelope.working_memory.unresolved_issues}
              />
              {snapshot.envelope.working_memory.session_memory ? (
                <SessionMemoryPanel memory={snapshot.envelope.working_memory.session_memory} />
              ) : null}
            </ContextLayer>
            <ContextLayer title="当前请求" summary="完整保留" defaultOpen>
              <p className="whitespace-pre-wrap text-xs leading-5 text-[var(--tc-text-primary)]">
                {snapshot.envelope.current_request.content}
              </p>
              <ContextTextList
                label="用户约束"
                items={snapshot.envelope.current_request.user_constraints}
              />
              <ContextJson label="正文范围" value={snapshot.envelope.current_request.scope} />
            </ContextLayer>
          </div>
        </div>
      ) : (
        <p className="mt-2 rounded-xl bg-[var(--tc-surface-muted)] px-3 py-4 text-center text-xs text-[var(--tc-text-muted)]">
          该次请求没有保存可查看的五层上下文快照。
        </p>
      )}
    </section>
  );
}

function ContextLayer({
  title,
  summary,
  defaultOpen = false,
  children,
}: {
  title: string;
  summary: string;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  return (
    <details
      open={defaultOpen || undefined}
      className="group rounded-xl bg-[var(--tc-surface-muted)] px-3 py-2"
    >
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 text-xs [&::-webkit-details-marker]:hidden">
        <span className="font-medium text-[var(--tc-text-primary)]">{title}</span>
        <span className="flex items-center gap-1 text-[var(--tc-text-muted)]">
          {summary}
          <ChevronRight className="size-3.5 transition-transform duration-150 group-open:rotate-90 motion-reduce:transition-none" />
        </span>
      </summary>
      <div className="mt-2 text-xs text-[var(--tc-text-secondary)]">{children}</div>
    </details>
  );
}

function ContextMemoryList({
  memories,
  emptyText,
}: {
  memories: GeneralAgentContextSnapshot["envelope"]["long_term_memory"];
  emptyText: string;
}) {
  if (!memories.length) {
    return <p className="text-[var(--tc-text-muted)]">{emptyText}</p>;
  }
  return (
    <div className="grid gap-2">
      {memories.map(memory => (
        <div
          key={memory.memory_id}
          className="rounded-[var(--tc-radius-control)] bg-[var(--tc-surface-page)] px-3 py-2"
        >
          <p className="text-[var(--tc-text-muted)]">
            {memoryKindLabels[memory.kind as AgentMemoryEntry["kind"]] ?? memory.kind}
          </p>
          <p className="mt-1 whitespace-pre-wrap leading-5">{memory.content}</p>
        </div>
      ))}
    </div>
  );
}

function ContextTextList({
  items,
  label,
}: {
  items: string[];
  label?: string;
}) {
  if (!items.length) {
    return null;
  }
  return (
    <div className={label ? "mt-3" : ""}>
      {label ? <p className="mb-1 text-[var(--tc-text-muted)]">{label}</p> : null}
      <ul className="grid gap-1 leading-5">
        {items.map((item, index) => (
          <li key={`${index}-${item}`}>· {item}</li>
        ))}
      </ul>
    </div>
  );
}

function ContextJson({ label, value }: { label: string; value: unknown }) {
  return (
    <details className="group mt-3">
      <summary className="cursor-pointer list-none text-[var(--tc-text-muted)] [&::-webkit-details-marker]:hidden">
        {label}
      </summary>
      <pre className="mt-1 max-h-52 overflow-auto whitespace-pre-wrap break-words rounded-[var(--tc-radius-control)] bg-[var(--tc-surface-page)] p-2 font-mono text-[11px] leading-5 text-[var(--tc-text-secondary)]">
        {JSON.stringify(value, null, 2)}
      </pre>
    </details>
  );
}

function GeneralConversationList({
  conversations,
  selectedConversationId,
  busy,
  onOpen,
  onDelete,
}: {
  conversations: GeneralAgentConversationSummary[];
  selectedConversationId: string;
  busy: boolean;
  onOpen: (conversationId: string) => void;
  onDelete: (conversation: GeneralAgentConversationSummary) => void;
}) {
  if (conversations.length === 0) {
    return <p className="px-2 py-3 text-xs text-[var(--tc-text-muted)]">暂无对话</p>;
  }
  return (
    <div className="grid gap-1">
      {conversations.map(conversation => (
        <div
          key={conversation.conversation_id}
          className={cn(
            "group grid grid-cols-[minmax(0,1fr)_28px] items-center rounded-[var(--tc-radius-control)]",
            selectedConversationId === conversation.conversation_id
              ? "bg-[var(--tc-surface-muted)]"
              : "hover:bg-[var(--tc-surface-muted)]",
          )}
        >
          <button
            type="button"
            className="min-w-0 px-2 py-2 text-left"
            onClick={() => onOpen(conversation.conversation_id)}
          >
            <span className="block truncate text-sm text-[var(--tc-text-primary)]">
              {conversation.title}
            </span>
            <span className="mt-0.5 flex items-center gap-2 text-xs text-[var(--tc-text-muted)]">
              <span>{generalRunStatusLabels[conversation.status]}</span>
              <span>{conversation.request_count} 次</span>
              <span>{formatTime(conversation.updated_at)}</span>
            </span>
          </button>
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            aria-label="删除对话"
            title="删除对话"
            disabled={busy || isGeneralAgentRunActive(conversation.status)}
            onClick={() => onDelete(conversation)}
            className="opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
          >
            <Trash2 className="size-3" />
          </Button>
        </div>
      ))}
    </div>
  );
}

const memoryKindLabels: Record<AgentMemoryEntry["kind"], string> = {
  user_instruction: "作者约束",
  task_summary: "任务摘要",
  resource_summary: "资料摘要",
  work_note: "过程记录",
  unresolved_issue: "未解决事项",
  fact_reference: "事实来源提示",
};

const memoryValidityLabels: Record<AgentMemoryEntry["validity"], string> = {
  active: "当前有效",
  stale: "来源已变化",
  rejected: "审查未通过",
  superseded: "已被新版本替代",
};

const contextPhaseLabels: Record<GeneralAgentContextSnapshot["phase"], string> = {
  plan: "规划阶段",
  replan: "重规划阶段",
  verify: "结果校验阶段",
};

const messageRoleLabels: Record<
  GeneralAgentContextSnapshot["envelope"]["history_memory"]["messages"][number]["role"],
  string
> = {
  user: "作者",
  assistant: "太初",
};

function GeneralRunPanel({
  run,
  nodes,
  continuedByRequestIndex,
  busy,
  clarificationAnswer,
  secondConfirmation,
  copied,
  onClarificationAnswerChange,
  onSecondConfirmationChange,
  onResume,
  onCopy,
}: {
  run: GeneralAgentRun;
  nodes: GeneralAgentNodeRun[];
  continuedByRequestIndex?: number;
  busy: boolean;
  clarificationAnswer: string;
  secondConfirmation: boolean;
  copied: boolean;
  onClarificationAnswerChange: (value: string) => void;
  onSecondConfirmationChange: (value: boolean) => void;
  onResume: (request: {
    answer?: string;
    approve?: boolean;
    second_confirmation?: boolean;
    effect_resolution?: "recheck" | "confirm_not_applied" | "cancel";
  }) => void;
  onCopy: () => void;
}) {
  const [planOpen, setPlanOpen] = useState(false);
  const request = continuedByRequestIndex ? null : run.pending_human_request;
  const hasLongGoal = run.user_goal.length > 180;
  const progressSummary = generalRunProgressSummary(run);
  const durationSummary = generalRunDurationLabel(run);
  return (
    <section className="w-full pb-5">
      <div className="flex justify-end">
        <div className="max-w-[72%]">
          <div className="rounded-2xl bg-[var(--tc-surface-page)] px-4 py-3">
            {hasLongGoal ? (
              <details className="group">
                <summary className="cursor-pointer list-none [&::-webkit-details-marker]:hidden">
                  <span className="line-clamp-4 cursor-text select-text whitespace-pre-wrap text-[15px] leading-7 text-[var(--tc-text-primary)] selection:bg-[var(--tc-text-primary)] selection:text-[var(--tc-surface-page)] group-open:line-clamp-none">
                    {run.user_goal}
                  </span>
                  <span className="mt-2 inline-flex items-center gap-1 text-xs text-[var(--tc-text-muted)]">
                    <span className="group-open:hidden">展开完整消息</span>
                    <span className="hidden group-open:inline">收起消息</span>
                    <ChevronDown className="size-3.5 transition-transform duration-150 group-open:rotate-180 motion-reduce:transition-none" />
                  </span>
                </summary>
              </details>
            ) : (
              <p className="cursor-text select-text whitespace-pre-wrap text-[15px] leading-7 text-[var(--tc-text-primary)] selection:bg-[var(--tc-text-primary)] selection:text-[var(--tc-surface-page)]">
                {run.user_goal}
              </p>
            )}
          </div>
          <p className="mt-1.5 px-1 text-right font-mono text-[11px] text-[var(--tc-text-muted)]">
            {formatTime(run.created_at)}
          </p>
        </div>
      </div>

      <div className="mt-5 flex justify-center">
        <div className="flex flex-wrap items-center justify-center gap-x-2 gap-y-1 rounded-full bg-[var(--tc-surface-page)] px-4 py-2 text-xs text-[var(--tc-text-muted)]">
          <span className="flex items-center gap-1.5 text-[var(--tc-text-secondary)]">
            <RunStatusIcon status={run.status} />
            {continuedByRequestIndex
              ? `已由第 ${continuedByRequestIndex} 次请求接续`
              : generalRunStatusLabels[run.status]}
          </span>
          <span aria-hidden="true">·</span>
          <span>{progressSummary}</span>
        </div>
      </div>

      {continuedByRequestIndex ? (
        <p className="mt-3 text-center text-xs text-[var(--tc-text-muted)]">
          本次请求停在待确认状态，后续输入已作为第 {continuedByRequestIndex} 次请求独立执行。
        </p>
      ) : null}

      {request?.kind === "clarification" ? (
        <div className="mt-6 mr-auto max-w-[760px] rounded-2xl bg-[var(--tc-surface-card)] p-4">
          <div className="flex items-start gap-2">
            <CirclePause className="mt-0.5 size-4 shrink-0" />
            <div className="min-w-0 flex-1">
              <h4 className="text-sm font-semibold text-[var(--tc-text-primary)]">
                需要你补充一个信息
              </h4>
              <p className="mt-1 text-sm leading-6 text-[var(--tc-text-secondary)]">
                {request.prompt}
              </p>
              <textarea
                value={clarificationAnswer}
                onChange={event => onClarificationAnswerChange(event.target.value)}
                rows={3}
                placeholder="输入你的回答"
                className="mt-3 w-full resize-y rounded-xl border border-[var(--tc-border-subtle)] bg-[var(--tc-surface-muted)] px-3 py-2 text-sm text-[var(--tc-text-primary)] outline-none placeholder:text-[var(--tc-text-muted)] focus:border-[var(--tc-border-strong)]"
              />
              <div className="mt-3 flex justify-end">
                <Button
                  type="button"
                  disabled={busy || !clarificationAnswer.trim()}
                  onClick={() => onResume({ answer: clarificationAnswer.trim() })}
                >
                  <RotateCcw className="size-4" />
                  补充并继续
                </Button>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {request?.kind === "write_authorization" ? (
        <div className="mt-6 mr-auto max-w-[760px] rounded-2xl bg-[var(--tc-surface-card)] p-4">
          <div className="flex items-start gap-2">
            <ShieldCheck className="mt-0.5 size-4 shrink-0" />
            <div className="min-w-0 flex-1">
              <h4 className="text-sm font-semibold text-[var(--tc-text-primary)]">
                等待写入授权
              </h4>
              <p className="mt-1 text-sm leading-6 text-[var(--tc-text-secondary)]">
                {request.prompt}
              </p>
              <div className="mt-3 grid gap-2 rounded-xl bg-[var(--tc-surface-muted)] p-3 text-xs text-[var(--tc-text-muted)]">
                <p>
                  操作：{generalCapabilityLabel(request.tool_name ?? "")}
                </p>
                <p>
                  作用范围：
                  {request.resource_scopes.map(formatResourceScope).join("、") ||
                    "未标明"}
                </p>
                <p className="break-all font-mono">输入哈希：{request.input_sha256}</p>
              </div>
              <details className="group mt-3">
                <summary className="inline-flex cursor-pointer list-none items-center gap-1.5 rounded-full bg-[var(--tc-surface-muted)] px-3 py-2 text-xs text-[var(--tc-text-secondary)] [&::-webkit-details-marker]:hidden">
                  查看确定输入（技术字段）
                  <ChevronDown className="size-3.5 transition-transform duration-150 group-open:rotate-180 motion-reduce:transition-none" />
                </summary>
                <div className="mt-2 rounded-xl bg-[var(--tc-surface-muted)] p-3">
                  <p className="text-xs text-[var(--tc-text-muted)]">
                    以下为授权绑定的技术输入，字段名称以接口契约为准。
                  </p>
                  <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-xl bg-[var(--tc-surface-page)] p-3 font-mono text-xs leading-5 text-[var(--tc-text-secondary)]">
                    {JSON.stringify(request.input_summary, null, 2)}
                  </pre>
                </div>
              </details>
              {request.second_confirmation_required ? (
                <label className="mt-3 flex items-center gap-2 rounded-xl bg-[var(--tc-surface-muted)] p-3 text-sm text-[var(--tc-text-secondary)]">
                  <Checkbox
                    checked={secondConfirmation}
                    onCheckedChange={onSecondConfirmationChange}
                  />
                  我已再次确认这是高风险写入
                </label>
              ) : null}
              <div className="mt-3 flex justify-end gap-2">
                <Button
                  type="button"
                  variant="outline"
                  disabled={busy}
                  onClick={() => onResume({ approve: false })}
                >
                  <Ban className="size-4" />
                  拒绝写入
                </Button>
                <Button
                  type="button"
                  disabled={
                    busy ||
                    (request.second_confirmation_required && !secondConfirmation)
                  }
                  onClick={() =>
                    onResume({
                      approve: true,
                      second_confirmation: secondConfirmation,
                    })
                  }
                >
                  <Check className="size-4" />
                  授权并继续
                </Button>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {request?.kind === "effect_reconciliation" ? (
        <div className="mt-6 mr-auto max-w-[760px] rounded-2xl bg-[var(--tc-surface-card)] p-4">
          <div className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            <div className="min-w-0 flex-1">
              <h4 className="text-sm font-semibold text-[var(--tc-text-primary)]">
                需要核对真实写入结果
              </h4>
              <p className="mt-1 text-sm leading-6 text-[var(--tc-text-secondary)]">
                {request.prompt}
              </p>
              <div className="mt-3 grid gap-2 rounded-xl bg-[var(--tc-surface-muted)] p-3 text-xs text-[var(--tc-text-muted)]">
                <p>操作：{generalCapabilityLabel(request.tool_name ?? "")}</p>
                <p>
                  作用范围：
                  {request.resource_scopes.map(formatResourceScope).join("、") ||
                    "未标明"}
                </p>
                <p className="break-all font-mono">
                  副作用记录：{request.effect_id ?? "未标明"}
                </p>
              </div>
              <p className="mt-3 text-xs leading-5 text-[var(--tc-text-muted)]">
                重新核对只读取真实资源；只有你确认原写入没有生效时，系统才会按冻结输入重试。
              </p>
              <div className="mt-3 flex flex-wrap justify-end gap-2">
                <Button
                  type="button"
                  variant="outline"
                  disabled={busy}
                  onClick={() => onResume({ effect_resolution: "cancel" })}
                >
                  <Ban className="size-4" />
                  停止任务
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  disabled={busy}
                  onClick={() =>
                    onResume({ effect_resolution: "confirm_not_applied" })
                  }
                >
                  <RefreshCw className="size-4" />
                  确认未写入并重试
                </Button>
                <Button
                  type="button"
                  disabled={busy}
                  onClick={() => onResume({ effect_resolution: "recheck" })}
                >
                  <RotateCcw className="size-4" />
                  重新核对资源
                </Button>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {run.plan || run.final_answer ? (
        <article className="mt-8 flex items-start gap-3">
          <span
            aria-hidden="true"
            className="tc-display-font flex size-8 shrink-0 items-center justify-center rounded-full bg-[var(--tc-surface-page)] text-sm text-[var(--tc-text-primary)]"
          >
            初
          </span>
          <div className="min-w-0 max-w-[760px] flex-1">
            <div className="mb-2 flex items-center gap-1 px-1">
              <h4 className="text-sm font-medium text-[var(--tc-text-primary)]">
                太初
              </h4>
              <Button
                type="button"
                variant="ghost"
                size="xs"
                aria-label={`${planOpen ? "收起" : "查看"}本次处理详情，${durationSummary}`}
                title={planOpen ? "收起本次处理详情" : "查看本次处理详情"}
                aria-expanded={planOpen}
                onClick={() => setPlanOpen(current => !current)}
                className="text-[var(--tc-text-muted)] hover:text-[var(--tc-text-primary)]"
              >
                {durationSummary}
                <ChevronRight
                  className={cn(
                    "size-3.5 transition-transform duration-150 motion-reduce:transition-none",
                    planOpen && "rotate-90",
                  )}
                />
              </Button>
            </div>
            {run.final_answer ? (
              <div className="cursor-text select-text px-1 py-1 selection:bg-[var(--tc-text-primary)] selection:text-[var(--tc-surface-page)]">
                <MarkdownContent content={run.final_answer} />
                {run.verification_issues.length ? (
                  <div className="mt-3 rounded-xl bg-[var(--tc-surface-page)] p-3 text-xs leading-5 text-[var(--tc-text-muted)]">
                    未完全解决：{run.verification_issues.join("；")}
                  </div>
                ) : null}
              </div>
            ) : null}
            {run.final_answer ? (
              <div className="mt-1 flex items-center px-1">
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-xs"
                  aria-label={copied ? "回复已复制" : "复制回复"}
                  title={copied ? "回复已复制" : "复制回复"}
                  onClick={onCopy}
                >
                  {copied ? (
                    <Check className="size-3.5" />
                  ) : (
                    <Clipboard className="size-3.5" />
                  )}
                </Button>
              </div>
            ) : null}
            {planOpen ? (
              <div className="mt-2 rounded-2xl bg-[var(--tc-surface-card)] p-4">
                <div className="mb-3 flex flex-wrap gap-2 text-xs text-[var(--tc-text-muted)]">
                  <span className="rounded-full bg-[var(--tc-surface-muted)] px-2.5 py-1">
                    本次使用 {run.compression_stats.selected_memory_count} 条自动运行记忆
                  </span>
                  <span className="rounded-full bg-[var(--tc-surface-muted)] px-2.5 py-1">
                    上下文{run.compression_stats.compressed ? "已压缩" : "未压缩"} ·
                    约 {run.compression_stats.estimated_token_count.toLocaleString("zh-CN")} Token
                  </span>
                </div>
                {run.plan ? (
                  <>
                    <p className="text-xs leading-5 text-[var(--tc-text-muted)]">
                      {run.plan.rationale}
                    </p>
                    <div className="mt-3 grid gap-2">
                      {nodes.length ? (
                        nodes.map(node => (
                          <GeneralNodeRow
                            key={node.node_id}
                            node={node}
                            runStatus={run.status}
                          />
                        ))
                      ) : (
                        <p className="rounded-xl bg-[var(--tc-surface-muted)] px-3 py-2 text-sm text-[var(--tc-text-muted)]">
                          本次任务无需调用额外能力。
                        </p>
                      )}
                    </div>
                  </>
                ) : (
                  <p className="text-sm text-[var(--tc-text-muted)]">
                    计划尚未生成。
                  </p>
                )}
              </div>
            ) : null}
          </div>
        </article>
      ) : null}

      {run.errors.length ? (
        <details className="group mt-3 ml-11 max-w-[760px]">
          <summary className="inline-flex cursor-pointer list-none items-center gap-1.5 rounded-full bg-[var(--tc-surface-muted)] px-3 py-2 text-xs text-[var(--tc-text-secondary)] [&::-webkit-details-marker]:hidden">
            查看运行错误
            <ChevronDown className="size-3.5 transition-transform duration-150 group-open:rotate-180 motion-reduce:transition-none" />
          </summary>
          <ul className="mt-2 grid gap-2 rounded-2xl bg-[var(--tc-surface-card)] p-3 text-xs text-[var(--tc-text-muted)]">
            {run.errors.map((item, index) => (
              <li
                key={`${index}-${item}`}
                className="rounded-xl bg-[var(--tc-surface-muted)] px-3 py-2"
              >
                {item}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}

function GeneralNodeRow({
  node,
  runStatus,
}: {
  node: GeneralAgentNodeRun;
  runStatus: GeneralAgentRunStatus;
}) {
  const visibleErrorMessage = generalNodeErrorMessage(
    node.error_message,
    runStatus,
  );
  return (
    <div className="grid grid-cols-[20px_minmax(0,1fr)_auto] items-start gap-2 rounded-xl bg-[var(--tc-surface-muted)] px-3 py-2.5">
      <ChevronRight className="mt-0.5 size-4 text-[var(--tc-text-muted)]" />
      <div className="min-w-0">
        <p className="text-sm font-medium text-[var(--tc-text-primary)]">
          {generalCapabilityLabel(node.capability_name)}
        </p>
        <p className="mt-0.5 line-clamp-2 text-xs leading-5 text-[var(--tc-text-muted)]">
          {node.objective}
        </p>
        {visibleErrorMessage ? (
          <p className="mt-1 text-xs text-[var(--tc-text-secondary)]">
            {visibleErrorMessage}
          </p>
        ) : null}
      </div>
      <span className="whitespace-nowrap text-xs text-[var(--tc-text-muted)]">
        {generalNodeStatusLabel(node.status, runStatus)}
      </span>
    </div>
  );
}

function RunStatusIcon({ status }: { status: GeneralAgentRunStatus }) {
  if (isGeneralAgentRunActive(status)) {
    return (
      <LoaderCircle className="size-4 animate-spin text-[var(--tc-text-secondary)] motion-reduce:animate-none" />
    );
  }
  if (status === "waiting_human") {
    return <CirclePause className="size-4 text-[var(--tc-text-secondary)]" />;
  }
  if (status === "completed") {
    return <Check className="size-4 text-[var(--tc-text-secondary)]" />;
  }
  if (status === "cancelled") {
    return <Ban className="size-4 text-[var(--tc-text-secondary)]" />;
  }
  return <AlertTriangle className="size-4 text-[var(--tc-text-secondary)]" />;
}

function formatResourceScope(scope: string): string {
  const [prefix, ...rest] = scope.split(":");
  const value = rest.join(":");
  const labels: Record<string, string> = {
    chapter_id: "章节",
    chapter_ids: "章节",
    card_id: "知识卡",
    card_ids: "知识卡",
    volume_id: "分卷",
    parent_id: "上级结构",
    item_ids: "结构项",
  };
  if (prefix === "tool") {
    return `操作：${generalCapabilityLabel(value)}`;
  }
  return labels[prefix] ? `${labels[prefix]}：${value}` : `资源：${scope}`;
}

function shortText(value: string, maxLength: number): string {
  return value.length > maxLength ? `${value.slice(0, maxLength)}…` : value;
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "时间未知";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function generalRunDurationLabel(run: GeneralAgentRun): string {
  const startedAt = new Date(run.started_at).getTime();
  const finishedAt = new Date(run.finished_at || run.updated_at).getTime();
  if (!Number.isFinite(startedAt) || !Number.isFinite(finishedAt)) {
    return "已处理：时间未知";
  }
  const totalSeconds = Math.max(0, Math.floor((finishedAt - startedAt) / 1_000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `已处理：${minutes}分${seconds}秒`;
}

function errorMessage(value: unknown): string {
  return value instanceof Error ? value.message : "通用写作助手请求失败。";
}
